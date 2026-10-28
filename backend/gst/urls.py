from django.urls import path

from gst.views import (
    gstr1_report,
    gstr3b_report,
    supply_context,
    tax_ledger_report,
    validate_gstin,
)

app_name = "gst"

urlpatterns = [
    path("validate-gstin/", validate_gstin, name="validate-gstin"),
    path("supply-context/", supply_context, name="supply-context"),
    path("reports/gstr1/", gstr1_report, name="gstr1"),
    path("reports/gstr3b/", gstr3b_report, name="gstr3b"),
    path("reports/tax-ledger/", tax_ledger_report, name="tax-ledger"),
]
