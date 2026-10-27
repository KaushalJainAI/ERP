"""
Regression tests for the gaps the frontend agents reported:

* /api/auth/me/ must not let a user escalate/deactivate themselves
* Marathi in LANGUAGES + seeded
* GLEntry date-range filters
* Profit & Loss (period) / Balance Sheet (as-of) / General Ledger (opening balance)
* read-only label fields on invoices and payment entries
* money strings always 2dp in report responses
* Company endpoint exposes the perpetual-inventory settings
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command

from accounts.models import Account, AccountRootType, AccountType
from accounts.services import gl as gl_service
from core.models import Company, DocStatus, DummySubmittableDoc, Language
from core.test_e2e import BaseAPITest
from users.models import Role, UserRole, User


def _post_gl(doc, debit_acc, credit_acc, amount, date):
    gl_service.post(
        voucher=doc,
        entries=[
            {"account": debit_acc, "debit": Decimal(amount), "credit": Decimal("0.00")},
            {
                "account": credit_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal(amount),
            },
        ],
        posting_date=date,
    )


class MeEndpointTests(BaseAPITest):
    def test_me_only_allows_personal_fields(self):
        user = User.objects.create_user(
            username="plain", email="plain@t.com", password="Pass123!"
        )
        role = Role.objects.create(name="r_plain")
        UserRole.objects.create(user=user, role=role)
        self.login("plain", "Pass123!")

        res = self.client.patch(
            "/api/auth/me/",
            {
                "first_name": "Pia",
                "last_name": "Lain",
                "email": "pia@t.com",
                "language": "hi",
                "is_active": False,
                "is_superuser": True,
                "is_staff": True,
                "roles": ["System Manager"],
                "username": "hacker",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Pia")
        self.assertEqual(user.last_name, "Lain")
        self.assertEqual(user.email, "pia@t.com")
        self.assertEqual(user.language, "hi")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.username, "plain")
        self.assertEqual(res.data["roles"], ["r_plain"])
        self.assertTrue(res.data["is_active"])
        self.assertFalse(res.data["is_superuser"])

    def test_superuser_cannot_deactivate_self_via_me(self):
        res = self.client.patch("/api/auth/me/", {"is_active": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class MarathiLanguageTests(BaseAPITest):
    def test_marathi_in_settings_languages(self):
        self.assertIn(("mr", "Marathi"), settings.LANGUAGES)
        self.assertEqual(len(settings.LANGUAGES), 13)

    def test_seed_defaults_creates_marathi(self):
        call_command("seed_defaults", verbosity=0)
        lang = Language.objects.get(code="mr")
        self.assertEqual(lang.name, "Marathi")
        self.assertFalse(lang.is_rtl)
        self.assertEqual(Language.objects.count(), 13)


class ReportFixtureMixin:
    def setUp(self):
        super().setUp()
        self.doc = DummySubmittableDoc.objects.create(
            name="GAP-DOC-1", title="gap", docstatus=DocStatus.SUBMITTED
        )
        self.doc2 = DummySubmittableDoc.objects.create(
            name="GAP-DOC-2", title="gap2", docstatus=DocStatus.SUBMITTED
        )
        self.doc3 = DummySubmittableDoc.objects.create(
            name="GAP-DOC-3", title="gap3", docstatus=DocStatus.SUBMITTED
        )
        # March: sale 1000 ; April: sale 500 + expense 200 ; May: sale 300
        _post_gl(
            self.doc,
            self.bank,
            self.sales_income,
            "1000.00",
            datetime.date(2026, 3, 10),
        )
        _post_gl(
            self.doc2,
            self.bank,
            self.sales_income,
            "500.00",
            datetime.date(2026, 4, 10),
        )
        _post_gl(
            self.doc3,
            self.purchase_expense,
            self.bank,
            "200.00",
            datetime.date(2026, 4, 20),
        )
        self.doc4 = DummySubmittableDoc.objects.create(
            name="GAP-DOC-4", title="gap4", docstatus=DocStatus.SUBMITTED
        )
        _post_gl(
            self.doc4,
            self.bank,
            self.sales_income,
            "300.00",
            datetime.date(2026, 5, 10),
        )

    def get(self, path, **params):
        params.setdefault("company", self.company.id)
        return self.client.get(path, params)


class GLEntryFilterTests(ReportFixtureMixin, BaseAPITest):
    def test_posting_date_range_filters(self):
        base = "/api/accounts/gl-entries/"
        res = self.client.get(
            base, {"posting_date__gte": "2026-04-01", "posting_date__lte": "2026-04-30"}
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["count"], 4)  # April: 2 vouchers x 2 legs
        res = self.client.get(base, {"posting_date__gte": "2026-05-01"})
        self.assertEqual(res.data["count"], 2)
        res = self.client.get(base, {"posting_date__lte": "2026-03-31"})
        self.assertEqual(res.data["count"], 2)

    def test_existing_filters_still_work(self):
        base = "/api/accounts/gl-entries/"
        self.assertEqual(
            self.client.get(base, {"posting_date": "2026-03-10"}).data["count"], 2
        )
        self.assertEqual(
            self.client.get(base, {"account": self.bank.id}).data["count"], 4
        )
        self.assertEqual(
            self.client.get(base, {"voucher_no": "GAP-DOC-2"}).data["count"], 2
        )
        self.assertEqual(
            self.client.get(base, {"company": self.company.id}).data["count"], 8
        )


class FinancialReportTests(ReportFixtureMixin, BaseAPITest):
    def test_pnl_all_time_backward_compatible(self):
        res = self.get("/api/accounts/reports/profit-and-loss/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_income"], "1800.00")
        self.assertEqual(res.data["total_expense"], "200.00")
        self.assertEqual(res.data["net_profit"], "1600.00")

    def test_pnl_period(self):
        res = self.get(
            "/api/accounts/reports/profit-and-loss/",
            from_date="2026-04-01",
            to_date="2026-04-30",
        )
        self.assertEqual(res.data["total_income"], "500.00")
        self.assertEqual(res.data["total_expense"], "200.00")
        self.assertEqual(res.data["net_profit"], "300.00")

    def test_pnl_account_breakdown(self):
        res = self.get(
            "/api/accounts/reports/profit-and-loss/",
            from_date="2026-04-01",
            to_date="2026-04-30",
        )
        by_name = {a["account_name"]: a for a in res.data["accounts"]}
        self.assertEqual(set(by_name), {"Sales", "Cost of Purchase"})
        self.assertEqual(by_name["Sales"]["balance"], "500.00")
        self.assertEqual(by_name["Sales"]["root_type"], "Income")
        self.assertEqual(by_name["Sales"]["account_id"], self.sales_income.id)
        self.assertEqual(by_name["Cost of Purchase"]["balance"], "200.00")

    def test_balance_sheet_as_of(self):
        res = self.get("/api/accounts/reports/balance-sheet/", to_date="2026-03-31")
        self.assertEqual(res.data["total_assets"], "1000.00")
        res = self.get("/api/accounts/reports/balance-sheet/", to_date="2026-04-30")
        self.assertEqual(res.data["total_assets"], "1300.00")
        self.assertEqual(
            {a["account_name"]: a["balance"] for a in res.data["accounts"]},
            {"HDFC Bank": "1300.00"},
        )
        res = self.get("/api/accounts/reports/balance-sheet/")  # all time
        self.assertEqual(res.data["total_assets"], "1600.00")
        self.assertEqual(res.data["total_liabilities"], "0.00")
        self.assertEqual(res.data["total_equity"], "0.00")

    def test_balance_sheet_ignores_from_date(self):
        res = self.get(
            "/api/accounts/reports/balance-sheet/",
            from_date="2026-05-01",
            to_date="2026-05-31",
        )
        self.assertEqual(res.data["total_assets"], "1600.00")

    def test_invalid_date_is_400(self):
        for path in (
            "profit-and-loss",
            "balance-sheet",
            "general-ledger",
            "trial-balance",
        ):
            res = self.get(f"/api/accounts/reports/{path}/", to_date="not-a-date")
            self.assertEqual(res.status_code, 400, path)

    def test_general_ledger_opening_balance_and_running_balance(self):
        res = self.get(
            "/api/accounts/reports/general-ledger/",
            account=self.bank.id,
            from_date="2026-04-01",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["opening_balance"], "1000.00")
        balances = [e["balance"] for e in res.data["entries"]]
        # +500 (Apr 10), -200 (Apr 20), +300 (May 10) starting from 1000.
        self.assertEqual(balances, ["1500.00", "1300.00", "1600.00"])
        self.assertEqual(res.data["closing_balance"], "1600.00")

    def test_general_ledger_account_without_from_date_opens_at_zero(self):
        res = self.get("/api/accounts/reports/general-ledger/", account=self.bank.id)
        self.assertEqual(res.data["opening_balance"], "0.00")
        self.assertEqual(res.data["entries"][0]["balance"], "1000.00")
        self.assertEqual(res.data["closing_balance"], "1600.00")

    def test_general_ledger_without_account_has_no_running_balance(self):
        res = self.get("/api/accounts/reports/general-ledger/", from_date="2026-04-01")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["entries"]), 6)
        self.assertIsNone(res.data["opening_balance"])
        self.assertIsNone(res.data["closing_balance"])
        self.assertTrue(all(e["balance"] is None for e in res.data["entries"]))

    def test_money_strings_are_always_two_decimal_places(self):
        def assert_2dp(value):
            self.assertRegex(value, r"^-?\d+\.\d{2}$")

        tb = self.get("/api/accounts/reports/trial-balance/").data
        assert_2dp(tb["total_debit"])
        assert_2dp(tb["total_credit"])
        for r in tb["rows"]:
            for k in ("debit", "credit", "net_balance"):
                assert_2dp(r[k])
        pnl = self.get("/api/accounts/reports/profit-and-loss/").data
        for k in ("total_income", "total_expense", "net_profit"):
            assert_2dp(pnl[k])
        for a in pnl["accounts"]:
            assert_2dp(a["balance"])
        bs = self.get("/api/accounts/reports/balance-sheet/").data
        for k in ("total_assets", "total_liabilities", "total_equity"):
            assert_2dp(bs[k])
        gl = self.get(
            "/api/accounts/reports/general-ledger/", account=self.bank.id
        ).data
        assert_2dp(gl["opening_balance"])
        assert_2dp(gl["closing_balance"])
        for e in gl["entries"]:
            for k in ("debit", "credit", "balance"):
                assert_2dp(e[k])
        # Empty ledger still yields "0.00", not "0".
        empty = self.client.get(
            "/api/accounts/reports/profit-and-loss/", {"company": 999999}
        ).data
        self.assertEqual(empty["net_profit"], "0.00")


class LabelFieldTests(BaseAPITest):
    def _line(self, qty="1.000", rate="100.00"):
        return {
            "item": self.item.id,
            "warehouse": self.warehouse.id,
            "qty": qty,
            "rate": rate,
        }

    def test_sales_invoice_labels(self):
        res = self.client.post(
            "/api/selling/sales-invoices/",
            {
                "name": "SINV-LBL-1",
                "customer": self.customer.id,
                "company": self.company.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "debit_to": self.debtors.id,
                "income_account": self.sales_income.id,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["debit_to_name"], "Debtors")
        self.assertEqual(res.data["income_account_name"], "Sales")
        # nullable FK: key present, value None
        self.assertIn("sales_order_name", res.data)
        self.assertIsNone(res.data["sales_order_name"])

        # list + retrieve also carry the labels
        got = self.client.get(f"/api/selling/sales-invoices/{res.data['id']}/")
        self.assertEqual(got.data["debit_to_name"], "Debtors")

    def test_purchase_invoice_labels(self):
        res = self.client.post(
            "/api/buying/purchase-invoices/",
            {
                "name": "PINV-LBL-1",
                "supplier": self.supplier.id,
                "company": self.company.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "credit_to": self.creditors.id,
                "expense_account": self.purchase_expense.id,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["credit_to_name"], "Creditors")
        self.assertEqual(res.data["expense_account_name"], "Cost of Purchase")
        self.assertIn("purchase_order_name", res.data)
        self.assertIsNone(res.data["purchase_order_name"])

    def test_invoice_from_order_carries_order_name(self):
        so = self.client.post(
            "/api/selling/sales-orders/",
            {
                "name": "SO-LBL-1",
                "customer": self.customer.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(so.status_code, 201, so.content)
        res = self.client.post(
            "/api/selling/sales-invoices/",
            {
                "name": "SINV-LBL-2",
                "customer": self.customer.id,
                "company": self.company.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "sales_order": so.data["id"],
                "debit_to": self.debtors.id,
                "income_account": self.sales_income.id,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["sales_order_name"], "SO-LBL-1")

    def test_payment_entry_labels(self):
        res = self.client.post(
            "/api/accounts/payment-entries/",
            {
                "name": "PE-LBL-1",
                "payment_type": "Receive",
                "posting_date": "2026-04-01",
                "company": self.company.id,
                "party_type": "Customer",
                "party_name": "Acme Corp",
                "paid_from": self.debtors.id,
                "paid_to": self.bank.id,
                "paid_amount": "100.00",
                "received_amount": "100.00",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["paid_from_name"], "Debtors")
        self.assertEqual(res.data["paid_to_name"], "HDFC Bank")
        # read-only: cannot be written
        bad = self.client.patch(
            f"/api/accounts/payment-entries/{res.data['id']}/",
            {"paid_to_name": "Hacked"},
            format="json",
        )
        self.assertEqual(bad.data.get("paid_to_name"), "HDFC Bank")


class CompanyPerpetualFieldsTests(BaseAPITest):
    def test_company_exposes_perpetual_inventory_fields_read_only(self):
        stock = Account.objects.create(
            name="Stock In Hand",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )
        Company.objects.filter(pk=self.company.pk).update(
            default_inventory_account=stock
        )
        res = self.client.get(f"/api/core/companies/{self.company.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["enable_perpetual_inventory"])
        self.assertEqual(res.data["default_inventory_account"], stock.id)
        self.assertIsNone(res.data["stock_adjustment_account"])
        self.assertIsNone(res.data["default_expense_account"])
        listing = self.client.get("/api/core/companies/")
        self.assertIn("enable_perpetual_inventory", listing.data["results"][0])
        # read-only viewset: no writes
        put = self.client.patch(
            f"/api/core/companies/{self.company.id}/",
            {"enable_perpetual_inventory": True},
            format="json",
        )
        self.assertEqual(put.status_code, 405)
