"""Stock Reconciliation: opening stock + physical counts (ERPNext parity).

Each line states the absolute counted qty / rate; submit posts delta SLEs through
``stock.services.ledger.post_reconciliation`` and (perpetual inventory) the GL
difference against the difference account.
"""

import datetime
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from core.models import Company, DocStatus
from stock.models import (
    UOM,
    Batch,
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    StockLedgerEntry,
    StockReconciliation,
    StockReconciliationItem,
    Warehouse,
)
from stock.services import ledger
from users.models import Role, RolePermission, User, UserRole

DATE = datetime.date(2026, 4, 1)
LATER = datetime.date(2026, 4, 10)


def _acc(company, name, root, parent=None, account_type=None, is_group=False):
    return Account.objects.create(
        name=name,
        company=company,
        parent=parent,
        root_type=root,
        account_type=account_type,
        is_group=is_group,
    )


class RecoBase(TestCase):
    def setUp(self):
        self.company = c = Company.objects.create(name="Nidhi Reco", abbr="NR")
        assets = _acc(c, "Assets", AccountRootType.ASSET, is_group=True)
        expenses = _acc(c, "Expenses", AccountRootType.EXPENSE, is_group=True)
        equity = _acc(c, "Equity", AccountRootType.EQUITY, is_group=True)
        self.inventory = _acc(
            c, "Stock In Hand", AccountRootType.ASSET, assets, AccountType.STOCK
        )
        self.cogs = _acc(
            c,
            "COGS",
            AccountRootType.EXPENSE,
            expenses,
            AccountType.COST_OF_GOODS_SOLD,
        )
        self.adjustment = _acc(c, "Stock Adjustment", AccountRootType.EXPENSE, expenses)
        self.opening = _acc(c, "Temporary Opening", AccountRootType.EQUITY, equity)
        c.enable_perpetual_inventory = True
        c.default_inventory_account = self.inventory
        c.default_expense_account = self.cogs
        c.stock_adjustment_account = self.adjustment
        c.save()

        uom = UOM.objects.create(name="Kg")
        group = ItemGroup.objects.create(name="Spices", is_group=False)
        mk = lambda code, **kw: Item.objects.create(  # noqa: E731
            code=code, name=code, item_group=group, stock_uom=uom, **kw
        )
        self.haldi = mk("HALDI")
        self.mirch = mk("MIRCH")
        self.batched = mk("BATCHED", has_batch_no=True)
        self.service = mk("SERV", is_stock_item=False)
        self.wh = Warehouse.objects.create(name="Store", company=c)
        self.wh2 = Warehouse.objects.create(name="Store 2", company=c)
        self.group_wh = Warehouse.objects.create(name="All", company=c, is_group=True)
        self._n = 0

    # ----------------------------------------------------------------- helpers
    def receipt(self, item, qty, rate, wh=None, date=DATE, batch=None):
        """A submitted Material Receipt (so the ledger is not empty)."""
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
            target_warehouse=wh or self.wh,
            qty=D(qty),
            valuation_rate=D(rate),
            batch=batch,
            **({} if not item.has_batch_no or batch else {"supplier_batch_no": "S1"}),
        )
        ste.submit()
        return ste

    def reco(
        self,
        lines,
        date=LATER,
        purpose="Stock Reconciliation",
        account=None,
        submit=True,
        time=None,
    ):
        """lines: (item, warehouse, qty|None, rate|None[, batch])."""
        self._n += 1
        doc = StockReconciliation.objects.create(
            name=f"MAT-RECO-T-{self._n}",
            company=self.company,
            posting_date=date,
            posting_time=time,
            purpose=purpose,
            expense_account=account or self.adjustment,
        )
        for line in lines:
            item, wh, qty, rate = line[:4]
            StockReconciliationItem.objects.create(
                reconciliation=doc,
                item=item,
                warehouse=wh,
                batch=line[4] if len(line) > 4 else None,
                qty=None if qty is None else D(qty),
                valuation_rate=None if rate is None else D(rate),
            )
        if submit:
            doc.submit()
            doc.refresh_from_db()
        return doc

    def bin(self, item, wh=None):
        return Bin.objects.get(item=item, warehouse=wh or self.wh)

    def verify(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def gl(self, doc):
        return {
            (e.account.name): (e.debit, e.credit)
            for e in GLEntry.objects.filter(
                voucher_type="stock.StockReconciliation",
                voucher_no=doc.name,
                is_cancelled=False,
            ).select_related("account")
        }


class OpeningStockTests(RecoBase):
    def test_opening_stock_on_empty_item(self):
        doc = self.reco(
            [(self.haldi, self.wh, "100", "50")],
            date=DATE,
            purpose="Opening Stock",
            account=self.opening,
        )
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.valuation_rate, b.stock_value),
            (D("100"), D("50"), D("5000.00")),
        )
        sle = StockLedgerEntry.objects.get(voucher_no=doc.name)
        self.assertEqual(
            (sle.qty, sle.stock_value_difference), (D("100"), D("5000.00"))
        )
        self.assertEqual(doc.difference_amount, D("5000.00"))
        line = doc.items.get()
        self.assertEqual(
            (line.current_qty, line.amount, line.amount_difference),
            (D("0"), D("5000.00"), D("5000.00")),
        )
        self.assertEqual(
            self.gl(doc),
            {
                "Stock In Hand": (D("5000.00"), D("0")),
                "Temporary Opening": (D("0"), D("5000.00")),
            },
        )
        self.verify()

    def test_opening_requires_balance_sheet_difference_account(self):
        with self.assertRaises(ValidationError) as ctx:
            self.reco(
                [(self.haldi, self.wh, "1", "1")],
                purpose="Opening Stock",
                account=self.adjustment,
            )
        self.assertIn("Asset / Liability / Equity", str(ctx.exception))
        self.assertEqual(StockLedgerEntry.objects.count(), 0)

    def test_first_ever_stock_entry_counts_as_opening(self):
        # ERPNext: an empty ledger makes any reconciliation an opening entry.
        with self.assertRaises(ValidationError):
            self.reco([(self.haldi, self.wh, "1", "1")], account=self.adjustment)
        self.reco([(self.haldi, self.wh, "1", "1")], account=self.opening)

    def test_perpetual_off_needs_no_account(self):
        self.company.enable_perpetual_inventory = False
        self.company.save()
        doc = self.reco([(self.haldi, self.wh, "4", "10")], account=self.opening)
        doc.refresh_from_db()
        self.assertFalse(GLEntry.objects.filter(voucher_no=doc.name).exists())
        self.assertEqual(self.bin(self.haldi).stock_value, D("40.00"))


class CountTests(RecoBase):
    def setUp(self):
        super().setUp()
        self.receipt(self.haldi, "10", "100")  # 10 @ 100 = 1000

    def test_count_up_keeps_rate(self):
        doc = self.reco([(self.haldi, self.wh, "15", None)])
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.stock_value, b.valuation_rate),
            (D("15"), D("1500.00"), D("100")),
        )
        sle = StockLedgerEntry.objects.get(voucher_no=doc.name)
        self.assertEqual((sle.qty, sle.stock_value_difference), (D("5"), D("500.00")))
        line = doc.items.get()
        self.assertEqual(
            (line.current_qty, line.current_valuation_rate), (D("10"), D("100"))
        )
        self.assertEqual(
            (line.qty, line.valuation_rate), (D("15"), D("100"))
        )  # resolved
        self.assertEqual(
            self.gl(doc),
            {
                "Stock In Hand": (D("500.00"), D("0")),
                "Stock Adjustment": (D("0"), D("500.00")),
            },
        )
        self.verify()

    def test_count_down_posts_loss(self):
        doc = self.reco([(self.haldi, self.wh, "7", None)])
        b = self.bin(self.haldi)
        self.assertEqual((b.actual_qty, b.stock_value), (D("7"), D("700.00")))
        self.assertEqual(doc.difference_amount, D("-300.00"))
        self.assertEqual(
            self.gl(doc),
            {
                "Stock In Hand": (D("0"), D("300.00")),
                "Stock Adjustment": (D("300.00"), D("0")),
            },
        )
        self.verify()

    def test_count_to_zero(self):
        doc = self.reco([(self.haldi, self.wh, "0", None)])
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.stock_value, b.valuation_rate), (D("0"), D("0.00"), D("0"))
        )
        self.assertEqual(doc.difference_amount, D("-1000.00"))
        self.verify()

    def test_rate_change_only(self):
        doc = self.reco([(self.haldi, self.wh, None, "120")])
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.stock_value, b.valuation_rate),
            (D("10"), D("1200.00"), D("120")),
        )
        sle = StockLedgerEntry.objects.get(voucher_no=doc.name)
        self.assertEqual((sle.qty, sle.stock_value_difference), (D("0"), D("200.00")))
        self.assertEqual(
            self.gl(doc),
            {
                "Stock In Hand": (D("200.00"), D("0")),
                "Stock Adjustment": (D("0"), D("200.00")),
            },
        )
        self.verify()

    def test_qty_and_rate_together(self):
        self.reco([(self.haldi, self.wh, "12", "90")])
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.stock_value, b.valuation_rate),
            (D("12"), D("1080.00"), D("90")),
        )
        self.verify()

    def test_no_change_row_removed_and_others_post(self):
        doc = self.reco(
            [
                (self.haldi, self.wh, "10", "100"),  # unchanged
                (self.mirch, self.wh, "3", "20"),  # opening of a second item
            ]
        )
        self.assertEqual(doc.items.count(), 1)
        self.assertEqual(doc.items.get().item, self.mirch)
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_no=doc.name).count(), 1
        )

    def test_all_rows_unchanged_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.reco([(self.haldi, self.wh, "10", None)])
        self.assertIn("Nothing to reconcile", str(ctx.exception))

    def test_negative_stock_is_counted_back(self):
        # By owner decision 2026-10-31 (Card P-1), stock can never go negative.
        # Direct negative updates are prevented by check_bin_actual_qty_non_negative.
        from django.db.utils import IntegrityError
        with self.assertRaises(IntegrityError):
            Bin.objects.filter(item=self.haldi).update(
                actual_qty=D("-3"), stock_value=D("-300.00"), valuation_rate=D("0")
            )

    def test_rate_required_when_no_current_rate(self):
        with self.assertRaises(ValidationError) as ctx:
            self.reco([(self.mirch, self.wh, "5", None)])
        self.assertIn("valuation rate is required", str(ctx.exception))
        with self.assertRaises(ValidationError):
            self.reco([(self.mirch, self.wh, "5", "0")])
        self.assertFalse(Bin.objects.filter(item=self.mirch).exists())

    def test_multiple_warehouses_in_one_document(self):
        self.reco(
            [(self.haldi, self.wh, "11", None), (self.haldi, self.wh2, "4", "60")]
        )
        self.assertEqual(self.bin(self.haldi).actual_qty, D("11"))
        self.assertEqual(self.bin(self.haldi, self.wh2).stock_value, D("240.00"))
        self.verify()


class ValidationTests(RecoBase):
    def setUp(self):
        super().setUp()
        self.receipt(self.haldi, "10", "100")

    def test_duplicate_rows_rejected_by_db_and_submit(self):
        doc = self.reco([(self.haldi, self.wh, "5", "1")], submit=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockReconciliationItem.objects.create(
                reconciliation=doc, item=self.haldi, warehouse=self.wh, qty=D("6")
            )

    def test_group_warehouse_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.reco([(self.haldi, self.group_wh, "5", "1")])
        self.assertIn("group warehouse", str(ctx.exception))

    def test_non_stock_item_rejected(self):
        with self.assertRaises(ValidationError):
            self.reco([(self.service, self.wh, "5", "1")])

    def test_foreign_company_warehouse_rejected(self):
        other = Company.objects.create(name="Other Co", abbr="OC")
        wh = Warehouse.objects.create(name="Elsewhere", company=other)
        with self.assertRaises(ValidationError):
            self.reco([(self.haldi, wh, "5", "1")])

    def test_db_constraints_on_lines(self):
        doc = self.reco([(self.haldi, self.wh, "5", "1")], submit=False)
        for kw in (
            dict(qty=D("-1")),
            dict(qty=D("1"), valuation_rate=D("-1")),
            dict(qty=None, valuation_rate=None),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                StockReconciliationItem.objects.create(
                    reconciliation=doc, item=self.mirch, warehouse=self.wh, **kw
                )

    def test_empty_document_rejected(self):
        with self.assertRaises(ValidationError):
            self.reco([])

    def test_backdated_reconciliation_is_blocked(self):
        # stock moved on 1 Apr; a count dated before that cannot be slotted in
        with self.assertRaises(ValidationError) as ctx:
            self.reco(
                [(self.haldi, self.wh, "3", None)], date=datetime.date(2026, 3, 31)
            )
        self.assertIn("Reconcile at or after the latest movement", str(ctx.exception))
        self.assertEqual(self.bin(self.haldi).actual_qty, D("10"))
        # an item with no later movement can still be counted on an earlier date
        self.reco([(self.mirch, self.wh, "3", "5")], date=datetime.date(2026, 3, 31))
        self.assertEqual(self.bin(self.mirch).actual_qty, D("3"))

    def test_same_day_time_ordering(self):
        StockLedgerEntry.objects.filter(item=self.haldi).update(
            posting_time=datetime.time(12, 0)
        )
        with self.assertRaises(ValidationError):
            self.reco(
                [(self.haldi, self.wh, "3", None)], date=DATE, time=datetime.time(9, 0)
            )
        self.reco(
            [(self.haldi, self.wh, "3", None)], date=DATE, time=datetime.time(13, 0)
        )

    def test_resubmit_and_edit_after_submit_blocked(self):
        doc = self.reco([(self.haldi, self.wh, "12", None)])
        with self.assertRaises(ValidationError):
            doc.submit()
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_no=doc.name).count(), 1
        )


class BatchTests(RecoBase):
    def setUp(self):
        super().setUp()
        self.b1 = Batch.objects.create(batch_no="B1", item=self.batched)
        self.b2 = Batch.objects.create(batch_no="B2", item=self.batched)
        self.receipt(self.batched, "10", "40", batch=self.b1)

    def test_batch_item_count_up_new_batch_and_down(self):
        self.reco(
            [
                (self.batched, self.wh, "6", None, self.b1),
                (self.batched, self.wh, "8", "50", self.b2),
            ]
        )
        self.assertEqual(self.bin(self.batched).actual_qty, D("14"))
        self.assertEqual(self.bin(self.batched).stock_value, D("640.00"))  # 240 + 400
        from stock.services import batches

        self.assertEqual(
            batches.batch_position(self.batched, self.wh, self.b1),
            (D("6"), D("240.00")),
        )
        self.assertEqual(
            batches.batch_position(self.batched, self.wh, self.b2),
            (D("8"), D("400.00")),
        )
        self.verify()

    def test_batch_required_and_checked(self):
        with self.assertRaises(ValidationError):
            self.reco([(self.batched, self.wh, "6", "40")])
        other = Batch.objects.create(batch_no="H1", item=self.haldi)
        with self.assertRaises(ValidationError):
            self.reco([(self.batched, self.wh, "6", "40", other)])
        with self.assertRaises(ValidationError):
            self.reco([(self.haldi, self.wh, "6", "40", self.b1)])

    def test_batch_duplicate_key_is_per_batch(self):
        doc = self.reco([(self.batched, self.wh, "1", "1", self.b1)], submit=False)
        StockReconciliationItem.objects.create(
            reconciliation=doc,
            item=self.batched,
            warehouse=self.wh,
            batch=self.b2,
            qty=D("2"),
            valuation_rate=D("3"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockReconciliationItem.objects.create(
                reconciliation=doc,
                item=self.batched,
                warehouse=self.wh,
                batch=self.b2,
                qty=D("9"),
            )

    def test_batch_rate_only(self):
        self.reco([(self.batched, self.wh, None, "60", self.b1)])
        self.assertEqual(self.bin(self.batched).stock_value, D("600.00"))
        self.verify()

    def test_cancel_batch_reco(self):
        doc = self.reco([(self.batched, self.wh, "8", "50", self.b2)])
        doc.cancel()
        self.assertEqual(self.bin(self.batched).actual_qty, D("10"))
        self.verify()


class CancelAndRepostTests(RecoBase):
    def setUp(self):
        super().setUp()
        self.receipt(self.haldi, "10", "100")

    def test_cancel_reverses_sle_and_gl(self):
        doc = self.reco([(self.haldi, self.wh, "7", "110")])
        self.assertEqual(self.bin(self.haldi).stock_value, D("770.00"))
        doc.cancel()
        b = self.bin(self.haldi)
        self.assertEqual(
            (b.actual_qty, b.stock_value, b.valuation_rate),
            (D("10"), D("1000.00"), D("100")),
        )
        self.assertEqual(
            StockLedgerEntry.objects.filter(
                voucher_no=doc.name, is_cancelled=False
            ).count(),
            0,
        )
        net = GLEntry.objects.filter(voucher_no=doc.name, is_cancelled=False).aggregate(
            d=Sum("debit"), c=Sum("credit")
        )
        self.assertIsNone(net["d"])
        self.verify()
        doc.refresh_from_db()
        self.assertEqual(doc.docstatus, DocStatus.CANCELLED)

    def test_cancel_count_up_blocked_when_stock_since_issued(self):
        doc = self.reco([(self.haldi, self.wh, "20", None)])
        self._n += 1
        ste = StockEntry.objects.create(
            name="STE-ISSUE",
            stock_entry_type=StockEntryType.MATERIAL_ISSUE,
            posting_date=LATER,
            posting_time=datetime.time(23, 0),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste, item=self.haldi, source_warehouse=self.wh, qty=D("18")
        )
        ste.submit()
        with self.assertRaises(ValidationError):
            doc.cancel()
        self.assertEqual(self.bin(self.haldi).actual_qty, D("2"))

    def test_amend_copies_lines(self):
        doc = self.reco([(self.haldi, self.wh, "7", None)])
        doc.cancel()
        new = doc.amend()
        self.assertEqual(new.docstatus, DocStatus.DRAFT)
        self.assertEqual(new.items.count(), 1)
        new.submit()
        self.assertEqual(self.bin(self.haldi).actual_qty, D("7"))
        self.verify()

    def test_repost_stock_reproduces_the_same_bins(self):
        self.receipt(self.mirch, "5", "20")
        self.reco(
            [
                (self.haldi, self.wh, "7", "105"),
                (self.mirch, self.wh, "0", None),
                (self.haldi, self.wh2, "3", "80"),
            ]
        )
        self.reco([(self.haldi, self.wh, None, "99")], date=datetime.date(2026, 4, 20))
        cancelled = self.reco(
            [(self.haldi, self.wh2, "50", "1")], date=datetime.date(2026, 4, 25)
        )
        cancelled.cancel()
        before = sorted(
            Bin.objects.values_list(
                "item_id", "warehouse_id", "actual_qty", "stock_value", "valuation_rate"
            )
        )
        call_command("repost_stock", stdout=StringIO())
        after = sorted(
            Bin.objects.values_list(
                "item_id", "warehouse_id", "actual_qty", "stock_value", "valuation_rate"
            )
        )
        self.assertEqual(before, after)
        self.verify()


class LedgerServiceTests(RecoBase):
    def test_target_resolution(self):
        t = ledger.resolve_reconciliation_target(D("10"), D("1000"), "10", "100")
        self.assertEqual((t["dq"], t["dv"]), (D("0"), D("0")))
        t = ledger.resolve_reconciliation_target(D("3"), D("10.00"), "3", None)
        self.assertEqual(t["dv"], D("0"))  # no invented rounding difference
        t = ledger.resolve_reconciliation_target(D("0"), D("0"), "0", None)
        self.assertEqual((t["dq"], t["dv"]), (D("0"), D("0")))
        with self.assertRaises(ValidationError):
            ledger.resolve_reconciliation_target(D("1"), D("1"), "-1", None)


class ApiTests(RecoBase):
    def setUp(self):
        super().setUp()
        self.receipt(self.haldi, "10", "100")
        self.receipt(
            self.batched,
            "4",
            "30",
            batch=Batch.objects.create(batch_no="AB", item=self.batched),
        )
        self.admin = User.objects.create_superuser("reco_admin", "ra@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def payload(self, **kw):
        body = {
            "company": self.company.id,
            "posting_date": "2026-04-10",
            "items": [
                {"item": self.haldi.id, "warehouse": self.wh.id, "qty": "8"},
            ],
        }
        body.update(kw)
        return body

    def test_create_defaults_account_snapshots_then_submit_and_cancel(self):
        r = self.c.post(
            "/api/stock/stock-reconciliations/", self.payload(), format="json"
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["expense_account"], self.adjustment.id)
        self.assertTrue(r.data["name"].startswith("MAT-RECO-2026-"))
        row = r.data["items"][0]
        self.assertEqual(row["current_qty"], "10.000")
        self.assertEqual(row["current_valuation_rate"], "100.000000")
        self.assertEqual(row["amount_difference"], "-200.00")
        self.assertEqual(r.data["difference_amount"], "-200.00")
        pk = r.data["id"]
        r = self.c.post(f"/api/stock/stock-reconciliations/{pk}/submit/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["docstatus"], 1)
        self.assertEqual(self.bin(self.haldi).actual_qty, D("8"))
        # immutable once submitted
        self.assertEqual(
            self.c.patch(
                f"/api/stock/stock-reconciliations/{pk}/",
                {"posting_date": "2026-04-11"},
                format="json",
            ).status_code,
            409,
        )
        self.assertEqual(
            self.c.delete(f"/api/stock/stock-reconciliations/{pk}/").status_code, 409
        )
        r = self.c.post(f"/api/stock/stock-reconciliations/{pk}/cancel/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.bin(self.haldi).actual_qty, D("10"))
        self.verify()

    def test_draft_update_replaces_lines_and_delete_draft(self):
        r = self.c.post(
            "/api/stock/stock-reconciliations/", self.payload(), format="json"
        )
        pk = r.data["id"]
        r = self.c.patch(
            f"/api/stock/stock-reconciliations/{pk}/",
            {"items": [{"item": self.haldi.id, "warehouse": self.wh.id, "qty": "12"}]},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["difference_amount"], "200.00")
        self.assertEqual(
            self.c.delete(f"/api/stock/stock-reconciliations/{pk}/").status_code, 204
        )

    def test_serializer_validation_errors(self):
        bad = [
            [{"item": self.haldi.id, "warehouse": self.wh.id}],  # nothing counted
            [{"item": self.haldi.id, "warehouse": self.wh.id, "qty": "-1"}],
            [
                {
                    "item": self.haldi.id,
                    "warehouse": self.group_wh.id,
                    "qty": "1",
                    "valuation_rate": "1",
                }
            ],
            [
                {
                    "item": self.batched.id,
                    "warehouse": self.wh.id,
                    "qty": "1",
                    "valuation_rate": "1",
                }
            ],
            [
                {"item": self.haldi.id, "warehouse": self.wh.id, "qty": "1"},
                {"item": self.haldi.id, "warehouse": self.wh.id, "qty": "2"},
            ],
        ]
        for items in bad:
            r = self.c.post(
                "/api/stock/stock-reconciliations/",
                self.payload(items=items),
                format="json",
            )
            self.assertEqual(r.status_code, 400, items)
        r = self.c.post(
            "/api/stock/stock-reconciliations/",
            self.payload(purpose="Opening Stock"),
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # default Stock Adjustment is P&L
        r = self.c.post(
            "/api/stock/stock-reconciliations/",
            self.payload(purpose="Opening Stock", expense_account=self.opening.id),
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)

    def test_submit_failure_is_400_and_atomic(self):
        r = self.c.post(
            "/api/stock/stock-reconciliations/",
            self.payload(
                items=[{"item": self.mirch.id, "warehouse": self.wh.id, "qty": "5"}]
            ),
            format="json",
        )
        self.assertEqual(r.status_code, 201)  # rate not yet known: draft is fine
        r = self.c.post(f"/api/stock/stock-reconciliations/{r.data['id']}/submit/")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(StockLedgerEntry.objects.filter(item=self.mirch).exists())

    def test_fetch_items(self):
        url = "/api/stock/stock-reconciliations/fetch-items/"
        r = self.c.get(url, {"company": self.company.id, "warehouse": self.wh.id})
        self.assertEqual(r.status_code, 200, r.content)
        rows = {(x["item_code"], x["batch_no"]): x for x in r.data["rows"]}
        self.assertEqual(set(rows), {("HALDI", None), ("BATCHED", "AB")})
        self.assertEqual(rows[("HALDI", None)]["current_qty"], "10.000")
        self.assertEqual(rows[("HALDI", None)]["current_valuation_rate"], "100.000000")
        self.assertEqual(rows[("BATCHED", "AB")]["current_qty"], "4.000")
        self.assertIsNone(rows[("HALDI", None)]["qty"])
        # a group warehouse expands to its leaves; empty warehouse -> nothing
        Warehouse.objects.filter(pk=self.wh.pk).update(parent=self.group_wh)
        r = self.c.get(url, {"company": self.company.id, "warehouse": self.group_wh.id})
        self.assertEqual(r.data["count"], 2)
        r = self.c.get(url, {"company": self.company.id, "warehouse": self.wh2.id})
        self.assertEqual(r.data["count"], 0)
        r = self.c.get(
            url,
            {
                "company": self.company.id,
                "warehouse": self.wh.id,
                "item": self.haldi.id,
            },
        )
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(self.c.get(url).status_code, 400)
        self.assertEqual(
            self.c.get(
                url, {"company": self.company.id, "warehouse": 99999}
            ).status_code,
            404,
        )

    def test_fetch_items_feeds_a_count_sheet(self):
        rows = self.c.get(
            "/api/stock/stock-reconciliations/fetch-items/",
            {"company": self.company.id, "warehouse": self.wh.id},
        ).data["rows"]
        items = [
            {
                "item": x["item"],
                "warehouse": x["warehouse"],
                "batch": x["batch"],
                "qty": "1",
            }
            for x in rows
        ]
        r = self.c.post(
            "/api/stock/stock-reconciliations/",
            self.payload(items=items),
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        r = self.c.post(f"/api/stock/stock-reconciliations/{r.data['id']}/submit/")
        self.assertEqual(r.status_code, 200, r.content)
        self.verify()

    def _client(self, name, **flags):
        user = User.objects.create_user(name, f"{name}@t.com", "pw")
        role = Role.objects.create(name=f"role_{name}")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role, model_label="stock.StockReconciliation", **flags
        )
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_rbac_submit_needs_can_submit(self):
        clerk = self._client("clerk", can_read=True, can_create=True, can_write=True)
        r = clerk.post(
            "/api/stock/stock-reconciliations/", self.payload(), format="json"
        )
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.data["id"]
        self.assertEqual(
            clerk.post(f"/api/stock/stock-reconciliations/{pk}/submit/").status_code,
            403,
        )
        self.assertEqual(
            clerk.post(f"/api/stock/stock-reconciliations/{pk}/cancel/").status_code,
            403,
        )
        nobody = self._client("nobody", can_create=True)
        self.assertEqual(
            nobody.get("/api/stock/stock-reconciliations/").status_code, 403
        )
        self.assertEqual(
            nobody.get(
                "/api/stock/stock-reconciliations/fetch-items/",
                {"company": self.company.id, "warehouse": self.wh.id},
            ).status_code,
            403,
        )
        manager = self._client("mgr", can_read=True, can_submit=True)
        self.assertEqual(
            manager.post(f"/api/stock/stock-reconciliations/{pk}/submit/").status_code,
            200,
        )

    def test_seeded_roles(self):
        call_command("seed_defaults")
        label = "stock.StockReconciliation"
        perm = lambda role, **f: RolePermission.objects.filter(  # noqa: E731
            role__name=role, model_label=label, **f
        ).exists()
        self.assertTrue(perm("Stock Manager", can_submit=True, can_cancel=True))
        self.assertTrue(perm("System Manager", can_submit=True))
        self.assertTrue(perm("Default Role", can_create=True))
        self.assertFalse(perm("Default Role", can_submit=True))
        self.assertFalse(perm("Sales Manager", can_read=True))

    def test_ledgers_have_no_write_route(self):
        self.assertEqual(
            self.c.post("/api/stock/sle/", {}, format="json").status_code, 405
        )


class IntegrityTests(RecoBase):
    def test_integrity_detects_document_drift(self):
        from stock.services import reconciliation

        self.receipt(self.haldi, "10", "100")
        doc = self.reco([(self.haldi, self.wh, "7", None)])
        self.assertEqual(reconciliation.integrity_errors(), [])
        StockReconciliation.objects.filter(pk=doc.pk).update(difference_amount=D("1"))
        self.assertEqual(len(reconciliation.integrity_errors()), 1)
        StockReconciliation.objects.filter(pk=doc.pk).update(
            difference_amount=doc.difference_amount, docstatus=DocStatus.DRAFT
        )
        self.assertIn("not submitted", reconciliation.integrity_errors()[0])
