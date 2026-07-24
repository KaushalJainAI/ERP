"""Django admin for core framework models.

Immutable / derived records (AuditLog, SystemHealthCheck) are read-only so
admin cannot bypass the posting services or integrity tools.
"""

from django.contrib import admin

from core.models import (
    AuditLog,
    Company,
    DummySubmittableDoc,
    FieldTranslation,
    FiscalYear,
    Language,
    NamingSeries,
    Outbox,
    SystemHealthCheck,
)


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """ModelAdmin that forbids add/change/delete (for immutable ledgers & projections).

    Detail pages remain viewable via has_view_permission; only mutations are blocked.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff


class TimeStampedAdminMixin:
    """Common readonly audit fields for TimeStampedModel subclasses."""

    readonly_timestamp_fields = (
        "created_at",
        "modified_at",
        "created_by",
        "modified_by",
    )


class SubmittableDocumentAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    """Base admin for SubmittableDocument models.

    docstatus is read-only so submit/cancel always go through model methods
    (and thus ledger posting), not a raw admin field edit.
    """

    list_filter = ("docstatus",)
    search_fields = ("name",)
    readonly_fields = (
        "docstatus",
        "amended_from",
        "created_at",
        "modified_at",
        "created_by",
        "modified_by",
    )


@admin.register(Company)
class CompanyAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "abbr", "default_currency", "country", "gstin", "is_active")
    list_filter = ("is_active", "country", "default_currency")
    search_fields = ("name", "abbr", "gstin")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("name",)


@admin.register(FiscalYear)
class FiscalYearAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_closed")
    list_filter = ("is_closed",)
    search_fields = ("name",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("-start_date",)


@admin.register(NamingSeries)
class NamingSeriesAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("prefix", "current", "padding")
    search_fields = ("prefix",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("prefix",)


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyModelAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "model_label",
        "object_id",
        "ip_address",
    )
    list_filter = ("action", "model_label")
    search_fields = ("model_label", "object_id", "user__email", "user__username")
    date_hierarchy = "timestamp"
    readonly_fields = (
        "timestamp",
        "user",
        "action",
        "model_label",
        "object_id",
        "changes",
        "ip_address",
    )
    ordering = ("-timestamp",)


@admin.register(SystemHealthCheck)
class SystemHealthCheckAdmin(ReadOnlyModelAdmin):
    list_display = ("checked_at", "check_name", "status")
    list_filter = ("status", "check_name")
    search_fields = ("check_name",)
    date_hierarchy = "checked_at"
    readonly_fields = ("checked_at", "check_name", "status", "details")
    ordering = ("-checked_at",)


@admin.register(Outbox)
class OutboxAdmin(admin.ModelAdmin):
    list_display = (
        "dedupe_key",
        "channel",
        "status",
        "attempts",
        "next_attempt_at",
        "created_at",
    )
    list_filter = ("channel", "status")
    search_fields = ("dedupe_key", "last_error")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    # Allow ops to inspect / re-queue (status, next_attempt_at) but not invent rows.
    def has_add_permission(self, request):
        return False


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_rtl", "is_active")
    list_filter = ("is_active", "is_rtl")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(FieldTranslation)
class FieldTranslationAdmin(admin.ModelAdmin):
    list_display = ("model_label", "object_id", "field_name", "language", "value_preview")
    list_filter = ("language", "model_label")
    search_fields = ("model_label", "field_name", "value", "object_id")
    ordering = ("model_label", "object_id", "field_name", "language")

    @admin.display(description="value")
    def value_preview(self, obj):
        text = obj.value or ""
        return text if len(text) <= 60 else f"{text[:57]}..."


@admin.register(DummySubmittableDoc)
class DummySubmittableDocAdmin(SubmittableDocumentAdmin):
    list_display = ("name", "title", "docstatus", "created_at")
    search_fields = ("name", "title")
