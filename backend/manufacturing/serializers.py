"""Serializers for BOM and Work Order (design §2, §3.1).

The BOM is a master written with ``NestedMasterWriteMixin``; its costs are
computed on read by ``manufacturing.services.costing`` and are never stored.
The Work Order's ``required_items`` are server-generated read-only rows.
"""

from django.db import transaction
from rest_framework import serializers

from core.mixins import NestedMasterWriteMixin
from core.models import NamingSeries
from manufacturing.models import BOM, BOMItem, BOMScrapItem, WorkOrder, WorkOrderItem
from manufacturing.services import boms as boms_service
from manufacturing.services import costing


class BOMItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    stock_uom = serializers.CharField(source="item.stock_uom.name", read_only=True)
    bom_no_name = serializers.CharField(source="bom_no.name", read_only=True, default=None)

    class Meta:
        model = BOMItem
        fields = (
            "id",
            "idx",
            "item",
            "item_code",
            "item_name",
            "stock_uom",
            "qty",
            "bom_no",
            "bom_no_name",
        )
        read_only_fields = ("id", "idx")


class BOMScrapItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = BOMScrapItem
        fields = ("id", "idx", "item", "item_code", "item_name", "qty", "rate")
        read_only_fields = ("id", "idx")


class BOMSerializer(NestedMasterWriteMixin, serializers.ModelSerializer):
    children_field = "items"

    items = BOMItemSerializer(many=True, required=False)
    scrap_items = BOMScrapItemSerializer(many=True, required=False)
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    raw_material_cost = serializers.SerializerMethodField()
    scrap_value = serializers.SerializerMethodField()
    good_qty = serializers.SerializerMethodField()
    unit_cost = serializers.SerializerMethodField()

    class Meta:
        model = BOM
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "item",
            "item_code",
            "item_name",
            "quantity",
            "process_loss_percentage",
            "rate_basis",
            "is_active",
            "is_default",
            "description",
            "items",
            "scrap_items",
            "raw_material_cost",
            "scrap_value",
            "good_qty",
            "unit_cost",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")
        extra_kwargs = {"name": {"required": False}}
        # The (company, item) constraint is *partial* (only default BOMs), so BOM
        # versions may share an item. DRF would otherwise generate a global
        # unique-together validator from the conditional constraint.
        validators = []

    def _cost(self, obj=None):
        # With many=True DRF keeps the *list* on ``self.instance`` and calls the
        # child serializer once per row; use the row passed in, not the list.
        obj = self.instance if obj is None else obj
        if not obj or not getattr(obj, "pk", None):
            return None
        try:
            return costing.bom_cost(obj)
        except Exception:
            # A cycle can only exist in legacy data; still render the header.
            return None

    def get_raw_material_cost(self, obj):
        cost = self._cost(obj)
        return str(cost["raw_material_cost"]) if cost else None

    def get_scrap_value(self, obj):
        cost = self._cost(obj)
        return str(cost["scrap_value"]) if cost else None

    def get_good_qty(self, obj):
        cost = self._cost(obj)
        return str(cost["good_qty"]) if cost else None

    def get_unit_cost(self, obj):
        cost = self._cost(obj)
        return str(cost["unit_cost"]) if cost else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        cost = self._cost(instance)
        if cost:
            by_row = {row["row"]: row for row in cost["components"]}
            for row in data.get("items", []):
                computed = by_row.get(row["id"])
                if computed:
                    row["rate"] = str(computed["rate"])
                    row["amount"] = str(computed["amount"])
                    row["source"] = computed["source"]
            data.update(
                {
                    "raw_material_cost": str(cost["raw_material_cost"]),
                    "scrap_value": str(cost["scrap_value"]),
                    "good_qty": str(cost["good_qty"]),
                    "unit_cost": str(cost["unit_cost"]),
                }
            )
        return data

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        if instance is not None and boms_service.usage_locked(instance):
            touched = {
                "quantity",
                "process_loss_percentage",
                "items",
                "scrap_items",
            } & set(attrs)
            if touched:
                boms_service.assert_usage_unlocked(instance)

        company = attrs.get("company", getattr(instance, "company", None))
        item = attrs.get("item", getattr(instance, "item", None))
        if company is None or item is None:
            raise serializers.ValidationError(
                {"item": "A BOM needs a company and a product item."}
            )
        if not item.is_stock_item:
            raise serializers.ValidationError(
                {"item": "The BOM product must be a stock item."}
            )

        # Cross-row rules run against a transient BOM carrying the merged header
        # values and the rows that are about to replace the current set.
        probe = BOM(pk=instance.pk if instance is not None else None)
        probe.company = company
        probe.item = item
        rows = attrs.get("items")
        scrap_rows = attrs.get("scrap_items")
        if rows is None:
            rows = []
        if scrap_rows is None:
            scrap_rows = []
        # A header-only PATCH keeps the existing rows: check them too so item
        # changes cannot silently invalidate a stored recipe.
        if instance is not None and "items" not in attrs:
            rows = [
                {"item": row.item, "bom_no": row.bom_no_id}
                for row in instance.items.select_related("item", "bom_no")
            ]
        if instance is not None and "scrap_items" not in attrs:
            scrap_rows = [
                {"item": row.item}
                for row in instance.scrap_items.select_related("item")
            ]
        try:
            boms_service.validate_bom_rows(probe, rows, scrap_rows)
        except Exception as exc:
            raise serializers.ValidationError(
                getattr(exc, "messages", [str(exc)])
            )
        return attrs

    def _write_scrap(self, bom, rows):
        bom.scrap_items.all().delete()
        for position, row in enumerate(rows, start=1):
            BOMScrapItem.objects.create(bom=bom, idx=position, **row)

    def create(self, validated_data):
        scrap_rows = validated_data.pop("scrap_items", None)
        if not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next("BOM-")
        with transaction.atomic():
            if validated_data.get("is_default"):
                boms_service.clear_other_defaults(
                    validated_data["company"].pk, validated_data["item"].pk
                )
            bom = super().create(validated_data)
            if scrap_rows is not None:
                self._write_scrap(bom, scrap_rows)
        return bom

    def update(self, instance, validated_data):
        scrap_rows = validated_data.pop("scrap_items", None)
        with transaction.atomic():
            if validated_data.get("is_default"):
                boms_service.clear_other_defaults(
                    validated_data.get("company", instance.company).pk,
                    validated_data.get("item", instance.item).pk,
                    exclude_pk=instance.pk,
                )
            bom = super().update(instance, validated_data)
            if scrap_rows is not None:
                self._write_scrap(bom, scrap_rows)
        return bom


class WorkOrderItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    source_warehouse_name = serializers.CharField(
        source="source_warehouse.name", read_only=True, default=None
    )

    class Meta:
        model = WorkOrderItem
        fields = (
            "id",
            "idx",
            "item",
            "item_code",
            "item_name",
            "source_warehouse",
            "source_warehouse_name",
            "required_qty",
            "transferred_qty",
            "consumed_qty",
        )
        read_only_fields = fields


class WorkOrderSerializer(serializers.ModelSerializer):
    required_items = WorkOrderItemSerializer(many=True, read_only=True)
    production_item_code = serializers.CharField(
        source="production_item.code", read_only=True
    )
    production_item_name = serializers.CharField(
        source="production_item.name", read_only=True
    )
    bom_name = serializers.CharField(source="bom.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    source_warehouse_name = serializers.CharField(
        source="source_warehouse.name", read_only=True
    )
    wip_warehouse_name = serializers.CharField(
        source="wip_warehouse.name", read_only=True, allow_null=True
    )
    fg_warehouse_name = serializers.CharField(
        source="fg_warehouse.name", read_only=True
    )
    scrap_warehouse_name = serializers.CharField(
        source="scrap_warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = WorkOrder
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "production_item",
            "production_item_code",
            "production_item_name",
            "bom",
            "bom_name",
            "qty",
            "source_warehouse",
            "source_warehouse_name",
            "wip_warehouse",
            "wip_warehouse_name",
            "fg_warehouse",
            "fg_warehouse_name",
            "scrap_warehouse",
            "scrap_warehouse_name",
            "skip_transfer",
            "planned_start_date",
            "expected_end_date",
            "stopped",
            "produced_qty",
            "process_loss_qty",
            "status",
            "docstatus",
            "amended_from",
            "required_items",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "name",
            "stopped",
            "produced_qty",
            "process_loss_qty",
            "status",
            "docstatus",
            "amended_from",
            "required_items",
            "created_at",
            "modified_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        bom = attrs.get("bom", getattr(instance, "bom", None))
        item = attrs.get("production_item", getattr(instance, "production_item", None))
        company = attrs.get("company", getattr(instance, "company", None))
        if bom is not None and company is not None and bom.company_id != company.pk:
            raise serializers.ValidationError(
                {"bom": "The BOM belongs to a different company."}
            )
        if bom is not None and item is not None and bom.item_id != item.pk:
            raise serializers.ValidationError(
                {"bom": "The BOM makes a different item than the production item."}
            )
        return attrs

    def create(self, validated_data):
        if not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next("WO-2026-")
        return super().create(validated_data)
