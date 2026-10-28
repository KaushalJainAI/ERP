from rest_framework import serializers
from decimal import Decimal
from gst.serializers import PartyGSTSerializerMixin
from buying.models import (
    SupplierGroup,
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrderTax,
    PurchaseInvoiceTax,
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
from stock.services import pricing as pricing_service


class SupplierGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = SupplierGroup
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "is_group",
            "created_at",
            "modified_at",
        )


class SupplierSerializer(
    PartyGSTSerializerMixin, TranslatedSerializerMixin, serializers.ModelSerializer
):
    company_name = serializers.CharField(source="company.name", read_only=True)
    supplier_group_name = serializers.CharField(
        source="supplier_group.name", read_only=True
    )
    state_name = serializers.CharField(
        source="state.name", read_only=True, allow_null=True
    )
    default_price_list_name = serializers.CharField(
        source="default_price_list.name", read_only=True, allow_null=True
    )

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
            "gst_category",
            "state",
            "state_name",
            "tax_id",
            "email",
            "phone",
            "is_active",
            "default_price_list",
            "default_price_list_name",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]


class PurchaseOrderItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
    )

    class Meta:
        model = PurchaseOrderItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "rate",
            "price_list_rate",
            "amount",
            *LINE_GST_FIELDS,
        )
        read_only_fields = ("price_list_rate", *LINE_GST_FIELDS)


PurchaseOrderTaxSerializer = tax_row_serializer(PurchaseOrderTax)


class PurchaseOrderSerializer(
    TaxableSerializerMixin, DraftNestedWriteMixin, serializers.ModelSerializer
):
    children_field = "items"
    name_prefix = "PO-2026-"

    items = PurchaseOrderItemSerializer(many=True)
    taxes = PurchaseOrderTaxSerializer(many=True, required=False)
    place_of_supply_name = serializers.CharField(
        source="place_of_supply.name", read_only=True, allow_null=True
    )
    taxes_and_charges_name = serializers.CharField(
        source="taxes_and_charges.title", read_only=True, allow_null=True
    )
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    price_list_name = serializers.CharField(
        source="price_list.name", read_only=True, allow_null=True
    )

    class Meta:
        model = PurchaseOrder
        fields = (
            "id",
            "name",
            "supplier",
            "supplier_name",
            "company",
            "price_list",
            "price_list_name",
            "transaction_date",
            "schedule_date",
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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return pricing_service.apply_line_rates(
            self,
            attrs,
            selling=False,
            party_field="supplier",
            date_field="transaction_date",
        )


class PurchaseInvoiceItemSerializer(
    PositiveQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default=None)
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
    )

    class Meta:
        model = PurchaseInvoiceItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "rate",
            "price_list_rate",
            "amount",
            "batch",
            "batch_no",
            "supplier_batch_no",
            "manufacturing_date",
            "expiry_date",
            *LINE_GST_FIELDS,
        )
        read_only_fields = ("price_list_rate", *LINE_GST_FIELDS)


PurchaseInvoiceTaxSerializer = tax_row_serializer(PurchaseInvoiceTax)


class PurchaseInvoiceSerializer(
    TaxableSerializerMixin, DraftNestedWriteMixin, serializers.ModelSerializer
):
    children_field = "items"
    name_prefix = "PINV-2026-"

    items = PurchaseInvoiceItemSerializer(many=True)
    taxes = PurchaseInvoiceTaxSerializer(many=True, required=False)
    place_of_supply_name = serializers.CharField(
        source="place_of_supply.name", read_only=True, allow_null=True
    )
    taxes_and_charges_name = serializers.CharField(
        source="taxes_and_charges.title", read_only=True, allow_null=True
    )
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    # purchase_order is nullable: allow_null so the key is kept (DRF otherwise
    # drops a null dotted source from the output).
    purchase_order_name = serializers.CharField(
        source="purchase_order.name", read_only=True, allow_null=True
    )
    credit_to_name = serializers.CharField(source="credit_to.name", read_only=True)
    expense_account_name = serializers.CharField(
        source="expense_account.name", read_only=True
    )
    price_list_name = serializers.CharField(
        source="price_list.name", read_only=True, allow_null=True
    )

    class Meta:
        model = PurchaseInvoice
        fields = (
            "id",
            "name",
            "supplier",
            "supplier_name",
            "company",
            "price_list",
            "price_list_name",
            "posting_date",
            "due_date",
            "purchase_order",
            "purchase_order_name",
            "update_stock",
            "credit_to",
            "credit_to_name",
            "expense_account",
            "expense_account_name",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "outstanding_amount",
            "status",
            "is_overdue",
            "display_status",
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
            "outstanding_amount",
            "status",
            "is_overdue",
            "display_status",
            *TAX_HEADER_READ_ONLY,
            "created_at",
            "modified_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return pricing_service.apply_line_rates(
            self,
            attrs,
            selling=False,
            party_field="supplier",
            date_field="posting_date",
        )
