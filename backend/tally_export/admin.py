from django.contrib import admin

from tally_export.models import (
    TallyExportBatch,
    TallyExportedVoucher,
    TallyExportSettings,
    TallyLedgerMap,
    TallyStockItemMap,
)


@admin.register(TallyExportSettings)
class TallyExportSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "tally_company_name")


@admin.register(TallyLedgerMap)
class TallyLedgerMapAdmin(admin.ModelAdmin):
    list_display = ("account", "party_type", "party", "tally_ledger", "source")
    search_fields = ("tally_ledger", "party", "account__name")
    raw_id_fields = ("account",)


@admin.register(TallyStockItemMap)
class TallyStockItemMapAdmin(admin.ModelAdmin):
    list_display = ("item", "tally_item", "tally_unit", "source")
    search_fields = ("tally_item", "item__code")
    raw_id_fields = ("item",)


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TallyExportBatch)
class TallyExportBatchAdmin(_ReadOnly):
    list_display = ("id", "company", "created_at", "voucher_count", "cancel_count")


@admin.register(TallyExportedVoucher)
class TallyExportedVoucherAdmin(_ReadOnly):
    list_display = ("batch", "doctype", "name", "action", "tally_voucher_type")
