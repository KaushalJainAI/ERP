from django.urls import path, include
from rest_framework.routers import DefaultRouter
from buying.views import (
    SupplierGroupViewSet,
    SupplierViewSet,
    PurchaseOrderViewSet,
    PurchaseInvoiceViewSet,
)

app_name = "buying"

router = DefaultRouter()
router.register("supplier-groups", SupplierGroupViewSet, basename="supplier-group")
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("purchase-orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("purchase-invoices", PurchaseInvoiceViewSet, basename="purchase-invoice")

urlpatterns = [
    path("", include(router.urls)),
]
