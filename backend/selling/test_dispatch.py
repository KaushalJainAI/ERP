import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from core.models import Company, FiscalYear
from accounts.models import Account, AccountRootType, AccountType
from stock.models import UOM, ItemGroup, Item
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
