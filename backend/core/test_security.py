"""
Security & authorization tests — the adversarial layer.

Modelled on the NGU `testing/security` suite: prove the API refuses the wrong
principal, enforces *per-action* authority (read ≠ create ≠ submit ≠ cancel ≠
delete), rate-limits credential stuffing, and keeps immutable ledgers immutable
through every HTTP verb. Every probe must get a clean 401/403/405/429 — never a
500 and never data it shouldn't see.
"""

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Company
from accounts.models import Account, AccountRootType, AccountType
from stock.models import UOM, ItemGroup, Item, Warehouse
from selling.models import CustomerGroup, Customer
from users.models import User, Role, UserRole, RolePermission


class AuthGuardTests(APITestCase):
    """Nothing transactional is reachable without a valid token."""

    def setUp(self):
        cache.clear()

    def test_anonymous_is_blocked_from_resources(self):
        for url in [
            "/api/selling/sales-invoices/",
            "/api/stock/items/",
            "/api/accounts/gl-entries/",
            "/api/accounts/reports/trial-balance/",
            "/api/auth/me/",
        ]:
            res = self.client.get(url)
            self.assertEqual(
                res.status_code,
                status.HTTP_401_UNAUTHORIZED,
                f"{url} was reachable anonymously",
            )

    def test_garbage_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not.a.real.token")
        res = self.client.get("/api/stock/items/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class RBACAuthorityTests(APITestCase):
    """
    Different roles carry different authority on the *same* endpoint. A clerk who
    may create a draft invoice must not be able to submit or delete it; a viewer
    may only read. This is the "difference in the authority of endpoints" contract.
    """

    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(name="RBAC Co", abbr="RBAC")
        self.cust_group = CustomerGroup.objects.create(name="G", is_group=True)
        self.customer = Customer.objects.create(
            name="Cust", company=self.company, customer_group=self.cust_group
        )
        self.assets = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.debtors = Account.objects.create(
            name="Debtors",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.income = Account.objects.create(
            name="Income", company=self.company, root_type=AccountRootType.INCOME
        )
        self.uom = UOM.objects.create(name="Nos")
        self.grp = ItemGroup.objects.create(name="P", is_group=True)
        self.item = Item.objects.create(
            code="I1", name="I1", item_group=self.grp, stock_uom=self.uom
        )
        self.warehouse = Warehouse.objects.create(name="WH", company=self.company)

    def _make_user(self, username, perm_flags):
        """Create a non-staff user whose role grants exactly `perm_flags` on SalesInvoice."""
        user = User.objects.create_user(
            username=username, email=f"{username}@t.com", password="Pass123!"
        )
        role = Role.objects.create(name=f"role_{username}")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role, model_label="selling.SalesInvoice", **perm_flags
        )
        return user

    def _auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "Pass123!"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def _invoice_payload(self):
        return {
            "name": "SINV-RBAC-1",
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": False,
            "debit_to": self.debtors.id,
            "income_account": self.income.id,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "1.000",
                    "rate": "100.00",
                }
            ],
        }

    def test_read_only_role_cannot_create(self):
        self._auth(self._make_user("viewer", {"can_read": True}))
        self.assertEqual(
            self.client.get("/api/selling/sales-invoices/").status_code,
            status.HTTP_200_OK,
        )
        res = self.client.post(
            "/api/selling/sales-invoices/", self._invoice_payload(), format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_role_cannot_submit_or_delete(self):
        """A user with create+read but no submit/delete is stopped at those actions."""
        self._auth(self._make_user("clerk", {"can_read": True, "can_create": True}))
        created = self.client.post(
            "/api/selling/sales-invoices/", self._invoice_payload(), format="json"
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        si_id = created.data["id"]

        submit = self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(
            submit.status_code,
            status.HTTP_403_FORBIDDEN,
            "create authority must not imply submit",
        )
        delete = self.client.delete(f"/api/selling/sales-invoices/{si_id}/")
        self.assertEqual(delete.status_code, status.HTTP_403_FORBIDDEN)

    def test_submitter_role_can_submit_but_not_cancel(self):
        submitter = self._make_user(
            "submitter", {"can_read": True, "can_create": True, "can_submit": True}
        )
        self._auth(submitter)
        si_id = self.client.post(
            "/api/selling/sales-invoices/", self._invoice_payload(), format="json"
        ).data["id"]
        submit = self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        cancel = self.client.post(f"/api/selling/sales-invoices/{si_id}/cancel/")
        self.assertEqual(
            cancel.status_code,
            status.HTTP_403_FORBIDDEN,
            "submit authority must not imply cancel",
        )

    def test_role_scoped_to_one_model_does_not_leak_to_another(self):
        """SalesInvoice permission must not grant access to Item endpoints."""
        self._auth(self._make_user("narrow", {"can_read": True, "can_create": True}))
        res = self.client.post(
            "/api/stock/items/",
            {
                "code": "HACK",
                "name": "Hack",
                "item_group": self.grp.id,
                "stock_uom": self.uom.id,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_reports_are_not_readable_without_report_permission(self):
        """A user with only SalesInvoice authority must not read financial/stock
        reports — the report endpoints enforce their own RBAC, not just login."""
        self._auth(self._make_user("report_probe", {"can_read": True}))
        for url in [
            f"/api/accounts/reports/trial-balance/?company={self.company.id}",
            f"/api/accounts/reports/general-ledger/?company={self.company.id}",
            f"/api/accounts/reports/profit-and-loss/?company={self.company.id}",
            f"/api/accounts/reports/balance-sheet/?company={self.company.id}",
            f"/api/stock/reports/stock-balance/?item={self.item.id}",
        ]:
            res = self.client.get(url)
            self.assertEqual(
                res.status_code,
                status.HTTP_403_FORBIDDEN,
                f"{url} leaked to a user without report permission",
            )

    def test_reports_readable_with_ledger_read_permission(self):
        """Granting read on the ledger model opens the reports for that user."""
        user = self._make_user("accountant", {"can_read": True})
        from users.models import Role, UserRole, RolePermission

        role = Role.objects.create(name="ledger_reader")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role, model_label="accounts.GLEntry", can_read=True
        )
        self._auth(user)
        res = self.client.get(
            f"/api/accounts/reports/trial-balance/?company={self.company.id}"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)


class LedgerImmutabilityOverAPITests(APITestCase):
    """Immutable facts have no write surface exposed on the API at all."""

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            username="imm_admin", email="imm@test.com", password="Pass123!"
        )
        self.client.force_authenticate(self.admin)

    def test_gl_and_sle_endpoints_are_read_only(self):
        # POST / PUT / DELETE are not routed on a ReadOnlyModelViewSet.
        self.assertEqual(
            self.client.post(
                "/api/accounts/gl-entries/", {}, format="json"
            ).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.post("/api/stock/sle/", {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.post("/api/stock/bins/", {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class ThrottlingTests(APITestCase):
    """The JWT login endpoint is rate-limited to blunt credential stuffing."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="throttle_user", email="thr@test.com", password="Pass123!"
        )

    def tearDown(self):
        cache.clear()

    def test_login_endpoint_throttles_brute_force(self):
        """After the `auth` scope budget is spent, further attempts get 429 —
        wrong-password attempts count too, so guessing is capped per IP."""
        got_429 = False
        # `auth` default is 10/min; fire well past it with bad credentials.
        for _ in range(25):
            res = self.client.post(
                "/api/auth/token/",
                {"username": "throttle_user", "password": "wrong"},
                format="json",
            )
            if res.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                got_429 = True
                break
            self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(
            got_429, "login endpoint did not throttle a burst of failed logins"
        )
