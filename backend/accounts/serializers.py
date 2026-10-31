from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator
from accounts.models import (
    Account,
    AccountType,
    GLEntry,
    ItemTaxTemplate,
    ItemTaxTemplateRow,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
    TaxTemplate,
    TaxTemplateRow,
    TaxPayment,
)
from accounts.mapping import (
    default_bank_or_cash_account,
    infer_party_type,
    party_account_for,
)
from core.mixins import (
    DraftNestedWriteMixin,
    NestedMasterWriteMixin,
    TranslatedSerializerMixin,
)
from core.models import Company, State
from stock.models import Item
from gst.constants import (
    AddDeduct,
    ChargeType,
    GSTDirection,
    TaxCategory,
    TemplateType,
)


class AccountSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Account
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "parent",
            "parent_name",
            "root_type",
            "account_type",
            "account_number",
            "is_group",
            "is_active",
            "gst_direction",
            "gst_component",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]
        # Declare only the real (company, name) rule: DRF 3.15 would also turn the
        # *conditional* GST-tag unique constraint into an unconditional unique-together
        # that makes the (nullable) tag fields required. The tag rule is checked in
        # validate() and by the partial unique index.
        validators = [
            UniqueTogetherValidator(
                queryset=Account.objects.all(), fields=("company", "name")
            )
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inst = self.instance

        def current(name):
            return attrs[name] if name in attrs else getattr(inst, name, None)

        direction, component = current("gst_direction"), current("gst_component")
        if (direction is None) != (component is None):
            missing = "gst_component" if direction else "gst_direction"
            raise serializers.ValidationError(
                {missing: _("GST direction and GST component must be set together.")}
            )
        if direction is not None and current("account_type") != AccountType.TAX:
            raise serializers.ValidationError(
                {"account_type": _("A GST-tagged account must have type 'Tax'.")}
            )
        if direction is not None:
            clash = Account.objects.filter(
                company=current("company"),
                gst_direction=direction,
                gst_component=component,
            )
            if inst is not None:
                clash = clash.exclude(pk=inst.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {
                        "gst_component": _(
                            "This company already has a %(direction)s %(component)s account."
                        )
                        % {"direction": direction, "component": component}
                    }
                )
        return attrs


class GLEntrySerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = GLEntry
        fields = (
            "id",
            "posting_date",
            "account",
            "account_name",
            "company",
            "party_type",
            "party",
            "debit",
            "credit",
            "voucher_type",
            "voucher_no",
            "against_voucher_type",
            "against_voucher_no",
            "is_cancelled",
            "fiscal_year",
            "remarks",
            "created_at",
        )
        read_only_fields = fields


class JournalEntryLineSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = JournalEntryLine
        fields = (
            "id",
            "account",
            "account_name",
            "debit",
            "credit",
            "party_type",
            "party",
        )


class JournalEntrySerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "lines"
    name_prefix = "JV-2026-"

    lines = JournalEntryLineSerializer(many=True)

    class Meta:
        model = JournalEntry
        fields = (
            "id",
            "name",
            "posting_date",
            "company",
            "docstatus",
            "total_debit",
            "total_credit",
            "user_remark",
            "voucher_type",
            "cheque_no",
            "cheque_date",
            "bill_no",
            "bill_date",
            "amended_from",
            "lines",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "total_debit",
            "total_credit",
            "created_at",
            "modified_at",
        )


class PaymentEntryReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEntryReference
        fields = ("id", "reference_doctype", "reference_name", "allocated_amount")
        extra_kwargs = {
            "allocated_amount": {"min_value": Decimal("0.01")},
        }


class PaymentEntrySerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "references"
    name_prefix = "PE-2026-"

    references = PaymentEntryReferenceSerializer(many=True, required=False)
    paid_from_name = serializers.CharField(source="paid_from.name", read_only=True)
    paid_to_name = serializers.CharField(source="paid_to.name", read_only=True)

    class Meta:
        model = PaymentEntry
        fields = (
            "id",
            "name",
            "payment_type",
            "posting_date",
            "company",
            "party_type",
            "party_name",
            "paid_from",
            "paid_from_name",
            "paid_to",
            "paid_to_name",
            "paid_amount",
            "received_amount",
            "reference_no",
            "docstatus",
            "amended_from",
            "references",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "created_at", "modified_at")
        # Operators only enter the party, the amount and the bank / cash account;
        # the other leg, the party type and the received amount are filled in
        # ``validate`` (ERPNext's ``set_missing_values``).
        extra_kwargs = {
            "party_type": {"required": False, "allow_null": True, "allow_blank": True},
            "paid_from": {"required": False, "allow_null": True},
            "paid_to": {"required": False, "allow_null": True},
            "received_amount": {"required": False, "allow_null": True},
        }

    def _fill_missing(self, attrs):
        """Fill blank party type / accounts / received amount (create, or on update
        only the keys explicitly sent as null)."""
        inst = self.instance

        def current(name):
            return attrs[name] if name in attrs else getattr(inst, name, None)

        def blank(name):
            if name in attrs:
                return attrs[name] in (None, "")
            return inst is None

        payment_type = current("payment_type")
        company = current("company")
        party_name = current("party_name")
        errors = {}

        if blank("party_type"):
            if payment_type in (PaymentType.RECEIVE, PaymentType.PAY) and party_name:
                attrs["party_type"] = infer_party_type(
                    company, payment_type, party_name
                )
            else:
                attrs["party_type"] = None
        party_type = current("party_type")

        need_from, need_to = blank("paid_from"), blank("paid_to")
        if payment_type == PaymentType.INTERNAL_TRANSFER:
            for name, needed in (("paid_from", need_from), ("paid_to", need_to)):
                if needed:
                    errors[name] = _("This field is required for an internal transfer.")
        elif need_from or need_to:
            if not party_name:
                errors["party_name"] = _(
                    "Select the party, or choose the paid from and paid to accounts."
                )
            else:
                party_account = self._party_account(
                    company, party_type, attrs.get("references")
                )
                if party_account is None:
                    errors["party_type"] = _(
                        "Choose the paid from and paid to accounts for a "
                        "%(party_type)s payment."
                    ) % {"party_type": party_type or _("this")}
                else:
                    try:
                        bank = default_bank_or_cash_account(company)
                    except DjangoValidationError as exc:
                        bank_field = (
                            "paid_to"
                            if payment_type == PaymentType.RECEIVE
                            else "paid_from"
                        )
                        errors[bank_field] = exc.messages[0]
                        bank = None
                    if bank is not None:
                        receive = payment_type == PaymentType.RECEIVE
                        if need_from:
                            attrs["paid_from"] = party_account if receive else bank
                        if need_to:
                            attrs["paid_to"] = bank if receive else party_account
        if errors:
            raise serializers.ValidationError(errors)

        if blank("received_amount"):
            attrs["received_amount"] = current("paid_amount") or Decimal("0.00")
        elif (
            inst is not None
            and "received_amount" not in attrs
            and "paid_amount" in attrs
            and inst.received_amount == inst.paid_amount
        ):
            # Same currency: keep the received amount in step with the paid amount.
            attrs["received_amount"] = attrs["paid_amount"]

        references = attrs.get("references")
        if references is not None:
            comp = current("company")
            total_allocated = Decimal("0.00")
            for row in references:
                ref_doc = row.get("reference_doctype")
                ref_name = row.get("reference_name")
                alloc_amt = row.get("allocated_amount") or Decimal("0.00")
                total_allocated += alloc_amt
                if ref_doc in PaymentEntry.ALLOCATION_TARGETS and ref_name and comp:
                    inv_model = PaymentEntry.invoice_model(ref_doc)
                    inv = inv_model.objects.filter(name=ref_name, company=comp).first()
                    if inv:
                        from accounts.services import outstanding as outstanding_service

                        open_amt = outstanding_service.current_outstanding(inv)
                        if inv.is_return:
                            open_amt = -open_amt
                        if alloc_amt > open_amt:
                            raise serializers.ValidationError(
                                {
                                    "references": _(
                                        "Allocated amount (%(allocated)s) exceeds the outstanding "
                                        "amount (%(outstanding)s) of '%(name)s'."
                                    )
                                    % {
                                        "allocated": alloc_amt,
                                        "outstanding": open_amt,
                                        "name": ref_name,
                                    }
                                }
                            )
            paid_amount = current("paid_amount") or Decimal("0.00")
            if total_allocated > paid_amount:
                raise serializers.ValidationError(
                    {
                        "references": _(
                            "Total allocated (%(allocated)s) exceeds the paid amount (%(paid)s)."
                        )
                        % {"allocated": total_allocated, "paid": paid_amount}
                    }
                )
        return attrs

    @staticmethod
    def _party_account(company, party_type, references):
        """The party-side control account. An allocated invoice's (or opening bill's)
        own booked account wins (the allocation check requires them to match); else
        the company default for the party type."""
        for row in references or ():
            doctype = row.get("reference_doctype")
            if doctype not in PaymentEntry.ALLOCATION_TARGETS:
                continue
            document = (
                PaymentEntry.invoice_model(doctype)
                .objects.filter(name=row.get("reference_name"), company=company)
                .first()
            )
            flow = (
                PaymentEntry.target_flow(doctype, document)
                if document is not None
                else None
            )
            if flow is None or flow[1] != party_type:
                continue
            return getattr(document, flow[2])
        try:
            return party_account_for(company, party_type)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"paid_from": exc.messages[0]})

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return self._fill_missing(attrs)


# --------------------------------------------------------------------------- GST


class ItemTaxTemplateRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemTaxTemplateRow
        fields = ("id", "gst_component", "tax_rate")


class ItemTaxTemplateSerializer(NestedMasterWriteMixin, serializers.ModelSerializer):
    children_field = "rows"

    rows = ItemTaxTemplateRowSerializer(many=True, required=False)

    class Meta:
        model = ItemTaxTemplate
        fields = (
            "id",
            "name",
            "gst_treatment",
            "is_active",
            "is_legacy",
            "rows",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")

    def validate_rows(self, rows):
        components = [r["gst_component"] for r in rows]
        if len(components) != len(set(components)):
            raise serializers.ValidationError(
                _("Each GST component may appear only once per template.")
            )
        return rows


class TaxTemplateRowSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)
    gst_direction = serializers.CharField(
        source="account.gst_direction", read_only=True, allow_null=True
    )
    gst_component = serializers.CharField(
        source="account.gst_component", read_only=True, allow_null=True
    )

    class Meta:
        model = TaxTemplateRow
        fields = (
            "id",
            "idx",
            "charge_type",
            "account",
            "account_name",
            "gst_direction",
            "gst_component",
            "description",
            "rate",
            "add_deduct_tax",
            "category",
        )
        read_only_fields = ("idx",)


class TaxTemplateSerializer(NestedMasterWriteMixin, serializers.ModelSerializer):
    children_field = "rows"

    rows = TaxTemplateRowSerializer(many=True, required=False)
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = TaxTemplate
        fields = (
            "id",
            "company",
            "company_name",
            "title",
            "template_type",
            "gst_supply_type",
            "is_reverse_charge",
            "is_default",
            "disabled",
            "rows",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")
        # DRF 3.15 turns the *conditional* "one default per slot" constraint into an
        # unconditional unique-together check, which would forbid any second template
        # with the same key. Declare only the real (company, title) rule here; the
        # default-slot rule is enforced in validate() and by the partial unique index.
        validators = [
            UniqueTogetherValidator(
                queryset=TaxTemplate.objects.all(), fields=("company", "title")
            )
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inst = self.instance

        def current(name):
            return attrs[name] if name in attrs else getattr(inst, name, None)

        company = current("company")
        template_type = current("template_type")

        if template_type == TemplateType.SALES and current("is_reverse_charge"):
            raise serializers.ValidationError(
                {
                    "is_reverse_charge": _(
                        "Reverse charge applies to purchase templates only."
                    )
                }
            )

        # Validate the rows being sent, else the stored ones (so changing the
        # company or type of an existing template re-checks them).
        if "rows" in attrs:
            rows = attrs["rows"]
        elif inst is not None:
            rows = [{"account": r.account} for r in inst.rows.select_related("account")]
        else:
            rows = []
        want_direction = (
            GSTDirection.OUTPUT
            if template_type == TemplateType.SALES
            else GSTDirection.INPUT
        )
        errors = []
        seen = set()
        for row in rows:
            account = row["account"]
            problems = []
            if account.company_id != getattr(company, "pk", None):
                problems.append(_("Account must belong to the template's company."))
            if account.is_group:
                problems.append(_("Account must not be a group account."))
            if account.account_type != AccountType.TAX:
                problems.append(_("Account must be of type 'Tax'."))
            if account.gst_direction and account.gst_direction != want_direction:
                problems.append(
                    _("A %(type)s template needs %(direction)s GST accounts.")
                    % {"type": template_type, "direction": want_direction}
                )
            if account.pk in seen:
                problems.append(_("Account is used twice in this template."))
            seen.add(account.pk)
            errors.append({"account": problems} if problems else {})
        if any(errors):
            raise serializers.ValidationError({"rows": errors})

        # Only one default per (company, type, supply type, reverse charge).
        if current("is_default"):
            clash = TaxTemplate.objects.filter(
                company=company,
                template_type=template_type,
                gst_supply_type=current("gst_supply_type") or "",
                is_reverse_charge=bool(current("is_reverse_charge")),
                is_default=True,
            )
            if inst is not None:
                clash = clash.exclude(pk=inst.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {
                        "is_default": _(
                            "Another default template already exists for this "
                            "company, type, supply type and reverse-charge setting."
                        )
                    }
                )
        return attrs


# ------------------------------------------------------- taxable documents

#: Header fields every taxable document serializer adds (SO / SI / PO / PI).
TAX_HEADER_WRITABLE = (
    "taxes_and_charges",
    "place_of_supply",
    "is_export_with_gst",
    "is_reverse_charge",
)
TAX_HEADER_READ_ONLY = (
    "gst_category",
    "party_gstin",
    "company_gstin",
    "net_total",
    "total_taxes_and_charges",
    "rounding_adjustment",
    "rounded_total",
)
#: GST columns of a document line (all server-owned).
LINE_GST_FIELDS = (
    "gst_hsn_code",
    "gst_treatment",
    "item_tax_template",
    "item_tax_rate",
    "gst_rate",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
)


class TaxRowSerializerBase(serializers.ModelSerializer):
    """A document tax row. Inputs: ``charge_type``, ``account``, ``description``,
    ``rate``, ``add_deduct_tax``, ``category``. ``net_amount`` / ``tax_amount`` /
    ``total`` and ``idx`` are calculated by the server."""

    account_name = serializers.CharField(source="account.name", read_only=True)
    gst_direction = serializers.CharField(
        source="account.gst_direction", read_only=True, allow_null=True
    )
    gst_component = serializers.CharField(
        source="account.gst_component", read_only=True, allow_null=True
    )
    rate = serializers.DecimalField(
        max_digits=7,
        decimal_places=3,
        default=Decimal("0"),
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    )

    class Meta:
        fields = (
            "id",
            "idx",
            "charge_type",
            "account",
            "account_name",
            "gst_direction",
            "gst_component",
            "description",
            "rate",
            "add_deduct_tax",
            "category",
            "net_amount",
            "tax_amount",
            "total",
        )
        read_only_fields = ("idx", "net_amount", "tax_amount", "total")
        # The (parent, idx) / (parent, account) uniqueness is checked by the
        # document serializer (the parent FK is not a field here).
        validators = []


def tax_row_serializer(model_class):
    """Build the nested tax-row serializer for one concrete tax-row model."""

    class _Meta(TaxRowSerializerBase.Meta):
        model = model_class

    return type(
        f"{model_class.__name__}Serializer",
        (TaxRowSerializerBase,),
        {"Meta": _Meta, "__module__": __name__},
    )


class TaxableSerializerMixin:
    """Serializer mixin for SO / SI / PO / PI (put before ``DraftNestedWriteMixin``).

    Adds the tax table as a second nested child, checks it for duplicate accounts and
    forgets the stored place of supply when the party changes (so it is defaulted for
    the new party).
    """

    extra_children_fields = ("taxes",)

    def validate_taxes(self, rows):
        accounts = [r["account"].pk for r in rows if "account" in r]
        if len(accounts) != len(set(accounts)):
            raise serializers.ValidationError(
                _("Each tax account may appear only once in the tax table.")
            )
        return rows

    def update(self, instance, validated_data):
        party_field = self.Meta.model.party_field
        if (
            "place_of_supply" not in validated_data
            and party_field in validated_data
            and validated_data[party_field] != getattr(instance, party_field)
        ):
            validated_data["place_of_supply"] = None
        return super().update(instance, validated_data)


class SignedQtyLineItemMixin:
    """Line serializer mixin of an invoice that may be a return: the quantity must
    not be zero. Its sign (negative on a return, positive otherwise) depends on the
    header, so ``ReturnSerializerMixin.validate`` checks it."""

    def validate_qty(self, value):
        if value is None or value == 0:
            raise serializers.ValidationError(_("Quantity must not be zero."))
        return value


#: Header fields of an invoice that can be a return (Sales / Purchase Invoice).
RETURN_HEADER_FIELDS = (
    "is_return",
    "return_against",
    "return_against_name",
    "update_outstanding_for_self",
)


class ReturnSerializerMixin:
    """Serializer mixin for Sales / Purchase Invoice (put before
    ``TaxableSerializerMixin``): ``is_return`` / ``return_against`` and the sign
    convention (a return has negative quantities, an invoice positive ones).

    The server re-validates all of it at submit (``accounts.services.returns``) and
    the database CHECKs the header; this only turns the obvious mistakes into a 400
    on save. A return is auto-numbered from ``return_name_prefix``.
    """

    return_name_prefix = ""

    def validate(self, attrs):
        from accounts.services import returns as returns_service

        attrs = super().validate(attrs)
        instance = self.instance
        is_return = attrs.get("is_return", instance.is_return if instance else False)
        if (
            instance is not None
            and "is_return" in attrs
            and attrs["is_return"] != instance.is_return
        ):
            raise serializers.ValidationError(
                {"is_return": _("A document cannot be converted to or from a return.")}
            )
        against = attrs.get(
            "return_against", instance.return_against if instance else None
        )
        if against is not None:
            if not is_return:
                raise serializers.ValidationError(
                    {"return_against": _("Only a return can point at an original.")}
                )
            if instance is not None and against.pk == instance.pk:
                raise serializers.ValidationError(
                    {
                        "return_against": _(
                            "A document cannot be returned against itself."
                        )
                    }
                )
        link = returns_service.link_field(self.Meta.model)
        for index, row in enumerate(attrs.get("items") or [], start=1):
            qty = row.get("qty")
            if qty is not None and ((qty < 0) != is_return):
                raise serializers.ValidationError(
                    {
                        "items": _(
                            "Row %(n)s: the quantity must be negative in a return "
                            "and positive otherwise."
                        )
                        % {"n": index}
                    }
                )
            original_line = row.get(link)
            if original_line is not None and (
                against is None
                or getattr(original_line, self.Meta.model.item_fk_name()).pk
                != against.pk
            ):
                raise serializers.ValidationError(
                    {
                        "items": _("Row %(n)s: the original row is not on %(name)s.")
                        % {"n": index, "name": against.name if against else "-"}
                    }
                )
        return attrs

    def create(self, validated_data):
        from core.models import NamingSeries

        if validated_data.get("is_return") and not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next(self.return_name_prefix)
        return super().create(validated_data)


# ------------------------------------------------------- dry-run calculation


class TaxCalcItemSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=Item.objects.all())
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0")
    )
    rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0")
    )

    def validate_qty(self, value):
        if value <= 0:
            raise serializers.ValidationError(_("Quantity must be greater than zero."))
        return value


class TaxCalcRowSerializer(serializers.Serializer):
    account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.all())
    rate = serializers.DecimalField(
        max_digits=7,
        decimal_places=3,
        default=Decimal("0"),
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    )
    charge_type = serializers.ChoiceField(
        choices=ChargeType.choices, default=ChargeType.ON_NET_TOTAL
    )
    add_deduct_tax = serializers.ChoiceField(choices=AddDeduct.choices, default="Add")
    category = serializers.ChoiceField(
        choices=TaxCategory.choices, default=TaxCategory.TOTAL
    )
    description = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )


class TaxCalculateSerializer(serializers.Serializer):
    """Request of ``POST /api/accounts/taxes/calculate/`` (a stateless dry run)."""

    DOCTYPES = (
        "selling.SalesOrder",
        "selling.SalesInvoice",
        "buying.PurchaseOrder",
        "buying.PurchaseInvoice",
    )

    doctype = serializers.ChoiceField(choices=[(d, d) for d in DOCTYPES])
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())
    party = serializers.IntegerField()
    posting_date = serializers.DateField(required=False, allow_null=True)
    place_of_supply = serializers.PrimaryKeyRelatedField(
        queryset=State.objects.all(), required=False, allow_null=True
    )
    is_export_with_gst = serializers.BooleanField(default=False)
    is_reverse_charge = serializers.BooleanField(default=False)
    taxes_and_charges = serializers.PrimaryKeyRelatedField(
        queryset=TaxTemplate.objects.all(), required=False, allow_null=True
    )
    items = TaxCalcItemSerializer(many=True, allow_empty=False)
    taxes = TaxCalcRowSerializer(many=True, required=False)

    def validate_taxes(self, rows):
        accounts = [r["account"].pk for r in rows]
        if len(accounts) != len(set(accounts)):
            raise serializers.ValidationError(
                _("Each tax account may appear only once in the tax table.")
            )
        return rows


class TaxPaymentSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    name_prefix = "TAX-2026-"

    paid_from_name = serializers.CharField(source="paid_from.name", read_only=True)
    journal_entry_name = serializers.CharField(
        source="journal_entry.name", read_only=True
    )

    class Meta:
        model = TaxPayment
        fields = (
            "id",
            "name",
            "company",
            "tax_type",
            "period",
            "posting_date",
            "challan_no",
            "paid_from",
            "paid_from_name",
            "igst_cash",
            "cgst_cash",
            "sgst_cash",
            "igst_credit_used",
            "cgst_credit_used",
            "sgst_credit_used",
            "tds_amount",
            "journal_entry",
            "journal_entry_name",
            "docstatus",
            "amended_from",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "journal_entry",
            "created_at",
            "modified_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        period = attrs.get("period")
        if period and period.day != 1:
            attrs["period"] = period.replace(day=1)
        return attrs
