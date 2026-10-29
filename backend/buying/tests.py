from buying.testing import next_bill_no
import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from core.models import Company, DocStatus
from accounts.models import Account, AccountRootType, AccountType, GLEntry
from stock.models import UOM, ItemGroup, Item, Warehouse, Bin
from buying.models import (
    SupplierGroup,
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
)


class BuyingAppTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Tata Steel", abbr="TS", default_currency="INR"
        )
        self.supp_group = SupplierGroup.objects.create(
            name="Raw Material Vendors", is_group=True
        )
        self.supplier = Supplier.objects.create(
            name="Jindal Steels",
            company=self.company,
            supplier_group=self.supp_group,
            gstin="06AAACJ0000A1Z2",
        )

        # Accounts setup
        self.liability_root = Account.objects.create(
            name="Liabilities",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.creditors_acc = Account.objects.create(
            name="Creditors Account",
            company=self.company,
            parent=self.liability_root,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        self.expense_root = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.purchase_expense_acc = Account.objects.create(
            name="Purchase Expense Account",
            company=self.company,
            parent=self.expense_root,
            root_type=AccountRootType.EXPENSE,
        )

        # Stock setup
        self.uom = UOM.objects.create(name="Kg")
        self.item_grp = ItemGroup.objects.create(name="Raw Metals", is_group=True)
        self.item = Item.objects.create(
            code="RM-001",
            name="Iron Ore 10mm",
            item_group=self.item_grp,
            stock_uom=self.uom,
        )
        self.warehouse = Warehouse.objects.create(
            name="Raw Material Store", company=self.company
        )

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_supplier_creation_success(self):
        """GOOD: Supplier master created under company."""
        self.assertEqual(self.supplier.company, self.company)
        self.assertEqual(str(self.supplier), "Jindal Steels (TS)")

    def test_purchase_order_lifecycle(self):
        """GOOD: PurchaseOrder creation and submit workflow."""
        po = PurchaseOrder.objects.create(
            name="PO-2026-0001",
            supplier=self.supplier,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 1),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("500.000"),
            rate=Decimal("40.00"),
        )
        po.submit()

        self.assertEqual(po.grand_total, Decimal("20000.00"))
        self.assertEqual(po.docstatus, DocStatus.SUBMITTED)

    def test_purchase_invoice_submit_and_cancel_gl_sle_posting(self):
        """GOOD: Submitting PurchaseInvoice posts GL (Expense Dr, Creditors Cr) and SLE (+qty); cancel reverses both."""
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name="PINV-2026-0001",
            supplier=self.supplier,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            update_stock=True,
            credit_to=self.creditors_acc,
            expense_account=self.purchase_expense_acc,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("100.000"),
            rate=Decimal("45.00"),
        )

        # Submit Invoice
        pi.submit()
        self.assertEqual(pi.grand_total, Decimal("4500.00"))
        self.assertEqual(pi.docstatus, DocStatus.SUBMITTED)

        # Verify GL entries (2 active rows: Expense Dr 4500, Creditors Cr 4500)
        gl_entries = GLEntry.objects.filter(
            voucher_no="PINV-2026-0001", is_cancelled=False
        )
        self.assertEqual(gl_entries.count(), 2)

        # Verify Stock Bin updated (+100)
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("100.000"))

        # Cancel Invoice
        pi.cancel()
        self.assertEqual(pi.docstatus, DocStatus.CANCELLED)

        # Verify GL reversals (4 total rows)
        self.assertEqual(GLEntry.objects.filter(voucher_no="PINV-2026-0001").count(), 4)

        # Verify Stock Bin restored to 0
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("0.000"))

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_duplicate_supplier_name_in_same_company_fails(self):
        """BAD: Creating duplicate supplier name in same company violates UNIQUE constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Supplier.objects.create(
                    name="Jindal Steels",
                    company=self.company,
                    supplier_group=self.supp_group,
                )

    def test_purchase_invoice_without_items_fails(self):
        """BAD: Submitting PurchaseInvoice without items raises ValidationError."""
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name="PINV-BAD-01",
            supplier=self.supplier,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            credit_to=self.creditors_acc,
            expense_account=self.purchase_expense_acc,
        )
        with self.assertRaises(ValidationError):
            pi.submit()

    def test_purchase_invoice_group_account_fails(self):
        """BAD: Submitting PurchaseInvoice with a group account raises ValidationError."""
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name="PINV-BAD-02",
            supplier=self.supplier,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            credit_to=self.liability_root,  # Group node
            expense_account=self.purchase_expense_acc,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("10.000"),
            rate=Decimal("40.00"),
        )
        with self.assertRaises(ValidationError):
            pi.submit()
