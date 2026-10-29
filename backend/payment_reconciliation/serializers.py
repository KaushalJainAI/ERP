from decimal import Decimal

from rest_framework import serializers

from payment_reconciliation.models import (
    PartyType,
    PaymentReconciliation,
    PaymentReconciliationAllocation,
)


class AllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReconciliationAllocation
        fields = [
            "id",
            "source_doctype",
            "source_name",
            "invoice_doctype",
            "invoice_name",
            "amount",
        ]
        read_only_fields = fields


class PaymentReconciliationSerializer(serializers.ModelSerializer):
    allocations = AllocationSerializer(many=True, read_only=True)
    party_account_name = serializers.CharField(
        source="party_account.name", read_only=True
    )

    class Meta:
        model = PaymentReconciliation
        fields = [
            "id",
            "name",
            "docstatus",
            "company",
            "posting_date",
            "party_type",
            "party_name",
            "party_account",
            "party_account_name",
            "total_allocated",
            "remarks",
            "allocations",
            "created_at",
            "modified_at",
        ]
        read_only_fields = fields


class PartyQuerySerializer(serializers.Serializer):
    party_type = serializers.ChoiceField(choices=PartyType.choices)
    party = serializers.CharField()
    from_date = serializers.DateField(required=False, allow_null=True)
    to_date = serializers.DateField(required=False, allow_null=True)
    include_imported = serializers.BooleanField(required=False, default=False)


class PickSerializer(serializers.Serializer):
    doctype = serializers.CharField()
    name = serializers.CharField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )


class AllocatePreviewSerializer(serializers.Serializer):
    party_type = serializers.ChoiceField(choices=PartyType.choices)
    party = serializers.CharField()
    payments = PickSerializer(many=True)
    invoices = PickSerializer(many=True)


class AllocationInputSerializer(serializers.Serializer):
    source_doctype = serializers.CharField()
    source_name = serializers.CharField()
    invoice_doctype = serializers.CharField()
    invoice_name = serializers.CharField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )


class ReconcileSerializer(serializers.Serializer):
    party_type = serializers.ChoiceField(choices=PartyType.choices)
    party = serializers.CharField()
    posting_date = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    allocations = AllocationInputSerializer(many=True, allow_empty=False)
