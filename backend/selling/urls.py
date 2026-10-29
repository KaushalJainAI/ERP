from django.urls import path, include
from rest_framework.routers import DefaultRouter
from selling.views import (
    CustomerGroupViewSet,
    CustomerViewSet,
    SalesOrderViewSet,
    SalesInvoiceViewSet,
    sales_analytics_overview,
    sales_conversion_analytics,
    sales_revenue_analytics,
    sales_products_analytics,
    sales_customers_analytics,
    sales_receivables_analytics,
)

app_name = "selling"

router = DefaultRouter()
router.register("customer-groups", CustomerGroupViewSet, basename="customer-group")
router.register("customers", CustomerViewSet, basename="customer")
router.register("sales-orders", SalesOrderViewSet, basename="sales-order")
router.register("sales-invoices", SalesInvoiceViewSet, basename="sales-invoice")

urlpatterns = [
    path("analytics/", sales_analytics_overview, name="sales-analytics-overview"),
    path(
        "analytics/conversion/",
        sales_conversion_analytics,
        name="sales-conversion-analytics",
    ),
    path(
        "analytics/revenue/",
        sales_revenue_analytics,
        name="sales-revenue-analytics",
    ),
    path(
        "analytics/products/",
        sales_products_analytics,
        name="sales-products-analytics",
    ),
    path(
        "analytics/customers/",
        sales_customers_analytics,
        name="sales-customers-analytics",
    ),
    path(
        "analytics/receivables/",
        sales_receivables_analytics,
        name="sales-receivables-analytics",
    ),
    path("", include(router.urls)),
]
