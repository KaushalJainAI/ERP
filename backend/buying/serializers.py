from django.db import transaction
from rest_framework import serializers
from buying.models import SupplierGroup, Supplier, PurchaseOrder, PurchaseOrderItem, PurchaseInvoice, PurchaseInvoiceItem
from core.mixins import (
    TranslatedSerializerMixin,
    PositiveQtyLineItemMixin,
    NonNegativeRateLineItemMixin,
)
from core.models import NamingSeries


class SupplierGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = SupplierGroup
        fields = ("id", "name", "parent", "parent_name", "is_group", "created_at", "modified_at")


class SupplierSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    supplier_group_name = serializers.CharField(source="supplier_group.name", read_only=True)

    class Meta:
        model = Supplier
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "supplier_group",
            "supplier_group_name",
            "gstin",
            "tax_id",
            "email",
            "phone",
            "is_active",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]


class PurchaseOrderItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ("id", "item", "item_code", "item_name", "warehouse", "qty", "rate", "amount")


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = (
            "id",
            "name",
            "supplier",
            "supplier_name",
            "company",
            "transaction_date",
            "schedule_date",
            "grand_total",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "grand_total", "created_at", "modified_at")

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next("PO-2026-")
            purchase_order = PurchaseOrder.objects.create(**validated_data)

            gt = 0
            for item in items_data:
                item_obj = PurchaseOrderItem.objects.create(purchase_order=purchase_order, **item)
                gt += item_obj.amount

            purchase_order.grand_total = gt
            purchase_order.save(update_fields=["grand_total"])
        return purchase_order


class PurchaseInvoiceItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = PurchaseInvoiceItem
        fields = ("id", "item", "item_code", "item_name", "warehouse", "qty", "rate", "amount")


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    items = PurchaseInvoiceItemSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = (
            "id",
            "name",
            "supplier",
            "supplier_name",
            "company",
            "posting_date",
            "due_date",
            "purchase_order",
            "update_stock",
            "credit_to",
            "expense_account",
            "grand_total",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "grand_total", "created_at", "modified_at")

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next("PINV-2026-")
            purchase_invoice = PurchaseInvoice.objects.create(**validated_data)

            gt = 0
            for item in items_data:
                item_obj = PurchaseInvoiceItem.objects.create(purchase_invoice=purchase_invoice, **item)
                gt += item_obj.amount

            purchase_invoice.grand_total = gt
            purchase_invoice.save(update_fields=["grand_total"])
        return purchase_invoice
