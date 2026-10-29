from django.urls import path, include
from rest_framework.routers import DefaultRouter
from accounts import report_views
from accounts.views import (
    AccountViewSet,
    GLEntryViewSet,
    ItemTaxTemplateViewSet,
    JournalEntryViewSet,
    PaymentEntryViewSet,
    TaxTemplateViewSet,
    calculate_taxes,
    trial_balance,
    general_ledger,
    profit_and_loss,
    balance_sheet,
    ageing,
)

app_name = "accounts"

router = DefaultRouter()
router.register("accounts", AccountViewSet, basename="account")
router.register("gl-entries", GLEntryViewSet, basename="gl-entry")
router.register("journal-entries", JournalEntryViewSet, basename="journal-entry")
router.register("payment-entries", PaymentEntryViewSet, basename="payment-entry")
router.register("tax-templates", TaxTemplateViewSet, basename="tax-template")
router.register(
    "item-tax-templates", ItemTaxTemplateViewSet, basename="item-tax-template"
)

urlpatterns = [
    path("taxes/calculate/", calculate_taxes, name="calculate-taxes"),
    path("reports/trial-balance/", trial_balance, name="trial-balance"),
    path("reports/general-ledger/", general_ledger, name="general-ledger"),
    path("reports/profit-and-loss/", profit_and_loss, name="profit-and-loss"),
    path("reports/balance-sheet/", balance_sheet, name="balance-sheet"),
    path("reports/ageing/", ageing, name="ageing"),
    path("reports/sales-register/", report_views.sales_register, name="sales-register"),
    path(
        "reports/purchase-register/",
        report_views.purchase_register,
        name="purchase-register",
    ),
    path("reports/day-book/", report_views.day_book, name="day-book"),
    path("reports/cash-book/", report_views.cash_book, name="cash-book"),
    path(
        "reports/party-statement/",
        report_views.party_statement,
        name="party-statement",
    ),
    path("", include(router.urls)),
]
