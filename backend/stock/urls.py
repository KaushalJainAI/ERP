from django.urls import path, include
from rest_framework.routers import DefaultRouter
from stock.views import (
    UOMViewSet,
    ItemGroupViewSet,
    ItemViewSet,
    WarehouseViewSet,
    StockLedgerEntryViewSet,
    BinViewSet,
    StockEntryViewSet,
    stock_balance,
)

app_name = "stock"

router = DefaultRouter()
router.register("uoms", UOMViewSet, basename="uom")
router.register("item-groups", ItemGroupViewSet, basename="item-group")
router.register("items", ItemViewSet, basename="item")
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("sle", StockLedgerEntryViewSet, basename="sle")
router.register("bins", BinViewSet, basename="bin")
router.register("stock-entries", StockEntryViewSet, basename="stock-entry")

urlpatterns = [
    path("reports/stock-balance/", stock_balance, name="stock-balance"),
    path("", include(router.urls)),
]
