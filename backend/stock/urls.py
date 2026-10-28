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
    BatchViewSet,
    PriceListViewSet,
    ItemPriceViewSet,
    item_price_rate,
    stock_balance,
    batch_wise_balance,
    expiry_report,
)

app_name = "stock"

router = DefaultRouter()
router.register("uoms", UOMViewSet, basename="uom")
router.register("item-groups", ItemGroupViewSet, basename="item-group")
router.register("items", ItemViewSet, basename="item")
router.register("batches", BatchViewSet, basename="batch")
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("price-lists", PriceListViewSet, basename="price-list")
router.register("item-prices", ItemPriceViewSet, basename="item-price")
router.register("sle", StockLedgerEntryViewSet, basename="sle")
router.register("bins", BinViewSet, basename="bin")
router.register("stock-entries", StockEntryViewSet, basename="stock-entry")

urlpatterns = [
    path("item-prices/rate/", item_price_rate, name="item-price-rate"),
    path("reports/stock-balance/", stock_balance, name="stock-balance"),
    path(
        "reports/batch-wise-balance/",
        batch_wise_balance,
        name="batch-wise-balance",
    ),
    path("reports/expiry/", expiry_report, name="expiry-report"),
    path("", include(router.urls)),
]
