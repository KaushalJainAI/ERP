from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError
from stock.models import (
    UOM,
    ItemGroup,
    Item,
    Warehouse,
    StockLedgerEntry,
    Bin,
    StockEntry,
    StockEntryItem,
)
from core.mixins import (
    DraftNestedWriteMixin,
    TranslatedSerializerMixin,
    PositiveQtyLineItemMixin,
)


class UOMSerializer(serializers.ModelSerializer):
    class Meta:
        model = UOM
        fields = ("id", "name", "created_at", "modified_at")


class ItemGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = ItemGroup
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "is_group",
            "created_at",
            "modified_at",
        )


class ItemSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    item_group_name = serializers.CharField(source="item_group.name", read_only=True)
    stock_uom_name = serializers.CharField(source="stock_uom.name", read_only=True)

    class Meta:
        model = Item
        fields = (
            "id",
            "code",
            "name",
            "description",
            "item_group",
            "item_group_name",
            "stock_uom",
            "stock_uom_name",
            "is_stock_item",
            "valuation_method",
            "standard_rate",
            "is_active",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name", "description"]


class WarehouseSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Warehouse
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "parent",
            "parent_name",
            "is_group",
            "created_at",
            "modified_at",
        )


class StockLedgerEntrySerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = StockLedgerEntry
        fields = (
            "id",
            "posting_date",
            "posting_time",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "valuation_rate",
            "stock_value_difference",
            "voucher_type",
            "voucher_no",
            "is_cancelled",
            "created_at",
        )
        read_only_fields = fields


class BinSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = Bin
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "actual_qty",
            "valuation_rate",
            "stock_value",
            "modified_at",
        )
        read_only_fields = fields


class StockEntryItemSerializer(PositiveQtyLineItemMixin, serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    source_warehouse_name = serializers.CharField(
        source="source_warehouse.name", read_only=True, allow_null=True
    )
    target_warehouse_name = serializers.CharField(
        source="target_warehouse.name", read_only=True, allow_null=True
    )

    def validate_valuation_rate(self, value):
        if value is not None and value < 0:
            raise DRFValidationError("Valuation rate cannot be negative.")
        return value

    class Meta:
        model = StockEntryItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "source_warehouse",
            "source_warehouse_name",
            "target_warehouse",
            "target_warehouse_name",
            "qty",
            "valuation_rate",
        )


class StockEntrySerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "items"
    name_prefix = "STE-2026-"

    items = StockEntryItemSerializer(many=True)

    class Meta:
        model = StockEntry
        fields = (
            "id",
            "name",
            "stock_entry_type",
            "posting_date",
            "posting_time",
            "company",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "created_at", "modified_at")
