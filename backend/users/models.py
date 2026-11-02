from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class Workspace(models.TextChoices):
    ORDERS = "orders", _("Orders")
    FACTORY = "factory", _("Factory")
    ACCOUNTS = "accounts", _("Accounts")
    ADMIN = "admin", _("Admin")


class User(AbstractUser):
    email = models.EmailField(unique=True)
    language = models.CharField(max_length=5, default="en")
    default_workspace = models.CharField(
        max_length=20,
        choices=Workspace.choices,
        default=Workspace.ADMIN,
    )
    # Version token for the UI (the generic form re-seeds itself when a
    # document's ``modified_at`` changes). Bumped by every profile/role/active
    # change; null for rows not edited since this column was added.
    modified_at = models.DateTimeField(auto_now=True, null=True, editable=False)
    # Sign out everywhere: tokens issued before this instant are refused
    # (users.auth). Set on deactivation and on any password change.
    tokens_valid_after = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(default_workspace__in=Workspace.values),
                name="check_user_default_workspace_valid",
            )
        ]

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email or self.username


class Role(models.Model):
    name = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=240, blank=True)

    def __str__(self):
        return self.name


class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_users")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="unique_user_role")
        ]

    def __str__(self):
        return f"{self.user.email} - {self.role.name}"


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    model_label = models.CharField(
        max_length=120
    )  # e.g. 'stock.Item' or 'accounts.SalesInvoice'
    can_read = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_write = models.BooleanField(default=False)
    can_submit = models.BooleanField(default=False)
    can_cancel = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role", "model_label"], name="unique_role_model_permission"
            )
        ]

    def __str__(self):
        return f"{self.role.name} -> {self.model_label}"
