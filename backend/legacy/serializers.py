from rest_framework import serializers

from legacy.models import TallyItemLine, TallyRecord

_RECORD_FIELDS = (
    "id",
    "kind",
    "company",
    "company_year",
    "fiscal_year",
    "guid",
    "name",
    "voucher_type",
    "base_voucher_type",
    "voucher_no",
    "posting_date",
    "party",
    "amount",
    "reference",
    "narration",
    "is_cancelled",
    "erp_links",
    "erp_name",
    "created_at",
)


class TallyRecordSerializer(serializers.ModelSerializer):
    """List view: everything but the raw XML (up to ~20 KB per record)."""

    class Meta:
        model = TallyRecord
        fields = _RECORD_FIELDS
        read_only_fields = _RECORD_FIELDS


class TallyRecordDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = TallyRecord
        fields = _RECORD_FIELDS + ("raw",)
        read_only_fields = fields


class TallyItemLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True, default=None)

    class Meta:
        model = TallyItemLine
        fields = (
            "id",
            "company",
            "record",
            "fiscal_year",
            "posting_date",
            "voucher_type",
            "voucher_no",
            "party",
            "customer",
            "supplier",
            "item_name",
            "item",
            "item_code",
            "is_inward",
            "qty",
            "billed_qty",
            "uom",
            "rate",
            "discount_percent",
            "amount",
            "ledger",
            "place_of_supply",
        )
        read_only_fields = fields
