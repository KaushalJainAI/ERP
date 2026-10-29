from rest_framework import serializers
from decimal import Decimal
from django.utils.translation import gettext as _
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
    PurchaseInvoiceCharge,
    PurchaseChargeTemplate,
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
from core.models import DocStatus
from core.mixins import (
    DraftNestedWriteMixin,
    TranslatedSerializerMixin,
    PositiveQtyLineItemMixin,
    NonNegativeRateLineItemMixin,
)
from accounts import mapping as account_mapping
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
    SignedQtyLineItemMixin, NonNegativeRateLineItemMixin, serializers.ModelSerializer
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
            "purchase_invoice_item",
            *LINE_GST_FIELDS,
        )
        read_only_fields = ("price_list_rate", *LINE_GST_FIELDS)


PurchaseInvoiceTaxSerializer = tax_row_serializer(PurchaseInvoiceTax)


class PurchaseInvoiceChargeSerializer(serializers.ModelSerializer):
    """One freight / hamali / loading row of a purchase bill."""

    account_name = serializers.CharField(
        source="account.name", read_only=True, allow_null=True
    )
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )

    class Meta:
        model = PurchaseInvoiceCharge
        fields = (
            "id",
            "description",
            "account",
            "account_name",
            "amount",
            "distribute_by",
            "included_in_bill",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("included_in_bill") and attrs.get("account") is None:
            raise serializers.ValidationError(
                {"account": _("Choose the expense account of a charge on the bill.")}
            )
        return attrs


class PurchaseChargeTemplateSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(
        source="account.name", read_only=True, allow_null=True
    )

    class Meta:
        model = PurchaseChargeTemplate
        fields = (
            "id",
            "company",
            "name",
            "account",
            "account_name",
            "distribute_by",
            "included_in_bill",
            "is_active",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")
        validators = []

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inst = self.instance
        in_bill = attrs.get("included_in_bill", inst.included_in_bill if inst else False)
        account = attrs.get("account", inst.account if inst else None)
        company = attrs.get("company", inst.company if inst else None)
        if in_bill and account is None:
            raise serializers.ValidationError(
                {"account": _("Choose the expense account of a charge on the bill.")}
            )
        if account is not None and company is not None and account.company_id != company.pk:
            raise serializers.ValidationError(
                {"account": _("The account must belong to the template's company.")}
            )
        name = attrs.get("name", inst.name if inst else None)
        if company is not None and name:
            clash = PurchaseChargeTemplate.objects.filter(company=company, name=name)
            if inst is not None:
                clash = clash.exclude(pk=inst.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"name": _("A charge template with this name already exists.")}
                )
        return attrs


class PurchaseInvoiceSerializer(
    ReturnSerializerMixin,
    TaxableSerializerMixin,
    DraftNestedWriteMixin,
    serializers.ModelSerializer,
):
    children_field = "items"
    extra_children_fields = ("taxes", "charges")
    name_prefix = "PINV-2026-"
    return_name_prefix = "PRET-2026-"

    items = PurchaseInvoiceItemSerializer(many=True)
    taxes = PurchaseInvoiceTaxSerializer(many=True, required=False)
    charges = PurchaseInvoiceChargeSerializer(many=True, required=False)
    charges_on_bill = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True
    )
    charges_off_bill = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True
    )
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
    return_against_name = serializers.CharField(
        source="return_against.name", read_only=True, allow_null=True
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
            "bill_no",
            "bill_date",
            "purchase_order",
            "purchase_order_name",
            "update_stock",
            *RETURN_HEADER_FIELDS,
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
            "charges",
            "charges_on_bill",
            "charges_off_bill",
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

    _DEFAULT_ACCOUNTS = {
        "credit_to": lambda company, attrs: account_mapping.default_payable_account(
            company
        ),
        "expense_account": lambda company, attrs: (
            account_mapping.default_expense_account(
                company, attrs.get("update_stock", True)
            )
        ),
    }

    def get_fields(self):
        return account_mapping.optional_account_fields(
            super().get_fields(), self._DEFAULT_ACCOUNTS
        )

    def validate(self, attrs):
        attrs = account_mapping.fill_default_accounts(
            self, attrs, self._DEFAULT_ACCOUNTS
        )
        attrs = super().validate(attrs)
        self._validate_bill(attrs)
        self._validate_charges(attrs)
        return pricing_service.apply_line_rates(
            self,
            attrs,
            selling=False,
            party_field="supplier",
            date_field="posting_date",
        )

    def _validate_bill(self, attrs):
        """Supplier bill number / date (ERPNext ``validate_supplier_invoice``)."""
        inst = self.instance
        bill_no = (attrs.get("bill_no", inst.bill_no if inst else "") or "").strip()
        if "bill_no" in attrs:
            attrs["bill_no"] = bill_no
        posting = attrs.get("posting_date", inst.posting_date if inst else None)
        bill_date = attrs.get("bill_date", inst.bill_date if inst else None)
        if bill_date and posting and bill_date > posting:
            raise serializers.ValidationError(
                {"bill_date": _("Bill Date cannot be after the posting date.")}
            )
        company = attrs.get("company", inst.company if inst else None)
        supplier = attrs.get("supplier", inst.supplier if inst else None)
        if bill_no and company is not None and supplier is not None:
            clash = PurchaseInvoice.objects.filter(
                company=company, supplier=supplier, bill_no__iexact=bill_no
            ).exclude(docstatus=DocStatus.CANCELLED)
            if inst is not None:
                clash = clash.exclude(pk=inst.pk)
            other = clash.first()
            if other is not None:
                raise serializers.ValidationError(
                    {
                        "bill_no": _(
                            "Supplier invoice no '%(bill_no)s' is already booked on "
                            "%(name)s."
                        )
                        % {"bill_no": bill_no, "name": other.name}
                    }
                )

    def _validate_charges(self, attrs):
        rows = attrs.get("charges")
        if not rows:
            return
        inst = self.instance
        if attrs.get("is_return", inst.is_return if inst else False):
            raise serializers.ValidationError(
                {"charges": _("A return (debit note) cannot carry extra charges.")}
            )
        company = attrs.get("company", inst.company if inst else None)
        for n, row in enumerate(rows, start=1):
            account = row.get("account")
            if account is not None and company is not None:
                if account.company_id != company.pk or account.is_group:
                    raise serializers.ValidationError(
                        {
                            "charges": _(
                                "Row %(n)s: the account must be a ledger of the "
                                "invoice's company."
                            )
                            % {"n": n}
                        }
                    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Landed cost per stock line: its amount plus its share of the charges.
        from buying.services import charges as charges_service

        rows = list(instance.charges.all()) if instance.pk else []
        shares = (
            charges_service.allocation(instance, rows=rows)
            if rows and not instance.is_return
            else {}
        )
        by_pk = {ln.pk: ln for ln in instance.items.all()} if shares else {}
        for item in data.get("items", []):
            line = by_pk.get(item.get("id"))
            share = shares.get(item.get("id"))
            if line is None or share is None:
                item["landed_cost_amount"] = None
                item["landed_rate"] = None
                continue
            landed = line.amount + share
            item["landed_cost_amount"] = str(landed)
            item["landed_rate"] = (
                str((landed / abs(line.qty)).quantize(Decimal("0.000001")))
                if line.qty
                else None
            )
        return data
