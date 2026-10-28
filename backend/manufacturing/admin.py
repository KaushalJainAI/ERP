"""Django admin for manufacturing masters and documents."""

from django.contrib import admin

from core.admin import SubmittableDocumentAdmin, TimeStampedAdminMixin
from manufacturing.models import BOM, BOMItem, BOMScrapItem, WorkOrder, WorkOrderItem


class BOMItemInline(admin.TabularInline):
    model = BOMItem
    fk_name = "bom"
    extra = 0
    autocomplete_fields = ("item", "bom_no")


class BOMScrapItemInline(admin.TabularInline):
    model = BOMScrapItem
    extra = 0
    autocomplete_fields = ("item",)


@admin.register(BOM)
class BOMAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "item",
        "quantity",
        "process_loss_percentage",
        "is_active",
        "is_default",
    )
    list_filter = ("company", "is_active", "is_default", "rate_basis")
    search_fields = ("name", "item__code", "item__name")
    autocomplete_fields = ("company", "item")
    inlines = (BOMItemInline, BOMScrapItemInline)
    readonly_fields = TimeStampedAdminMixin.readonly_timestamp_fields


class WorkOrderItemInline(admin.TabularInline):
    model = WorkOrderItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "item",
        "source_warehouse",
        "required_qty",
        "transferred_qty",
        "consumed_qty",
    )
    autocomplete_fields = ("item", "source_warehouse")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "company",
        "production_item",
        "qty",
        "status",
        "docstatus",
        "planned_start_date",
    )
    list_filter = ("company", "docstatus", "status", "stopped")
    search_fields = ("name", "production_item__code", "production_item__name")
    autocomplete_fields = (
        "company",
        "production_item",
        "bom",
        "source_warehouse",
        "wip_warehouse",
        "fg_warehouse",
        "scrap_warehouse",
    )
    inlines = (WorkOrderItemInline,)
