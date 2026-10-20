from rest_framework import generics, permissions, viewsets
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from users.models import Role, RolePermission
from users.serializers import UserSerializer, RoleSerializer, RolePermissionSerializer
from users.permissions import HasRolePermission


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """JWT login endpoint with a tight per-IP brute-force throttle (`auth` scope,
    default 10/min — see REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class ThrottledTokenRefreshView(TokenRefreshView):
    """Refresh endpoint under the same brute-force `auth` scope as login."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class CurrentUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class RoleViewSet(viewsets.ModelViewSet):
    """Role registry.

    Governed by RBAC rather than ``IsAdminUser``: role management is the
    control plane that grants authority, so it must not sit behind a flag
    (``is_staff``) that any operator can hold. Only a superuser or a holder of
    a role carrying ``users.Role`` permission (the seeded *System Manager*)
    may create, read, or edit roles.
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]


class RolePermissionViewSet(viewsets.ModelViewSet):
    """Role -> permission grants.

    Same reasoning as :class:`RoleViewSet`. Leaving this on ``IsAdminUser``
    would let any staff user grant themselves ``can_read`` on
    ``accounts.GLEntry`` and read the whole company's books.
    """

    queryset = RolePermission.objects.all()
    serializer_class = RolePermissionSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
