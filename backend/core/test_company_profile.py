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

    def test_company_default_warehouses_api(self):
        from stock.models import Warehouse

        role = Role.objects.create(name="company-admin-wh")
        UserRole.objects.create(user=self.user, role=role)
        RolePermission.objects.create(
            role=role, model_label="core.Company", can_read=True, can_write=True
        )
        self.client.force_authenticate(self.user)

        group_wh = Warehouse.objects.create(
            name="Group Warehouse", company=self.company, is_group=True
        )
        leaf_wh1 = Warehouse.objects.create(
            name="Raw Material WH", company=self.company, is_group=False
        )
        leaf_wh2 = Warehouse.objects.create(
            name="WIP WH", company=self.company, is_group=False
        )
        leaf_wh3 = Warehouse.objects.create(
            name="FG WH", company=self.company, is_group=False
        )

        # Refuse group warehouse
        res = self.client.patch(
            f"/api/core/companies/{self.company.pk}/",
            {"unprocessed_warehouse": group_wh.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("unprocessed_warehouse", res.json())

        # Accept valid warehouses
        res = self.client.patch(
            f"/api/core/companies/{self.company.pk}/",
            {
                "unprocessed_warehouse": leaf_wh1.pk,
                "in_process_warehouse": leaf_wh2.pk,
                "ready_warehouse": leaf_wh3.pk,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.company.refresh_from_db()
        self.assertEqual(self.company.unprocessed_warehouse_id, leaf_wh1.pk)
        self.assertEqual(self.company.in_process_warehouse_id, leaf_wh2.pk)
        self.assertEqual(self.company.ready_warehouse_id, leaf_wh3.pk)
