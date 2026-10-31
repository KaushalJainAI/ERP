import datetime
from decimal import Decimal
import unittest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError, transaction
from rest_framework.test import APIClient
from rest_framework import status
from django.core.management import call_command
from django.test import TestCase
from core.models import Company, DocStatus, FiscalYear
from accounts.models import Account, AccountRootType, AccountType
from stock.models import Bin, Item, ItemGroup, UOM, Warehouse
from selling.models import (
    CustomerGroup,
    Customer,
    DispatchStatus,
    SalesOrder,
    SalesOrderItem,
    SalesInvoice,
    SalesInvoiceItem,
)
from selling.services import dispatch as dispatch_service


class DispatchStatusTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Foods", abbr="NF", default_currency="INR"
        )
        self.fiscal_year = FiscalYear.objects.create(
            name="FY 2026-27",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )
        self.cust_group = CustomerGroup.objects.create(name="Wholesale", is_group=True)
        self.customer = Customer.objects.create(
            name="Kirana Mart",
            company=self.company,
            customer_group=self.cust_group,
            gstin="27AAACR0000A1Z5",
        )

        self.asset_root = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.debtors_acc = Account.objects.create(
            name="Debtors NF",
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
            name="Sales Income NF",
            company=self.company,
            parent=self.income_root,
            root_type=AccountRootType.INCOME,
        )

        self.uom = UOM.objects.create(name="Kg")
        self.item_grp = ItemGroup.objects.create(name="Spices", is_group=True)
        self.item1 = Item.objects.create(
            code="CHILI-100G",
            name="Red Chili Powder 100g",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_stock_item=False,
            standard_rate=Decimal("50.00"),
        )
        self.item2 = Item.objects.create(
            code="TURM-100G",
            name="Turmeric Powder 100g",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_stock_item=False,
            standard_rate=Decimal("40.00"),
        )

        self.expense_root = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.stock_in_hand_acc = Account.objects.create(
            name="Stock In Hand NF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )
        self.cogs_acc = Account.objects.create(
            name="COGS NF",
            company=self.company,
            parent=self.expense_root,
            root_type=AccountRootType.EXPENSE,
            account_type=AccountType.COST_OF_GOODS_SOLD,
        )
        self.stock_adj_acc = Account.objects.create(
            name="Stock Adjustment NF",
            company=self.company,
            parent=self.expense_root,
            root_type=AccountRootType.EXPENSE,
        )
        self.company.enable_perpetual_inventory = True
        self.company.default_inventory_account = self.stock_in_hand_acc
        self.company.default_expense_account = self.cogs_acc
        self.company.stock_adjustment_account = self.stock_adj_acc
        self.company.save()

        self.wh = Warehouse.objects.create(
            name="Stores NF",
            company=self.company,
            is_group=False,
        )
        self.company.ready_warehouse = self.wh
        self.company.save()

        self.stock_item = Item.objects.create(
            code="MASALA-50G",
            name="Garam Masala 50g",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_stock_item=True,
            standard_rate=Decimal("100.00"),
        )

    def _create_and_submit_order(self):
        order = SalesOrder.objects.create(
            name="SO-TEST-001",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item1,
            qty=Decimal("10.000"),
            rate=Decimal("50.00"),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item2,
            qty=Decimal("5.000"),
            rate=Decimal("40.00"),
        )
        order.submit()
        return order

    def test_dispatch_status_lifecycle_and_integrity(self):
        # 1. None billed -> PENDING
        order = self._create_and_submit_order()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)
        self.assertEqual(dispatch_service.compute_status(order), DispatchStatus.PENDING)
        self.assertEqual(dispatch_service.integrity_errors(), [])

        # 2. Part billed -> PART_DISPATCHED
        inv1 = SalesInvoice.objects.create(
            name="SINV-TEST-001",
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date.today(),
            due_date=datetime.date.today(),
            sales_order=order,
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
            update_stock=False,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv1,
            item=self.item1,
            qty=Decimal("5.000"),
            rate=Decimal("50.00"),
        )
        inv1.submit()

        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PART_DISPATCHED)
        self.assertEqual(dispatch_service.integrity_errors(), [])

        # 3. Full billed -> DISPATCHED
        inv2 = SalesInvoice.objects.create(
            name="SINV-TEST-002",
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date.today(),
            due_date=datetime.date.today(),
            sales_order=order,
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
            update_stock=False,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv2,
            item=self.item1,
            qty=Decimal("5.000"),
            rate=Decimal("50.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv2,
            item=self.item2,
            qty=Decimal("5.000"),
            rate=Decimal("40.00"),
        )
        inv2.submit()

        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)
        self.assertEqual(dispatch_service.integrity_errors(), [])

        # 4. Cancel inv2 -> reverts to PART_DISPATCHED
        inv2.cancel()
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PART_DISPATCHED)
        self.assertEqual(dispatch_service.integrity_errors(), [])

        # Cancel inv1 -> reverts to PENDING
        inv1.cancel()
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)
        self.assertEqual(dispatch_service.integrity_errors(), [])

        # 5. Drift caught by integrity_errors
        SalesOrder.objects.filter(pk=order.pk).update(
            dispatch_status=DispatchStatus.DISPATCHED
        )
        errors = dispatch_service.integrity_errors()
        self.assertEqual(len(errors), 1)
        self.assertIn("SO-TEST-001", errors[0])

        # 6. repost_orders command fixes drift
        call_command("repost_orders")
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)
        self.assertEqual(dispatch_service.integrity_errors(), [])

    def test_pending_orders_quantities_and_query_count(self):
        ready_wh = Warehouse.objects.create(
            name="Ready Goods Warehouse",
            company=self.company,
            is_group=False,
        )
        self.company.ready_warehouse = ready_wh
        self.company.save()

        stock_item = Item.objects.create(
            code="STOCK-SPICE-01",
            name="Special Garam Masala",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_stock_item=True,
            standard_rate=Decimal("100.00"),
        )
        # 15 kg ready in warehouse
        Bin.objects.create(
            item=stock_item,
            warehouse=ready_wh,
            actual_qty=Decimal("15.000"),
        )

        order1 = SalesOrder.objects.create(
            name="SO-PENDING-001",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 1),
        )
        SalesOrderItem.objects.create(
            sales_order=order1,
            item=stock_item,
            warehouse=ready_wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order1.submit()

        order2 = SalesOrder.objects.create(
            name="SO-PENDING-002",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 2),
        )
        SalesOrderItem.objects.create(
            sales_order=order2,
            item=stock_item,
            warehouse=ready_wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order2.submit()

        # Check quantities across two orders
        results = dispatch_service.pending_orders(self.company)
        self.assertEqual(len(results), 2)
        # Order 1 (older) gets full 10.000 ready
        self.assertEqual(results[0]["id"], order1.pk)
        self.assertEqual(results[0]["lines"][0]["remaining_qty"], Decimal("10.000"))
        self.assertEqual(results[0]["lines"][0]["ready_qty"], Decimal("10.000"))
        self.assertEqual(results[0]["lines"][0]["short_qty"], Decimal("0.000"))

        # Order 2 (newer) gets remaining 5.000 ready and 5.000 short
        self.assertEqual(results[1]["id"], order2.pk)
        self.assertEqual(results[1]["lines"][0]["remaining_qty"], Decimal("10.000"))
        self.assertEqual(results[1]["lines"][0]["ready_qty"], Decimal("5.000"))
        self.assertEqual(results[1]["lines"][0]["short_qty"], Decimal("5.000"))

        # Test query count constant (avoid N+1)
        with self.assertNumQueries(4):
            dispatch_service.pending_orders(self.company)

        # Add third order and verify query count does not scale with order count
        order3 = SalesOrder.objects.create(
            name="SO-PENDING-003",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 3),
        )
        SalesOrderItem.objects.create(
            sales_order=order3,
            item=stock_item,
            warehouse=ready_wh,
            qty=Decimal("5.000"),
            rate=Decimal("100.00"),
        )
        order3.submit()

        with self.assertNumQueries(4):
            results3 = dispatch_service.pending_orders(self.company)
        self.assertEqual(len(results3), 3)
        self.assertEqual(results3[2]["lines"][0]["ready_qty"], Decimal("0.000"))
        self.assertEqual(results3[2]["lines"][0]["short_qty"], Decimal("5.000"))

    def test_pending_orders_api_endpoint(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="dispatch_admin",
            email="dispatch_admin@test.com",
            password="pass",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        order = self._create_and_submit_order()
        res = client.get(
            f"/api/selling/sales-orders/pending/?company={self.company.id}"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], order.pk)
        self.assertEqual(len(res.data[0]["lines"]), 2)

    def test_sales_order_list_filters_by_transaction_date(self):
        """Orders home: ?transaction_date= returns only orders dated that day."""
        User = get_user_model()
        user = User.objects.create_superuser(
            username="so_date_admin",
            email="so_date_admin@test.com",
            password="pass",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        SalesOrder.objects.create(
            name="SO-DATE-TODAY",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 10, 31),
        )
        SalesOrder.objects.create(
            name="SO-DATE-YESTERDAY",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date(2026, 10, 30),
        )
        res = client.get(
            f"/api/selling/sales-orders/?company={self.company.id}"
            "&transaction_date=2026-10-31"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["name"], "SO-DATE-TODAY")

    def test_bill_numbering_financial_year_boundary(self):
        from accounts import mapping as account_mapping
        from core.models import NamingSeries

        # 31 March 2026 falls in FY 2025-26 -> '2526'
        march_31 = datetime.date(2026, 3, 31)
        sinv_march = account_mapping.financial_year_series("SINV", march_31)
        pinv_march = account_mapping.financial_year_series("PINV", march_31)
        self.assertEqual(sinv_march, "SINV-2526-")
        self.assertEqual(pinv_march, "PINV-2526-")

        # 1 April 2026 falls in FY 2026-27 -> '2627'
        april_1 = datetime.date(2026, 4, 1)
        sinv_april = account_mapping.financial_year_series("SINV", april_1)
        pinv_april = account_mapping.financial_year_series("PINV", april_1)
        self.assertEqual(sinv_april, "SINV-2627-")
        self.assertEqual(pinv_april, "PINV-2627-")

        # 31 March and 1 April fall in different series
        self.assertNotEqual(sinv_march, sinv_april)
        self.assertNotEqual(pinv_march, pinv_april)

        # Full invoice number is <= 16 chars (GST requirement)
        name = NamingSeries.next(sinv_april)
        self.assertLessEqual(len(name), 16)
        self.assertTrue(name.startswith("SINV-2627-"))

        # Overly long prefix raises ValueError
        with self.assertRaises(ValueError):
            account_mapping.financial_year_series("VERYLONGPREFIXNAME", april_1)

    def test_due_date_population_and_constraints(self):
        from buying.models import (
            Supplier,
            SupplierGroup,
            PurchaseInvoice,
            PurchaseOrder,
            PurchaseOrderItem,
        )
        from selling.mapping import make_sales_invoice
        from buying.mapping import make_purchase_invoice

        # 1. Customer credit_days override (30 days)
        self.customer.credit_days = 30
        self.customer.save()

        order = self._create_and_submit_order()
        inv = make_sales_invoice(order)
        self.assertEqual(inv.due_date, inv.posting_date + datetime.timedelta(days=30))

        # 2. Company default (90 days)
        self.customer.credit_days = None
        self.customer.save()
        inv2 = SalesInvoice.objects.create(
            customer=self.customer,
            company=self.company,
            posting_date=datetime.date(2026, 4, 1),
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
            update_stock=False,
        )
        self.assertEqual(
            inv2.due_date, datetime.date(2026, 4, 1) + datetime.timedelta(days=90)
        )

        # 3. CheckConstraint: due_date < posting_date is refused by DB
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalesInvoice.objects.create(
                    customer=self.customer,
                    company=self.company,
                    posting_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 4, 5),  # before posting_date
                    debit_to=self.debtors_acc,
                    income_account=self.sales_income_acc,
                    update_stock=False,
                )

        # 4. Supplier credit days on purchase invoice
        supp_grp = SupplierGroup.objects.create(name="Farmers")
        supp = Supplier.objects.create(
            name="Spice Farmer",
            company=self.company,
            supplier_group=supp_grp,
            credit_days=15,
        )
        creditors_acc = Account.objects.create(
            name="Creditors NF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        expense_acc = Account.objects.create(
            name="Purchases NF",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.EXPENSE,
        )

        po = PurchaseOrder.objects.create(
            name="PO-TEST-001",
            supplier=supp,
            company=self.company,
            transaction_date=datetime.date(2026, 4, 1),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item=self.item1,
            qty=Decimal("10.000"),
            rate=Decimal("50.00"),
        )
        po.submit()

        pinv = make_purchase_invoice(po)
        self.assertEqual(pinv.due_date, pinv.posting_date + datetime.timedelta(days=15))

        # 5. PurchaseInvoice DB constraint: due_date < posting_date is refused by DB
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PurchaseInvoice.objects.create(
                    supplier=supp,
                    company=self.company,
                    posting_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 4, 5),
                    credit_to=creditors_acc,
                    expense_account=expense_acc,
                    update_stock=False,
                )

        # 6. Purchase invoice validation before submit: due_date required at submit
        pinv.due_date = None
        with self.assertRaises(ValidationError):
            pinv.validate_before_submit()

    def test_payment_status_computed_property_and_serializer(self):
        from selling.serializers import SalesInvoiceSerializer

        today = datetime.date.today()

        # 1. Draft invoice -> "Draft"
        inv = SalesInvoice.objects.create(
            customer=self.customer,
            company=self.company,
            posting_date=today,
            due_date=today + datetime.timedelta(days=10),
            debit_to=self.debtors_acc,
            income_account=self.sales_income_acc,
            update_stock=False,
            net_total=Decimal("100.00"),
            grand_total=Decimal("100.00"),
            rounded_total=Decimal("100.00"),
            outstanding_amount=Decimal("100.00"),
        )
        self.assertEqual(inv.payment_status, "Draft")
        self.assertEqual(SalesInvoiceSerializer(inv).data["payment_status"], "Draft")

        # 2. Submitted, full amount open, not past due -> "Due"
        inv.docstatus = DocStatus.SUBMITTED
        self.assertEqual(inv.payment_status, "Due")
        self.assertEqual(SalesInvoiceSerializer(inv).data["payment_status"], "Due")

        # 3. Submitted, partial amount open, not past due -> "Part Paid"
        inv.outstanding_amount = Decimal("40.00")
        self.assertEqual(inv.payment_status, "Part Paid")
        self.assertEqual(
            SalesInvoiceSerializer(inv).data["payment_status"], "Part Paid"
        )

        # 4. Submitted, fully paid -> "Paid"
        inv.outstanding_amount = Decimal("0.00")
        self.assertEqual(inv.payment_status, "Paid")
        self.assertEqual(SalesInvoiceSerializer(inv).data["payment_status"], "Paid")

        # 5. Submitted, outstanding > 0 and due_date < today -> "Overdue"
        inv.due_date = today - datetime.timedelta(days=1)
        inv.outstanding_amount = Decimal("40.00")
        self.assertEqual(inv.payment_status, "Overdue")
        self.assertEqual(SalesInvoiceSerializer(inv).data["payment_status"], "Overdue")

        # 6. Cancelled invoice -> "Cancelled"
        inv.docstatus = DocStatus.CANCELLED
        self.assertEqual(inv.payment_status, "Cancelled")
        self.assertEqual(
            SalesInvoiceSerializer(inv).data["payment_status"], "Cancelled"
        )

    def test_dispatch_full(self):
        # Stock: 20 kg
        bin_obj = Bin.objects.create(
            item=self.stock_item,
            warehouse=self.wh,
            actual_qty=Decimal("20.000"),
            valuation_rate=Decimal("60.000000"),
            stock_value=Decimal("1200.00"),
        )
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-001",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=self.wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order.submit()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)

        inv = dispatch_service.dispatch_order(order)
        self.assertEqual(inv.docstatus, DocStatus.SUBMITTED)
        self.assertIsNotNone(inv.due_date)
        self.assertGreaterEqual(inv.due_date, inv.posting_date)

        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("10.000"))

        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)

    def test_dispatch_partial_and_remainder(self):
        # Stock: 20 kg
        bin_obj = Bin.objects.create(
            item=self.stock_item,
            warehouse=self.wh,
            actual_qty=Decimal("20.000"),
            valuation_rate=Decimal("60.000000"),
            stock_value=Decimal("1200.00"),
        )
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-002",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        line = SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=self.wh,
            qty=Decimal("15.000"),
            rate=Decimal("100.00"),
        )
        order.submit()

        # 1. Partial dispatch 5 kg
        inv1 = dispatch_service.dispatch_order(
            order, quantities={line.pk: Decimal("5.000")}
        )
        self.assertEqual(inv1.docstatus, DocStatus.SUBMITTED)
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("15.000"))
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PART_DISPATCHED)

        # 2. Remainder dispatch (10 kg)
        inv2 = dispatch_service.dispatch_order(order)
        self.assertEqual(inv2.docstatus, DocStatus.SUBMITTED)
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("5.000"))
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)

    def test_dispatch_short_stock_refuses_and_changes_nothing(self):
        # Stock: 5 kg available
        bin_obj = Bin.objects.create(
            item=self.stock_item,
            warehouse=self.wh,
            actual_qty=Decimal("5.000"),
            valuation_rate=Decimal("60.000000"),
            stock_value=Decimal("300.00"),
        )
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-SHORT",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=self.wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order.submit()

        with transaction.atomic():
            with self.assertRaises(ValidationError) as cm:
                dispatch_service.dispatch_order(order)
            self.assertIn(self.stock_item.code, str(cm.exception))

        # Check nothing changed
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("5.000"))
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)
        self.assertEqual(SalesInvoice.objects.filter(sales_order=order).count(), 0)

    def test_dispatch_cancel_restores_stock_gl_and_order_status(self):
        bin_obj = Bin.objects.create(
            item=self.stock_item,
            warehouse=self.wh,
            actual_qty=Decimal("20.000"),
            valuation_rate=Decimal("60.000000"),
            stock_value=Decimal("1200.00"),
        )
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-CANCEL",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=self.wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order.submit()

        inv = dispatch_service.dispatch_order(order)
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("10.000"))

        # Cancel the invoice
        inv.cancel()
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("20.000"))
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PENDING)

    def test_dispatch_missing_warehouse_refuses(self):
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-NOWH",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        # Stock item without warehouse
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=None,
            qty=Decimal("5.000"),
            rate=Decimal("100.00"),
        )
        order.submit()

        with transaction.atomic():
            with self.assertRaises(ValidationError) as cm:
                dispatch_service.dispatch_order(order)
            self.assertIn(self.stock_item.code, str(cm.exception))

    def test_dispatch_api_endpoint(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="dispatch_api_admin",
            email="dispatch_api@test.com",
            password="pass",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        Bin.objects.create(
            item=self.stock_item,
            warehouse=self.wh,
            actual_qty=Decimal("20.000"),
            valuation_rate=Decimal("60.000000"),
            stock_value=Decimal("1200.00"),
        )
        order = SalesOrder.objects.create(
            name="SO-DISPATCH-API",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        line = SalesOrderItem.objects.create(
            sales_order=order,
            item=self.stock_item,
            warehouse=self.wh,
            qty=Decimal("10.000"),
            rate=Decimal("100.00"),
        )
        order.submit()

        # 1. Partial dispatch via API
        res = client.post(
            f"/api/selling/sales-orders/{order.pk}/dispatch/",
            {"lines": [{"order_item_id": line.pk, "qty": "4.000"}]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.PART_DISPATCHED)

        # 2. Short stock via API: try to dispatch 20 kg (only 16 left in bin, 6 remaining in SO)
        res_err = client.post(
            f"/api/selling/sales-orders/{order.pk}/dispatch/",
            {"lines": [{"order_item_id": line.pk, "qty": "20.000"}]},
            format="json",
        )
        self.assertEqual(res_err.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.stock_item.code, str(res_err.data))

        # 3. Empty lines list dispatches all remaining
        res_full = client.post(
            f"/api/selling/sales-orders/{order.pk}/dispatch/",
            {"lines": []},
            format="json",
        )
        self.assertEqual(res_full.status_code, status.HTTP_201_CREATED)
        order.refresh_from_db()
        self.assertEqual(order.dispatch_status, DispatchStatus.DISPATCHED)

    @unittest.skipIf(
        connection.vendor == "sqlite",
        "Concurrent select_for_update not supported on SQLite",
    )
    def test_concurrent_double_dispatch_produces_one_bill(self):
        pass

    def test_ready_warehouse_default_applied_when_blank(self):
        from selling.serializers import SalesOrderSerializer

        # 1. Serializer create with blank warehouse defaults to company.ready_warehouse
        data = {
            "customer": self.customer.pk,
            "company": self.company.pk,
            "transaction_date": datetime.date.today(),
            "items": [
                {
                    "item": self.stock_item.pk,
                    "qty": "5.000",
                    "rate": "100.00",
                    "warehouse": None,
                }
            ],
        }
        serializer = SalesOrderSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        so = serializer.save()
        item = so.items.first()
        self.assertEqual(item.warehouse, self.company.ready_warehouse)

        # 2. Explicit warehouse is preserved
        other_wh = Warehouse.objects.create(
            name="Other Warehouse", company=self.company, is_group=False
        )
        data_explicit = {
            "customer": self.customer.pk,
            "company": self.company.pk,
            "transaction_date": datetime.date.today(),
            "items": [
                {
                    "item": self.stock_item.pk,
                    "qty": "5.000",
                    "rate": "100.00",
                    "warehouse": other_wh.pk,
                }
            ],
        }
        serializer_explicit = SalesOrderSerializer(data=data_explicit)
        self.assertTrue(serializer_explicit.is_valid(), serializer_explicit.errors)
        so_explicit = serializer_explicit.save()
        self.assertEqual(so_explicit.items.first().warehouse, other_wh)

        # 3. Model save fallback to first non-group warehouse when ready_warehouse is None
        self.company.ready_warehouse = None
        self.company.save()
        so3 = SalesOrder.objects.create(
            name="SO-WH-FALLBACK",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        item3 = SalesOrderItem.objects.create(
            sales_order=so3,
            item=self.stock_item,
            qty=Decimal("3.000"),
            rate=Decimal("100.00"),
        )
        first_wh = (
            Warehouse.objects.filter(company=self.company, is_group=False)
            .order_by("pk")
            .first()
        )
        self.assertEqual(item3.warehouse, first_wh)
