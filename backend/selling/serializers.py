from rest_framework import serializers
from selling.models import CustomerGroup, Customer, SalesOrder, SalesOrderItem, SalesInvoice, SalesInvoiceItem
from core.mixins import TranslatedSerializerMixin
from core.models import NamingSeries


class CustomerGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = CustomerGroup
        fields = ("id", "name", "parent", "parent_name", "is_group", "created_at", "modified_at")


class CustomerSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    customer_group_name = serializers.CharField(source="customer_group.name", read_only=True)

    class Meta:
        model = Customer
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "customer_group",
            "customer_group_name",
            "gstin",
            "tax_id",
            "email",
            "phone",
            "is_active",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]


class SalesOrderItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = ("id", "item", "item_code", "item_name", "warehouse", "qty", "rate", "amount")


class SalesOrderSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = SalesOrder
        fields = (
            "id",
            "name",
            "customer",
            "customer_name",
            "company",
            "transaction_date",
            "delivery_date",
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
        if not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next("SO-2026-")
        sales_order = SalesOrder.objects.create(**validated_data)

        gt = 0
        for item in items_data:
            item_obj = SalesOrderItem.objects.create(sales_order=sales_order, **item)
            gt += item_obj.amount

        sales_order.grand_total = gt
        sales_order.save(update_fields=["grand_total"])
        return sales_order


class SalesInvoiceItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = SalesInvoiceItem
        fields = ("id", "item", "item_code", "item_name", "warehouse", "qty", "rate", "amount")


class SalesInvoiceSerializer(serializers.ModelSerializer):
    items = SalesInvoiceItemSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = SalesInvoice
        fields = (
            "id",
            "name",
            "customer",
            "customer_name",
            "company",
            "posting_date",
            "due_date",
            "sales_order",
            "update_stock",
            "debit_to",
            "income_account",
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
        if not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next("SINV-2026-")
        sales_invoice = SalesInvoice.objects.create(**validated_data)

        gt = 0
        for item in items_data:
            item_obj = SalesInvoiceItem.objects.create(sales_invoice=sales_invoice, **item)
            gt += item_obj.amount

        sales_invoice.grand_total = gt
        sales_invoice.save(update_fields=["grand_total"])
        return sales_invoice
