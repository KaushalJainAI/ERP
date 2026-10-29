"""Django admin for buying (Supplier, Purchase Order, Purchase Invoice)."""

from django.contrib import admin

from core.admin import SubmittableDocumentAdmin, TimeStampedAdminMixin
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseInvoiceTax,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderTax,
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
        "gst_category",
        "state",
        "email",
        "phone",
        "is_active",
    )
    list_filter = ("company", "supplier_group", "is_active", "gst_category")
    search_fields = ("name", "gstin", "tax_id", "email", "phone")
    autocomplete_fields = ("company", "supplier_group", "state", "default_price_list")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("company", "name")


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1
    autocomplete_fields = ("item", "warehouse")
    fields = ("item", "warehouse", "qty", "rate", "price_list_rate", "amount")
    readonly_fields = ("price_list_rate", "amount")


class PurchaseOrderTaxInline(admin.TabularInline):
    model = PurchaseOrderTax
    extra = 0
    autocomplete_fields = ("account",)
    fields = (
        "idx",
        "account",
        "description",
        "rate",
        "add_deduct_tax",
        "net_amount",
        "tax_amount",
        "total",
    )
    readonly_fields = ("net_amount", "tax_amount", "total")


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
    inlines = [PurchaseOrderItemInline, PurchaseOrderTaxInline]
    readonly_fields = SubmittableDocumentAdmin.readonly_fields + (
        "grand_total",
        "gst_category",
        "party_gstin",
        "company_gstin",
        "net_total",
        "total_taxes_and_charges",
        "rounding_adjustment",
        "rounded_total",
    )
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
        (
            "GST",
            {
                "fields": (
                    "taxes_and_charges",
                    "place_of_supply",
                    "is_export_with_gst",
                    "is_reverse_charge",
                    "gst_category",
                    "party_gstin",
                    "company_gstin",
                    "net_total",
                    "total_taxes_and_charges",
                    "rounding_adjustment",
                    "rounded_total",
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
    fields = ("item", "warehouse", "qty", "rate", "price_list_rate", "amount")
    readonly_fields = ("price_list_rate", "amount")


class PurchaseInvoiceTaxInline(admin.TabularInline):
    model = PurchaseInvoiceTax
    extra = 0
    autocomplete_fields = ("account",)
    fields = (
        "idx",
        "account",
        "description",
        "rate",
        "add_deduct_tax",
        "net_amount",
        "tax_amount",
        "total",
    )
    readonly_fields = ("net_amount", "tax_amount", "total")


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
    inlines = [PurchaseInvoiceItemInline, PurchaseInvoiceTaxInline]
    readonly_fields = SubmittableDocumentAdmin.readonly_fields + (
        "grand_total",
        "gst_category",
        "party_gstin",
        "company_gstin",
        "net_total",
        "total_taxes_and_charges",
        "rounding_adjustment",
        "rounded_total",
    )
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
                    "bill_no",
                    "bill_date",
                    "purchase_order",
                    "update_stock",
                )
            },
        ),
        ("Accounts", {"fields": ("credit_to", "expense_account")}),
        (
            "GST",
            {
                "fields": (
                    "taxes_and_charges",
                    "place_of_supply",
                    "is_export_with_gst",
                    "is_reverse_charge",
                    "gst_category",
                    "party_gstin",
                    "company_gstin",
                    "net_total",
                    "total_taxes_and_charges",
                    "rounding_adjustment",
                    "rounded_total",
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
