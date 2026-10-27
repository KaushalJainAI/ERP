from django.urls import path

from gst.views import supply_context, validate_gstin

app_name = "gst"

urlpatterns = [
    path("validate-gstin/", validate_gstin, name="validate-gstin"),
    path("supply-context/", supply_context, name="supply-context"),
]
