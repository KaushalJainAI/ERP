import datetime
from rest_framework import serializers
from decimal import Decimal
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
    RETURN_HEADER_FIELDS,
    ReturnSerializerMixin,
    SignedQtyLineItemMixin,
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
from accounts import mapping as account_mapping
from stock.services import pricing as pricing_service


class CustomerGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    default_price_list_name = serializers.CharField(
        source="default_price_list.name", read_only=True, allow_null=True
    )

    class Meta:
        model = CustomerGroup
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "is_group",
            "default_price_list",
            "default_price_list_name",
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
    default_price_list_name = serializers.CharField(
        source="default_price_list.name", read_only=True, allow_null=True
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
            "default_price_list",
            "default_price_list_name",
            "credit_limit",
            "bypass_credit_limit_check",
            "credit_days",
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
    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
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
            "price_list_rate",
            "amount",
            "batch",
            "batch_no",
            *LINE_GST_FIELDS,
        )
        read_only_fields = ("price_list_rate", *LINE_GST_FIELDS)


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
    price_list_name = serializers.CharField(
        source="price_list.name", read_only=True, allow_null=True
    )

    class Meta:
        model = SalesOrder
        fields = (
            "id",
            "name",
            "customer",
            "customer_name",
            "company",
            "price_list",
            "price_list_name",
            "transaction_date",
            "delivery_date",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "place_of_supply_name",
            "taxes_and_charges_name",
            "docstatus",
            "dispatch_status",
            "amended_from",
            "items",
            "taxes",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "dispatch_status",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            "created_at",
            "modified_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = attrs.get("company") or getattr(self.instance, "company", None)
        if company and "items" in attrs:
            from stock.models import Warehouse

            default_wh = getattr(company, "ready_warehouse", None)
            if not default_wh:
                default_wh = (
                    Warehouse.objects.filter(company=company, is_group=False)
                    .order_by("pk")
                    .first()
                )
            if default_wh:
                for line in attrs["items"]:
                    if not line.get("warehouse"):
                        line["warehouse"] = default_wh

        return pricing_service.apply_line_rates(
            self,
            attrs,
            selling=True,
            party_field="customer",
            date_field="transaction_date",
        )


class SalesInvoiceItemSerializer(
    SignedQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )
    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
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
            "price_list_rate",
            "amount",
            "batch",
            "batch_no",
            "sales_invoice_item",
            *LINE_GST_FIELDS,
        )
        read_only_fields = ("price_list_rate", *LINE_GST_FIELDS)


SalesInvoiceTaxSerializer = tax_row_serializer(SalesInvoiceTax)


class SalesInvoiceSerializer(
    ReturnSerializerMixin,
    TaxableSerializerMixin,
    DraftNestedWriteMixin,
    serializers.ModelSerializer,
):
    children_field = "items"
    name_prefix = "SINV-2026-"
    return_name_prefix = "SRET-2026-"

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
    source_journal_name = serializers.CharField(
        source="source_journal.name", read_only=True, allow_null=True
    )
    return_against_name = serializers.CharField(
        source="return_against.name", read_only=True, allow_null=True
    )
    debit_to_name = serializers.CharField(source="debit_to.name", read_only=True)
    income_account_name = serializers.CharField(
        source="income_account.name", read_only=True
    )
    price_list_name = serializers.CharField(
        source="price_list.name", read_only=True, allow_null=True
    )
    payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = SalesInvoice
        fields = (
            "id",
            "name",
            "customer",
            "customer_name",
            "company",
            "price_list",
            "price_list_name",
            "posting_date",
            "due_date",
            "sales_order",
            "sales_order_name",
            "update_stock",
            *RETURN_HEADER_FIELDS,
            "debit_to",
            "debit_to_name",
            "income_account",
            "income_account_name",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "outstanding_amount",
            "status",
            "is_overdue",
            "display_status",
            "payment_status",
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
            "payment_status",
            *TAX_HEADER_READ_ONLY,
            "created_at",
            "modified_at",
        )

    _DEFAULT_ACCOUNTS = {
        "debit_to": lambda company, attrs: account_mapping.default_receivable_account(
            company
        ),
        "income_account": lambda company, attrs: (
            account_mapping.default_income_account(company)
        ),
    }

    def get_fields(self):
        return account_mapping.optional_account_fields(
            super().get_fields(), self._DEFAULT_ACCOUNTS
        )

    def create(self, validated_data):
        if not validated_data.get("name"):
            posting_date = validated_data.get("posting_date") or datetime.date.today()
            from core.models import NamingSeries

            if validated_data.get("is_return"):
                validated_data["name"] = NamingSeries.next(
                    account_mapping.financial_year_series("SRET", posting_date)
                )
            else:
                validated_data["name"] = NamingSeries.next(
                    account_mapping.financial_year_series("SINV", posting_date)
                )
        return super().create(validated_data)

    def validate(self, attrs):
        attrs = account_mapping.fill_default_accounts(
            self, attrs, self._DEFAULT_ACCOUNTS
        )
        if not attrs.get("due_date"):
            customer = attrs.get("customer", getattr(self.instance, "customer", None))
            posting_date = attrs.get(
                "posting_date", getattr(self.instance, "posting_date", None)
            )
            if customer and posting_date:
                from accounts.services import terms as terms_service

                attrs["due_date"] = terms_service.calculate_due_date(
                    "Customer", customer, posting_date
                )
        from accounts.services import terms as terms_service

        terms_service.validate_due_date(self, attrs)
        attrs = super().validate(attrs)
        return pricing_service.apply_line_rates(
            self,
            attrs,
            selling=True,
            party_field="customer",
            date_field="posting_date",
        )
