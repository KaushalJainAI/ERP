"""Phase-3 Task 1(b): batches on Sales Order / Invoice and Purchase Invoice lines."""

from buying.testing import next_bill_no
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import Account, AccountRootType, AccountType
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, Supplier, SupplierGroup
from core.models import Company, FiscalYear
from selling.mapping import make_sales_invoice
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import Batch, Item, ItemGroup, StockLedgerEntry, UOM, Warehouse
from stock.services import batches as batch_service

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


class DocumentBatchBase(TestCase):
    def setUp(self):
        c = self.company = Company.objects.create(name="Nidhi Spice", abbr="NS")
        FiscalYear.objects.create(
            name="FY 2026-27",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )
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
            "COGS",
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

        uom = UOM.objects.create(name="Kg")
        grp = ItemGroup.objects.create(name="Spices", is_group=False)
        self.haldi = Item.objects.create(
            code="HALDI-W",
            name="Turmeric Whole",
            item_group=grp,
            stock_uom=uom,
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=365,
        )
        self.salt = Item.objects.create(
            code="SALT", name="Salt", item_group=grp, stock_uom=uom, has_batch_no=True
        )
        self.plain = Item.objects.create(
            code="POUCH", name="Pouch", item_group=grp, stock_uom=uom
        )
        self.wh = Warehouse.objects.create(name="Store", company=c)

        self.supplier = Supplier.objects.create(
            name="Supp", company=c, supplier_group=SupplierGroup.objects.create(name="SG")
        )
        self.customer = Customer.objects.create(
            name="Cust", company=c, customer_group=CustomerGroup.objects.create(name="CG")
        )
        self._n = 0

    def name(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def purchase(self, lines, submit=True, update_stock=True):
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name=self.name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=DATE,
            update_stock=update_stock,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        for item, qty, rate, *rest in lines:
            PurchaseInvoiceItem.objects.create(
                purchase_invoice=pi,
                item=item,
                warehouse=self.wh,
                qty=D(qty),
                rate=D(rate),
                **(rest[0] if rest else {}),
            )
        if submit:
            pi.submit()
        return pi

    def sell(self, lines, submit=True, update_stock=True):
        si = SalesInvoice.objects.create(
            name=self.name("SINV"),
            customer=self.customer,
            company=self.company,
            posting_date=DATE,
            update_stock=update_stock,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        for item, qty, rate, *rest in lines:
            SalesInvoiceItem.objects.create(
                sales_invoice=si,
                item=item,
                warehouse=self.wh,
                qty=D(qty),
                rate=D(rate),
                **(rest[0] if rest else {}),
            )
        if submit:
            si.submit()
        return si


class PurchaseBatchTests(DocumentBatchBase):
    def test_purchase_auto_creates_batch_from_new_batch_fields(self):
        pi = self.purchase(
            [
                (
                    self.haldi,
                    10,
                    108,
                    {"manufacturing_date": DATE, "supplier_batch_no": "SUP-9"},
                )
            ]
        )
        line = pi.items.get()
        self.assertIsNotNone(line.batch_id)
        self.assertEqual(line.batch.supplier_batch_no, "SUP-9")
        self.assertEqual(line.batch.expiry_date, DATE + datetime.timedelta(days=365))
        sle = StockLedgerEntry.objects.get(voucher_no=pi.name, item=self.haldi)
        self.assertEqual(sle.batch_id, line.batch_id)
        self.assertEqual(sle.qty, D("10.000"))

    def test_purchase_reuses_a_picked_batch(self):
        b = Batch.objects.create(batch_no="LOT-1", item=self.haldi, expiry_date=DATE)
        pi = self.purchase([(self.haldi, 5, 100, {"batch": b})])
        self.assertEqual(pi.items.get().batch_id, b.pk)
        self.assertEqual(Batch.objects.filter(item=self.haldi).count(), 1)

    def test_purchase_refuses_batch_and_new_fields_together(self):
        b = Batch.objects.create(batch_no="LOT-1", item=self.haldi, expiry_date=DATE)
        with self.assertRaisesMessage(ValidationError, "not both"):
            self.purchase(
                [(self.haldi, 5, 100, {"batch": b, "expiry_date": DATE})]
            )

    def test_purchase_refuses_missing_expiry_source(self):
        # Tracks expiry but has no shelf life and no dates on the line: refused.
        no_shelf = Item.objects.create(
            code="NO-SHELF",
            name="No Shelf Life",
            item_group=self.haldi.item_group,
            stock_uom=self.haldi.stock_uom,
            has_batch_no=True,
            has_expiry_date=True,
        )
        with self.assertRaisesMessage(ValidationError, "tracks expiry"):
            self.purchase([(no_shelf, 5, 100)])

    def test_purchase_refuses_batch_on_untracked_item(self):
        b = Batch.objects.create(batch_no="P-1", item=self.plain)
        with self.assertRaisesMessage(ValidationError, "not batch-tracked"):
            self.purchase([(self.plain, 5, 10, {"batch": b})])

    def test_purchase_refuses_batch_when_update_stock_is_off(self):
        b = Batch.objects.create(batch_no="LOT-1", item=self.haldi, expiry_date=DATE)
        with self.assertRaisesMessage(ValidationError, "does not update stock"):
            self.purchase([(self.haldi, 5, 100, {"batch": b})], update_stock=False)
        self.assertFalse(StockLedgerEntry.objects.filter(voucher_no__startswith="PINV").exists())


class SalesBatchTests(DocumentBatchBase):
    def setUp(self):
        super().setUp()
        self.purchase([(self.haldi, 10, 108, {"manufacturing_date": DATE})])
        self.batch = self.haldi.pi_items.get().batch

    def test_sale_without_a_batch_is_fifo_picked(self):
        si = self.sell([(self.haldi, 2, 150)])
        sle = StockLedgerEntry.objects.get(voucher_no=si.name, item=self.haldi)
        self.assertEqual(sle.batch_id, self.batch.pk)
        self.assertEqual(sle.qty, D("-2.000"))

    def test_sale_beyond_usable_batch_stock_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "Insufficient usable batch stock"):
            self.sell([(self.haldi, 50, 150)])
        self.assertFalse(
            StockLedgerEntry.objects.filter(voucher_no__startswith="SINV").exists()
        )

    def test_sale_posts_the_lot(self):
        si = self.sell([(self.haldi, 2, 150, {"batch": self.batch})])
        sle = StockLedgerEntry.objects.get(voucher_no=si.name, item=self.haldi)
        self.assertEqual(sle.batch_id, self.batch.pk)
        self.assertEqual(sle.qty, D("-2.000"))
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh, self.batch), D("8.000"))

    def test_sale_refuses_without_stock_update(self):
        with self.assertRaisesMessage(ValidationError, "does not update stock"):
            self.sell([(self.haldi, 2, 150, {"batch": self.batch})], update_stock=False)

    def test_sale_refuses_batch_on_untracked_item(self):
        b = Batch.objects.create(batch_no="P-1", item=self.plain)
        with self.assertRaisesMessage(ValidationError, "not batch-tracked"):
            self.sell([(self.plain, 1, 10, {"batch": b})])

    def test_sale_refuses_another_items_batch(self):
        other = Batch.objects.create(batch_no="S-1", item=self.salt)
        with self.assertRaisesMessage(ValidationError, "different item"):
            self.sell([(self.haldi, 1, 150, {"batch": other})])

    def test_order_batch_is_informational_and_mapped_to_the_invoice(self):
        order = SalesOrder.objects.create(
            name=self.name("SO"),
            customer=self.customer,
            company=self.company,
            transaction_date=DATE,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.haldi,
            warehouse=self.wh,
            qty=D("3.000"),
            rate=D("150.00"),
            batch=self.batch,
        )
        order.submit()
        self.assertEqual(order.items.get().batch_id, self.batch.pk)
        self.assertEqual(
            StockLedgerEntry.objects.filter(item=self.haldi).count(), 1
        )  # only the purchase lot; an SO posts nothing

        invoice = make_sales_invoice(order)
        self.assertEqual(invoice.items.get().batch_id, self.batch.pk)
        self.assertEqual(invoice.sales_order_id, order.pk)
