"""RBAC hardening tests.

Covers the defects fixed in the 2026-10-21 RBAC pass:

* **P0.3** -- ``is_staff`` used to bypass RBAC entirely, handing every
  staff-flagged operator the whole company's books. In Django ``is_staff``
  only means "may sign in to the admin site".
* **P0.4** -- ``HasRolePermission`` failed *open* when a view could not be
  tied to a model, which made any function-based view world-readable to
  every logged-in user. It must now fail closed.
* **Seed drift** -- the seeded model-label list had drifted (``accounts.
  SalesInvoice`` for a model living in ``selling``, plus three labels for
  models that do not exist) while omitting the two labels the report
  endpoints are gated on. No seeded role could read a single report, and
  every manager role held every permission on every model.
"""

from django.apps import apps as django_apps
from django.core.cache import cache
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import Role, RolePermission, User, UserRole
from users.permissions import HasRolePermission, resolve_model_label

REPORT_URLS = [
    "/api/accounts/reports/trial-balance/",
    "/api/accounts/reports/general-ledger/",
    "/api/accounts/reports/profit-and-loss/",
    "/api/accounts/reports/balance-sheet/",
]

STOCK_REPORT_URL = "/api/stock/reports/stock-balance/"


class StaffIsNotSuperuserTests(APITestCase):
    """`is_staff` must not grant business-data authority (P0.3).

    Staff keep their Django admin-site access via `ModelAdmin`; authority over
    the API comes from roles.
    """

    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="staffer",
            email="staff@test.com",
            password="Pass123!",
            is_staff=True,
        )
        self.client.force_authenticate(self.staff)

    def test_staff_without_roles_is_denied_business_endpoints(self):
        for url in [
            "/api/stock/items/",
            "/api/accounts/accounts/",
            "/api/selling/sales-invoices/",
            "/api/buying/purchase-invoices/",
        ]:
            res = self.client.get(url)
            self.assertEqual(
                res.status_code, status.HTTP_403_FORBIDDEN, f"{url} leaked to staff"
            )

    def test_staff_without_roles_cannot_read_reports(self):
        for url in REPORT_URLS + [STOCK_REPORT_URL]:
            res = self.client.get(url)
            self.assertEqual(
                res.status_code, status.HTTP_403_FORBIDDEN, f"{url} leaked to staff"
            )

    def test_staff_cannot_manage_roles(self):
        """Role management is the control plane -- it is not staff-gated."""
        res = self.client.get("/api/auth/roles/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_self_escalate_through_the_permissions_api(self):
        """A staff user must not be able to grant themselves ledger read."""
        role = Role.objects.create(name="Some Role")
        res = self.client.post(
            "/api/auth/permissions/",
            {"role": role.id, "model_label": "accounts.GLEntry", "can_read": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            RolePermission.objects.filter(
                role=role, model_label="accounts.GLEntry", can_read=True
            ).exists()
        )

    def test_superuser_still_bypasses_rbac(self):
        admin = User.objects.create_superuser(
            username="root", email="root@test.com", password="Pass123!"
        )
        self.client.force_authenticate(admin)
        self.assertEqual(
            self.client.get("/api/stock/items/").status_code, status.HTTP_200_OK
        )
        self.assertEqual(
            self.client.get("/api/auth/roles/").status_code, status.HTTP_200_OK
        )
        for url in REPORT_URLS:
            self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK, url)


class FailClosedTests(APITestCase):
    """A view with no resolvable model must be denied, never allowed (P0.4)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="modelless", email="modelless@test.com", password="Pass123!"
        )

    def _request(self, method="GET"):
        return type("Request", (), {"user": self.user, "method": method})()

    def test_view_without_model_is_denied(self):
        class ModelLessView:
            """A view exposing no model, queryset, or model_label."""

        self.assertFalse(
            HasRolePermission().has_permission(self._request(), ModelLessView)
        )

    def test_view_whose_get_queryset_raises_is_denied(self):
        class BrokenView:
            def get_queryset(self):
                raise RuntimeError("boom")

        self.assertFalse(
            HasRolePermission().has_permission(self._request(), BrokenView)
        )

    def test_view_with_explicit_model_label_is_evaluated(self):
        class LabelledView:
            model_label = "stock.Item"

        # No role grants it, so it is still denied -- but the label resolved.
        self.assertFalse(
            HasRolePermission().has_permission(self._request(), LabelledView)
        )

        role = Role.objects.create(name="Reader")
        UserRole.objects.create(user=self.user, role=role)
        RolePermission.objects.create(
            role=role, model_label="stock.Item", can_read=True
        )
        self.assertTrue(
            HasRolePermission().has_permission(self._request(), LabelledView)
        )

    def test_user_without_roles_is_denied_everywhere(self):
        class LabelledView:
            model_label = "stock.Item"

        self.assertFalse(
            HasRolePermission().has_permission(self._request(), LabelledView)
        )

    def test_empty_queryset_still_resolves_the_model(self):
        """An empty table must not make the view unresolvable.

        A Django QuerySet is falsy when it has no rows, so a truthiness check
        on ``view.queryset`` discards it and the model is never found. On a
        fresh install -- or a table the user has not written to yet -- that
        turned the permission check into a fail-open for every viewset.
        """
        from stock.models import Item

        class EmptyTableView:
            # `Item.objects.all()` with no rows -> falsy queryset.
            queryset = Item.objects.all()

        self.assertEqual(
            resolve_model_label(EmptyTableView()),
            "stock.Item",
        )

    def test_empty_queryset_view_is_evaluated_not_denied_blindly(self):
        from stock.models import Item

        class EmptyTableView:
            queryset = Item.objects.all()

        role = Role.objects.create(name="Reader")
        UserRole.objects.create(user=self.user, role=role)
        RolePermission.objects.create(
            role=role, model_label="stock.Item", can_read=True
        )
        self.assertTrue(
            HasRolePermission().has_permission(self._request(), EmptyTableView)
        )


class SeedPermissionMatrixTests(APITestCase):
    """The seed must produce a valid, scoped, idempotent permission matrix."""

    def setUp(self):
        cache.clear()
        call_command("seed_defaults", verbosity=0)

    def _snapshot(self):
        return sorted(
            (
                p.role.name,
                p.model_label,
                p.can_read,
                p.can_create,
                p.can_write,
                p.can_submit,
                p.can_cancel,
                p.can_delete,
            )
            for p in RolePermission.objects.select_related("role")
        )

    def _user_with_role(self, role_name):
        slug = role_name.replace(" ", "_").lower()
        user = User.objects.create_user(
            username=slug, email=f"{slug}@test.com", password="Pass123!"
        )
        UserRole.objects.create(user=user, role=Role.objects.get(name=role_name))
        return user

    # -- integrity of the label list -----------------------------------------

    def test_every_seeded_label_resolves_to_a_real_model(self):
        valid = {m._meta.label for m in django_apps.get_models()}
        for perm in RolePermission.objects.all():
            with self.subTest(label=perm.model_label):
                self.assertIn(
                    perm.model_label,
                    valid,
                    f"seeded a permission on non-existent model {perm.model_label}",
                )

    def test_report_bearing_labels_are_seeded(self):
        """accounts.GLEntry and stock.Bin gate the reports -- they must exist."""
        for label in ("accounts.GLEntry", "stock.Bin"):
            with self.subTest(label=label):
                self.assertTrue(
                    RolePermission.objects.filter(
                        model_label=label, can_read=True
                    ).exists(),
                    f"no seeded role can read {label}, so the reports are unreachable",
                )

    def test_phantom_labels_are_pruned_on_reseed(self):
        """The old seed's bogus labels must not survive a re-seed."""
        role = Role.objects.get(name="Sales Manager")
        RolePermission.objects.create(
            role=role, model_label="selling.Quotation", can_read=True
        )
        call_command("seed_defaults", verbosity=0)
        self.assertFalse(
            RolePermission.objects.filter(
                role=role, model_label="selling.Quotation"
            ).exists()
        )

    def test_seed_is_idempotent(self):
        before = self._snapshot()
        call_command("seed_defaults", verbosity=0)
        self.assertEqual(before, self._snapshot())

    def test_reseed_revokes_a_widened_role(self):
        """The seed is authoritative for the roles it owns."""
        role = Role.objects.get(name="Sales Manager")
        RolePermission.objects.filter(role=role, model_label="accounts.GLEntry").update(
            can_read=True, can_delete=True
        )
        call_command("seed_defaults", verbosity=0)
        self.assertFalse(
            RolePermission.objects.filter(
                role=role, model_label="accounts.GLEntry", can_read=True
            ).exists(),
            "Sales Manager must not hold ledger read after a re-seed",
        )

    def test_reseed_does_not_touch_user_created_roles(self):
        custom = Role.objects.create(name="Night Shift Auditor")
        RolePermission.objects.create(
            role=custom, model_label="accounts.GLEntry", can_read=True
        )
        call_command("seed_defaults", verbosity=0)
        self.assertTrue(
            RolePermission.objects.filter(
                role=custom, model_label="accounts.GLEntry", can_read=True
            ).exists(),
            "the seed must not rewrite roles it does not own",
        )

    # -- role scoping --------------------------------------------------------

    def test_sales_manager_cannot_read_the_ledger(self):
        self.client.force_authenticate(self._user_with_role("Sales Manager"))
        res = self.client.get("/api/accounts/gl-entries/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        for url in REPORT_URLS:
            res = self.client.get(url)
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, url)

    def test_sales_manager_manages_the_selling_chain(self):
        self.client.force_authenticate(self._user_with_role("Sales Manager"))
        for url in [
            "/api/selling/sales-invoices/",
            "/api/selling/sales-orders/",
            "/api/selling/customers/",
        ]:
            res = self.client.get(url)
            self.assertEqual(res.status_code, status.HTTP_200_OK, url)

    def test_accounts_manager_reads_the_financial_reports(self):
        self.client.force_authenticate(self._user_with_role("Accounts Manager"))
        for url in REPORT_URLS:
            res = self.client.get(url)
            self.assertEqual(res.status_code, status.HTTP_200_OK, url)

    def test_stock_manager_reads_the_stock_balance_report(self):
        self.client.force_authenticate(self._user_with_role("Stock Manager"))
        res = self.client.get(STOCK_REPORT_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_stock_manager_cannot_read_the_financial_reports(self):
        self.client.force_authenticate(self._user_with_role("Stock Manager"))
        for url in REPORT_URLS:
            res = self.client.get(url)
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, url)

    def test_default_role_cannot_read_the_reports(self):
        """A read+create clerk must not be able to read the books."""
        self.client.force_authenticate(self._user_with_role("Default Role"))
        for url in REPORT_URLS + [STOCK_REPORT_URL]:
            res = self.client.get(url)
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, url)

    def test_default_role_may_read_masters_and_create_documents(self):
        self.client.force_authenticate(self._user_with_role("Default Role"))
        res = self.client.get("/api/stock/items/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_system_manager_manages_roles(self):
        self.client.force_authenticate(self._user_with_role("System Manager"))
        self.assertEqual(
            self.client.get("/api/auth/roles/").status_code, status.HTTP_200_OK
        )
        self.assertEqual(
            self.client.get("/api/auth/permissions/").status_code, status.HTTP_200_OK
        )

    def test_purchase_manager_cannot_read_the_ledger(self):
        self.client.force_authenticate(self._user_with_role("Purchase Manager"))
        res = self.client.get("/api/accounts/gl-entries/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        res = self.client.get("/api/buying/purchase-invoices/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
