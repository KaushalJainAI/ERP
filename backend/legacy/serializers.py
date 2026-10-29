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
    """Detail view: the voucher as a bill, its item lines and the accounting it became."""

    item_lines = serializers.SerializerMethodField()
    ledger_lines = serializers.SerializerMethodField()

    class Meta:
        model = TallyRecord
        fields = _RECORD_FIELDS + ("raw", "item_lines", "ledger_lines")
        read_only_fields = fields

    def get_item_lines(self, obj):
        lines = obj.item_lines.select_related("item").order_by("id")
        return TallyItemLineSerializer(lines, many=True).data

    def get_ledger_lines(self, obj):
        # The TLY-* Journal Entry holds the voucher's accounting (party, sales, tax, round-off).
        from accounts.models import JournalEntryLine

        if not obj.erp_name:
            return []
        lines = (
            JournalEntryLine.objects.filter(
                journal_entry__company_id=obj.company_id,
                journal_entry__name=obj.erp_name,
            )
            .select_related("account")
            .order_by("id")
        )
        return [
            {
                "account": line.account.name,
                "account_type": line.account.account_type,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "party": line.party,
            }
            for line in lines
        ]


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
