import datetime
from rest_framework import serializers
from decimal import Decimal
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from gst.serializers import PartyGSTSerializerMixin
from contacts.parties import (
    ROOT_SUPPLIER_GROUP,
    PartyRecordSerializerMixin,
)
from buying.models import (
    SupplierGroup,
    Supplier,
    ExpenseCategory,
    RecurringExpense,
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
    PartyRecordSerializerMixin,
    PartyGSTSerializerMixin,
    TranslatedSerializerMixin,
    serializers.ModelSerializer,
):
    party_model = Supplier
    group_field = "supplier_group"
    group_model = SupplierGroup
    root_group_name = ROOT_SUPPLIER_GROUP
    name_taken_message = gettext_noop("A supplier named %(name)s already exists.")

    company_name = serializers.CharField(source="company.name", read_only=True)
    supplier_group_name = serializers.CharField(
        source="supplier_group.name", read_only=True
    )
    warnings = serializers.SerializerMethodField()
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
            "warnings",
            "gstin",
            "gst_category",
            "state",
            "state_name",
            "tax_id",
            "email",
            "phone",
            "is_active",
            "is_cash_expense",
            "default_price_list",
            "default_price_list_name",
            "credit_days",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]
        extra_kwargs = {"supplier_group": {"required": False}}


class ExpenseCategorySerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    item_name = serializers.CharField(
        source="item.name", read_only=True, allow_null=True
    )

    class Meta:
        model = ExpenseCategory
        fields = (
            "id",
            "company",
            "company_name",
            "name",
            "account",
            "account_name",
            "item",
            "item_name",
            "is_active",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]


class RecurringExpenseSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = RecurringExpense
        fields = (
            "id",
            "company",
            "company_name",
            "category",
            "category_name",
            "supplier",
            "supplier_name",
            "amount",
            "day_of_month",
            "is_active",
            "remarks",
            "description",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")


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
        company = attrs.get("company") or getattr(self.instance, "company", None)
        if company and "items" in attrs:
            account_mapping.fill_blank_stock_warehouses(
                company, attrs["items"], "unprocessed_warehouse"
            )
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
    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )
    is_stock_item = serializers.BooleanField(
        source="item.is_stock_item", read_only=True
    )
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
            "is_stock_item",
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
        in_bill = attrs.get(
            "included_in_bill", inst.included_in_bill if inst else False
        )
        account = attrs.get("account", inst.account if inst else None)
        company = attrs.get("company", inst.company if inst else None)
        if in_bill and account is None:
            raise serializers.ValidationError(
                {"account": _("Choose the expense account of a charge on the bill.")}
            )
        if (
            account is not None
            and company is not None
            and account.company_id != company.pk
        ):
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
    source_journal_name = serializers.CharField(
        source="source_journal.name", read_only=True, allow_null=True
    )
    return_against_name = serializers.CharField(
        source="return_against.name", read_only=True, allow_null=True
    )
    credit_to_name = serializers.CharField(source="credit_to.name", read_only=True)
    expense_account_name = serializers.CharField(
        source="expense_account.name", read_only=True
    )
    expense_category_name = serializers.CharField(
        source="expense_category.name", read_only=True, allow_null=True
    )
    price_list_name = serializers.CharField(
        source="price_list.name", read_only=True, allow_null=True
    )
    payment_status = serializers.CharField(read_only=True)
    payment_label = serializers.CharField(read_only=True)

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
            "is_expense",
            "expense_category",
            "expense_category_name",
            "recurring_expense",
            "itc_eligible",
            "grand_total",
            *TAX_HEADER_READ_ONLY,
            *TAX_HEADER_WRITABLE,
            "outstanding_amount",
            "status",
            "is_overdue",
            "display_status",
            "payment_status",
            "payment_label",
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
            "payment_status",
            "payment_label",
            "recurring_expense",
            *TAX_HEADER_READ_ONLY,
            "created_at",
            "modified_at",
        )

    _DEFAULT_ACCOUNTS = {
        "credit_to": lambda company, attrs: account_mapping.default_payable_account(
            company
        ),
        "expense_account": lambda company, attrs: (
            attrs["expense_category"].account
            if attrs.get("expense_category")
            else account_mapping.default_expense_account(
                company, attrs.get("update_stock", True)
            )
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
                    account_mapping.financial_year_series("PRET", posting_date)
                )
            else:
                validated_data["name"] = NamingSeries.next(
                    account_mapping.financial_year_series("PINV", posting_date)
                )
        return super().create(validated_data)

    def validate(self, attrs):
        attrs = account_mapping.fill_default_accounts(
            self, attrs, self._DEFAULT_ACCOUNTS
        )
        if not attrs.get("due_date"):
            supplier = attrs.get("supplier", getattr(self.instance, "supplier", None))
            posting_date = attrs.get(
                "posting_date", getattr(self.instance, "posting_date", None)
            )
            base_date = (
                attrs.get("bill_date")
                or posting_date
                or getattr(self.instance, "bill_date", None)
            )
            if supplier and base_date and posting_date:
                from accounts.services import terms as terms_service

                calc = terms_service.calculate_due_date("Supplier", supplier, base_date)
                attrs["due_date"] = max(calc, posting_date)
        from accounts.services import terms as terms_service

        terms_service.validate_due_date(self, attrs)
        attrs = super().validate(attrs)
        company = attrs.get("company", getattr(self.instance, "company", None))
        if company and "items" in attrs:
            account_mapping.fill_blank_stock_warehouses(
                company, attrs["items"], "unprocessed_warehouse"
            )
        is_expense = attrs.get(
            "is_expense", getattr(self.instance, "is_expense", False)
        )
        expense_category = attrs.get(
            "expense_category", getattr(self.instance, "expense_category", None)
        )
        if is_expense and not expense_category:
            raise serializers.ValidationError(
                {
                    "expense_category": _(
                        "Expense category is required for expense bills."
                    )
                }
            )
        update_stock = attrs.get(
            "update_stock", getattr(self.instance, "update_stock", True)
        )
        if is_expense and update_stock:
            raise serializers.ValidationError(
                {"update_stock": _("Expenses cannot update stock.")}
            )
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
