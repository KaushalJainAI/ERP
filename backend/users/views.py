from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from core.mixins import AuditLogMixin, record_audit_log
from users.auth import (
    SessionAwareTokenRefreshSerializer,
    issue_tokens,
    revoke_all_sessions,
)
from users.models import Role, RolePermission, User
from users.serializers import (
    ChangePasswordSerializer,
    RolePermissionSerializer,
    RoleSerializer,
    SetPasswordSerializer,
    UserAdminSerializer,
    UserSerializer,
)
from users.permissions import HasRolePermission


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """JWT login endpoint with a tight per-IP brute-force throttle (`auth` scope,
    default 10/min — see REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class ThrottledTokenRefreshView(TokenRefreshView):
    """Refresh endpoint under the same brute-force `auth` scope as login.
    Refuses inactive users and sessions revoked by ``tokens_valid_after``."""

    serializer_class = SessionAwareTokenRefreshSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class LogoutView(APIView):
    """POST /api/auth/logout/ ``{refresh}`` -- sign this device out.

    Blacklists the refresh token so a copy of it cannot mint new access tokens.
    Open to anonymous callers (the access token may already have expired);
    it can only revoke a token the caller already holds. Always 200, so it
    does not reveal whether a token was valid."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        raw = request.data.get("refresh")
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass
        return Response({"detail": _("Signed out.")})


class LogoutAllView(APIView):
    """POST /api/auth/me/logout-all/ -- sign out on every device (lost phone)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        with transaction.atomic():
            revoke_all_sessions(request.user)
            record_audit_log(
                request.user,
                "update",
                request.user,
                changes={"sessions": "signed out everywhere"},
            )
        return Response({"detail": _("Signed out on all devices.")})


class CurrentUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangeOwnPasswordView(APIView):
    """POST /api/auth/me/change-password/ -- the signed-in user changes their own
    password. Needs the old password (a stolen access token alone is not enough)
    and is under the same brute-force ``auth`` throttle as login."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            request.user.set_password(ser.validated_data["new_password"])
            request.user.save(update_fields=["password", "modified_at"])
            # Other devices (and any stolen token) are signed out; this device
            # continues with the fresh pair returned below.
            revoke_all_sessions(request.user)
            record_audit_log(
                request.user,
                "update",
                request.user,
                changes={"password": "changed (self-service)"},
            )
        return Response(
            {"detail": _("Password changed."), **issue_tokens(request.user)}
        )


class UserViewSet(viewsets.ModelViewSet):
    """User management, RBAC-gated on ``users.User`` (role assignment is further
    gated on ``users.UserRole``): only a superuser or a System Manager.

    * No hard delete (``DELETE`` is 405): users are referenced by audit rows, so
      they are deactivated instead (``deactivate`` / ``activate``), as in ERPNext.
    * ``PUT`` is treated as ``PATCH`` (the generic form sends the editable subset).
    * Non-superusers can never touch a superuser account (otherwise a System
      Manager could reset a superuser's password and take the account over).
    * Nobody can deactivate themselves, change their own roles, or set their own
      password through the admin action (use ``me/change-password``).
    """

    queryset = User.objects.prefetch_related("user_roles__role").order_by("username")
    serializer_class = UserAdminSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    search_fields = ("username", "email", "first_name", "last_name")
    filterset_fields = ("is_active", "language")
    ordering_fields = ("username", "email", "date_joined", "last_login", "is_active")
    # These POST actions edit an existing user: they need ``can_write``, not
    # ``can_create`` (which would let a create-only role reset passwords).
    action_permission_flags = {
        "set_password": "can_write",
        "deactivate": "can_write",
        "activate": "can_write",
    }

    def get_object(self):
        obj = super().get_object()
        if (
            self.request.method not in permissions.SAFE_METHODS
            and obj.is_superuser
            and not self.request.user.is_superuser
        ):
            raise PermissionDenied(
                _("Only a superuser can modify a superuser account.")
            )
        return obj

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    _AUDITED = ("email", "first_name", "last_name", "language", "is_active")

    def _snapshot(self, user):
        snap = {f: getattr(user, f) for f in self._AUDITED}
        snap["role_ids"] = sorted(user.user_roles.values_list("role_id", flat=True))
        return snap

    def perform_create(self, serializer):
        with transaction.atomic():
            user = serializer.save()
            record_audit_log(
                self.request.user,
                "create",
                user,
                changes={**self._snapshot(user), "username": user.username},
            )

    def perform_update(self, serializer):
        with transaction.atomic():
            before = self._snapshot(serializer.instance)
            user = serializer.save()
            after = self._snapshot(user)
            changes = {
                k: {"from": before[k], "to": after[k]}
                for k in after
                if before[k] != after[k]
            }
            if before["is_active"] and not after["is_active"]:
                revoke_all_sessions(user)
            record_audit_log(self.request.user, "update", user, changes=changes)

    def _set_active(self, request, active):
        user = self.get_object()
        if not active and user.pk == request.user.pk:
            raise ValidationError({"detail": _("You cannot deactivate yourself.")})
        with transaction.atomic():
            if user.is_active != active:
                user.is_active = active
                user.save(update_fields=["is_active", "modified_at"])
                if not active:
                    revoke_all_sessions(user)
                record_audit_log(
                    request.user,
                    "update",
                    user,
                    changes={"is_active": {"from": not active, "to": active}},
                )
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        return self._set_active(request, True)

    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        if user.pk == request.user.pk:
            raise ValidationError(
                {"detail": _("Use change-password to change your own password.")}
            )
        ser = SetPasswordSerializer(data=request.data, context={"target": user})
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            user.set_password(ser.validated_data["new_password"])
            user.save(update_fields=["password", "modified_at"])
            revoke_all_sessions(user)
            record_audit_log(
                request.user, "update", user, changes={"password": "reset by admin"}
            )
        return Response({"detail": _("Password updated.")})


class RoleViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Role registry.

    Governed by RBAC rather than ``IsAdminUser``: role management is the
    control plane that grants authority, so it must not sit behind a flag
    (``is_staff``) that any operator can hold. Only a superuser or a holder of
    a role carrying ``users.Role`` permission (the seeded *System Manager*)
    may create, read, or edit roles. Mutations are written to the AuditLog.
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]


class RolePermissionViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Role -> permission grants (read-write for System Managers / superusers).

    Same reasoning as :class:`RoleViewSet`. Leaving this on ``IsAdminUser``
    would let any staff user grant themselves ``can_read`` on
    ``accounts.GLEntry`` and read the whole company's books. Mutations are
    written to the AuditLog and the ``model_label`` must be an installed model.
    """

    queryset = RolePermission.objects.all()
    serializer_class = RolePermissionSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
