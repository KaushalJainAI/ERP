"""Fiscal-period lock: posting into a missing or closed fiscal year is refused."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
)
from core.models import Company, FiscalYear
from stock.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    StockLedgerEntry,
    Warehouse,
)

D = datetime.date


class FiscalLockBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Lock Co", abbr="LC", default_currency="INR"
        )
        root = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.bank = Account.objects.create(
            name="Bank",
            company=self.company,
            parent=root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        inc_root = Account.objects.create(
            name="Income",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.sales = Account.objects.create(
            name="Sales",
            company=self.company,
            parent=inc_root,
            root_type=AccountRootType.INCOME,
        )

    def make_je(self, name, date):
        je = JournalEntry.objects.create(
            name=name, posting_date=date, company=self.company
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.bank, debit=Decimal("100.00")
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.sales, credit=Decimal("100.00")
        )
        return je

    def make_fy(self, name, start, end, closed=False):
        return FiscalYear.objects.create(
            name=name, start_date=start, end_date=end, is_closed=closed
        )


class GLFiscalLockTests(FiscalLockBase):
    def test_open_fy_stamps_every_gl_row(self):
        fy = self.make_fy("FY-OPEN", D(2030, 4, 1), D(2031, 3, 31))
        je = self.make_je("JE-OPEN", D(2030, 5, 1))
        je.submit()
        rows = GLEntry.objects.filter(voucher_no="JE-OPEN")
        self.assertEqual(rows.count(), 2)
        self.assertEqual({r.fiscal_year for r in rows}, {fy.name})

    def test_default_fy_is_stamped_in_normal_tests(self):
        je = self.make_je("JE-DEF", D(2026, 5, 1))
        je.submit()
        self.assertEqual(
            {r.fiscal_year for r in GLEntry.objects.filter(voucher_no="JE-DEF")},
            {"TEST-FY"},
        )

    def test_closed_fy_refuses_submit_and_writes_no_gl(self):
        self.make_fy("FY-CLOSED", D(2030, 4, 1), D(2031, 3, 31), closed=True)
        je = self.make_je("JE-CLOSED", D(2030, 5, 1))
        with self.assertRaisesMessage(
            ValidationError, "Fiscal year FY-CLOSED is closed"
        ):
            je.submit()
        self.assertEqual(GLEntry.objects.filter(voucher_no="JE-CLOSED").count(), 0)
        je.refresh_from_db()
        self.assertEqual(je.docstatus, 0)

    def test_cancel_refused_when_fy_closed_after_submit(self):
        fy = self.make_fy("FY-LATER", D(2030, 4, 1), D(2031, 3, 31))
        je = self.make_je("JE-LATER", D(2030, 5, 1))
        je.submit()
        fy.is_closed = True
        fy.save()
        with self.assertRaisesMessage(ValidationError, "is closed"):
            je.cancel()
        # Nothing reversed, document still submitted (cancel is atomic).
        rows = GLEntry.objects.filter(voucher_no="JE-LATER")
        self.assertEqual(rows.count(), 2)
        self.assertFalse(rows.filter(is_cancelled=True).exists())
        je.refresh_from_db()
        self.assertEqual(je.docstatus, 1)

    def test_cancel_allowed_in_open_fy_and_keeps_fiscal_year(self):
        fy = self.make_fy("FY-OK", D(2030, 4, 1), D(2031, 3, 31))
        je = self.make_je("JE-OK", D(2030, 5, 1))
        je.submit()
        je.cancel()
        rows = GLEntry.objects.filter(voucher_no="JE-OK")
        self.assertEqual(rows.count(), 4)
        self.assertEqual({r.fiscal_year for r in rows}, {fy.name})

    def test_caller_supplied_fiscal_year_is_ignored(self):
        from accounts.services import gl as gl_service

        je = self.make_je("JE-ARG", D(2026, 5, 1))
        rows = gl_service.post(
            je,
            [
                {"account": self.bank, "debit": Decimal("5")},
                {"account": self.sales, "credit": Decimal("5")},
            ],
            posting_date=D(2026, 5, 1),
            fiscal_year="BOGUS",
        )
        self.assertEqual({r.fiscal_year for r in rows}, {"TEST-FY"})

    def test_for_date_returns_open_year_and_raises_on_closed(self):
        fy = self.make_fy("FY-X", D(2040, 4, 1), D(2041, 3, 31))
        self.assertEqual(FiscalYear.for_date(D(2040, 4, 1)), fy)
        self.assertEqual(FiscalYear.for_date(D(2041, 3, 31)), fy)
        fy.is_closed = True
        fy.save()
        with self.assertRaises(ValidationError):
            FiscalYear.for_date(D(2040, 6, 1))


@pytest.mark.no_default_fiscal_year
class NoFiscalYearTests(FiscalLockBase):
    def test_no_fy_covering_date_refuses_gl_post(self):
        self.assertEqual(FiscalYear.objects.count(), 0)
        je = self.make_je("JE-NOFY", D(2026, 5, 1))
        with self.assertRaisesMessage(
            ValidationError, "No fiscal year found for posting date 2026-05-01."
        ):
            je.submit()
        self.assertEqual(GLEntry.objects.filter(voucher_no="JE-NOFY").count(), 0)

    def test_date_outside_existing_fy_refused(self):
        self.make_fy("FY-ONLY", D(2030, 4, 1), D(2031, 3, 31))
        je = self.make_je("JE-OUT", D(2029, 12, 31))
        with self.assertRaisesMessage(ValidationError, "No fiscal year found"):
            je.submit()


class StockFiscalLockTests(FiscalLockBase):
    def setUp(self):
        super().setUp()
        uom = UOM.objects.create(name="Nos")
        grp = ItemGroup.objects.create(name="Goods", is_group=True)
        self.item = Item.objects.create(
            code="FL-1", name="Widget", item_group=grp, stock_uom=uom
        )
        self.wh = Warehouse.objects.create(name="Lock Store", company=self.company)

    def make_receipt(self, name, date):
        se = StockEntry.objects.create(
            name=name,
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=date,
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=se,
            item=self.item,
            target_warehouse=self.wh,
            qty=Decimal("10.000"),
            valuation_rate=Decimal("20.000000"),
        )
        return se

    def test_stock_entry_in_closed_fy_refused_no_sle_or_bin(self):
        self.make_fy("FY-SC", D(2030, 4, 1), D(2031, 3, 31), closed=True)
        se = self.make_receipt("SE-CLOSED", D(2030, 6, 1))
        with self.assertRaisesMessage(ValidationError, "is closed"):
            se.submit()
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_no="SE-CLOSED").count(), 0
        )
        self.assertFalse(Bin.objects.filter(item=self.item, warehouse=self.wh).exists())

    def test_stock_cancel_refused_when_fy_closed_after_submit(self):
        fy = self.make_fy("FY-SC2", D(2030, 4, 1), D(2031, 3, 31))
        se = self.make_receipt("SE-LATER", D(2030, 6, 1))
        se.submit()
        fy.is_closed = True
        fy.save()
        with self.assertRaisesMessage(ValidationError, "is closed"):
            se.cancel()
        sles = StockLedgerEntry.objects.filter(voucher_no="SE-LATER")
        self.assertEqual(sles.count(), 1)
        self.assertFalse(sles.filter(is_cancelled=True).exists())
        b = Bin.objects.get(item=self.item, warehouse=self.wh)
        self.assertEqual(b.actual_qty, Decimal("10.000"))


@pytest.mark.no_default_fiscal_year
class StockNoFiscalYearTests(FiscalLockBase):
    def setUp(self):
        super().setUp()
        uom = UOM.objects.create(name="Nos")
        grp = ItemGroup.objects.create(name="Goods", is_group=True)
        self.item = Item.objects.create(
            code="FL-2", name="Widget2", item_group=grp, stock_uom=uom
        )
        self.wh = Warehouse.objects.create(name="Lock Store 2", company=self.company)

    def test_stock_entry_without_fy_refused(self):
        se = StockEntry.objects.create(
            name="SE-NOFY",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=D(2026, 6, 1),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=se,
            item=self.item,
            target_warehouse=self.wh,
            qty=Decimal("10.000"),
            valuation_rate=Decimal("20.000000"),
        )
        with self.assertRaisesMessage(ValidationError, "No fiscal year found"):
            se.submit()
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_no="SE-NOFY").count(), 0
        )
        self.assertFalse(Bin.objects.filter(item=self.item, warehouse=self.wh).exists())
