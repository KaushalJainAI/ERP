from django.urls import path

from printing import views

app_name = "printing"

urlpatterns = [
    path(
        "sales-invoice/<int:pk>/",
        views.SalesInvoicePrint.as_view(),
        name="sales-invoice",
    ),
    path(
        "delivery-challan/sales-invoice/<int:pk>/",
        views.ChallanFromInvoicePrint.as_view(),
        name="challan-sales-invoice",
    ),
    path(
        "delivery-challan/sales-order/<int:pk>/",
        views.ChallanFromOrderPrint.as_view(),
        name="challan-sales-order",
    ),
    path(
        "purchase-order/<int:pk>/",
        views.PurchaseOrderPrint.as_view(),
        name="purchase-order",
    ),
    path(
        "purchase-invoice/<int:pk>/",
        views.PurchaseInvoicePrint.as_view(),
        name="purchase-invoice",
    ),
    path(
        "payment-entry/<int:pk>/",
        views.PaymentEntryPrint.as_view(),
        name="payment-entry",
    ),
]
