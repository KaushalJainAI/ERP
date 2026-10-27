"""
URL configuration for ERP project.
"""

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # OpenAPI Schema & Docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Auth endpoints
    path("api/auth/", include("users.urls")),
    # App routers
    path("api/core/", include("core.urls")),
    path("api/contacts/", include("contacts.urls")),
    path("api/accounts/", include("accounts.urls")),
    path("api/stock/", include("stock.urls")),
    path("api/selling/", include("selling.urls")),
    path("api/buying/", include("buying.urls")),
    path("api/gst/", include("gst.urls")),
]
