from django.urls import include, path
from rest_framework.routers import DefaultRouter

from ngu.views import NguCatalogEntryViewSet

app_name = "ngu"

router = DefaultRouter()
router.register("catalog", NguCatalogEntryViewSet, basename="ngu-catalog")

urlpatterns = [path("", include(router.urls))]
