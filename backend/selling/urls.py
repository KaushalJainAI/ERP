from django.urls import path, include
from rest_framework.routers import DefaultRouter
from selling.views import (
    CustomerGroupViewSet,
    CustomerViewSet,
    SalesOrderViewSet,
    SalesInvoiceViewSet,
)

app_name = "selling"

router = DefaultRouter()
router.register("customer-groups", CustomerGroupViewSet, basename="customer-group")
router.register("customers", CustomerViewSet, basename="customer")
router.register("sales-orders", SalesOrderViewSet, basename="sales-order")
router.register("sales-invoices", SalesInvoiceViewSet, basename="sales-invoice")

urlpatterns = [
    path("", include(router.urls)),
]
