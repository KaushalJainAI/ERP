from rest_framework import serializers
from selling.models import (
    CustomerGroup,
    Customer,
    SalesOrder,
    SalesOrderItem,
    SalesInvoice,
    SalesInvoiceItem,
)
from core.mixins import (
    DraftNestedWriteMixin,
    TranslatedSerializerMixin,
    PositiveQtyLineItemMixin,
    NonNegativeRateLineItemMixin,
)


class CustomerGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = CustomerGroup
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "is_group",
            "created_at",
            "modified_at",
        )


class CustomerSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    customer_group_name = serializers.CharField(
        source="customer_group.name", read_only=True
    )

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


class SalesOrderItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = SalesOrderItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "rate",
            "amount",
        )


class SalesOrderSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "items"
    name_prefix = "SO-2026-"

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
        read_only_fields = (
            "id",
            "docstatus",
            "grand_total",
            "created_at",
            "modified_at",
        )


class SalesInvoiceItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = SalesInvoiceItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "rate",
            "amount",
        )


class SalesInvoiceSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "items"
    name_prefix = "SINV-2026-"

    items = SalesInvoiceItemSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    # sales_order is nullable: allow_null so the key is kept (DRF otherwise
    # drops a null dotted source from the output).
    sales_order_name = serializers.CharField(
        source="sales_order.name", read_only=True, allow_null=True
    )
    debit_to_name = serializers.CharField(source="debit_to.name", read_only=True)
    income_account_name = serializers.CharField(
        source="income_account.name", read_only=True
    )

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
            "sales_order_name",
            "update_stock",
            "debit_to",
            "debit_to_name",
            "income_account",
            "income_account_name",
            "grand_total",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "grand_total",
            "created_at",
            "modified_at",
        )
