from django.urls import include, path
from rest_framework.routers import DefaultRouter

from manufacturing.views import (
    BOMViewSet,
    WorkOrderViewSet,
    bom_cost_report,
    production_yield,
)

router = DefaultRouter()
router.register("boms", BOMViewSet, basename="bom")
router.register("work-orders", WorkOrderViewSet, basename="workorder")

urlpatterns = [
    path("reports/production-yield/", production_yield, name="production-yield"),
    path("reports/bom-cost/", bom_cost_report, name="bom-cost"),
    path("", include(router.urls)),
]
