from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from tally_export.models import (
    TallyExportBatch,
    TallyExportedVoucher,
    TallyExportSettings,
    TallyLedgerMap,
    TallyStockItemMap,
)


class TallyExportSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TallyExportSettings
        fields = (
            "id",
            "company",
            "tally_company_name",
            "voucher_type_overrides",
            "excluded_prefixes",
            "modified_at",
        )
        read_only_fields = ("id", "modified_at")

    def validate_voucher_type_overrides(self, value):
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip()
            for k, v in value.items()
        ):
            raise serializers.ValidationError(
                "Expected an object of voucher key -> Tally voucher type name."
            )
        return value

    def validate_excluded_prefixes(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("Expected a list of strings.")
        return value


class TallyLedgerMapSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_type = serializers.CharField(source="account.account_type", read_only=True)

    class Meta:
        model = TallyLedgerMap
        fields = (
            "id",
            "company",
            "account",
            "account_name",
            "account_type",
            "party_type",
            "party",
            "tally_ledger",
            "tally_group",
            "source",
            "master_batch",
            "modified_at",
        )
        read_only_fields = ("id", "source", "master_batch", "modified_at")

    def validate_tally_ledger(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tally ledger name is required.")
        return value

    def validate(self, attrs):
        inst = self.instance
        merged = {
            k: attrs.get(k, getattr(inst, k, None))
            for k in ("company", "account", "party_type", "party")
        }
        if bool(merged["party_type"]) != bool(merged["party"]):
            raise serializers.ValidationError(
                "Party type and party are set together or not at all."
            )
        probe = TallyLedgerMap(**merged)
        try:
            probe.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


class TallyStockItemMapSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = TallyStockItemMap
        fields = (
            "id",
            "company",
            "item",
            "item_code",
            "item_name",
            "tally_item",
            "tally_unit",
            "source",
            "master_batch",
            "modified_at",
        )
        read_only_fields = ("id", "source", "master_batch", "modified_at")

    def validate_tally_item(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tally stock item name is required.")
        return value


class TallyExportedVoucherSerializer(serializers.ModelSerializer):
    class Meta:
        model = TallyExportedVoucher
        fields = (
            "id",
            "doctype",
            "name",
            "docstatus",
            "action",
            "tally_voucher_type",
            "posting_date",
            "remote_id",
            "voucher_hash",
        )


class TallyExportBatchSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True, default=None
    )
    has_masters = serializers.SerializerMethodField()

    class Meta:
        model = TallyExportBatch
        fields = (
            "id",
            "company",
            "kind",
            "from_date",
            "to_date",
            "source_batch",
            "created_by",
            "created_by_name",
            "created_at",
            "file_sha256",
            "masters_sha256",
            "has_masters",
            "voucher_count",
            "cancel_count",
            "ledger_master_count",
            "item_master_count",
        )
        read_only_fields = fields

    def get_has_masters(self, obj) -> bool:
        return bool(obj.masters_file)
