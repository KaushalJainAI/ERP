from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from users.models import User, Role, UserRole, RolePermission
from users.permissions import HasRolePermission


class DummyView:
    model_label = "stock.Item"

    class MetaModel:
        app_label = "stock"
        object_name = "Item"

    model = MetaModel


class UsersAppTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="john",
            email="John.Doe@Example.COM",
            password="secretpassword123",
            language="hi",
        )
        self.role = Role.objects.create(name="Stock Manager", description="Manages stock items")

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_user_email_lowercased_on_save(self):
        """GOOD: User email is automatically lowercased when saved."""
        self.assertEqual(self.user.email, "john.doe@example.com")
        self.assertEqual(self.user.language, "hi")

    def test_role_and_permission_assignment(self):
        """GOOD: Role, UserRole, and RolePermission created cleanly."""
        ur = UserRole.objects.create(user=self.user, role=self.role)
        perm = RolePermission.objects.create(
            role=self.role,
            model_label="stock.Item",
            can_read=True,
            can_create=True,
        )
        self.assertEqual(UserRole.objects.count(), 1)
        self.assertEqual(RolePermission.objects.count(), 1)

    def test_has_role_permission_granted(self):
        """GOOD: HasRolePermission grants access when user role has permission flag."""
        UserRole.objects.create(user=self.user, role=self.role)
        RolePermission.objects.create(
            role=self.role,
            model_label="stock.Item",
            can_read=True,
        )

        perm_checker = HasRolePermission()
        request = type("Request", (), {"user": self.user, "method": "GET"})()
        view = DummyView()

        self.assertTrue(perm_checker.has_permission(request, view))

    def test_jwt_token_obtain_success(self):
        """GOOD: POST /api/auth/token/ returns valid access & refresh JWT tokens."""
        response = self.client.post(
            "/api/auth/token/",
            {"username": "john", "password": "secretpassword123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_duplicate_user_role_fails(self):
        """BAD: Duplicate (user, role) pair violates UNIQUE constraint."""
        UserRole.objects.create(user=self.user, role=self.role)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserRole.objects.create(user=self.user, role=self.role)

    def test_duplicate_role_permission_fails(self):
        """BAD: Duplicate (role, model_label) pair violates UNIQUE constraint."""
        RolePermission.objects.create(role=self.role, model_label="stock.Item", can_read=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RolePermission.objects.create(role=self.role, model_label="stock.Item", can_read=False)

    def test_has_role_permission_denied_when_missing_flag(self):
        """BAD: User lacking permission flag is denied by HasRolePermission."""
        UserRole.objects.create(user=self.user, role=self.role)
        # Role permission only has can_read=True
        RolePermission.objects.create(
            role=self.role,
            model_label="stock.Item",
            can_read=True,
            can_create=False,
        )

        perm_checker = HasRolePermission()
        # Attempt POST (can_create)
        request = type("Request", (), {"user": self.user, "method": "POST"})()
        view = DummyView()

        self.assertFalse(perm_checker.has_permission(request, view))

    def test_jwt_token_obtain_invalid_credentials_fails(self):
        """BAD: POST /api/auth/token/ with wrong password returns 401 Unauthorized."""
        response = self.client.post(
            "/api/auth/token/",
            {"username": "john", "password": "wrongpassword"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
