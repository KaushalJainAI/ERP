from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import (
    AttachmentViewSet,
    CompanyViewSet,
    StateViewSet,
    dashboard,
    import_confirm,
    import_preview,
    import_template,
    setup_health_view,
)

app_name = "core"

router = DefaultRouter()
router.register("companies", CompanyViewSet, basename="company")
router.register("states", StateViewSet, basename="state")
router.register("attachments", AttachmentViewSet, basename="attachment")

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("setup-health/", setup_health_view, name="setup-health"),
    path("import/template/", import_template, name="import-template"),
    path("import/preview/", import_preview, name="import-preview"),
    path("import/confirm/", import_confirm, name="import-confirm"),
    path("", include(router.urls)),
]
