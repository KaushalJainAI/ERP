"""Django admin for stock (Item, Warehouse, SLE, Bin, Stock Entry).

StockLedgerEntry is append-only and Bin is a derived projection — both are
read-only in admin. Corrections go through reverse/repost services.
"""

from django.contrib import admin

from core.admin import (
    ReadOnlyModelAdmin,
    SubmittableDocumentAdmin,
    TimeStampedAdminMixin,
)
from stock.models import (
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockLedgerEntry,
    UOM,
    Warehouse,
)


@admin.register(UOM)
class UOMAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "gst_uqc")
    search_fields = ("name",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("name",)


@admin.register(ItemGroup)
class ItemGroupAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "parent", "is_group")
    list_filter = ("is_group",)
    search_fields = ("name",)
    autocomplete_fields = ("parent",)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("name",)


@admin.register(Item)
class ItemAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "item_group",
        "stock_uom",
        "is_stock_item",
        "valuation_method",
        "standard_rate",
        "is_active",
    )
    list_filter = (
        "is_active",
        "is_stock_item",
        "valuation_method",
        "item_group",
    )
    search_fields = ("code", "name", "description", "gst_hsn_code")
    autocomplete_fields = ("item_group", "stock_uom", "item_tax_template")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("code",)


@admin.register(Warehouse)
class WarehouseAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "parent", "is_group")
    list_filter = ("company", "is_group")
    search_fields = ("name",)
    autocomplete_fields = ("company", "parent")
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields
    ordering = ("company", "name")


@admin.register(StockLedgerEntry)
class StockLedgerEntryAdmin(ReadOnlyModelAdmin):
    list_display = (
        "posting_date",
        "posting_time",
        "item",
        "warehouse",
        "qty",
        "valuation_rate",
        "stock_value_difference",
        "voucher_type",
        "voucher_no",
        "is_cancelled",
    )
    list_filter = ("is_cancelled", "voucher_type", "posting_date", "warehouse")
    search_fields = (
        "voucher_no",
        "item__code",
        "item__name",
        "warehouse__name",
    )
    date_hierarchy = "posting_date"
    readonly_fields = (
        "posting_date",
        "posting_time",
        "item",
        "warehouse",
        "qty",
        "valuation_rate",
        "stock_value_difference",
        "voucher_type",
        "voucher_no",
        "is_cancelled",
        "created_at",
    )
    ordering = ("-posting_date", "-posting_time", "-id")


@admin.register(Bin)
class BinAdmin(ReadOnlyModelAdmin):
    """Derived stock balance — rebuild via repost_stock, never edit by hand."""

    list_display = (
        "item",
        "warehouse",
        "actual_qty",
        "valuation_rate",
        "stock_value",
        "modified_at",
    )
    list_filter = ("warehouse",)
    search_fields = ("item__code", "item__name", "warehouse__name")
    readonly_fields = (
        "item",
        "warehouse",
        "actual_qty",
        "valuation_rate",
        "stock_value",
        "created_at",
        "modified_at",
        "created_by",
        "modified_by",
    )
    ordering = ("item__code", "warehouse__name")


class StockEntryItemInline(admin.TabularInline):
    model = StockEntryItem
    extra = 1
    autocomplete_fields = ("item", "source_warehouse", "target_warehouse")
    fields = (
        "item",
        "source_warehouse",
        "target_warehouse",
        "qty",
        "valuation_rate",
    )


@admin.register(StockEntry)
class StockEntryAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "stock_entry_type",
        "posting_date",
        "company",
        "docstatus",
    )
    list_filter = ("docstatus", "stock_entry_type", "company", "posting_date")
    search_fields = ("name",)
    date_hierarchy = "posting_date"
    autocomplete_fields = ("company", "amended_from")
    inlines = [StockEntryItemInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "stock_entry_type",
                    "company",
                    "posting_date",
                    "posting_time",
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
