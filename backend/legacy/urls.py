from django.urls import include, path
from rest_framework.routers import DefaultRouter

from legacy.views import TallyItemLineViewSet, TallyRecordViewSet

app_name = "legacy"

router = DefaultRouter()
router.register("tally-records", TallyRecordViewSet, basename="tally-record")
router.register("tally-item-lines", TallyItemLineViewSet, basename="tally-item-line")

urlpatterns = [
    path("", include(router.urls)),
]
