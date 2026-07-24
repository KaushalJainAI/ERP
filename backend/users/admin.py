"""Django admin for users & RBAC."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from users.models import Role, RolePermission, User, UserRole


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "language",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "language")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("email",)

    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Preferences", {"fields": ("language",)}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Preferences", {"fields": ("email", "language")}),
    )


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    fields = (
        "model_label",
        "can_read",
        "can_create",
        "can_write",
        "can_submit",
        "can_cancel",
        "can_delete",
    )


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name", "description")
    ordering = ("name",)
    inlines = [RolePermissionInline, UserRoleInline]


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    search_fields = ("user__email", "user__username", "role__name")
    autocomplete_fields = ("user", "role")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "model_label",
        "can_read",
        "can_create",
        "can_write",
        "can_submit",
        "can_cancel",
        "can_delete",
    )
    list_filter = (
        "can_read",
        "can_create",
        "can_write",
        "can_submit",
        "can_cancel",
        "can_delete",
        "role",
    )
    search_fields = ("model_label", "role__name")
    autocomplete_fields = ("role",)
