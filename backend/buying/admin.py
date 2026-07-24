"""Django admin for buying (Supplier, Purchase Order, Purchase Invoice)."""

from django.contrib import admin

from core.admin import SubmittableDocumentAdmin, TimeStampedAdminMixin
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierGroup,
)


@admin.register(SupplierGroup)
class SupplierGroupAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "parent", "is_group")
    list_filter = ("is_group",)
    search_fields = ("name",)
    autocomplete_fields = ("parent",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("name",)


@admin.register(Supplier)
class SupplierAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "supplier_group",
        "gstin",
        "email",
        "phone",
        "is_active",
    )
    list_filter = ("company", "supplier_group", "is_active")
    search_fields = ("name", "gstin", "tax_id", "email", "phone")
    autocomplete_fields = ("company", "supplier_group")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("company", "name")


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1
    autocomplete_fields = ("item", "warehouse")
    fields = ("item", "warehouse", "qty", "rate", "amount")
    readonly_fields = ("amount",)


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "supplier",
        "company",
        "transaction_date",
        "schedule_date",
        "grand_total",
        "docstatus",
    )
    list_filter = ("docstatus", "company", "transaction_date")
    search_fields = ("name", "supplier__name")
    date_hierarchy = "transaction_date"
    autocomplete_fields = ("supplier", "company", "amended_from")
    inlines = [PurchaseOrderItemInline]
    readonly_fields = SubmittableDocumentAdmin.readonly_fields + ("grand_total",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "supplier",
                    "company",
                    "transaction_date",
                    "schedule_date",
                )
            },
        ),
        ("Status", {"fields": ("docstatus", "amended_from", "grand_total")}),
        (
            "Audit",
            {
                "fields": ("created_at", "modified_at", "created_by", "modified_by"),
                "classes": ("collapse",),
            },
        ),
    )


class PurchaseInvoiceItemInline(admin.TabularInline):
    model = PurchaseInvoiceItem
    extra = 1
    autocomplete_fields = ("item", "warehouse")
    fields = ("item", "warehouse", "qty", "rate", "amount")
    readonly_fields = ("amount",)


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "supplier",
        "company",
        "posting_date",
        "due_date",
        "grand_total",
        "update_stock",
        "docstatus",
    )
    list_filter = ("docstatus", "company", "update_stock", "posting_date")
    search_fields = ("name", "supplier__name")
    date_hierarchy = "posting_date"
    autocomplete_fields = (
        "supplier",
        "company",
        "purchase_order",
        "credit_to",
        "expense_account",
        "amended_from",
    )
    inlines = [PurchaseInvoiceItemInline]
    readonly_fields = SubmittableDocumentAdmin.readonly_fields + ("grand_total",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "supplier",
                    "company",
                    "posting_date",
                    "due_date",
                    "purchase_order",
                    "update_stock",
                )
            },
        ),
        ("Accounts", {"fields": ("credit_to", "expense_account")}),
        ("Status", {"fields": ("docstatus", "amended_from", "grand_total")}),
        (
            "Audit",
            {
                "fields": ("created_at", "modified_at", "created_by", "modified_by"),
                "classes": ("collapse",),
            },
        ),
    )
