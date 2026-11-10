from django.urls import path, include
from rest_framework.routers import DefaultRouter
from accounts import bank_views, report_views
from accounts.cash_views import cash_in_hand
from accounts.closing_views import FiscalYearViewSet, PeriodClosingVoucherViewSet
from accounts.views import (
    AccountViewSet,
    GLEntryViewSet,
    ItemTaxTemplateViewSet,
    JournalEntryViewSet,
    PaymentEntryViewSet,
    TaxTemplateViewSet,
    TaxPaymentViewSet,
    calculate_taxes,
    trial_balance,
    general_ledger,
    profit_and_loss,
    balance_sheet,
    ageing,
    collections_view,
    bills_to_pay_view,
    expense_summary_view,
    gst_position_view,
    gst_credit_at_risk_view,
)

app_name = "accounts"

router = DefaultRouter()
router.register("accounts", AccountViewSet, basename="account")
router.register("gl-entries", GLEntryViewSet, basename="gl-entry")
router.register("journal-entries", JournalEntryViewSet, basename="journal-entry")
router.register("payment-entries", PaymentEntryViewSet, basename="payment-entry")
router.register("tax-payments", TaxPaymentViewSet, basename="tax-payment")
router.register("tax-templates", TaxTemplateViewSet, basename="tax-template")
router.register(
    "item-tax-templates", ItemTaxTemplateViewSet, basename="item-tax-template"
)
router.register("bank-accounts", bank_views.BankAccountViewSet, basename="bank-account")
router.register(
    "bank-transactions", bank_views.BankTransactionViewSet, basename="bank-transaction"
)
router.register(
    "bank-clearances", bank_views.BankClearanceViewSet, basename="bank-clearance"
)
router.register(
    "bank-statements", bank_views.BankStatementImportViewSet, basename="bank-statement"
)
router.register(
    "period-closing-vouchers",
    PeriodClosingVoucherViewSet,
    basename="period-closing-voucher",
)
router.register("fiscal-years", FiscalYearViewSet, basename="fiscal-year")

urlpatterns = [
    path("taxes/calculate/", calculate_taxes, name="calculate-taxes"),
    path("reports/trial-balance/", trial_balance, name="trial-balance"),
    path("reports/general-ledger/", general_ledger, name="general-ledger"),
    path("reports/profit-and-loss/", profit_and_loss, name="profit-and-loss"),
    path("reports/balance-sheet/", balance_sheet, name="balance-sheet"),
    path("reports/ageing/", ageing, name="ageing"),
    path("reports/expense-summary/", expense_summary_view, name="expense-summary"),
    path("reports/gst-position/", gst_position_view, name="gst-position"),
    path(
        "reports/gst-credit-at-risk/",
        gst_credit_at_risk_view,
        name="gst-credit-at-risk",
    ),
    path("cash-in-hand/", cash_in_hand, name="cash-in-hand"),
    path("collections/", collections_view, name="collections"),
    path("bills-to-pay/", bills_to_pay_view, name="bills-to-pay"),
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
    path(
        "bank-reconciliation/",
        bank_views.bank_reconciliation,
        name="bank-reconciliation",
    ),
    path(
        "bank-reconciliation/match/",
        bank_views.bank_match,
        name="bank-reconciliation-match",
    ),
    path(
        "bank-reconciliation/accept-suggestions/",
        bank_views.bank_accept_suggestions,
        name="bank-reconciliation-accept-suggestions",
    ),
    path(
        "bank-reconciliation/unmatch/",
        bank_views.bank_unmatch,
        name="bank-reconciliation-unmatch",
    ),
    path(
        "bank-reconciliation/clear/",
        bank_views.bank_clear,
        name="bank-reconciliation-clear",
    ),
    path(
        "reports/bank-reconciliation-statement/",
        bank_views.bank_reconciliation_statement,
        name="bank-reconciliation-statement",
    ),
    path("", include(router.urls)),
]
