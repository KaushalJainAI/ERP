import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from core.models import Company, DocStatus, FiscalYear
from accounts.models import Account, AccountRootType, AccountType, GLEntry
from stock.models import UOM, ItemGroup, Item, Warehouse, Bin
from selling.models import (
    CustomerGroup,
    Customer,
    SalesOrder,
    SalesOrderItem,
    SalesInvoice,
    SalesInvoiceItem,
)
from stock.services import ledger as stock_service


class SellingAppTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Bharat Tech", abbr="BT", default_currency="INR"
        )
        self.fiscal_year = FiscalYear.objects.create(
            name="FY 2026-27",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )
        self.cust_group = CustomerGroup.objects.create(
            name="Commercial Clients", is_group=True
        )
        self.customer = Customer.objects.create(
            name="Reliance Industries",
            company=self.company,
            customer_group=self.cust_group,
            gstin="27AAACR0000A1Z5",
        )

        # Accounts setup
        self.asset_root = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.debtors_acc = Account.objects.create(
            name="Debtors Account",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.income_root = Account.objects.create(
            name="Income",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.sales_income_acc = Account.objects.create(
            name="Sales Income Account",
            company=self.company,
            parent=self.income_root,
            root_type=AccountRootType.INCOME,
        )

        # Stock setup
        self.uom = UOM.objects.create(name="Units")
        self.item_grp = ItemGroup.objects.create(name="Hardware", is_group=True)
        self.item = Item.objects.create(
            code="HW-001",
            name="Server Rack",
            item_group=self.item_grp,
            stock_uom=self.uom,
        )
        self.warehouse = Warehouse.objects.create(
            name="Main Store", company=self.company
        )

        # Seed initial stock (+100 qty @ 50.00)
        dummy_receipt = Customer.objects.create(
            name="Seed Supplier", company=self.company, customer_group=self.cust_group
        )
        stock_service.post(
            voucher=dummy_receipt,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("100.000"),
                    "valuation_rate": Decimal("50.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_customer_creation_success(self):
        """GOOD: Customer master created under company."""
        self.assertEqual(self.customer.company, self.company)
        self.assertEqual(str(self.customer), "Reliance Industries (BT)")

    def test_sales_order_lifecycle(self):
        """GOOD: SalesOrder creation and submit workflow."""
        so = SalesOrder.objects.create(
            name="SO-2026-0001",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 1),
        )
        SalesOrderItem.objects.create(
            sales_order=so,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("10.000"),
            rate=Decimal("200.00"),
        )
        so.submit()
        self.assertEqual(so.grand_total, Decimal("2000.00"))
        self.assertEqual(so.docstatus, DocStatus.SUBMITTED)

    def test_sales_invoice_submit_and_cancel_gl_sle_posting(self):
        """GOOD: Submitting SalesInvoice posts GL (Debtors Dr, Income Cr) and SLE (-qty); cancel reverses both."""
        si = SalesInvoice.objects.create(
            name="SINV-2026-0001",
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            update_stock=True,
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("5.000"),
            rate=Decimal("300.00"),
        )

        # Submit Invoice
        si.submit()
        self.assertEqual(si.grand_total, Decimal("1500.00"))
        self.assertEqual(si.docstatus, DocStatus.SUBMITTED)

        # Verify GL entries (2 active rows: Debtors Dr 1500, Income Cr 1500)
        gl_entries = GLEntry.objects.filter(
            voucher_no="SINV-2026-0001", is_cancelled=False
        )
        self.assertEqual(gl_entries.count(), 2)

        # Verify Stock Bin updated (100 - 5 = 95)
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("95.000"))

        # Cancel Invoice
        si.cancel()
        self.assertEqual(si.docstatus, DocStatus.CANCELLED)

        # Verify GL reversals (4 total rows)
        self.assertEqual(GLEntry.objects.filter(voucher_no="SINV-2026-0001").count(), 4)

        # Verify Stock Bin restored to 100
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("100.000"))

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_duplicate_customer_name_in_same_company_fails(self):
        """BAD: Creating duplicate customer name in same company violates UNIQUE constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(
                    name="Reliance Industries",
                    company=self.company,
                    customer_group=self.cust_group,
                )

    def test_sales_invoice_without_items_fails(self):
        """BAD: Submitting SalesInvoice without items raises ValidationError."""
        si = SalesInvoice.objects.create(
            name="SINV-BAD-01",
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
        )
        with self.assertRaises(ValidationError):
            si.submit()

    def test_sales_invoice_group_account_fails(self):
        """BAD: Submitting SalesInvoice with a group account raises ValidationError."""
        si = SalesInvoice.objects.create(
            name="SINV-BAD-02",
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date(2026, 4, 2),
            debit_to=self.asset_root,  # Group node
            income_account=self.sales_income_acc,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("1.000"),
            rate=Decimal("100.00"),
        )
        with self.assertRaises(ValidationError):
            si.submit()
