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
    TaxTemplate,
    TaxTemplateRow,
)
from core.mixins import (
    DraftNestedWriteMixin,
    NestedMasterWriteMixin,
    TranslatedSerializerMixin,
)
from gst.constants import GSTDirection, TemplateType


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
