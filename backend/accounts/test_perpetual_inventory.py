"""Perpetual inventory: GL <-> stock valuation reconciliation (improvements.md P0.1 / G4)."""

import datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, Supplier, SupplierGroup
from core.models import Company, SystemHealthCheck
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesInvoiceItem
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

D = Decimal
DATE = datetime.date(2026, 4, 1)


def _acc(company, name, root, parent=None, account_type=None, is_group=False):
    return Account.objects.create(
        name=name,
        company=company,
        parent=parent,
        root_type=root,
        account_type=account_type,
        is_group=is_group,
    )


class PerpetualInventoryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Perpetual Co", abbr="PC")
        c = self.company
        assets = _acc(c, "Assets", AccountRootType.ASSET, is_group=True)
        expenses = _acc(c, "Expenses", AccountRootType.EXPENSE, is_group=True)
        income = _acc(c, "Income", AccountRootType.INCOME, is_group=True)
        liab = _acc(c, "Liabilities", AccountRootType.LIABILITY, is_group=True)
        self.inventory = _acc(
            c, "Stock In Hand", AccountRootType.ASSET, assets, AccountType.STOCK
        )
        self.debtors = _acc(
            c, "Debtors", AccountRootType.ASSET, assets, AccountType.RECEIVABLE
        )
        self.cogs = _acc(
            c,
            "Cost of Goods Sold",
            AccountRootType.EXPENSE,
            expenses,
            AccountType.COST_OF_GOODS_SOLD,
        )
        self.adjustment = _acc(c, "Stock Adjustment", AccountRootType.EXPENSE, expenses)
        self.purchases = _acc(c, "Purchases", AccountRootType.EXPENSE, expenses)
        self.sales = _acc(c, "Sales", AccountRootType.INCOME, income)
        self.creditors = _acc(
            c, "Creditors", AccountRootType.LIABILITY, liab, AccountType.PAYABLE
        )
        self.assets_group = assets

        c.enable_perpetual_inventory = True
        c.default_inventory_account = self.inventory
        c.default_expense_account = self.cogs
        c.stock_adjustment_account = self.adjustment
        c.save()

        uom = UOM.objects.create(name="Nos")
        grp = ItemGroup.objects.create(name="All Items", is_group=True)
        self.item = Item.objects.create(
            code="GOOD", name="Goods", item_group=grp, stock_uom=uom
        )
        self.service = Item.objects.create(
            code="SERV",
            name="Freight",
            item_group=grp,
            stock_uom=uom,
            is_stock_item=False,
        )
        self.wh = Warehouse.objects.create(name="Main", company=c)
        self.wh2 = Warehouse.objects.create(name="Second", company=c)

        self.supplier = Supplier.objects.create(
            name="Supp",
            company=c,
            supplier_group=SupplierGroup.objects.create(name="SG"),
        )
        self.customer = Customer.objects.create(
            name="Cust",
            company=c,
            customer_group=CustomerGroup.objects.create(name="CG"),
        )
        self._n = 0

    # ------------------------------------------------------------------ helpers
    def _name(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def purchase(self, lines, submit=True, update_stock=True):
        """lines: list of (item, qty, rate)."""
        pi = PurchaseInvoice.objects.create(
            name=self._name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=DATE,
            update_stock=update_stock,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        for item, qty, rate in lines:
            PurchaseInvoiceItem.objects.create(
                purchase_invoice=pi,
                item=item,
                warehouse=self.wh,
                qty=D(qty),
                rate=D(rate),
            )
        if submit:
            pi.submit()
        return pi

    def sell(self, lines, submit=True):
        si = SalesInvoice.objects.create(
            name=self._name("SINV"),
            customer=self.customer,
            company=self.company,
            posting_date=DATE,
            update_stock=True,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        for item, qty, rate in lines:
            SalesInvoiceItem.objects.create(
                sales_invoice=si, item=item, warehouse=self.wh, qty=D(qty), rate=D(rate)
            )
        if submit:
            si.submit()
        return si

    def stock_entry(self, kind, qty, rate="0", source=None, target=None, submit=True):
        se = StockEntry.objects.create(
            name=self._name("STE"),
            stock_entry_type=kind,
            posting_date=DATE,
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=se,
            item=self.item,
            qty=D(qty),
            valuation_rate=D(rate),
            source_warehouse=source,
            target_warehouse=target,
        )
        if submit:
            se.submit()
        return se

    def balance(self, account, voucher=None):
        qs = GLEntry.objects.filter(account=account, is_cancelled=False)
        if voucher is not None:
            qs = qs.filter(voucher_no=voucher.name)
        agg = qs.aggregate(dr=Sum("debit"), cr=Sum("credit"))
        return (agg["dr"] or D("0")) - (agg["cr"] or D("0"))

    def stock_value(self):
        return Bin.objects.filter(warehouse__company=self.company).aggregate(
            v=Sum("stock_value")
        )["v"] or D("0")

    def assert_voucher_balanced(self, voucher):
        agg = GLEntry.objects.filter(
            voucher_no=voucher.name, is_cancelled=False
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        self.assertEqual(agg["dr"], agg["cr"])

    def integrity_ok(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    # ------------------------------------------------------------ purchase invoice
    def test_purchase_invoice_capitalises_stock(self):
        pi = self.purchase([(self.item, "10", "100")])
        self.assertEqual(self.balance(self.inventory, pi), D("1000.00"))
        self.assertEqual(self.balance(self.creditors, pi), D("-1000.00"))
        self.assertEqual(self.balance(self.purchases, pi), D("0.00"))
        self.assertEqual(self.balance(self.inventory), self.stock_value())
        self.assert_voucher_balanced(pi)

    def test_purchase_invoice_mixed_stock_and_service_lines(self):
        pi = self.purchase([(self.item, "10", "100"), (self.service, "1", "250")])
        self.assertEqual(self.balance(self.inventory, pi), D("1000.00"))
        self.assertEqual(self.balance(self.purchases, pi), D("250.00"))
        self.assertEqual(self.balance(self.creditors, pi), D("-1250.00"))
        self.assert_voucher_balanced(pi)
        # The service line must not create a Bin or SLE.
        self.assertFalse(Bin.objects.filter(item=self.service).exists())
        self.assertFalse(StockLedgerEntry.objects.filter(item=self.service).exists())
        self.assertEqual(self.balance(self.inventory), self.stock_value())

    def test_purchase_invoice_without_update_stock_expenses_everything(self):
        pi = self.purchase([(self.item, "10", "100")], update_stock=False)
        self.assertEqual(self.balance(self.inventory, pi), D("0"))
        self.assertEqual(self.balance(self.purchases, pi), D("1000.00"))
        self.assertFalse(StockLedgerEntry.objects.exists())

    def test_perpetual_off_keeps_old_behaviour(self):
        self.company.enable_perpetual_inventory = False
        self.company.save()
        pi = self.purchase([(self.item, "10", "100")])
        self.assertEqual(self.balance(self.purchases, pi), D("1000.00"))
        self.assertEqual(self.balance(self.inventory), D("0"))
        si = self.sell([(self.item, "4", "150")])
        self.assertEqual(self.balance(self.cogs, si), D("0"))
        self.assertEqual(Bin.objects.get(item=self.item).stock_value, D("600.00"))

    # ------------------------------------------------------------- sales invoice
    def test_sales_invoice_books_cogs_at_moving_average(self):
        self.purchase([(self.item, "10", "100")])
        self.purchase([(self.item, "10", "120")])  # avg = 110
        si = self.sell([(self.item, "5", "200")])
        self.assertEqual(self.balance(self.cogs, si), D("550.00"))
        self.assertEqual(self.balance(self.inventory, si), D("-550.00"))
        self.assertEqual(self.balance(self.sales, si), D("-1000.00"))
        self.assertEqual(self.balance(self.debtors, si), D("1000.00"))
        self.assert_voucher_balanced(si)
        # COGS and the inventory credit are in the same voucher as the revenue.
        self.assertEqual(GLEntry.objects.filter(voucher_no=si.name).count(), 4)
        self.assertEqual(self.balance(self.inventory), self.stock_value())
        self.assertEqual(self.stock_value(), D("1650.00"))

    def test_sales_invoice_service_line_has_no_cogs(self):
        self.purchase([(self.item, "10", "100")])
        si = self.sell([(self.item, "2", "150"), (self.service, "1", "500")])
        self.assertEqual(self.balance(self.cogs, si), D("200.00"))
        self.assertEqual(self.balance(self.sales, si), D("-800.00"))
        self.assertEqual(self.balance(self.inventory), self.stock_value())

    def test_sales_invoice_cancel_reverses_cogs(self):
        self.purchase([(self.item, "10", "100")])
        si = self.sell([(self.item, "5", "200")])
        si.cancel()
        self.assertEqual(self.balance(self.cogs), D("0"))
        self.assertEqual(self.balance(self.inventory), D("1000.00"))
        self.assertEqual(self.stock_value(), D("1000.00"))
        self.integrity_ok()

    # ------------------------------------------------------------- stock entries
    def test_material_receipt_posts_inventory_vs_adjustment(self):
        se = self.stock_entry(
            StockEntryType.MATERIAL_RECEIPT, "5", "40", target=self.wh
        )
        self.assertEqual(self.balance(self.inventory, se), D("200.00"))
        self.assertEqual(self.balance(self.adjustment, se), D("-200.00"))
        self.assert_voucher_balanced(se)
        self.assertEqual(self.balance(self.inventory), self.stock_value())

    def test_material_issue_posts_adjustment_vs_inventory(self):
        self.stock_entry(StockEntryType.MATERIAL_RECEIPT, "10", "40", target=self.wh)
        se = self.stock_entry(StockEntryType.MATERIAL_ISSUE, "3", "0", source=self.wh)
        self.assertEqual(self.balance(self.adjustment, se), D("120.00"))
        self.assertEqual(self.balance(self.inventory, se), D("-120.00"))
        self.assertEqual(self.balance(self.inventory), self.stock_value())

    def test_material_transfer_posts_no_gl(self):
        self.stock_entry(StockEntryType.MATERIAL_RECEIPT, "10", "40", target=self.wh)
        se = self.stock_entry(
            StockEntryType.MATERIAL_TRANSFER, "4", "0", source=self.wh, target=self.wh2
        )
        self.assertFalse(GLEntry.objects.filter(voucher_no=se.name).exists())
        self.assertEqual(self.balance(self.inventory), self.stock_value())
        self.assertEqual(self.stock_value(), D("400.00"))

    def test_stock_entry_cancel_reverses_gl(self):
        se = self.stock_entry(
            StockEntryType.MATERIAL_RECEIPT, "5", "40", target=self.wh
        )
        se.cancel()
        self.assertEqual(self.balance(self.inventory), D("0"))
        self.assertEqual(self.balance(self.adjustment), D("0"))
        self.assertEqual(self.stock_value(), D("0"))
        self.integrity_ok()

    def test_transfer_cancel_without_gl_is_harmless(self):
        self.stock_entry(StockEntryType.MATERIAL_RECEIPT, "10", "40", target=self.wh)
        se = self.stock_entry(
            StockEntryType.MATERIAL_TRANSFER, "4", "0", source=self.wh, target=self.wh2
        )
        se.cancel()
        self.assertEqual(self.stock_value(), D("400.00"))
        self.integrity_ok()

    def test_purchase_invoice_cancel_reverses_inventory(self):
        pi = self.purchase([(self.item, "10", "100"), (self.service, "1", "250")])
        pi.cancel()
        self.assertEqual(self.balance(self.inventory), D("0"))
        self.assertEqual(self.balance(self.purchases), D("0"))
        self.assertEqual(self.balance(self.creditors), D("0"))
        self.assertEqual(self.stock_value(), D("0"))

    # ----------------------------------------------------------- configuration
    def _assert_refused(self, fn, field):
        with self.assertRaises(ValidationError) as ctx:
            fn()
        self.assertIn(field, str(ctx.exception))
        self.assertFalse(GLEntry.objects.exists())
        self.assertFalse(StockLedgerEntry.objects.exists())

    def test_missing_inventory_account_refuses_purchase_submit(self):
        self.company.default_inventory_account = None
        self.company.save()
        pi = self.purchase([(self.item, "10", "100")], submit=False)
        self._assert_refused(pi.submit, "default_inventory_account")
        pi.refresh_from_db()
        self.assertEqual(pi.docstatus, 0)

    def test_missing_cogs_account_refuses_sales_submit(self):
        self.purchase([(self.item, "10", "100")])
        self.company.default_expense_account = None
        self.company.save()
        before_gl = GLEntry.objects.count()
        before_sle = StockLedgerEntry.objects.count()
        si = self.sell([(self.item, "2", "150")], submit=False)
        with self.assertRaises(ValidationError) as ctx:
            si.submit()
        self.assertIn("default_expense_account", str(ctx.exception))
        self.assertEqual(GLEntry.objects.count(), before_gl)
        self.assertEqual(StockLedgerEntry.objects.count(), before_sle)

    def test_missing_adjustment_account_refuses_stock_entry(self):
        self.company.stock_adjustment_account = None
        self.company.save()
        se = self.stock_entry(
            StockEntryType.MATERIAL_RECEIPT, "5", "40", target=self.wh, submit=False
        )
        self._assert_refused(se.submit, "stock_adjustment_account")

    def test_group_inventory_account_refused(self):
        self.company.default_inventory_account = self.assets_group
        self.company.save()
        pi = self.purchase([(self.item, "10", "100")], submit=False)
        self._assert_refused(pi.submit, "group")

    def test_other_company_inventory_account_refused(self):
        other = Company.objects.create(name="Other Co", abbr="OC")
        foreign = _acc(other, "Stock In Hand", AccountRootType.ASSET)
        self.company.default_inventory_account = foreign
        self.company.save()
        pi = self.purchase([(self.item, "10", "100")], submit=False)
        self._assert_refused(pi.submit, "must belong")

    # --------------------------------------------------------- verify_integrity
    def test_verify_integrity_green_after_mixed_sequence(self):
        self.purchase([(self.item, "10", "100"), (self.service, "1", "99.99")])
        self.purchase([(self.item, "10", "120")])
        self.stock_entry(StockEntryType.MATERIAL_RECEIPT, "7", "33.333", target=self.wh)
        si = self.sell([(self.item, "5", "200")])
        self.stock_entry(StockEntryType.MATERIAL_ISSUE, "2", "0", source=self.wh)
        self.stock_entry(
            StockEntryType.MATERIAL_TRANSFER, "4", "0", source=self.wh, target=self.wh2
        )
        si.cancel()
        self.integrity_ok()
        check = SystemHealthCheck.objects.filter(
            check_name="perpetual_inventory_balance"
        ).latest("pk")
        self.assertEqual(check.status, "ok")
        self.assertEqual(self.balance(self.inventory), self.stock_value())

    def test_verify_integrity_detects_inventory_drift(self):
        self.purchase([(self.item, "10", "100")])
        self.integrity_ok()
        Bin.objects.filter(item=self.item).update(stock_value=D("1500.00"))
        with self.assertRaises(CommandError):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        check = SystemHealthCheck.objects.filter(
            check_name="perpetual_inventory_balance"
        ).latest("pk")
        self.assertEqual(check.status, "alert")
        self.assertIn("Perpetual Inventory Drift", check.details["errors"][0])

    def test_verify_integrity_detects_gl_side_drift(self):
        """A manual GL posting to the inventory account (outside stock) is drift."""
        from accounts.services import gl as gl_service

        self.purchase([(self.item, "10", "100")])
        voucher = self.company  # any model instance; name attribute set below
        voucher.name = "MANUAL-1"
        voucher.posting_date = DATE
        gl_service.post(
            voucher,
            [
                {"account": self.inventory, "debit": D("500.00")},
                {"account": self.adjustment, "credit": D("500.00")},
            ],
            posting_date=DATE,
        )
        with self.assertRaises(CommandError):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
