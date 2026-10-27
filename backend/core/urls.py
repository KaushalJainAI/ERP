from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import CompanyViewSet, dashboard

app_name = "core"

router = DefaultRouter()
router.register("companies", CompanyViewSet, basename="company")

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("", include(router.urls)),
]
