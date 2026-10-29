"""Company isolation: the membership boundary and the request's active company."""

import datetime
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import GLEntry, Account
from core.models import Company, UserCompany
from selling.models import Customer, CustomerGroup
from users.models import Role, RolePermission, User, UserRole


def results(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class CompanyIsolationAPITests(APITestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="Alpha Foods", abbr="ALP")
        self.company_b = Company.objects.create(name="Beta Foods", abbr="BET")

        self.role = Role.objects.create(name="Sales Operator")
        for label in ("selling.Customer", "accounts.GLEntry"):
            RolePermission.objects.create(
                role=self.role,
                model_label=label,
                can_read=True,
                can_create=True,
                can_write=True,
            )

        self.user = User.objects.create_user(
            username="member", email="member@example.com", password="test-pass-123"
        )
        UserRole.objects.create(user=self.user, role=self.role)
        UserCompany.objects.create(
            user=self.user, company=self.company_a, is_default=True
        )

        self.group = CustomerGroup.objects.create(name="Retail")
        self.customer_a = Customer.objects.create(
            name="Alpha Customer", company=self.company_a, customer_group=self.group
        )
        self.customer_b = Customer.objects.create(
            name="Beta Customer", company=self.company_b, customer_group=self.group
        )

        self.client.force_authenticate(self.user)

    def test_company_list_contains_only_memberships(self):
        response = self.client.get("/api/core/companies/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = results(response)
        self.assertEqual([row["id"] for row in rows], [self.company_a.id])

    def test_list_defaults_to_the_members_default_company(self):
        response = self.client.get("/api/selling/customers/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = results(response)
        self.assertEqual([row["id"] for row in rows], [self.customer_a.id])

    def test_tampered_company_header_is_denied(self):
        response = self.client.get(
            "/api/selling/customers/", HTTP_X_COMPANY_ID=str(self.company_b.id)
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_company_detail_is_not_found(self):
        response = self.client.get(
            f"/api/selling/customers/{self.customer_b.id}/",
            HTTP_X_COMPANY_ID=str(self.company_a.id),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_cannot_be_moved_to_another_company(self):
        response = self.client.post(
            "/api/selling/customers/",
            {
                "name": "Gamma Customer",
                "company": self.company_b.id,
                "customer_group": self.group.id,
            },
            format="json",
            HTTP_X_COMPANY_ID=str(self.company_a.id),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Customer.objects.get(name="Gamma Customer")
        self.assertEqual(created.company_id, self.company_a.id)

    def test_report_ignores_a_company_query_parameter(self):
        account_a = Account.objects.create(
            name="Alpha Sales", company=self.company_a, root_type="Income"
        )
        account_b = Account.objects.create(
            name="Beta Sales", company=self.company_b, root_type="Income"
        )
        GLEntry.objects.create(
            posting_date=datetime.date(2026, 4, 1),
            account=account_a,
            company=self.company_a,
            credit=Decimal("100.00"),
            voucher_type="Sales Invoice",
            voucher_no="A-1",
        )
        GLEntry.objects.create(
            posting_date=datetime.date(2026, 4, 1),
            account=account_b,
            company=self.company_b,
            credit=Decimal("200.00"),
            voucher_type="Sales Invoice",
            voucher_no="B-1",
        )

        response = self.client.get(
            f"/api/accounts/reports/trial-balance/?company={self.company_b.id}",
            HTTP_X_COMPANY_ID=str(self.company_a.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [row["account_name"] for row in response.data["rows"]]
        self.assertEqual(names, ["Alpha Sales"])
        self.assertEqual(response.data["total_credit"], "100.00")

    def test_user_without_a_company_is_denied(self):
        outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="test-pass-123"
        )
        UserRole.objects.create(user=outsider, role=self.role)
        self.client.force_authenticate(outsider)

        response = self.client.get("/api/selling/customers/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_can_enter_any_active_company(self):
        root = User.objects.create_superuser(
            username="root", email="root@example.com", password="test-pass-123"
        )
        self.client.force_authenticate(root)

        response = self.client.get(
            "/api/selling/customers/", HTTP_X_COMPANY_ID=str(self.company_b.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = results(response)
        self.assertEqual([row["id"] for row in rows], [self.customer_b.id])
