"""Django admin for accounts (CoA, GL, Journal Entry, Payment Entry).

GLEntry is append-only and registered read-only — never written via admin.
"""

from django.contrib import admin

from accounts.models import (
    Account,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
)
from core.admin import (
    ReadOnlyModelAdmin,
    SubmittableDocumentAdmin,
    TimeStampedAdminMixin,
)


@admin.register(Account)
class AccountAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "root_type",
        "account_type",
        "account_number",
        "is_group",
        "is_active",
        "parent",
    )
    list_filter = ("company", "root_type", "account_type", "is_group", "is_active")
    search_fields = ("name", "account_number")
    autocomplete_fields = ("company", "parent")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("company", "name")


@admin.register(GLEntry)
class GLEntryAdmin(ReadOnlyModelAdmin):
    list_display = (
        "posting_date",
        "account",
        "company",
        "debit",
        "credit",
        "voucher_type",
        "voucher_no",
        "party_type",
        "party",
        "is_cancelled",
        "fiscal_year",
    )
    list_filter = (
        "company",
        "voucher_type",
        "is_cancelled",
        "fiscal_year",
        "posting_date",
    )
    search_fields = (
        "voucher_no",
        "account__name",
        "party",
        "against_voucher_no",
        "remarks",
    )
    date_hierarchy = "posting_date"
    readonly_fields = (
        "posting_date",
        "account",
        "company",
        "party_type",
        "party",
        "debit",
        "credit",
        "voucher_type",
        "voucher_no",
        "against_voucher_type",
        "against_voucher_no",
        "is_cancelled",
        "fiscal_year",
        "remarks",
        "created_at",
    )
    ordering = ("-posting_date", "-id")


class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    extra = 1
    autocomplete_fields = ("account",)
    fields = ("account", "debit", "credit", "party_type", "party")


@admin.register(JournalEntry)
class JournalEntryAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "posting_date",
        "company",
        "total_debit",
        "total_credit",
        "docstatus",
    )
    list_filter = ("docstatus", "company", "posting_date")
    search_fields = ("name", "user_remark")
    date_hierarchy = "posting_date"
    autocomplete_fields = ("company", "amended_from")
    inlines = [JournalEntryLineInline]
    readonly_fields = SubmittableDocumentAdmin.readonly_fields + (
        "total_debit",
        "total_credit",
    )
    fieldsets = (
        (None, {"fields": ("name", "company", "posting_date", "user_remark")}),
        (
            "Status",
            {"fields": ("docstatus", "amended_from", "total_debit", "total_credit")},
        ),
        (
            "Audit",
            {
                "fields": ("created_at", "modified_at", "created_by", "modified_by"),
                "classes": ("collapse",),
            },
        ),
    )


class PaymentEntryReferenceInline(admin.TabularInline):
    model = PaymentEntryReference
    extra = 0
    fields = ("reference_doctype", "reference_name", "allocated_amount")


@admin.register(PaymentEntry)
class PaymentEntryAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "payment_type",
        "posting_date",
        "company",
        "party_type",
        "party_name",
        "paid_amount",
        "docstatus",
    )
    list_filter = ("docstatus", "payment_type", "company", "posting_date")
    search_fields = ("name", "party_name", "reference_no")
    date_hierarchy = "posting_date"
    autocomplete_fields = ("company", "paid_from", "paid_to", "amended_from")
    inlines = [PaymentEntryReferenceInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "payment_type",
                    "company",
                    "posting_date",
                    "reference_no",
                )
            },
        ),
        ("Party", {"fields": ("party_type", "party_name")}),
        (
            "Accounts",
            {
                "fields": (
                    "paid_from",
                    "paid_to",
                    "paid_amount",
                    "received_amount",
                )
            },
        ),
        ("Status", {"fields": ("docstatus", "amended_from")}),
        (
            "Audit",
            {
                "fields": ("created_at", "modified_at", "created_by", "modified_by"),
                "classes": ("collapse",),
            },
        ),
    )
