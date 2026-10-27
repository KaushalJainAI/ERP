"""Dashboard summary endpoint and the `seed_demo` command."""

from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import GLEntry
from core.models import Company
from stock.models import Bin, StockLedgerEntry
from users.models import Role, RolePermission, User, UserRole

URL = "/api/core/dashboard/"


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    def test_seed_demo_is_idempotent_and_ledger_clean(self):
        call_command("seed_demo", stdout=StringIO())
        counts = (
            GLEntry.objects.count(),
            StockLedgerEntry.objects.count(),
            Bin.objects.count(),
        )
        self.assertGreater(counts[0], 0)
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(
            counts,
            (
                GLEntry.objects.count(),
                StockLedgerEntry.objects.count(),
                Bin.objects.count(),
            ),
        )
        self.assertEqual(User.objects.filter(username="admin").count(), 1)
        # Every voucher balances and the Bins tie to the ledger.
        call_command("verify_integrity", stdout=StringIO())

    def test_seed_demo_creates_known_superuser(self):
        call_command("seed_demo", stdout=StringIO())
        admin = User.objects.get(username="admin")
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password("admin123"))

    @override_settings(DEBUG=False)
    def test_seed_demo_refuses_outside_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())
        self.assertFalse(User.objects.filter(username="admin").exists())


class DashboardTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        with override_settings(DEBUG=True):
            call_command("seed_demo", stdout=StringIO())
        cls.admin = User.objects.get(username="admin")

    def setUp(self):
        cache.clear()

    def _user_with(self, username, labels):
        user = User.objects.create_user(
            username=username, email=f"{username}@t.com", password="Pass123!"
        )
        if labels:
            role = Role.objects.create(name=f"role_{username}")
            UserRole.objects.create(user=user, role=role)
            for label in labels:
                RolePermission.objects.create(
                    role=role, model_label=label, can_read=True
                )
        return user

    def test_superuser_gets_real_numbers(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.data
        self.assertEqual(data["company"]["name"], "Acme Corp")
        self.assertEqual(data["company"]["currency"], "INR")
        self.assertEqual(data["total_sales"], "10750.00")
        self.assertEqual(data["total_purchases"], "22000.00")
        # 10750 invoiced - 5000 received; 22000 billed, nothing paid.
        self.assertEqual(data["receivables_outstanding"], "5750.00")
        self.assertEqual(data["payables_outstanding"], "22000.00")
        self.assertEqual(data["open_sales_orders"], {"count": 1, "value": "10360.00"})
        self.assertEqual(data["stock_value"], "175300.00")
        self.assertEqual(data["draft_documents"]["sales_orders"], 1)
        self.assertEqual(data["draft_documents"]["total"], 1)
        # Money is serialised as strings, like the rest of the API.
        for key in (
            "total_sales",
            "total_purchases",
            "receivables_outstanding",
            "payables_outstanding",
            "stock_value",
        ):
            self.assertIsInstance(data[key], str)
            Decimal(data[key])

    def test_stock_value_matches_bins(self):
        self.client.force_authenticate(self.admin)
        from django.db.models import Sum

        expected = Bin.objects.aggregate(t=Sum("stock_value"))["t"]
        self.assertEqual(Decimal(self.client.get(URL).data["stock_value"]), expected)

    def test_integrity_reflects_latest_health_check(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(URL).data["integrity"]["status"], "never_run")
        call_command("verify_integrity", stdout=StringIO())
        integrity = self.client.get(URL).data["integrity"]
        self.assertEqual(integrity["status"], "ok")
        self.assertEqual(integrity["check_name"], "ledger_and_stock_integrity")
        self.assertIsNotNone(integrity["checked_at"])

    def test_cancelled_invoice_drops_out_of_totals(self):
        from selling.models import SalesInvoice

        SalesInvoice.objects.get(name="SINV-DEMO-0001").cancel(user=self.admin)
        self.client.force_authenticate(self.admin)
        data = self.client.get(URL).data
        self.assertEqual(data["total_sales"], "0.00")
        # Payment of 5000 remains on the ledger against a now-cancelled invoice.
        self.assertEqual(data["receivables_outstanding"], "-5000.00")
        # The order is open again once its invoice is cancelled.
        self.assertEqual(data["open_sales_orders"]["count"], 2)

    def test_sections_are_filtered_by_role(self):
        user = self._user_with("clerk", ["selling.SalesInvoice"])
        self.client.force_authenticate(user)
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_sales"], "10750.00")
        for hidden in (
            "total_purchases",
            "receivables_outstanding",
            "payables_outstanding",
            "stock_value",
            "integrity",
        ):
            self.assertIsNone(res.data[hidden], hidden)
        self.assertEqual(res.data["draft_documents"], {"sales_invoices": 0, "total": 0})

    def test_user_without_any_role_is_denied(self):
        self.client.force_authenticate(self._user_with("norole", []))
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN)

    def test_user_with_unrelated_role_is_denied(self):
        self.client.force_authenticate(self._user_with("other", ["stock.Item"]))
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_unauthorized(self):
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_read_only(self):
        self.client.force_authenticate(self.admin)
        for method in ("post", "put", "patch", "delete"):
            res = getattr(self.client, method)(URL, {}, format="json")
            self.assertEqual(
                res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, method
            )

    def test_company_param_validation(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(URL, {"company": "abc"}).status_code, 400)
        self.assertEqual(self.client.get(URL, {"company": 9999}).status_code, 404)
        other = Company.objects.create(name="Empty Co", abbr="EMP")
        res = self.client.get(URL, {"company": other.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_sales"], "0.00")
        self.assertEqual(res.data["stock_value"], "0.00")
