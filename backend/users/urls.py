from django.urls import path, include
from rest_framework.routers import DefaultRouter
from users.views import (
    ChangeOwnPasswordView,
    UserViewSet,
    CurrentUserView,
    RoleViewSet,
    RolePermissionViewSet,
    ThrottledTokenObtainPairView,
    ThrottledTokenRefreshView,
)

app_name = "users"

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")
router.register("permissions", RolePermissionViewSet, basename="permission")

urlpatterns = [
    path("token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path(
        "me/change-password/", ChangeOwnPasswordView.as_view(), name="change_password"
    ),
    path("me/", CurrentUserView.as_view(), name="current_user"),
    path("", include(router.urls)),
]
