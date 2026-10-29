from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import AttachmentViewSet, CompanyViewSet, StateViewSet, dashboard

app_name = "core"

router = DefaultRouter()
router.register("companies", CompanyViewSet, basename="company")
router.register("states", StateViewSet, basename="state")
router.register("attachments", AttachmentViewSet, basename="attachment")

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("", include(router.urls)),
]
