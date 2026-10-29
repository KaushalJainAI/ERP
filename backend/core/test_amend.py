"""amend() copies child rows and recomputes derived totals."""

from buying.testing import next_bill_no
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, Supplier, SupplierGroup
from core.models import Company, DocStatus
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesInvoiceItem
from stock.models import UOM, Item, ItemGroup

D = datetime.date(2026, 6, 1)


class AmendCopiesChildrenTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Amend Co", abbr="AC", default_currency="INR"
        )
        assets = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.bank = Account.objects.create(
            name="Bank",
            company=self.company,
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.debtors = Account.objects.create(
            name="Debtors",
            company=self.company,
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        liab = Account.objects.create(
            name="Liabilities",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.creditors = Account.objects.create(
            name="Creditors",
            company=self.company,
            parent=liab,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        inc = Account.objects.create(
            name="Income",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.sales = Account.objects.create(
            name="Sales",
            company=self.company,
            parent=inc,
            root_type=AccountRootType.INCOME,
        )
        exp = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.purchases = Account.objects.create(
            name="Purchases",
            company=self.company,
            parent=exp,
            root_type=AccountRootType.EXPENSE,
        )
        uom = UOM.objects.create(name="Nos")
        grp = ItemGroup.objects.create(name="Goods", is_group=True)
        self.item1 = Item.objects.create(
            code="AM-1", name="One", item_group=grp, stock_uom=uom
        )
        self.item2 = Item.objects.create(
            code="AM-2", name="Two", item_group=grp, stock_uom=uom
        )

    def test_journal_entry_amend_copies_lines_and_can_submit(self):
        je = JournalEntry.objects.create(
            name="JE-A", posting_date=D, company=self.company
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.bank, debit=Decimal("70.00")
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.sales, credit=Decimal("70.00")
        )
        je.submit()
        je.cancel()
        je.refresh_from_db()

        new = je.amend()

        self.assertEqual(new.docstatus, DocStatus.DRAFT)
        self.assertEqual(new.amended_from_id, je.pk)
        self.assertNotEqual(new.pk, je.pk)
        self.assertEqual(new.name, "JE-A-1")
        new_lines = sorted(new.lines.all(), key=lambda line: line.pk)
        self.assertEqual(
            [(line.account_id, line.debit, line.credit) for line in new_lines],
            [
                (self.bank.pk, Decimal("70.00"), Decimal("0.00")),
                (self.sales.pk, Decimal("0.00"), Decimal("70.00")),
            ],
        )
        new.refresh_from_db()
        self.assertEqual(
            (new.total_debit, new.total_credit), (Decimal("70.00"), Decimal("70.00"))
        )
        # Original untouched.
        self.assertEqual(je.lines.count(), 2)
        self.assertEqual({line.journal_entry_id for line in je.lines.all()}, {je.pk})
        # The amended draft is submittable.
        new.submit()
        new.refresh_from_db()
        self.assertEqual(new.docstatus, DocStatus.SUBMITTED)

    def test_sales_invoice_amend_copies_items_and_grand_total(self):
        cg = CustomerGroup.objects.create(name="CG", is_group=True)
        cust = Customer.objects.create(
            name="Cust", company=self.company, customer_group=cg
        )
        si = SalesInvoice.objects.create(
            name="SINV-A",
            customer=cust,
            company=self.company,
            posting_date=D,
            update_stock=False,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si, item=self.item1, qty=Decimal("2"), rate=Decimal("10.00")
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si, item=self.item2, qty=Decimal("3"), rate=Decimal("5.50")
        )
        si.submit()
        si.cancel()
        si.refresh_from_db()
        self.assertEqual(si.grand_total, Decimal("36.50"))

        new = si.amend()

        new.refresh_from_db()
        self.assertEqual(new.docstatus, DocStatus.DRAFT)
        self.assertEqual(new.amended_from_id, si.pk)
        self.assertEqual(new.grand_total, Decimal("36.50"))
        self.assertEqual(new.items.count(), 2)
        self.assertEqual(
            sorted((i.item_id, i.qty, i.rate, i.amount) for i in new.items.all()),
            sorted((i.item_id, i.qty, i.rate, i.amount) for i in si.items.all()),
        )
        self.assertEqual(si.items.count(), 2)
        new.submit()
        new.refresh_from_db()
        self.assertEqual(new.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(new.grand_total, Decimal("36.50"))

    def test_purchase_invoice_amend_copies_items_and_grand_total(self):
        sg = SupplierGroup.objects.create(name="SG", is_group=True)
        sup = Supplier.objects.create(
            name="Sup", company=self.company, supplier_group=sg
        )
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name="PINV-A",
            supplier=sup,
            company=self.company,
            posting_date=D,
            update_stock=False,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi,
            item=self.item1,
            qty=Decimal("4"),
            rate=Decimal("25.00"),
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi, item=self.item2, qty=Decimal("1"), rate=Decimal("0.75")
        )
        pi.submit()
        pi.cancel()
        pi.refresh_from_db()

        new = pi.amend()

        new.refresh_from_db()
        self.assertEqual(new.items.count(), 2)
        self.assertEqual(new.grand_total, Decimal("100.75"))
        self.assertEqual(pi.items.count(), 2)
        new.submit()
        new.refresh_from_db()
        self.assertEqual(new.docstatus, DocStatus.SUBMITTED)

    def test_payment_entry_amend_copies_references(self):
        pe = PaymentEntry.objects.create(
            name="PE-A",
            payment_type=PaymentType.RECEIVE,
            posting_date=D,
            company=self.company,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=Decimal("50.00"),
            received_amount=Decimal("50.00"),
        )
        pe.submit()
        pe.cancel()
        pe.refresh_from_db()
        # Submit now validates references against real invoices, so attach the row to
        # the cancelled entry directly: this test is about amend copying child rows.
        PaymentEntryReference.objects.create(
            payment_entry=pe,
            reference_doctype="selling.SalesInvoice",
            reference_name="SINV-X",
            allocated_amount=Decimal("50.00"),
        )

        new = pe.amend()

        refs = list(new.references.all())
        self.assertEqual(len(refs), 1)
        self.assertEqual(
            (refs[0].reference_name, refs[0].allocated_amount),
            ("SINV-X", Decimal("50.00")),
        )
        self.assertEqual(pe.references.count(), 1)

    def test_amend_requires_cancelled(self):
        je = JournalEntry.objects.create(
            name="JE-D", posting_date=D, company=self.company
        )
        with self.assertRaises(ValidationError):
            je.amend()
        self.assertFalse(JournalEntry.objects.filter(amended_from=je).exists())
