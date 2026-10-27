from rest_framework import serializers
from gst.serializers import PartyGSTSerializerMixin
from selling.models import (
    CustomerGroup,
    Customer,
    SalesOrder,
    SalesOrderItem,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrderTax,
    SalesInvoiceTax,
)
from accounts.serializers import (
    LINE_GST_FIELDS,
    TAX_HEADER_READ_ONLY,
    TAX_HEADER_WRITABLE,
    TaxableSerializerMixin,
    tax_row_serializer,
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


class CustomerSerializer(
    PartyGSTSerializerMixin, TranslatedSerializerMixin, serializers.ModelSerializer
):
    company_name = serializers.CharField(source="company.name", read_only=True)
    customer_group_name = serializers.CharField(
        source="customer_group.name", read_only=True
    )
    state_name = serializers.CharField(
        source="state.name", read_only=True, allow_null=True
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
            "gst_category",
            "state",
            "state_name",
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
            *LINE_GST_FIELDS,
        )
        read_only_fields = LINE_GST_FIELDS


SalesOrderTaxSerializer = tax_row_serializer(SalesOrderTax)


class SalesOrderSerializer(
    TaxableSerializerMixin, DraftNestedWriteMixin, serializers.ModelSerializer
):
    children_field = "items"
    name_prefix = "SO-2026-"

    items = SalesOrderItemSerializer(many=True)
    taxes = SalesOrderTaxSerializer(many=True, required=False)
    place_of_supply_name = serializers.CharField(
        source="place_of_supply.name", read_only=True, allow_null=True
    )
    taxes_and_charges_name = serializers.CharField(
        source="taxes_and_charges.title", read_only=True, allow_null=True
    )
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
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "place_of_supply_name",
            "taxes_and_charges_name",
            "docstatus",
            "amended_from",
            "items",
            "taxes",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
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
            *LINE_GST_FIELDS,
        )
        read_only_fields = LINE_GST_FIELDS


SalesInvoiceTaxSerializer = tax_row_serializer(SalesInvoiceTax)


class SalesInvoiceSerializer(
    TaxableSerializerMixin, DraftNestedWriteMixin, serializers.ModelSerializer
):
    children_field = "items"
    name_prefix = "SINV-2026-"

    items = SalesInvoiceItemSerializer(many=True)
    taxes = SalesInvoiceTaxSerializer(many=True, required=False)
    place_of_supply_name = serializers.CharField(
        source="place_of_supply.name", read_only=True, allow_null=True
    )
    taxes_and_charges_name = serializers.CharField(
        source="taxes_and_charges.title", read_only=True, allow_null=True
    )
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
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "place_of_supply_name",
            "taxes_and_charges_name",
            "docstatus",
            "amended_from",
            "items",
            "taxes",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            "created_at",
            "modified_at",
        )
