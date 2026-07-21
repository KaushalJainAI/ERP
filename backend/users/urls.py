from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from users.views import CurrentUserView, RoleViewSet, RolePermissionViewSet

app_name = "users"

router = DefaultRouter()
router.register("roles", RoleViewSet, basename="role")
router.register("permissions", RolePermissionViewSet, basename="permission")

urlpatterns = [
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", CurrentUserView.as_view(), name="current_user"),
    path("", include(router.urls)),
]
