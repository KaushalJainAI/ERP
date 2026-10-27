from django.urls import path

from gst.views import validate_gstin

app_name = "gst"

urlpatterns = [
    path("validate-gstin/", validate_gstin, name="validate-gstin"),
]
