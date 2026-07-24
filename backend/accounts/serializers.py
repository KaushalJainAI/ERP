from django.db import transaction
from rest_framework import serializers
from accounts.models import Account, GLEntry, JournalEntry, JournalEntryLine, PaymentEntry, PaymentEntryReference
from core.mixins import TranslatedSerializerMixin
from core.models import NamingSeries


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
        fields = ("id", "account", "account_name", "debit", "credit", "party_type", "party")


class JournalEntrySerializer(serializers.ModelSerializer):
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
        read_only_fields = ("id", "docstatus", "total_debit", "total_credit", "created_at", "modified_at")

    def create(self, validated_data):
        lines_data = validated_data.pop("lines", [])
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next("JV-2026-")
            journal_entry = JournalEntry.objects.create(**validated_data)

            total_dr, total_cr = 0, 0
            for line in lines_data:
                JournalEntryLine.objects.create(journal_entry=journal_entry, **line)
                total_dr += line.get("debit", 0)
                total_cr += line.get("credit", 0)

            journal_entry.total_debit = total_dr
            journal_entry.total_credit = total_cr
            journal_entry.save(update_fields=["total_debit", "total_credit"])
        return journal_entry


class PaymentEntryReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEntryReference
        fields = ("id", "reference_doctype", "reference_name", "allocated_amount")


class PaymentEntrySerializer(serializers.ModelSerializer):
    references = PaymentEntryReferenceSerializer(many=True, required=False)

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
            "paid_to",
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

    def create(self, validated_data):
        refs_data = validated_data.pop("references", [])
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next("PE-2026-")
            payment_entry = PaymentEntry.objects.create(**validated_data)

            for ref in refs_data:
                PaymentEntryReference.objects.create(payment_entry=payment_entry, **ref)

        return payment_entry

