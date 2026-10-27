from rest_framework import serializers
from accounts.models import (
    Account,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
)
from core.mixins import DraftNestedWriteMixin, TranslatedSerializerMixin


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
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name"]


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
