import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from core.models import Company, DocStatus, AuditLog
from users.models import Role, RolePermission, User, UserRole
from selling.models import Customer, CustomerGroup, SalesOrder, SalesOrderItem
from stock.models import Item, ItemGroup, UOM, Warehouse


class SoftDeleteAndFKGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(name="Test Corp", abbr="TC")
        self.group = CustomerGroup.objects.create(name="General")

        # Admin user
        self.admin = User.objects.create_superuser(
            username="super_admin", email="admin@test.com", password="password123"
        )

        # Standard staff user with role permissions
        self.staff_user = User.objects.create_user(
            username="staff_user", email="staff@test.com", password="password123"
        )
        self.staff_role = Role.objects.create(name="Staff", description="Staff")
        UserRole.objects.create(user=self.staff_user, role=self.staff_role)
        RolePermission.objects.create(
            role=self.staff_role,
            model_label="selling.Customer",
            can_read=True,
            can_create=True,
            can_write=True,
            can_delete=True,
        )
        RolePermission.objects.create(
            role=self.staff_role,
            model_label="selling.SalesOrder",
            can_read=True,
            can_create=True,
            can_write=True,
            can_delete=True,
        )

        self.customer = Customer.objects.create(
            company=self.company,
            name="Alpha Customer",
            customer_group=self.group,
            is_active=True,
        )

        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(name="Products")
        self.item = Item.objects.create(
            code="ITEM-001",
            name="Widget",
            stock_uom=self.uom,
            item_group=self.item_group,
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company, name="Stores", is_group=False
        )

    def test_non_admin_delete_performs_soft_delete(self):
        """Standard users deleting an active master soft-deletes it (is_active=False)."""
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.delete(f"/api/selling/customers/{self.customer.pk}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data.get("soft_deleted"))

        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                action="soft_delete",
                model_label="selling.Customer",
                object_id=self.customer.name,
            ).exists()
        )

    def test_admin_delete_performs_hard_delete_when_unreferenced(self):
        """Administrators permanently remove unreferenced master records."""
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f"/api/selling/customers/{self.customer.pk}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_fk_protected_delete_returns_409_with_details_not_500(self):
        """Deleting a master referenced by a protected FK returns 409 with details instead of 500."""
        # Create Sales Order referencing the customer
        order = SalesOrder.objects.create(
            name="SO-TEST-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.DRAFT,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal("5.000"),
            rate=Decimal("100.00"),
            amount=Decimal("500.00"),
        )

        # Admin attempts hard delete on Customer
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f"/api/selling/customers/{self.customer.pk}/")

        # MUST NOT be 500! Must be clean 409 Conflict with descriptive error
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "foreign_key_protected")
        self.assertIn("referenced by", res.data.get("error", "").lower())
        # The customer record must still safely exist
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_non_admin_draft_document_delete_soft_cancels(self):
        """Non-admin deleting a draft document cancels it as a soft delete."""
        order = SalesOrder.objects.create(
            name="SO-DRAFT-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.DRAFT,
        )
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.delete(f"/api/selling/sales-orders/{order.pk}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data.get("soft_deleted"))

        order.refresh_from_db()
        self.assertEqual(order.docstatus, DocStatus.CANCELLED)

    def test_submitted_document_delete_refused_with_409(self):
        """Submitted documents cannot be deleted by anyone; must cancel & amend."""
        order = SalesOrder.objects.create(
            name="SO-SUBMITTED-001",
            company=self.company,
            customer=self.customer,
            transaction_date=datetime.date.today(),
            docstatus=DocStatus.SUBMITTED,
        )
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f"/api/selling/sales-orders/{order.pk}/")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("cancel and amend", res.data.get("error", "").lower())
