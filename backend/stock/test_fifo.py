"""Automatic FIFO batch picking: a stock-out of a batch-tracked item that names no
batch consumes the oldest-manufactured batches first, split over as many batches
as needed, on every posting path (invoice, stock entries, manufacturing)."""

import datetime
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from io import StringIO
from rest_framework.test import APIClient

from accounts.test_returns import ReturnBase
from manufacturing.models import BOM, BOMItem
from manufacturing.services import work_orders as wo_service
from manufacturing.tests import ManuBase
from selling import mapping as selling_mapping
from stock.models import (
    Batch,
    Bin,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    StockLedgerEntry,
    Warehouse,
)
from stock.services import batches as batch_service
from stock.services import ledger as stock_service
from users.models import User

D0 = datetime.date


class FifoBase(ReturnBase):
    """Three lots of HALDI in one store, manufactured on different days (and
    received out of order, so FIFO is not just 'first received')."""

    def setUp(self):
        super().setUp()
        self.wh2 = Warehouse.objects.create(name="Store 2", company=self.company)
        # received newest-first on purpose
        self.purchase([(self.haldi, 10, 30, {"manufacturing_date": D0(2026, 3, 20)})])
        self.purchase([(self.haldi, 10, 32, {"manufacturing_date": D0(2026, 3, 1)})])
        self.purchase([(self.haldi, 10, 34, {"manufacturing_date": D0(2026, 3, 10)})])
        b = {x.manufacturing_date: x for x in Batch.objects.filter(item=self.haldi)}
        self.oldest, self.middle, self.newest = (
            b[D0(2026, 3, 1)],
            b[D0(2026, 3, 10)],
            b[D0(2026, 3, 20)],
        )

    def bal(self, batch, wh=None):
        return batch_service.batch_balance(self.haldi, wh or self.wh, batch)

    def entry(self, etype, lines, date=D0(2026, 4, 5)):
        self._n += 1
        ste = StockEntry.objects.create(
            name=f"STE-F-{self._n}",
            stock_entry_type=etype,
            posting_date=date,
            company=self.company,
        )
        for ln in lines:
            StockEntryItem.objects.create(stock_entry=ste, **ln)
        return ste


class SalesInvoiceFifoTests(FifoBase):
    def test_split_across_two_batches_oldest_first(self):
        si = self.sell([(self.haldi, 14, 80)])
        sles = list(
            StockLedgerEntry.objects.filter(voucher_no=si.name, is_cancelled=False).order_by("id")
        )
        self.assertEqual(
            [(s.batch_id, s.qty) for s in sles],
            [(self.oldest.pk, D("-10.000")), (self.middle.pk, D("-4.000"))],
        )
        # costed per batch: 10 x 32 + 4 x 34
        self.assertEqual(sum(s.stock_value_difference for s in sles), D("-456.00"))
        self.assertEqual(self.bal(self.oldest), 0)
        self.assertEqual(self.bal(self.middle), D("6"))
        self.assertEqual(self.bal(self.newest), D("10"))
        self.integrity_ok()
        # The derived trace shows what FIFO picked for the blank line.
        rows = batch_service.voucher_batches("selling.SalesInvoice", si.name)
        self.assertEqual(
            [(r["batch_no"], r["qty"]) for r in rows],
            [(self.oldest.batch_no, D("-10.000")), (self.middle.batch_no, D("-4.000"))],
        )

    def test_explicit_batch_is_honoured(self):
        si = self.sell([(self.haldi, 5, 80, {"batch": self.newest})])
        sle = StockLedgerEntry.objects.get(voucher_no=si.name, is_cancelled=False)
        self.assertEqual((sle.batch_id, sle.qty), (self.newest.pk, D("-5.000")))
        self.assertEqual(self.bal(self.oldest), D("10"))

    def test_two_lines_of_the_same_item_do_not_double_allocate(self):
        si = self.sell([(self.haldi, 8, 80), (self.haldi, 8, 80)])
        sles = list(
            StockLedgerEntry.objects.filter(voucher_no=si.name, is_cancelled=False).order_by("id")
        )
        self.assertEqual(
            [(s.batch_id, s.qty) for s in sles],
            [
                (self.oldest.pk, D("-8.000")),
                (self.oldest.pk, D("-2.000")),
                (self.middle.pk, D("-6.000")),
            ],
        )

    def test_expired_and_disabled_batches_are_skipped(self):
        self.oldest.expiry_date = D0(2026, 3, 31)  # expired before the sale (04-01)
        self.oldest.save()
        self.middle.is_disabled = True
        self.middle.save()
        si = self.sell([(self.haldi, 4, 80)])
        sle = StockLedgerEntry.objects.get(voucher_no=si.name, is_cancelled=False)
        self.assertEqual(sle.batch_id, self.newest.pk)
        with self.assertRaisesMessage(ValidationError, "Insufficient usable batch stock"):
            self.sell([(self.haldi, 7, 80)])  # only 6 left in the usable batch

    def test_undated_batch_is_picked_last_then_creation_order(self):
        undated = Batch.objects.create(batch_no="UNDATED", item=self.haldi, expiry_date=D0(2027, 1, 1))
        self.purchase([(self.haldi, 5, 30, {"batch": undated})])
        si = self.sell([(self.haldi, 35, 80)])
        picked = [
            s.batch_id
            for s in StockLedgerEntry.objects.filter(voucher_no=si.name, is_cancelled=False).order_by("id")
        ]
        self.assertEqual(picked, [self.oldest.pk, self.middle.pk, self.newest.pk, undated.pk])

    def test_shortage_is_refused_with_nothing_posted(self):
        before = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "required 31.000, available 30.000"):
            self.sell([(self.haldi, 31, 80)])
        self.assertEqual(StockLedgerEntry.objects.count(), before)

    def test_cancel_reverses_every_split_leg(self):
        si = self.sell([(self.haldi, 14, 80)])
        si.cancel()
        self.assertEqual(self.bal(self.oldest), D("10"))
        self.assertEqual(self.bal(self.middle), D("10"))
        self.assertEqual(Bin.objects.get(item=self.haldi, warehouse=self.wh).actual_qty, D("30"))
        live = StockLedgerEntry.objects.filter(voucher_no=si.name, is_cancelled=False)
        self.assertFalse(live.exists())
        self.assertEqual(StockLedgerEntry.objects.filter(voucher_no=si.name).count(), 4)
        self.integrity_ok()

    def test_sales_return_of_a_fifo_picked_line(self):
        si = self.sell([(self.haldi, 14, 80)])
        ret = selling_mapping.make_sales_return(si)
        line = ret.items.get()
        self.assertIsNone(line.batch_id)
        # A return is stock coming in: it must name the lot it goes back to ...
        with self.assertRaisesMessage(ValidationError, "Batch required"):
            ret.submit()
        # ... which must be one the invoice actually took from (a ledger fact).
        line.batch = self.newest
        line.save()
        with self.assertRaisesMessage(ValidationError, "batch of"):
            ret.submit()
        line.batch = self.middle
        line.save()
        ret.submit()
        self.assertEqual(self.bal(self.middle), D("20") - D("4") + D("14") - D("10"))
        self.integrity_ok()


class StockEntryFifoTests(FifoBase):
    def test_material_issue_picks_fifo_and_allows_expired_write_off(self):
        self.oldest.expiry_date = D0(2026, 3, 31)
        self.oldest.save()
        ste = self.entry(
            StockEntryType.MATERIAL_ISSUE,
            [dict(item=self.haldi, source_warehouse=self.wh, qty=D("12"))],
        )
        ste.submit()
        # The expired oldest lot is exactly what a write-off should consume first.
        self.assertEqual(self.bal(self.oldest), 0)
        self.assertEqual(self.bal(self.middle), D("8"))

    def test_material_transfer_carries_the_picked_batches_and_value(self):
        ste = self.entry(
            StockEntryType.MATERIAL_TRANSFER,
            [
                dict(
                    item=self.haldi,
                    source_warehouse=self.wh,
                    target_warehouse=self.wh2,
                    qty=D("14"),
                )
            ],
        )
        ste.submit()
        self.assertEqual(self.bal(self.oldest, self.wh2), D("10"))
        self.assertEqual(self.bal(self.middle, self.wh2), D("4"))
        self.assertEqual(self.bal(self.oldest, self.wh), 0)
        self.assertEqual(self.bal(self.middle, self.wh), D("6"))
        sles = StockLedgerEntry.objects.filter(voucher_no=ste.name, is_cancelled=False)
        self.assertEqual(sles.count(), 4)
        # Internal move: value is conserved batch by batch (10 x 32 and 4 x 34).
        self.assertEqual(sum(s.stock_value_difference for s in sles), D("0.00"))
        self.assertEqual(
            batch_service.batch_position(self.haldi, self.wh2, self.oldest)[1], D("320.00")
        )
        self.assertEqual(
            batch_service.batch_position(self.haldi, self.wh2, self.middle)[1], D("136.00")
        )
        ste.cancel()
        self.assertEqual(self.bal(self.oldest, self.wh), D("10"))
        self.assertEqual(self.bal(self.oldest, self.wh2), 0)
        self.integrity_ok()

    def test_transfer_with_two_lines_pairs_each_out_with_its_own_in(self):
        ste = self.entry(
            StockEntryType.MATERIAL_TRANSFER,
            [
                dict(item=self.haldi, source_warehouse=self.wh, target_warehouse=self.wh2, qty=D("12")),
                dict(item=self.haldi, source_warehouse=self.wh, target_warehouse=self.wh2, qty=D("12")),
            ],
        )
        ste.submit()
        self.assertEqual(self.bal(self.oldest, self.wh2), D("10"))
        self.assertEqual(self.bal(self.middle, self.wh2), D("10"))
        self.assertEqual(self.bal(self.newest, self.wh2), D("4"))
        self.integrity_ok()

    def test_stock_entry_shortage_message(self):
        ste = self.entry(
            StockEntryType.MATERIAL_ISSUE,
            [dict(item=self.haldi, source_warehouse=self.wh, qty=D("99"))],
        )
        with self.assertRaisesMessage(ValidationError, "Insufficient usable batch stock"):
            ste.submit()


class LockOrderTests(FifoBase):
    def test_allocation_runs_after_the_bin_is_locked(self):
        calls = []
        real_lock = Bin.objects.select_for_update
        real_alloc = batch_service.allocate_fifo

        def lock(*a, **k):
            calls.append("lock")
            return real_lock(*a, **k)

        def alloc(*a, **k):
            calls.append("allocate")
            return real_alloc(*a, **k)

        doc = self.doc_for_ledger()
        with mock.patch.object(
            type(Bin.objects), "select_for_update", side_effect=lock
        ), mock.patch.object(batch_service, "allocate_fifo", side_effect=alloc):
            stock_service.post(
                doc,
                [{"item": self.haldi, "warehouse": self.wh, "qty": D("-12"), "valuation_rate": D("0")}],
                posting_date=D0(2026, 4, 5),
            )
        self.assertEqual(calls[0], "lock")
        self.assertIn("allocate", calls)
        self.assertLess(calls.index("lock"), calls.index("allocate"))

    def doc_for_ledger(self):
        from core.models import DocStatus, DummySubmittableDoc

        return DummySubmittableDoc.objects.create(
            name="LOCK-1", title="t", docstatus=DocStatus.SUBMITTED
        )


class FifoEndpointTests(FifoBase):
    def test_fifo_preview_and_fefo_still_work(self):
        c = APIClient()
        c.force_authenticate(User.objects.create_superuser("f_admin", "f@t.com", "pw"))
        r = c.get(
            f"/api/stock/batches/fifo/?item={self.haldi.pk}&warehouse={self.wh.pk}"
            "&qty=14&posting_date=2026-04-05"
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            [(a["batch_no"], a["allocate_qty"]) for a in r.json()["allocations"]],
            [(self.oldest.batch_no, "10.000"), (self.middle.batch_no, "4.000")],
        )
        self.assertEqual(r.json()["shortfall"], "0.000")
        short = c.get(
            f"/api/stock/batches/fifo/?item={self.haldi.pk}&warehouse={self.wh.pk}&qty=40"
        ).json()
        self.assertEqual(short["shortfall"], "10.000")
        self.assertEqual(
            c.get(f"/api/stock/batches/fifo/?item={self.haldi.pk}").status_code, 400
        )
        fefo = c.get(
            f"/api/stock/batches/fefo/?item={self.haldi.pk}&warehouse={self.wh.pk}&qty=5"
        )
        self.assertEqual(fefo.status_code, 200)

    def test_for_voucher_endpoint(self):
        si = self.sell([(self.haldi, 14, 80)])
        c = APIClient()
        c.force_authenticate(User.objects.create_superuser("f2_admin", "f2@t.com", "pw"))
        r = c.get(
            f"/api/stock/batches/for-voucher/?voucher_type=selling.SalesInvoice&voucher_no={si.name}"
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([x["qty"] for x in r.json()], ["-10.000", "-4.000"])
        self.assertEqual(
            c.get("/api/stock/batches/for-voucher/").status_code, 400
        )


class ManufactureFifoTests(ManuBase):
    def test_manufacture_consumes_oldest_raw_batches_and_conserves_value(self):
        from stock.models import Batch as B

        # Two raw lots received newest-first; the older one is cheaper.
        self.receipt(self.batch_raw, self.raw, "6", "12", date=datetime.date(2026, 4, 3))
        self.receipt(self.batch_raw, self.raw, "6", "10", date=datetime.date(2026, 4, 2))
        newer, older = (
            B.objects.get(item=self.batch_raw, manufacturing_date=datetime.date(2026, 4, 3)),
            B.objects.get(item=self.batch_raw, manufacturing_date=datetime.date(2026, 4, 2)),
        )
        bom = BOM.objects.create(
            name="BOM-FIFO",
            company=self.company,
            item=self.batch_fg,
            quantity=D("10"),
            process_loss_percentage=D("0"),
        )
        BOMItem.objects.create(bom=bom, idx=1, item=self.batch_raw, qty=D("10"))
        wo = self.submit_wo(self.work_order(bom, qty="10", name="WO-FIFO"))
        transfer = wo_service.make_stock_entry(
            wo, StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        )
        # The draft leaves the batch blank (FIFO decides at submit).
        self.assertIsNone(transfer.items.get().batch_id)
        transfer.submit()
        # 10 raw moved to WIP: 6 from the older lot, 4 from the newer, same batches in WIP.
        self.assertEqual(batch_service.batch_balance(self.batch_raw, self.wip, older), D("6"))
        self.assertEqual(batch_service.batch_balance(self.batch_raw, self.wip, newer), D("4"))
        self.assertEqual(batch_service.batch_balance(self.batch_raw, self.raw, older), D("0"))

        wo.refresh_from_db()
        manufacture = wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE)
        manufacture.submit()
        out = StockLedgerEntry.objects.filter(
            voucher_no=manufacture.name, is_cancelled=False, qty__lt=0
        ).order_by("id")
        self.assertEqual(
            [(s.batch_id, s.qty) for s in out], [(older.pk, D("-6.000")), (newer.pk, D("-4.000"))]
        )
        # 6 x 10 + 4 x 12 = 108 of raw value becomes the finished lot.
        self.assertEqual(sum(s.stock_value_difference for s in out), D("-108.00"))
        self.assertEqual(
            Bin.objects.get(item=self.batch_fg, warehouse=self.fg).stock_value, D("108.00")
        )
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

        manufacture.cancel()
        self.assertEqual(batch_service.batch_balance(self.batch_raw, self.wip, older), D("6"))
        self.assertEqual(batch_service.batch_balance(self.batch_raw, self.wip, newer), D("4"))
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
