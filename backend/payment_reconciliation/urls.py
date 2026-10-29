from django.urls import include, path
from rest_framework.routers import DefaultRouter

from payment_reconciliation.views import PaymentReconciliationViewSet

app_name = "payment_reconciliation"

router = DefaultRouter()
router.register(
    "entries", PaymentReconciliationViewSet, basename="payment-reconciliation"
)

urlpatterns = [path("", include(router.urls))]
