from django.urls import include, path
from rest_framework.routers import DefaultRouter

from tally_export.views import (
    TallyExportBatchViewSet,
    TallyExportedVoucherViewSet,
    TallyExportSettingsViewSet,
    TallyLedgerMapViewSet,
    TallyStockItemMapViewSet,
)

app_name = "tally_export"

router = DefaultRouter()
router.register("settings", TallyExportSettingsViewSet, basename="tally-settings")
router.register("ledger-maps", TallyLedgerMapViewSet, basename="tally-ledger-map")
router.register("item-maps", TallyStockItemMapViewSet, basename="tally-item-map")
router.register("batches", TallyExportBatchViewSet, basename="tally-batch")
router.register(
    "exported-vouchers", TallyExportedVoucherViewSet, basename="tally-exported-voucher"
)

urlpatterns = [
    path("", include(router.urls)),
]
