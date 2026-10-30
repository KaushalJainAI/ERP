import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from django.core.management import call_command
from django.test import TestCase
from core.models import Company, FiscalYear
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
