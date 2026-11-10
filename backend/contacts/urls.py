from django.urls import include, path
from rest_framework.routers import DefaultRouter

from contacts.views import AddressViewSet, NamesToCompleteView

app_name = "contacts"

router = DefaultRouter()
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = [
    path("names-to-complete/", NamesToCompleteView.as_view(), name="names-to-complete"),
    path("", include(router.urls)),
]
