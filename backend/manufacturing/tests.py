"""Manufacturing (BOM, Work Order, transfer / manufacture / repack entries).

Run with ``pytest manufacturing``.  The GL assertions need perpetual inventory
and a WIP warehouse that has its own Stock account (design §14.4).
"""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from core.models import Company, DocStatus, FiscalYear
from manufacturing.models import BOM, WorkOrder, WorkOrderStatus
from manufacturing.services import costing
from manufacturing.services import work_orders as wo_service
from stock.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    Warehouse,
)
from users.models import User

D0 = datetime.date


class ManuBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Test", abbr="NT", default_currency="INR"
        )
        FiscalYear.objects.create(
            name="2026-2027",
            start_date=D0(2026, 4, 1),
            end_date=D0(2027, 3, 31),
        )
        self.stock_acc = Account.objects.create(
            company=self.company,
            name="Stock Assets",
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )
        self.wip_acc = Account.objects.create(
            company=self.company,
            name="Work In Progress",
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )
        self.adjust_acc = Account.objects.create(
            company=self.company,
            name="Stock Adjustment",
            root_type=AccountRootType.EXPENSE,
        )
        self.company.default_inventory_account = self.stock_acc
        self.company.stock_adjustment_account = self.adjust_acc
        self.company.enable_perpetual_inventory = True
        self.company.save()

        uom = UOM.objects.create(name="Kg")
        group = ItemGroup.objects.create(name="Spices", is_group=False)

        def mk(code, rate="0", **kw):
            return Item.objects.create(
                code=code,
                name=code,
                item_group=group,
                stock_uom=uom,
                standard_rate=D(rate),
                **kw,
            )

        self.turmeric = mk("TURMERIC", "100")
        self.salt = mk("SALT", "20")
        self.powder = mk("HALDI-P")
        self.dust = mk("DUST", "5")
        self.batch_raw = mk(
            "BATCH-RAW",
            "10",
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=3650,
        )
        self.batch_fg = mk(
            "BATCH-FG",
            "0",
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=3650,
        )

        self.raw = Warehouse.objects.create(name="Raw", company=self.company)
        self.wip = Warehouse.objects.create(
            name="WIP", company=self.company, account=self.wip_acc
        )
        self.fg = Warehouse.objects.create(name="FG", company=self.company)
        self.scrap = Warehouse.objects.create(name="Scrap", company=self.company)
        self._n = 0

    # ---------------------------------------------------------------- helpers
    def receipt(self, item, warehouse, qty, rate, batch=None, date=D0(2026, 4, 1)):
        self._n += 1
        ste = StockEntry.objects.create(
            name=f"STE-R-{self._n}",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=date,
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste,
            item=item,
            target_warehouse=warehouse,
            qty=D(qty),
            valuation_rate=D(rate),
            batch=batch,
        )
        ste.submit()
        return ste

    def bom(self, **kw):
        data = {
            "name": kw.pop("name", "BOM-HALDI"),
            "company": self.company,
            "item": self.powder,
            "quantity": D("100"),
            "process_loss_percentage": D("1.25"),
        }
        data.update(kw)
        bom = BOM.objects.create(**data)
        from manufacturing.models import BOMItem, BOMScrapItem

        BOMItem.objects.create(bom=bom, idx=1, item=self.turmeric, qty=D("80"))
        BOMItem.objects.create(bom=bom, idx=2, item=self.salt, qty=D("20"))
        BOMScrapItem.objects.create(
            bom=bom, idx=1, item=self.dust, qty=D("1"), rate=D("5")
        )
        return bom

    def work_order(self, bom, qty="98.75", **kw):
        data = {
            "name": kw.pop("name", "WO-1"),
            "company": self.company,
            "production_item": bom.item,
            "bom": bom,
            "qty": D(qty),
            "source_warehouse": self.raw,
            "wip_warehouse": self.wip,
            "fg_warehouse": self.fg,
            "scrap_warehouse": self.scrap,
        }
        data.update(kw)
        wo = WorkOrder.objects.create(**data)
        wo.save()  # regenerate required items
        return wo

    def submit_wo(self, wo):
        wo.submit()
        return WorkOrder.objects.get(pk=wo.pk)

    def entry(self, etype, rows, **kw):
        self._n += 1
        ste = StockEntry.objects.create(
            name=kw.pop("name", f"STE-M-{self._n}"),
            stock_entry_type=etype,
            posting_date=kw.pop("posting_date", D0(2026, 4, 10)),
            company=self.company,
            **kw,
        )
        for row in rows:
            StockEntryItem.objects.create(stock_entry=ste, **row)
        return ste

    def gl(self, ste):
        rows = GLEntry.objects.filter(voucher_no=ste.name, is_cancelled=False)
        debit = sum(r.debit for r in rows)
        credit = sum(r.credit for r in rows)
        return rows, debit, credit

    def run_wo(self, **kw):
        """Receipts → submitted WO → transfer → manufacture."""
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        bom = self.bom(**kw)
        wo = self.submit_wo(self.work_order(bom))
        transfer = wo_service.make_stock_entry(wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE)
        transfer.submit()
        wo.refresh_from_db()
        manufacture = wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE)
        return wo, transfer, manufacture


class BOMTests(ManuBase):
    def test_cost_rolls_from_standard_rate_and_good_output(self):
        bom = self.bom(rate_basis="Standard Rate")
        cost = costing.bom_cost(bom)
        self.assertEqual(cost["raw_material_cost"], D("8400.00"))
        self.assertEqual(cost["scrap_value"], D("5.00"))
        # 8400 − 5 over 98.75 good units
        self.assertEqual(cost["good_qty"], D("98.750"))
        self.assertEqual(cost["unit_cost"], D("85.012658"))

    def test_default_bom_is_unique_per_item(self):
        from manufacturing.serializers import BOMSerializer

        payload = {
            "company": self.company.pk,
            "item": self.powder.pk,
            "quantity": "100.000",
            "is_default": True,
            "items": [{"item": self.turmeric.pk, "qty": "80.000"}],
        }
        first = BOMSerializer(data=payload)
        first.is_valid(raise_exception=True)
        bom1 = first.save()
        second = BOMSerializer(data={**payload, "quantity": "90.000"})
        second.is_valid(raise_exception=True)
        bom2 = second.save()
        bom1.refresh_from_db()
        self.assertFalse(bom1.is_default)
        self.assertTrue(bom2.is_default)

    def test_component_may_not_be_the_product_or_repeat(self):

        bom = BOM.objects.create(
            name="BOM-BAD",
            company=self.company,
            item=self.powder,
            quantity=D("1"),
        )
        bom.item = self.powder
        from manufacturing.services import boms as boms_service

        with self.assertRaisesMessage(ValidationError, "cannot also be a BOM component"):
            boms_service.validate_bom_rows(
                bom, [{"item": self.powder}], []
            )

    def test_sub_bom_must_make_the_component_item(self):
        from manufacturing.services import boms as boms_service

        sub = self.bom(name="BOM-SUB", item=self.turmeric)
        parent = BOM.objects.create(
            name="BOM-PARENT",
            company=self.company,
            item=self.powder,
            quantity=D("1"),
        )
        with self.assertRaisesMessage(ValidationError, "not the row item"):
            boms_service.validate_bom_rows(
                parent, [{"item": self.salt, "bom_no": sub.pk}], []
            )

    def test_cycle_is_refused(self):
        from manufacturing.models import BOMItem
        from manufacturing.services import boms as boms_service

        a = BOM.objects.create(
            name="BOM-CYC-A", company=self.company, item=self.turmeric, quantity=D("1")
        )
        b = BOM.objects.create(
            name="BOM-CYC-B", company=self.company, item=self.salt, quantity=D("1")
        )
        BOMItem.objects.create(bom=a, idx=1, item=self.salt, qty=D("1"), bom_no=b)
        BOMItem.objects.create(bom=b, idx=1, item=self.turmeric, qty=D("1"), bom_no=a)
        with self.assertRaisesMessage(ValidationError, "contains a cycle"):
            boms_service.validate_bom_rows(a, [{"item": self.salt, "bom_no": b}], [])

    def test_explode_includes_sub_assembly_components(self):
        sub = self.bom(name="BOM-SUB2", item=self.turmeric, quantity=D("10"))
        parent = BOM.objects.create(
            name="BOM-P", company=self.company, item=self.powder, quantity=D("10")
        )
        from manufacturing.models import BOMItem

        BOMItem.objects.create(
            bom=parent, idx=1, item=self.turmeric, qty=D("5"), bom_no=sub
        )
        data = costing.explode(parent)
        self.assertTrue(data["rows"][0]["needs_sub_assembly"])
        self.assertEqual(data["rows"][1]["level"], 1)
        self.assertEqual(data["rows"][1]["qty"], D("40.000"))


class WorkOrderTests(ManuBase):
    def test_required_items_gross_up_the_loss(self):
        bom = self.bom()
        wo = self.work_order(bom)
        rows = {r.item.code: r.required_qty for r in wo.required_items.all()}
        self.assertEqual(rows["TURMERIC"], D("80.000"))
        self.assertEqual(rows["SALT"], D("20.000"))

    def test_half_work_order_scales_raw_requirement(self):
        bom = self.bom()
        wo = self.work_order(bom, qty="49.375")
        rows = {r.item.code: r.required_qty for r in wo.required_items.all()}
        self.assertEqual(rows["TURMERIC"], D("40.000"))
        self.assertEqual(rows["SALT"], D("10.000"))

    def test_submit_gate_requires_wip_and_distinct_fg(self):
        bom = self.bom()
        wo = self.work_order(bom, wip_warehouse=None)
        with self.assertRaisesMessage(ValidationError, "WIP Warehouse is required"):
            wo.submit()

    def test_stop_resume_and_status(self):
        bom = self.bom()
        wo = self.submit_wo(self.work_order(bom))
        self.assertEqual(wo.status, WorkOrderStatus.NOT_STARTED)
        wo_service.set_stopped(wo, True)
        wo.refresh_from_db()
        self.assertTrue(wo.stopped)
        self.assertEqual(wo.status, WorkOrderStatus.STOPPED)
        wo_service.set_stopped(wo, False)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrderStatus.NOT_STARTED)

    def test_cancel_guard_blocks_while_entries_exist(self):
        _, transfer, _ = self.run_wo()
        wo = WorkOrder.objects.get(pk=transfer.work_order_id)
        with self.assertRaisesMessage(ValidationError, "Cancel the stock entries"):
            wo.cancel()


class TransferTests(ManuBase):
    def test_transfer_moves_to_wip_and_tracks_progress(self):
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        bom = self.bom()
        wo = self.submit_wo(self.work_order(bom))
        transfer = wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        )
        self.assertEqual(transfer.items.count(), 2)
        transfer.submit()
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrderStatus.IN_PROCESS)
        self.assertEqual(
            {r.item.code: r.transferred_qty for r in wo.required_items.all()},
            {"TURMERIC": D("80.000"), "SALT": D("20.000")},
        )
        self.assertEqual(Bin.objects.get(item=self.turmeric, warehouse=self.wip).actual_qty, D("80.000"))
        self.assertEqual(Bin.objects.get(item=self.turmeric, warehouse=self.raw).actual_qty, D("0.000"))
        rows, debit, credit = self.gl(transfer)
        self.assertEqual(debit, credit)
        self.assertEqual(
            {(r.account.name, r.debit, r.credit) for r in rows},
            {
                ("Work In Progress", D("8400.00"), D("0.00")),
                ("Stock Assets", D("0.00"), D("8400.00")),
            },
        )

    def test_transfer_cannot_exceed_required_quantity(self):
        self.receipt(self.turmeric, self.raw, "100", "100")
        bom = self.bom()
        wo = self.submit_wo(self.work_order(bom))
        transfer = wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        )
        transfer.items.filter(item=self.turmeric).update(qty=D("90"))
        with self.assertRaisesMessage(ValidationError, "only 80.000 remains"):
            transfer.submit()


class ManufactureTests(ManuBase):
    def test_full_flow_values_finished_goods_and_completes_wo(self):
        _, _, entry = self.run_wo()
        entry.submit()
        wo = WorkOrder.objects.get(pk=entry.work_order_id)
        self.assertEqual(wo.status, WorkOrderStatus.COMPLETED)
        self.assertEqual(wo.produced_qty, D("98.750"))
        self.assertEqual(wo.process_loss_qty, D("1.250"))
        self.assertEqual(Bin.objects.get(item=self.powder, warehouse=self.fg).actual_qty, D("98.750"))
        self.assertEqual(Bin.objects.get(item=self.powder, warehouse=self.fg).stock_value, D("8395.00"))
        finished = entry.items.get(is_finished_item=True)
        self.assertEqual(finished.qty, D("98.750"))
        self.assertEqual(finished.valuation_rate, D("85.012658"))
        self.assertEqual(entry.fg_completed_qty, D("98.750"))
        rows, debit, credit = self.gl(entry)
        self.assertEqual(debit, credit)
        accounts = {(r.account.name, r.debit, r.credit) for r in rows}
        self.assertIn(("Stock Assets", D("8400.00"), D("0.00")), accounts)
        self.assertIn(("Work In Progress", D("0.00"), D("8400.00")), accounts)

    def test_manufacture_cannot_exceed_remaining_wo_qty(self):
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        bom = self.bom()
        wo = self.submit_wo(self.work_order(bom, qty="49.375"))
        wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        ).submit()
        with self.assertRaisesMessage(ValidationError, "basis remains"):
            wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE, qty="98.75")

    def test_manufacture_requires_exactly_one_finished_row(self):
        bom = self.bom()
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        wo = self.submit_wo(self.work_order(bom))
        entry = self.entry(
            StockEntryType.MANUFACTURE,
            [
                {
                    "item": self.turmeric,
                    "source_warehouse": self.raw,
                    "qty": D("80"),
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("40"),
                    "is_finished_item": True,
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("40"),
                    "is_finished_item": True,
                },
            ],
            bom=bom,
            work_order=wo,
        )
        with self.assertRaisesMessage(ValidationError, "exactly one finished row"):
            entry.submit()

    def test_consumption_must_be_a_bom_component(self):
        bom = self.bom()
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        wo = self.submit_wo(self.work_order(bom))
        entry = self.entry(
            StockEntryType.MANUFACTURE,
            [
                {
                    "item": self.dust,
                    "source_warehouse": self.raw,
                    "qty": D("1"),
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("40"),
                    "is_finished_item": True,
                },
            ],
            bom=bom,
            work_order=wo,
        )
        with self.assertRaisesMessage(ValidationError, "not a component"):
            entry.submit()

    def test_scrap_reduces_finished_value_and_lands_in_scrap_warehouse(self):
        bom = self.bom()
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        wo = self.submit_wo(self.work_order(bom))
        wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        ).submit()
        entry = wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE)
        entry.submit()
        scrap = entry.items.get(is_scrap_item=True)
        self.assertEqual(scrap.qty, D("1.000"))
        self.assertEqual(
            Bin.objects.get(item=self.dust, warehouse=self.scrap).stock_value, D("5.00")
        )
        self.assertEqual(
            Bin.objects.get(item=self.powder, warehouse=self.fg).stock_value, D("8395.00")
        )

    def test_cancel_reverses_ledger_gl_and_progress(self):
        _, _, entry = self.run_wo()
        entry.submit()
        entry.cancel()
        wo = WorkOrder.objects.get(pk=entry.work_order_id)
        self.assertEqual(wo.produced_qty, D("0.000"))
        self.assertEqual(wo.status, WorkOrderStatus.IN_PROCESS)
        self.assertEqual(Bin.objects.get(item=self.powder, warehouse=self.fg).actual_qty, D("0.000"))
        rows = GLEntry.objects.filter(voucher_no=entry.name)
        self.assertTrue(rows.exists())
        self.assertEqual(
            sum((r.debit for r in rows), D("0.00")),
            sum((r.credit for r in rows), D("0.00")),
        )
        # The active GL of a fully cancelled voucher is empty.
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=entry.name, is_cancelled=False).exists()
        )

    def test_standalone_manufacture_needs_a_bom(self):
        entry = self.entry(
            StockEntryType.MANUFACTURE,
            [
                {
                    "item": self.turmeric,
                    "source_warehouse": self.raw,
                    "qty": D("1"),
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("1"),
                    "is_finished_item": True,
                },
            ],
        )
        with self.assertRaisesMessage(ValidationError, "Work Order or a BOM"):
            entry.submit()


class RepackTests(ManuBase):
    def test_repack_allocates_cost_over_finished_rows(self):
        self.receipt(self.turmeric, self.raw, "80", "100")
        self.receipt(self.salt, self.raw, "20", "20")
        entry = self.entry(
            StockEntryType.REPACK,
            [
                {
                    "item": self.turmeric,
                    "source_warehouse": self.raw,
                    "qty": D("10"),
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("6"),
                    "is_finished_item": True,
                    "cost_allocation_per": D("60"),
                },
                {
                    "item": self.salt,
                    "target_warehouse": self.fg,
                    "qty": D("4"),
                    "is_finished_item": True,
                    "cost_allocation_per": D("40"),
                },
            ],
        )
        entry.submit()
        self.assertEqual(entry.fg_completed_qty, D("10.000"))
        self.assertEqual(entry.process_loss_qty, D("0.000"))
        self.assertEqual(
            Bin.objects.get(item=self.powder, warehouse=self.fg).stock_value, D("600.00")
        )
        self.assertEqual(
            Bin.objects.get(item=self.salt, warehouse=self.fg).stock_value, D("400.00")
        )

    def test_repack_allocation_must_total_100(self):
        self.receipt(self.turmeric, self.raw, "10", "100")
        entry = self.entry(
            StockEntryType.REPACK,
            [
                {
                    "item": self.turmeric,
                    "source_warehouse": self.raw,
                    "qty": D("10"),
                },
                {
                    "item": self.powder,
                    "target_warehouse": self.fg,
                    "qty": D("6"),
                    "is_finished_item": True,
                    "cost_allocation_per": D("60"),
                },
                {
                    "item": self.salt,
                    "target_warehouse": self.fg,
                    "qty": D("4"),
                    "is_finished_item": True,
                    "cost_allocation_per": D("30"),
                },
            ],
        )
        with self.assertRaisesMessage(ValidationError, "must total 100"):
            entry.submit()

    def test_repack_cannot_reference_a_work_order(self):
        bom = self.bom()
        wo = self.work_order(bom)
        entry = self.entry(
            StockEntryType.REPACK,
            [
                {
                    "item": self.turmeric,
                    "source_warehouse": self.raw,
                    "qty": D("1"),
                }
            ],
            work_order=wo,
        )
        with self.assertRaisesMessage(ValidationError, "no Work Order"):
            entry.submit()


class BatchManufacturingTests(ManuBase):
    def test_manufacture_creates_finished_batch_and_keeps_value(self):
        from stock.models import Batch

        self.receipt(
            self.batch_raw,
            self.raw,
            "10",
            "10",
        )
        # Receipt of a batch item without a batch creates one automatically.
        self.assertEqual(Batch.objects.filter(item=self.batch_raw).count(), 1)
        bom = BOM.objects.create(
            name="BOM-BATCH",
            company=self.company,
            item=self.batch_fg,
            quantity=D("10"),
            process_loss_percentage=D("0"),
        )
        from manufacturing.models import BOMItem

        BOMItem.objects.create(bom=bom, idx=1, item=self.batch_raw, qty=D("10"))
        wo = self.submit_wo(
            self.work_order(bom, qty="10", name="WO-BATCH")
        )
        wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        ).submit()
        entry = wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE)
        entry.submit()
        finished = entry.items.get(is_finished_item=True)
        self.assertIsNotNone(finished.batch)
        self.assertTrue(finished.batch.batch_no.startswith("BATCH-FG-"))
        self.assertIsNotNone(finished.batch.expiry_date)
        self.assertEqual(
            Bin.objects.get(item=self.batch_fg, warehouse=self.fg).stock_value,
            D("100.00"),
        )


class RepostAndIntegrityTests(ManuBase):
    def test_repost_command_repairs_a_tampered_projection(self):
        from django.core.management import call_command

        from manufacturing.services import integrity

        _, _, entry = self.run_wo()
        entry.submit()
        wo = WorkOrder.objects.get(pk=entry.work_order_id)
        WorkOrder.objects.filter(pk=wo.pk).update(produced_qty=D("0.000"))
        errors, _warnings = integrity.check()
        self.assertTrue(any("repost_work_orders" in e for e in errors))
        call_command("repost_work_orders", "--work-order", wo.name, verbosity=0)
        wo.refresh_from_db()
        self.assertEqual(wo.produced_qty, D("98.750"))
        self.assertEqual(integrity.check(), ([], []))

    def test_integrity_catches_value_drift(self):
        from manufacturing.services import integrity

        _, _, entry = self.run_wo()
        entry.submit()
        from stock.models import StockLedgerEntry

        row = StockLedgerEntry.objects.filter(
            voucher_no=entry.name, is_cancelled=False
        ).first()
        StockLedgerEntry.objects.filter(pk=row.pk).update(
            stock_value_difference=row.stock_value_difference + D("1.00")
        )
        errors, _warnings = integrity.check()
        self.assertTrue(any("value drift" in e.lower() for e in errors))


class ManufacturingApiTests(ManuBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("m_admin", "m@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def test_bom_create_with_rows_and_derived_cost(self):
        r = self.c.post(
            "/api/manufacturing/boms/",
            {
                "company": self.company.pk,
                "item": self.powder.pk,
                "quantity": "100.000",
                "process_loss_percentage": "1.250",
                "rate_basis": "Standard Rate",
                "items": [
                    {"item": self.turmeric.pk, "qty": "80.000"},
                    {"item": self.salt.pk, "qty": "20.000"},
                ],
                "scrap_items": [{"item": self.dust.pk, "qty": "1.000", "rate": "5.000000"}],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data["name"].startswith("BOM-"))
        self.assertEqual(r.data["raw_material_cost"], "8400.00")
        self.assertEqual(r.data["unit_cost"], "85.012658")
        self.assertEqual(len(r.data["items"]), 2)

    def test_work_order_create_generates_rows_and_stop_action(self):
        bom = self.bom()
        r = self.c.post(
            "/api/manufacturing/work-orders/",
            {
                "company": self.company.pk,
                "production_item": self.powder.pk,
                "bom": bom.pk,
                "qty": "98.750",
                "source_warehouse": self.raw.pk,
                "wip_warehouse": self.wip.pk,
                "fg_warehouse": self.fg.pk,
                "scrap_warehouse": self.scrap.pk,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        wo = WorkOrder.objects.get(pk=r.data["id"])
        self.assertEqual(wo.required_items.count(), 2)
        self.c.post(f"/api/manufacturing/work-orders/{wo.pk}/submit/")
        wo.refresh_from_db()
        self.assertEqual(wo.docstatus, DocStatus.SUBMITTED)
        r = self.c.post(f"/api/manufacturing/work-orders/{wo.pk}/stop/")
        self.assertEqual(r.status_code, 200, r.content)
        wo.refresh_from_db()
        self.assertTrue(wo.stopped)

    def test_usage_lock_blocks_bom_edit_after_submit(self):
        bom = self.bom()
        self.submit_wo(self.work_order(bom))
        r = self.c.patch(
            f"/api/manufacturing/boms/{bom.pk}/",
            {"quantity": "200.000"},
            format="json",
        )
        self.assertEqual(r.status_code, 409, r.content)

    def test_bom_list_renders_computed_costs(self):
        self.bom(rate_basis="Standard Rate")
        r = self.c.get("/api/manufacturing/boms/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["raw_material_cost"], "8400.00")

    def test_bom_cost_and_explode_endpoints(self):
        bom = self.bom(rate_basis="Standard Rate")
        r = self.c.get(f"/api/manufacturing/boms/{bom.pk}/cost/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["unit_cost"], "85.012658")
        r = self.c.get(f"/api/manufacturing/boms/{bom.pk}/explode/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data["rows"]), 2)

    def test_make_stock_entry_endpoint(self):
        bom = self.bom()
        wo = self.submit_wo(self.work_order(bom))
        r = self.c.post(
            f"/api/manufacturing/work-orders/{wo.pk}/make-stock-entry/",
            {"type": "material_transfer"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.data["items"]), 2)

    def test_production_yield_report(self):
        _, _, entry = self.run_wo()
        entry.submit()
        r = self.c.get(f"/api/manufacturing/reports/production-yield/?company={self.company.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data["rows"]), 1)
        self.assertEqual(r.data["rows"][0]["produced_qty"], "98.750")
        self.assertEqual(r.data["rows"][0]["process_loss_qty"], "1.250")

    def test_bom_cost_report(self):
        self.bom(rate_basis="Standard Rate")
        r = self.c.get(f"/api/manufacturing/reports/bom-cost/?company={self.company.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data["rows"]), 1)
        self.assertEqual(r.data["rows"][0]["unit_cost"], "85.012658")
