import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType
from core.models import Company, DocStatus, FiscalYear
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import Item, ItemGroup, UOM, Warehouse
from users.models import Role, RolePermission, User, UserRole


class SalesKPITrackingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Company & Fiscal Year setup
        self.company = Company.objects.create(
            name="Spice World Corp", abbr="SWC", default_currency="INR"
        )
        self.fy26 = FiscalYear.objects.create(
            name="FY 2026-27",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )
        self.fy25 = FiscalYear.objects.create(
            name="FY 2025-26",
            start_date=datetime.date(2025, 4, 1),
            end_date=datetime.date(2026, 3, 31),
        )

        # Chart of accounts
        self.asset_root = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.debtors = Account.objects.create(
            name="Debtors",
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
        self.sales_income = Account.objects.create(
            name="Sales Income",
            company=self.company,
            parent=self.income_root,
            root_type=AccountRootType.INCOME,
        )

        # Stock setup: Items & Spice Blends
        self.uom = UOM.objects.create(name="Kg")
        self.spice_group = ItemGroup.objects.create(name="Spices & Seasonings", is_group=False)
        self.packaging_group = ItemGroup.objects.create(name="Packaging", is_group=False)
        self.warehouse = Warehouse.objects.create(name="Central Warehouse", company=self.company)

        # Spice blend item
        self.garam_masala = Item.objects.create(
            code="SPICE-GM-500",
            name="Premium Garam Masala 500g",
            item_group=self.spice_group,
            stock_uom=self.uom,
        )
        # Whole spice item
        self.turmeric = Item.objects.create(
            code="SPICE-TR-1KG",
            name="Pure Turmeric Powder 1kg",
            item_group=self.spice_group,
            stock_uom=self.uom,
        )
        # Non-spice product
        self.carton = Item.objects.create(
            code="PKG-BOX-L",
            name="Cardboard Outer Carton",
            item_group=self.packaging_group,
            stock_uom=self.uom,
        )

        # Customers
        self.cust_group = CustomerGroup.objects.create(name="Wholesale Distributors", is_group=True)
        self.cust_alpha = Customer.objects.create(
            name="Alpha Distributors", company=self.company, customer_group=self.cust_group
        )
        self.cust_beta = Customer.objects.create(
            name="Beta Spice Traders", company=self.company, customer_group=self.cust_group
        )
        self.cust_gamma = Customer.objects.create(
            name="Gamma Foods", company=self.company, customer_group=self.cust_group
        )
        self.cust_dormant = Customer.objects.create(
            name="Dormant Retailer", company=self.company, customer_group=self.cust_group
        )

        # Historical purchase for dormant customer (in previous FY 2025-26) to test churn & acquisition
        old_inv = SalesInvoice.objects.create(
            name="SINV-OLD-01",
            company=self.company,
            customer=self.cust_dormant,
            posting_date=datetime.date(2025, 10, 10),
            debit_to=self.debtors,
            income_account=self.sales_income,
            grand_total=Decimal("5000.00"),
            rounded_total=Decimal("5000.00"),
            net_total=Decimal("5000.00"),
            outstanding_amount=Decimal("0.00"),
            update_stock=False,
            docstatus=DocStatus.SUBMITTED,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=old_inv,
            item=self.turmeric,
            qty=Decimal("50.000"),
            rate=Decimal("100.00"),
            amount=Decimal("5000.00"),
        )

        # Users and Roles for RBAC tests
        self.superuser = User.objects.create_superuser(
            username="admin_user", email="admin@spiceworld.com", password="password123"
        )

        # Sales Manager
        self.sales_mgr_user = User.objects.create_user(
            username="sales_manager", email="sales@spiceworld.com", password="password123"
        )
        sales_role = Role.objects.create(name="Sales Manager Role")
        RolePermission.objects.create(role=sales_role, model_label="selling.SalesOrder", can_read=True)
        RolePermission.objects.create(role=sales_role, model_label="selling.SalesInvoice", can_read=True)
        UserRole.objects.create(user=self.sales_mgr_user, role=sales_role)

        # Auditor (accounts reader)
        self.auditor_user = User.objects.create_user(
            username="auditor", email="auditor@spiceworld.com", password="password123"
        )
        auditor_role = Role.objects.create(name="Auditor Role")
        RolePermission.objects.create(role=auditor_role, model_label="accounts.GLEntry", can_read=True)
        UserRole.objects.create(user=self.auditor_user, role=auditor_role)

        # Unauthorized user (e.g. HR / basic user with no sales or GL read permissions)
        self.unauth_user = User.objects.create_user(
            username="unauth_user", email="hr@spiceworld.com", password="password123"
        )
        unauth_role = Role.objects.create(name="HR Role")
        RolePermission.objects.create(role=unauth_role, model_label="hr.Employee", can_read=True)
        UserRole.objects.create(user=self.unauth_user, role=unauth_role)

        # Seed realistic transactions in Q1 of FY 2026-27 (Apr - Jun 2026)
        # Order 1: Alpha Distributors (Submitted, Converted to Invoice after 3 days)
        self.so1 = SalesOrder.objects.create(
            name="SO-TEST-01",
            company=self.company,
            customer=self.cust_alpha,
            transaction_date=datetime.date(2026, 4, 10),
            grand_total=Decimal("20000.00"),
            rounded_total=Decimal("20000.00"),
            net_total=Decimal("20000.00"),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=self.so1,
            item=self.garam_masala,
            qty=Decimal("100.000"),
            rate=Decimal("200.00"),
            amount=Decimal("20000.00"),
        )

        # Invoice 1 against Order 1 (posted 3 days later, fully paid)
        self.si1 = SalesInvoice.objects.create(
            name="SINV-TEST-01",
            company=self.company,
            customer=self.cust_alpha,
            sales_order=self.so1,
            posting_date=datetime.date(2026, 4, 13),
            due_date=datetime.date(2026, 5, 13),
            debit_to=self.debtors,
            income_account=self.sales_income,
            grand_total=Decimal("21000.00"),
            rounded_total=Decimal("21000.00"),
            net_total=Decimal("20000.00"),
            total_taxes_and_charges=Decimal("1000.00"),
            outstanding_amount=Decimal("0.00"),  # Fully paid
            update_stock=False,
            docstatus=DocStatus.SUBMITTED,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.si1,
            item=self.garam_masala,
            qty=Decimal("100.000"),
            rate=Decimal("200.00"),
            amount=Decimal("20000.00"),
        )

        # Order 2: Beta Spice Traders (Submitted, Partially converted)
        self.so2 = SalesOrder.objects.create(
            name="SO-TEST-02",
            company=self.company,
            customer=self.cust_beta,
            transaction_date=datetime.date(2026, 5, 1),
            grand_total=Decimal("30000.00"),
            rounded_total=Decimal("30000.00"),
            net_total=Decimal("30000.00"),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=self.so2,
            item=self.turmeric,
            qty=Decimal("200.000"),
            rate=Decimal("150.00"),
            amount=Decimal("30000.00"),
        )

        # Invoice 2 against Order 2 (posted 5 days later, unpaid with outstanding)
        self.si2 = SalesInvoice.objects.create(
            name="SINV-TEST-02",
            company=self.company,
            customer=self.cust_beta,
            sales_order=self.so2,
            posting_date=datetime.date(2026, 5, 6),
            due_date=datetime.date(2026, 5, 20),
            debit_to=self.debtors,
            income_account=self.sales_income,
            grand_total=Decimal("15750.00"),
            rounded_total=Decimal("15750.00"),
            net_total=Decimal("15000.00"),
            total_taxes_and_charges=Decimal("750.00"),
            outstanding_amount=Decimal("15750.00"),  # Open receivable
            update_stock=False,
            docstatus=DocStatus.SUBMITTED,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.si2,
            item=self.turmeric,
            qty=Decimal("100.000"),
            rate=Decimal("150.00"),
            amount=Decimal("15000.00"),
        )

        # Order 3: Gamma Foods (Submitted, Unconverted / Open)
        self.so3 = SalesOrder.objects.create(
            name="SO-TEST-03",
            company=self.company,
            customer=self.cust_gamma,
            transaction_date=datetime.date(2026, 5, 15),
            grand_total=Decimal("10000.00"),
            rounded_total=Decimal("10000.00"),
            net_total=Decimal("10000.00"),
            docstatus=DocStatus.SUBMITTED,
        )
        SalesOrderItem.objects.create(
            sales_order=self.so3,
            item=self.carton,
            qty=Decimal("500.000"),
            rate=Decimal("20.00"),
            amount=Decimal("10000.00"),
        )

        # Invoice 3: Standalone Direct Invoice for Alpha (Cartons, overdue)
        self.si3 = SalesInvoice.objects.create(
            name="SINV-TEST-03",
            company=self.company,
            customer=self.cust_alpha,
            posting_date=datetime.date(2026, 5, 20),
            due_date=datetime.date(2026, 5, 25),
            debit_to=self.debtors,
            income_account=self.sales_income,
            grand_total=Decimal("5000.00"),
            rounded_total=Decimal("5000.00"),
            net_total=Decimal("5000.00"),
            total_taxes_and_charges=Decimal("0.00"),
            outstanding_amount=Decimal("5000.00"),
            update_stock=False,
            docstatus=DocStatus.SUBMITTED,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.si3,
            item=self.carton,
            qty=Decimal("250.000"),
            rate=Decimal("20.00"),
            amount=Decimal("5000.00"),
        )

    # 1. RBAC and Auth Tests
    def test_anonymous_access_denied(self):
        res = self.client.get("/api/selling/analytics/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthorized_role_forbidden(self):
        self.client.force_authenticate(user=self.unauth_user)
        res = self.client.get(f"/api/selling/analytics/?company={self.company.id}")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_manager_access_allowed(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(f"/api/selling/analytics/?company={self.company.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("summary", res.data)
        self.assertIn("conversion_and_velocity", res.data)

    def test_auditor_access_allowed(self):
        self.client.force_authenticate(user=self.auditor_user)
        res = self.client.get(f"/api/selling/analytics/?company={self.company.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_superuser_access_allowed(self):
        self.client.force_authenticate(user=self.superuser)
        res = self.client.get(f"/api/selling/analytics/?company={self.company.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    # 2. Sales Conversion & Velocity Metrics
    def test_sales_conversion_and_velocity(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/conversion/?company={self.company.id}&from_date=2026-04-01&to_date=2026-06-30"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        # 3 submitted orders (SO1, SO2, SO3)
        self.assertEqual(data["total_orders_count"], 3)
        # 2 converted orders (SO1 fully, SO2 partially)
        self.assertEqual(data["converted_orders_count"], 2)
        self.assertEqual(data["unconverted_orders_count"], 1)
        # Conversion rate: 2/3 * 100 = 66.67%
        self.assertAlmostEqual(data["conversion_rate_pct"], 66.67, places=1)
        self.assertEqual(data["fully_converted_count"], 1)
        self.assertEqual(data["partially_converted_count"], 1)

        # SO1: Apr 10 to Apr 13 = 3 days; SO2: May 1 to May 6 = 5 days -> Avg: (3+5)/2 = 4.0 days
        self.assertEqual(data["average_conversion_days"], 4.0)
        self.assertEqual(data["median_conversion_days"], 4.0)
        self.assertEqual(data["velocity_distribution"]["1_to_3_days"], 1)
        self.assertEqual(data["velocity_distribution"]["4_to_7_days"], 1)

        # Pipeline funnel
        self.assertEqual(len(data["pipeline_funnel"]), 4)
        stages = [item["stage"] for item in data["pipeline_funnel"]]
        self.assertIn("Submitted Orders", stages)
        self.assertIn("Converted to Invoices", stages)

    # 3. Revenue, Volume, AOV and Growth Metrics
    def test_revenue_and_volume_trends(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/revenue/?company={self.company.id}&from_date=2026-04-01&to_date=2026-06-30"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        current = data["current_period"]
        # Invoices: SI1 (net 20000), SI2 (net 15000), SI3 (net 5000) -> Net Total = 40000.00
        self.assertEqual(current["net_revenue"], "40000.00")
        # Gross Total: 21000 + 15750 + 5000 = 41750.00
        self.assertEqual(current["gross_revenue"], "41750.00")
        self.assertEqual(current["total_tax"], "1750.00")
        self.assertEqual(current["invoices_count"], 3)
        # Volume: 100 + 100 + 250 = 450.000
        self.assertEqual(current["volume"], "450.000")
        # AOV: 40000 / 3 = 13333.33
        self.assertEqual(current["average_order_value"], "13333.33")

        # Monthly trends
        trends = data["monthly_trends"]
        self.assertTrue(len(trends) >= 2)  # Apr 2026 and May 2026
        months = [t["label"] for t in trends]
        self.assertIn("Apr 2026", months)
        self.assertIn("May 2026", months)

    # 4. Top Selling Products & Spice Blends
    def test_top_products_and_spice_blends(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/products/?company={self.company.id}&from_date=2026-04-01&to_date=2026-06-30"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        self.assertIn("top_products", data)
        self.assertIn("top_spice_blends", data)

        top_products = data["top_products"]
        # Product 1 should be Garam Masala (net revenue 20000.00)
        self.assertEqual(top_products[0]["item_name"], "Premium Garam Masala 500g")
        self.assertEqual(top_products[0]["revenue"], "20000.00")
        self.assertTrue(top_products[0]["is_spice_blend"])

        # Dedicated spice blends list
        spice_blends = data["top_spice_blends"]
        spice_names = [p["item_name"] for p in spice_blends]
        self.assertIn("Premium Garam Masala 500g", spice_names)
        self.assertIn("Pure Turmeric Powder 1kg", spice_names)
        self.assertNotIn("Cardboard Outer Carton", spice_names)

    # 5. Customer Acquisition, Churn, and Concentration Analysis
    def test_customer_analytics(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/customers/?company={self.company.id}&from_date=2026-04-01&to_date=2026-06-30"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        # Active in period: Alpha and Beta
        self.assertEqual(data["active_customers_count"], 2)
        # New customers in period: Alpha and Beta both had no prior purchases
        self.assertEqual(data["new_customers_count"], 2)
        # Churned: Dormant Retailer had past purchases before 2026-04-01, but 0 in Q1
        self.assertEqual(data["churned_customers_count"], 1)

        # Concentration & Pareto
        concentration = data["concentration"]
        self.assertIn("top_5_share_pct", concentration)
        self.assertIn("pareto_20_pct_customers_share", concentration)
        self.assertIn("hhi_index", concentration)
        self.assertIn("concentration_risk", concentration)

        # Top customer should be Alpha (20000 + 5000 = 25000.00)
        top_custs = data["top_customers"]
        self.assertEqual(top_custs[0]["customer_name"], "Alpha Distributors")
        self.assertEqual(top_custs[0]["revenue"], "25000.00")

    # 6. Outstanding Receivables Ageing & DSO
    def test_receivables_ageing_and_dso(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/receivables/?company={self.company.id}&from_date=2026-04-01&to_date=2026-06-30"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        # Outstanding: SI2 (15750.00) + SI3 (5000.00) = 20750.00
        self.assertEqual(data["total_outstanding_receivables"], "20750.00")
        # Credit Sales: 41750.00
        self.assertEqual(data["period_credit_sales"], "41750.00")
        self.assertTrue(data["days_sales_outstanding"] > 0)

        # Ageing buckets
        ageing = data["ageing_summary"]
        self.assertIn("current_0_30", ageing)
        self.assertIn("overdue_31_60", ageing)
        self.assertIn("overdue_61_90", ageing)
        self.assertIn("overdue_91_plus", ageing)

    # 7. Summary Overview Endpoint & Named Periods
    def test_analytics_summary_overview(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(f"/api/selling/analytics/?company={self.company.id}&period=this_fy")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data

        self.assertIn("summary", data)
        self.assertIn("conversion_and_velocity", data)
        self.assertIn("revenue_and_volume", data)
        self.assertIn("top_products", data)
        self.assertIn("customer_analytics", data)
        self.assertIn("receivables_and_dso", data)

    def test_invalid_date_range_fails(self):
        self.client.force_authenticate(user=self.sales_mgr_user)
        res = self.client.get(
            f"/api/selling/analytics/?company={self.company.id}&from_date=2026-06-30&to_date=2026-04-01"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
