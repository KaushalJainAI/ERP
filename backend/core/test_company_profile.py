"""Company profile page API: active-company lookup, edit permission and validation."""

from rest_framework.test import APITestCase

from core.models import Company, UserCompany
from users.models import Role, RolePermission, User, UserRole


class CompanyProfileTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Profile Co",
            abbr="PROF",
            country="India",
            default_currency="INR",
        )
        self.user = User.objects.create_user(
            username="profile", email="profile@t.com", password="Pass123!"
        )
        UserCompany.objects.get_or_create(
            user=self.user, company=self.company, defaults={"is_default": True}
        )

    def test_current_company_returns_profile_and_write_flag(self):
        self.client.force_authenticate(self.user)
        res = self.client.get("/api/core/companies/current/")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["id"], self.company.pk)
        self.assertFalse(body["can_write"])

    def test_edit_requires_model_permission(self):
        self.client.force_authenticate(self.user)
        res = self.client.patch(
            f"/api/core/companies/{self.company.pk}/",
            {"phone_no": "+91 99999 00000"},
            format="json",
        )
        self.assertEqual(res.status_code, 403, res.content)

        role = Role.objects.create(name="company-admin")
        UserRole.objects.create(user=self.user, role=role)
        RolePermission.objects.create(
            role=role, model_label="core.Company", can_read=True, can_write=True
        )

        res = self.client.patch(
            f"/api/core/companies/{self.company.pk}/",
            {
                "legal_name": "Profile Private Limited",
                "phone_no": "+91 99999 00000",
                "pincode": "400001",
                "pan": "ABCDE1234F",
                "cin": "U12345MH2020PTC123456",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.company.refresh_from_db()
        self.assertEqual(self.company.pan, "ABCDE1234F")
        self.assertEqual(self.company.pincode, "400001")
        self.assertTrue(res.json()["can_write"])

    def test_invalid_statutory_fields_are_rejected(self):
        role = Role.objects.create(name="company-admin")
        UserRole.objects.create(user=self.user, role=role)
        RolePermission.objects.create(
            role=role, model_label="core.Company", can_read=True, can_write=True
        )
        self.client.force_authenticate(self.user)
        res = self.client.patch(
            f"/api/core/companies/{self.company.pk}/",
            {"pan": "bad-pan", "pincode": "12"},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("pan", res.json())
        self.assertIn("pincode", res.json())
