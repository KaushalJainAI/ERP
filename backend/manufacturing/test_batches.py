"""Production batches: grind to bulk, then pack per SKU (planning doc 03, Q2).

Numbers used throughout: grinding BOM makes 100 kg with 1.25 % loss, so a Work
Order for 98.75 kg needs exactly 80 kg turmeric and 20 kg salt. Grinding posts
96 kg of bulk (4 kg loss). Packing uses 60 kg: 100 x 500 g (50 kg) and 5 x 1 kg
(5 kg) packs, so 5 kg is packing loss.
"""

import datetime
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from core.models import DocStatus
from manufacturing.models import (
    BOM,
    Batch,
    BatchStage,
    BatchStatus,
    BOMItem,
    WorkOrder,
    WorkOrderStatus,
)
from manufacturing.services import batches as batch_service
from manufacturing.tests import ManuBase
from stock.models import (
    Bin,
    Item,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    Warehouse,
)
from users.models import User

D0 = datetime.date


class BatchBase(ManuBase):
    def setUp(self):
        super().setUp()
        # The company's stage warehouses (P-1): Unprocessed = raw, In Process =
        # wip (its own Work In Progress account), Ready = fg.
        self.company.unprocessed_warehouse = self.raw
        self.company.in_process_warehouse = self.wip
        self.company.ready_warehouse = self.fg
        self.company.save()

        group = self.turmeric.item_group
        uom = self.turmeric.stock_uom
        self.pouch = Item.objects.create(
            code="POUCH-500", name="POUCH-500", item_group=group, stock_uom=uom
        )
        self.sku_500 = Item.objects.create(
            code="HALDI-500GM", name="HALDI-500GM", item_group=group, stock_uom=uom
        )
        self.sku_1kg = Item.objects.create(
            code="HALDI-1KG", name="HALDI-1KG", item_group=group, stock_uom=uom
        )
        self.sku_250 = Item.objects.create(
            code="HALDI-250GM", name="HALDI-250GM", item_group=group, stock_uom=uom
        )

        # Grinding: 100 kg basis, 1.25 % loss, no scrap.
        self.grind_bom = BOM.objects.create(
            name="BOM-GRIND",
            company=self.company,
            item=self.powder,
            quantity=D("100"),
            process_loss_percentage=D("1.25"),
            is_default=True,
        )
        BOMItem.objects.create(
            bom=self.grind_bom, idx=1, item=self.turmeric, qty=D("80")
        )
        BOMItem.objects.create(bom=self.grind_bom, idx=2, item=self.salt, qty=D("20"))

        # Packing: one pack each, bulk per pack plus pouch.
        self.pack_500 = self._pack_bom(self.sku_500, D("0.5"))
        self.pack_1kg = self._pack_bom(self.sku_1kg, D("1"))

        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        self.receipt(self.pouch, self.raw, "1000", "2")

    def _pack_bom(self, sku, bulk_per_pack):
        bom = BOM.objects.create(
            name=f"BOM-{sku.code}",
            company=self.company,
            item=sku,
            quantity=D("1"),
            process_loss_percentage=D("0"),
            is_default=True,
        )
        BOMItem.objects.create(bom=bom, idx=1, item=self.powder, qty=bulk_per_pack)
        BOMItem.objects.create(bom=bom, idx=2, item=self.pouch, qty=D("1"))
        return bom

    # ---------------------------------------------------------------- helpers
    def bin_qty(self, item, warehouse):
        row = Bin.objects.filter(item=item, warehouse=warehouse).first()
        return row.actual_qty if row else D("0.000")

    def bin_value(self, item, warehouse):
        row = Bin.objects.filter(item=item, warehouse=warehouse).first()
        return row.stock_value if row else D("0.00")

    def loss_warehouse(self):
        return Warehouse.objects.get(company=self.company, name="Process Loss")

    def start(self, qty="98.75"):
        batch = batch_service.start_grinding(self.company, self.powder, qty)
        return batch, batch.grinding_work_order

    def snapshot(self):
        return {
            "bins": sorted(
                (b.item.code, b.warehouse.name, b.actual_qty, b.stock_value)
                for b in Bin.objects.all()
            ),
            "entries": StockEntry.objects.filter(docstatus=DocStatus.SUBMITTED).count(),
            "work_orders": WorkOrder.objects.count(),
        }


class BatchFullCycleTests(BatchBase):
    def test_full_cycle_moves_stock_and_verify_integrity_is_green(self):
        batch, wo = self.start()
        # start_grinding posts nothing: raw is still on the shelf.
        self.assertEqual(batch.status, BatchStatus.GRINDING)
        self.assertEqual(wo.stage, BatchStage.GRINDING)
        self.assertEqual(self.bin_qty(self.turmeric, self.raw), D("80.000"))
        self.assertFalse(
            StockEntry.objects.filter(
                work_order=wo, docstatus=DocStatus.SUBMITTED
            ).exists()
        )

        # Grinding: 96 kg actually produced, 4 kg grinding loss.
        batch = batch_service.finish_grinding(wo, "96")
        wo.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.PACKING)
        self.assertEqual(wo.stage, BatchStage.PACKING)
        self.assertEqual(wo.produced_qty, D("96.000"))
        self.assertEqual(wo.process_loss_qty, D("4.000"))
        self.assertEqual(self.bin_qty(self.turmeric, self.raw), D("0.000"))
        self.assertEqual(self.bin_qty(self.powder, self.wip), D("96.000"))
        self.assertEqual(self.bin_value(self.powder, self.wip), D("8400.00"))

        # Packing: 60 kg used, 55 packed, 5 kg loss to Process Loss.
        result = batch_service.pack(
            wo, "60", [(self.sku_500, "100"), (self.sku_1kg, "5")]
        )
        batch.refresh_from_db()
        self.assertEqual(result["packing_loss"], D("5.000"))
        self.assertEqual(result["bulk_packed"], D("55.000"))
        self.assertEqual(batch.status, BatchStatus.PACKING)
        self.assertEqual(self.bin_qty(self.powder, self.wip), D("36.000"))
        self.assertEqual(self.bin_qty(self.powder, self.loss_warehouse()), D("5.000"))
        self.assertEqual(self.bin_qty(self.sku_500, self.fg), D("100.000"))
        self.assertEqual(self.bin_value(self.sku_500, self.fg), D("4575.00"))
        self.assertEqual(self.bin_qty(self.sku_1kg, self.fg), D("5.000"))
        self.assertEqual(self.bin_value(self.sku_1kg, self.fg), D("447.50"))
        self.assertEqual(self.bin_qty(self.pouch, self.raw), D("895.000"))

        # The rest of the bulk goes into the 500 g pack: the batch is done.
        batch_service.pack(wo, "36", [(self.sku_500, "72")])
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.DONE)
        self.assertEqual(self.bin_qty(self.powder, self.wip), D("0.000"))
        self.assertEqual(self.bin_qty(self.sku_500, self.fg), D("172.000"))

        # Every packing Work Order belongs to the batch and is Ready.
        packing = WorkOrder.objects.filter(batch=batch)
        self.assertEqual(packing.count(), 3)
        self.assertTrue(all(p.stage == BatchStage.READY for p in packing))
        self.assertEqual(Batch.objects.count(), 1)

        # verify_integrity raises CommandError when any check fails.
        call_command("verify_integrity", stdout=StringIO())

    def test_loss_warehouse_is_created_once(self):
        self.assertFalse(Warehouse.objects.filter(name="Process Loss").exists())
        first = batch_service.ensure_loss_warehouse(self.company)
        second = batch_service.ensure_loss_warehouse(self.company)
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(first.is_group)
        self.assertEqual(Warehouse.objects.filter(name="Process Loss").count(), 1)


class BatchCancelTests(BatchBase):
    def test_cancel_restores_stock_and_reverses_gl(self):
        before = {
            "turmeric": self.bin_qty(self.turmeric, self.raw),
            "salt": self.bin_qty(self.salt, self.raw),
            "pouch": self.bin_qty(self.pouch, self.raw),
        }
        batch, wo = self.start()
        batch_service.finish_grinding(wo, "96")
        batch_service.pack(wo, "60", [(self.sku_500, "100"), (self.sku_1kg, "5")])

        batch_service.cancel_batch(wo)
        batch.refresh_from_db()
        wo.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.CANCELLED)
        self.assertEqual(wo.status, WorkOrderStatus.CANCELLED)
        self.assertTrue(
            WorkOrder.objects.filter(
                batch=batch, status=WorkOrderStatus.CANCELLED
            ).count()
            == 2
        )

        self.assertEqual(self.bin_qty(self.turmeric, self.raw), before["turmeric"])
        self.assertEqual(self.bin_qty(self.salt, self.raw), before["salt"])
        self.assertEqual(self.bin_qty(self.pouch, self.raw), before["pouch"])
        self.assertEqual(self.bin_value(self.turmeric, self.raw), D("8000.00"))
        self.assertEqual(self.bin_qty(self.powder, self.wip), D("0.000"))
        self.assertEqual(self.bin_qty(self.powder, self.loss_warehouse()), D("0.000"))
        self.assertEqual(self.bin_qty(self.sku_500, self.fg), D("0.000"))
        self.assertEqual(self.bin_qty(self.sku_1kg, self.fg), D("0.000"))

        out = StringIO()
        call_command("verify_integrity", stdout=out)

    def test_cancel_twice_is_refused(self):
        _batch, wo = self.start()
        batch_service.cancel_batch(wo)
        with self.assertRaisesMessage(ValidationError, "already cancelled"):
            batch_service.cancel_batch(wo)


class BatchRefusalTests(BatchBase):
    def grind_to_packing(self):
        batch, wo = self.start()
        batch_service.finish_grinding(wo, "96")
        return batch, wo

    def test_pack_refused_when_bulk_used_exceeds_bulk_produced(self):
        _batch, wo = self.grind_to_packing()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "left in this batch"):
            batch_service.pack(wo, "97", [(self.sku_500, "100"), (self.sku_1kg, "5")])
        self.assertEqual(self.snapshot(), before)

    def test_pack_refused_when_packets_need_more_bulk_than_used(self):
        _batch, wo = self.grind_to_packing()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "need"):
            batch_service.pack(wo, "50", [(self.sku_500, "100"), (self.sku_1kg, "5")])
        self.assertEqual(self.snapshot(), before)

    def test_sku_without_bom_refused_and_nothing_partial_is_posted(self):
        batch, wo = self.grind_to_packing()
        before = self.snapshot()
        # The first SKU is valid, so it would post if the second were not checked first.
        with self.assertRaisesMessage(ValidationError, "has no default BOM"):
            batch_service.pack(wo, "55", [(self.sku_500, "100"), (self.sku_250, "10")])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(WorkOrder.objects.filter(batch=batch).exists())

    def test_pack_refused_before_grinding_is_finished(self):
        _batch, wo = self.start()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "Only a batch in Packing"):
            batch_service.pack(wo, "10", [(self.sku_500, "20")])
        self.assertEqual(self.snapshot(), before)

    def test_finish_grinding_twice_is_refused(self):
        _batch, wo = self.grind_to_packing()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "Only a batch in Grinding"):
            batch_service.finish_grinding(wo, "90")
        self.assertEqual(self.snapshot(), before)

    def test_output_above_planned_is_refused(self):
        _batch, wo = self.start()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "more than the"):
            batch_service.finish_grinding(wo, "99")
        self.assertEqual(self.snapshot(), before)
        wo.refresh_from_db()
        self.assertEqual(wo.stage, BatchStage.GRINDING)

    def test_short_raw_material_refuses_finish_and_rolls_back(self):
        batch, wo = self.start()
        # Issue 10 kg turmeric away so only 70 kg is left; grinding needs 80.
        ste = StockEntry.objects.create(
            name="STE-ISSUE-SHORT",
            stock_entry_type=StockEntryType.MATERIAL_ISSUE,
            posting_date=D0(2026, 4, 2),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste,
            item=self.turmeric,
            source_warehouse=self.raw,
            qty=D("10"),
            valuation_rate=D("100"),
        )
        ste.submit()
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            batch_service.finish_grinding(wo, "96")
        batch.refresh_from_db()
        wo.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.GRINDING)
        self.assertEqual(wo.stage, BatchStage.GRINDING)
        self.assertEqual(wo.produced_qty, D("0.000"))
        self.assertEqual(self.snapshot(), before)

    def test_start_without_default_bom_is_refused(self):
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "has no default BOM"):
            batch_service.start_grinding(self.company, self.salt, "10")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Batch.objects.exists())

    def test_start_refused_without_company_stage_warehouses(self):
        self.company.in_process_warehouse = None
        self.company.save()
        with self.assertRaisesMessage(ValidationError, "warehouses for the company"):
            batch_service.start_grinding(self.company, self.powder, "98.75")
        self.assertFalse(Batch.objects.exists())


class PackingLossWarningTests(BatchBase):
    """The allowance is the SKU BOMs' process_loss_percentage. It only warns."""

    def set_allowance(self, pct):
        BOM.objects.filter(pk__in=[self.pack_500.pk, self.pack_1kg.pk]).update(
            process_loss_percentage=D(pct)
        )

    def grind_to_packing(self):
        batch, wo = self.start()
        batch_service.finish_grinding(wo, "96")
        return batch, wo

    def test_loss_above_allowance_warns_and_still_posts(self):
        _batch, wo = self.grind_to_packing()
        # Allowance 0 (the BOMs as built). 5 kg of 60 kg is loss.
        result = batch_service.pack(
            wo, "60", [(self.sku_500, "100"), (self.sku_1kg, "5")]
        )
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("above the", result["warnings"][0])
        # The posting went through unchanged.
        self.assertEqual(result["packing_loss"], D("5.000"))
        self.assertEqual(self.bin_qty(self.powder, self.loss_warehouse()), D("5.000"))
        self.assertEqual(self.bin_qty(self.sku_500, self.fg), D("100.000"))

    def test_no_warning_when_there_is_no_loss(self):
        _batch, wo = self.grind_to_packing()
        result = batch_service.pack(
            wo, "55", [(self.sku_500, "100"), (self.sku_1kg, "5")]
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["packing_loss"], D("0.000"))
        self.assertEqual(self.bin_qty(self.sku_500, self.fg), D("100.000"))

    def test_no_warning_at_or_below_allowance_and_posts(self):
        self.set_allowance("10")
        _batch, wo = self.grind_to_packing()
        # Gross bulk for 100 x 500 g and 5 x 1 kg at a 10 % allowance is about
        # 61.112; 62 leaves about 0.888 loss, 1.43 % of bulk used: within 10 %.
        result = batch_service.pack(
            wo, "62", [(self.sku_500, "100"), (self.sku_1kg, "5")]
        )
        self.assertEqual(result["warnings"], [])
        self.assertGreater(result["packing_loss"], D("0"))
        self.assertEqual(
            self.bin_qty(self.powder, self.loss_warehouse()), result["packing_loss"]
        )

    def test_warning_above_a_nonzero_allowance_still_posts(self):
        self.set_allowance("1")
        _batch, wo = self.grind_to_packing()
        # Same 62 kg: about 6.44 kg loss, about 10.4 % of bulk used, above 1 %.
        result = batch_service.pack(
            wo, "62", [(self.sku_500, "100"), (self.sku_1kg, "5")]
        )
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(
            self.bin_qty(self.powder, self.loss_warehouse()), result["packing_loss"]
        )


class BatchApiTests(BatchBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("batch_admin", "b@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def test_api_start_finish_pack_and_cancel(self):
        r = self.c.post(
            "/api/manufacturing/batches/",
            {
                "company": self.company.pk,
                "bulk_item": self.powder.pk,
                "bulk_qty": "98.75",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["status"], BatchStatus.GRINDING)
        batch_id = r.data["id"]

        r = self.c.post(
            f"/api/manufacturing/batches/{batch_id}/finish-grinding/",
            {"bulk_qty": "96"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], BatchStatus.PACKING)

        r = self.c.post(
            f"/api/manufacturing/batches/{batch_id}/pack/",
            {
                "bulk_used": "60",
                "lines": [
                    {"sku": self.sku_500.pk, "packets": "100"},
                    {"sku": self.sku_1kg.pk, "packets": "5"},
                ],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["packing_loss"], "5.000")

        r = self.c.post(
            f"/api/manufacturing/batches/{batch_id}/cancel/", {}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], BatchStatus.CANCELLED)

    def test_api_refusal_returns_400_and_posts_nothing(self):
        r = self.c.post(
            "/api/manufacturing/batches/",
            {"company": self.company.pk, "bulk_item": self.salt.pk, "bulk_qty": "10"},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Batch.objects.exists())

    def test_api_batch_is_listed_and_bad_lines_are_refused(self):
        batch, wo = self.start()
        r = self.c.get("/api/manufacturing/batches/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data["results"]), 1)
        r = self.c.post(
            f"/api/manufacturing/batches/{batch.pk}/pack/",
            {"bulk_used": "10", "lines": [{"sku": 999999, "packets": "1"}]},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
