from django.urls import path, include
from rest_framework.routers import DefaultRouter
from accounts.views import (
    AccountViewSet,
    GLEntryViewSet,
    ItemTaxTemplateViewSet,
    JournalEntryViewSet,
    PaymentEntryViewSet,
    TaxTemplateViewSet,
    trial_balance,
    general_ledger,
    profit_and_loss,
    balance_sheet,
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
    path("reports/trial-balance/", trial_balance, name="trial-balance"),
    path("reports/general-ledger/", general_ledger, name="general-ledger"),
    path("reports/profit-and-loss/", profit_and_loss, name="profit-and-loss"),
    path("reports/balance-sheet/", balance_sheet, name="balance-sheet"),
    path("", include(router.urls)),
]
