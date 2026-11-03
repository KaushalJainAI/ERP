import datetime
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Company, DocStatus, FiscalYear, AuditLog
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from stock.models import Bin, Item, ItemGroup, StockLedgerEntry, UOM, Warehouse
from selling.models import (
    Customer,
    CustomerGroup,
    DispatchStatus,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from users.models import Role, RolePermission, User, UserRole
from selling.services import dispatch as dispatch_service


class HardenedSystemsIntegrityTests(TestCase):
    """Rigorous stress and edge-case test suite for Systems Thinking invariants:
    - Atomicity & mid-operation rollback guarantees
    - Deep Foreign Key chain protection (zero 500 errors)
    - Strict reverse-chronological cancellation order
    - Conservation laws: Sum(Debits) == Sum(Credits), Non-negative inventory
    - Dual-tier deletion policy (Soft vs. Hard delete RBAC)
    """

    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(
            name="Apex Foods",
            abbr="AF",
            default_currency="INR",
            enable_perpetual_inventory=True,
        )
        self.fiscal_year = FiscalYear.objects.create(
            name="FY 2026-27",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )

        # Accounts setup
        self.asset_root = Account.objects.create(
            name="Assets AF",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.debtors_acc = Account.objects.create(
            name="Debtors AF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.bank_acc = Account.objects.create(
            name="HDFC Bank AF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.stock_in_hand_acc = Account.objects.create(
            name="Stock In Hand AF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )

        self.income_root = Account.objects.create(
            name="Income AF",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.sales_income_acc = Account.objects.create(
            name="Sales Income AF",
            company=self.company,
            parent=self.income_root,
            root_type=AccountRootType.INCOME,
        )

        self.expense_root = Account.objects.create(
            name="Expenses AF",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.cogs_acc = Account.objects.create(
            name="COGS AF",
            company=self.company,
            parent=self.expense_root,
            root_type=AccountRootType.EXPENSE,
            account_type=AccountType.COST_OF_GOODS_SOLD,
        )
        self.stock_adj_acc = Account.objects.create(
            name="Stock Adjustment AF",
            company=self.company,
            parent=self.expense_root,
            root_type=AccountRootType.EXPENSE,
        )

        self.company.default_receivable_account = self.debtors_acc
        self.company.default_income_account = self.sales_income_acc
        self.company.default_inventory_account = self.stock_in_hand_acc
        self.company.default_expense_account = self.cogs_acc
        self.company.stock_adjustment_account = self.stock_adj_acc
        self.company.save()

        # Customer & Item Setup
        self.cust_group = CustomerGroup.objects.create(name="Retailers")
        self.customer = Customer.objects.create(
            name="Metro Store",
            company=self.company,
            customer_group=self.cust_group,
            is_active=True,
        )

        self.uom = UOM.objects.create(name="Pack")
        self.item_group = ItemGroup.objects.create(name="Packaged")
        self.item = Item.objects.create(
            code="TEA-PREM-500G",
            name="Premium Tea 500g",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=True,
            standard_rate=Decimal("200.00"),
            is_active=True,
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company, name="Finished Goods WH", is_group=False
        )

        # Pre-seed stock in Bin: 50 packs @ 120.00 valuation rate
        self.bin = Bin.objects.create(
            item=self.item,
            warehouse=self.warehouse,
            actual_qty=Decimal("50.000"),
            valuation_rate=Decimal("120.000000"),
            stock_value=Decimal("6000.00"),
        )

        # Users & RBAC
        self.admin = User.objects.create_superuser(
            username="hard_admin", email="admin@hard.test", password="password123"
        )
        self.staff_user = User.objects.create_user(
            username="hard_staff", email="staff@hard.test", password="password123"
        )
        self.staff_role = Role.objects.create(name="StaffRole", description="Staff")
        UserRole.objects.create(user=self.staff_user, role=self.staff_role)

        for label in [
            "selling.Customer",
            "selling.SalesOrder",
            "selling.SalesInvoice",
            "stock.Item",
            "stock.Warehouse",
            "accounts.Account",
        ]:
            RolePermission.objects.create(
                role=self.staff_role,
                model_label=label,
                can_read=True,
                can_create=True,
                can_write=True,
                can_delete=True,
            )

    # -------------------------------------------------------------------------
    # TEST 1: Atomicity & Mid-Operation Failure Rollback
    # -------------------------------------------------------------------------
    def test_insufficient_stock_dispatch_rolls_back_everything(self):
        """When an order requests more stock than available in the warehouse,
        dispatch_order MUST abort, raise ValidationError, and leave 0 leaked rows.
        """
        # Create Sales Order for 100 packs (we only have 50 in bin)
        order = SalesOrder.objects.create(
            name="SO-SHORT-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("100.000"),
            rate=Decimal("200.00"),
            amount=Decimal("20000.00"),
        )

        sle_count_before = StockLedgerEntry.objects.count()
        gl_count_before = GLEntry.objects.count()
        invoice_count_before = SalesInvoice.objects.count()

        # Attempt to dispatch
        with self.assertRaises(ValidationError) as ctx:
            dispatch_service.dispatch_order(order)
        self.assertIn("Insufficient stock", str(ctx.exception))

        # Assert 100% rollback: Zero records leaked
        self.assertEqual(StockLedgerEntry.objects.count(), sle_count_before)
        self.assertEqual(GLEntry.objects.count(), gl_count_before)
        self.assertEqual(SalesInvoice.objects.count(), invoice_count_before)

        # Assert stock bin is completely untouched
        self.bin.refresh_from_db()
        self.assertEqual(self.bin.actual_qty, Decimal("50.000"))

        # Assert order dispatch status remains PENDING
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)

    # -------------------------------------------------------------------------
    # TEST 2: Adversarial Cancellation Sequence (Enforcing Invariants)
    # -------------------------------------------------------------------------
    def test_adversarial_cancellation_ordering(self):
        """Verifies strict reverse-chronological dependency order during cancellation:
        1. Cannot cancel SalesOrder while SalesInvoice is active.
        2. Cannot cancel SalesInvoice while Payment is allocated.
        3. Undoing in reverse (Payment -> Invoice -> Order) succeeds and restores all state.
        """
        # 1. Create and dispatch order for 10 packs
        order = SalesOrder.objects.create(
            name="SO-CHAIN-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("10.000"),
            rate=Decimal("200.00"),
            amount=Decimal("2000.00"),
        )

        invoice = dispatch_service.dispatch_order(order)
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)
        self.assertEqual(invoice.docstatus, DocStatus.SUBMITTED)

        # 2. Record Customer Payment for the invoice
        payment = PaymentEntry.objects.create(
            name="PAY-CHAIN-001",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.customer.name,
            paid_from=self.debtors_acc,
            paid_to=self.bank_acc,
            paid_amount=Decimal("2000.00"),
            received_amount=Decimal("2000.00"),
            docstatus=DocStatus.DRAFT,
        )
        PaymentEntryReference.objects.create(
            payment_entry=payment,
            reference_doctype="selling.SalesInvoice",
            reference_name=invoice.name,
            allocated_amount=Decimal("2000.00"),
        )
        payment.submit()

        invoice.refresh_from_db()
        self.assertEqual(invoice.outstanding_amount, Decimal("0.00"))

        # ATTACK A: Try to cancel the Sales Order while Invoice is submitted
        with self.assertRaises(ValidationError):
            order.cancel()

        # ATTACK B: Try to cancel the Sales Invoice while Payment is active
        with self.assertRaises(ValidationError):
            invoice.cancel()

        # LEGITIMATE REVERSAL STEP 1: Cancel Payment
        payment.cancel()
        invoice.refresh_from_db()
        self.assertEqual(invoice.outstanding_amount, Decimal("2000.00"))

        # LEGITIMATE REVERSAL STEP 2: Cancel Invoice
        invoice.cancel()
        self.bin.refresh_from_db()
        # Stock restored from 40 back to 50!
        self.assertEqual(self.bin.actual_qty, Decimal("50.000"))

        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)

        # LEGITIMATE REVERSAL STEP 3: Cancel Sales Order
        order.cancel()
        self.assertEqual(order.docstatus, DocStatus.CANCELLED)

    # -------------------------------------------------------------------------
    # TEST 3: Deep Foreign Key Chain Deletion Protection (No 500 Errors!)
    # -------------------------------------------------------------------------
    def test_deep_fk_chain_deletions_return_409_never_500(self):
        """Asserts that attempting to delete referenced records across the entire
        operational tree returns clean HTTP 409 Conflict with details, never 500.
        """
        # Create an active invoice
        order = SalesOrder.objects.create(
            name="SO-FK-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("5.000"),
            rate=Decimal("200.00"),
            amount=Decimal("1000.00"),
        )
        invoice = dispatch_service.dispatch_order(order)

        self.client.force_authenticate(user=self.admin)

        # 1. Attempt to delete Customer (referenced by SalesOrder, SalesInvoice)
        res_cust = self.client.delete(f"/api/selling/customers/{self.customer.pk}/")
        self.assertEqual(res_cust.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_cust.data.get("code"), "foreign_key_protected")
        self.assertIn("referenced by", res_cust.data.get("error", "").lower())

        # 2. Attempt to delete Item (referenced by SalesOrderItem, SalesInvoiceItem, SLE)
        res_item = self.client.delete(f"/api/stock/items/{self.item.pk}/")
        self.assertEqual(res_item.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_item.data.get("code"), "foreign_key_protected")

        # 3. Attempt to delete Warehouse (referenced by SLE, SalesOrderItem)
        res_wh = self.client.delete(f"/api/stock/warehouses/{self.warehouse.pk}/")
        self.assertEqual(res_wh.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_wh.data.get("code"), "foreign_key_protected")

        # 4. Attempt to delete Account (referenced by GLEntry, SalesInvoice)
        res_acc = self.client.delete(f"/api/accounts/accounts/{self.debtors_acc.pk}/")
        self.assertEqual(res_acc.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_acc.data.get("code"), "foreign_key_protected")

    # -------------------------------------------------------------------------
    # TEST 4: Conservation Laws & Accounting Equation Integrity
    # -------------------------------------------------------------------------
    def test_conservation_laws_after_complete_flow(self):
        """Asserts physical stock non-negativity and exact double-entry balance:
        Sum(Debits) == Sum(Credits) across all posted vouchers.
        """
        order = SalesOrder.objects.create(
            name="SO-CONSERVE-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("15.000"),
            rate=Decimal("200.00"),
            amount=Decimal("3000.00"),
        )
        invoice = dispatch_service.dispatch_order(order)

        # 1. Conservation of Mass / Physical Stock
        self.bin.refresh_from_db()
        self.assertEqual(self.bin.actual_qty, Decimal("35.000"))
        self.assertGreaterEqual(self.bin.actual_qty, Decimal("0.000"))

        # 2. Conservation of Monetary Value (Double Entry Equality)
        totals = GLEntry.objects.filter(company=self.company).aggregate(
            total_debit=Sum("debit"), total_credit=Sum("credit")
        )
        self.assertAlmostEqual(totals["total_debit"], totals["total_credit"], places=2)

    # -------------------------------------------------------------------------
    # TEST 5: Invalid Non-Existent Foreign Keys on API Writes
    # -------------------------------------------------------------------------
    def test_non_existent_foreign_keys_return_400_never_500(self):
        """Attempting to post with a non-existent foreign key ID must return
        clean HTTP 400 Bad Request, never 500.
        """
        self.client.force_authenticate(user=self.staff_user)

        # Send invalid customer_group ID
        payload = {
            "name": "Ghost Customer",
            "customer_group": 999999,
        }
        res = self.client.post("/api/selling/customers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Customer.objects.filter(name="Ghost Customer").exists())
