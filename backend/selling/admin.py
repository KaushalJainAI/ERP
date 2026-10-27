"""Django admin for selling (Customer, Sales Order, Sales Invoice)."""

from django.contrib import admin

from core.admin import SubmittableDocumentAdmin, TimeStampedAdminMixin
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesInvoiceTax,
    SalesOrder,
    SalesOrderItem,
    SalesOrderTax,
)


@admin.register(CustomerGroup)
class CustomerGroupAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "parent", "is_group")
    list_filter = ("is_group",)
    search_fields = ("name",)
    autocomplete_fields = ("parent",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("name",)


@admin.register(Customer)
class CustomerAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "customer_group",
        "gstin",
        "gst_category",
        "state",
        "email",
        "phone",
        "is_active",
    )
    list_filter = ("company", "customer_group", "is_active", "gst_category")
    search_fields = ("name", "gstin", "tax_id", "email", "phone")
    autocomplete_fields = ("company", "customer_group", "state")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("company", "name")


class SalesOrderItemInline(admin.TabularInline):
    model = SalesOrderItem
    extra = 1
    autocomplete_fields = ("item", "warehouse")
    fields = ("item", "warehouse", "qty", "rate", "amount")
    readonly_fields = ("amount",)


class SalesOrderTaxInline(admin.TabularInline):
    model = SalesOrderTax
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


@admin.register(SalesOrder)
class SalesOrderAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "customer",
        "company",
        "transaction_date",
        "delivery_date",
        "grand_total",
        "docstatus",
    )
    list_filter = ("docstatus", "company", "transaction_date")
    search_fields = ("name", "customer__name")
    date_hierarchy = "transaction_date"
    autocomplete_fields = ("customer", "company", "amended_from")
    inlines = [SalesOrderItemInline, SalesOrderTaxInline]
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
                    "customer",
                    "company",
                    "transaction_date",
                    "delivery_date",
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


class SalesInvoiceItemInline(admin.TabularInline):
    model = SalesInvoiceItem
    extra = 1
    autocomplete_fields = ("item", "warehouse")
    fields = ("item", "warehouse", "qty", "rate", "amount")
    readonly_fields = ("amount",)


class SalesInvoiceTaxInline(admin.TabularInline):
    model = SalesInvoiceTax
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


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "customer",
        "company",
        "posting_date",
        "due_date",
        "grand_total",
        "update_stock",
        "docstatus",
    )
    list_filter = ("docstatus", "company", "update_stock", "posting_date")
    search_fields = ("name", "customer__name")
    date_hierarchy = "posting_date"
    autocomplete_fields = (
        "customer",
        "company",
        "sales_order",
        "debit_to",
        "income_account",
        "amended_from",
    )
    inlines = [SalesInvoiceItemInline, SalesInvoiceTaxInline]
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
                    "customer",
                    "company",
                    "posting_date",
                    "due_date",
                    "sales_order",
                    "update_stock",
                )
            },
        ),
        ("Accounts", {"fields": ("debit_to", "income_account")}),
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
