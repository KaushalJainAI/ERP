from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
"""JWT session revocation.

SimpleJWT tokens are stateless: deleting them in the browser does not stop a
copied token from working until it expires, and with refresh rotation a refresh
token can be renewed indefinitely. Two mechanisms close that gap:

* **Per-device sign-out** blacklists the refresh token the device holds
  (``rest_framework_simplejwt.token_blacklist``).
* **Sign out everywhere** stamps ``User.tokens_valid_after``. Any access or
  refresh token issued before that instant is refused, including rotated
  refresh tokens that the blacklist app never recorded. Deactivating a user,
  an admin password reset and a self-service password change all do this.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

SESSION_REVOKED = _("Your session has ended. Please sign in again.")


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate using either username or email address (case-insensitive)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or not password:
            return None
        try:
            user = UserModel.objects.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).first()
        except Exception:
            return None

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None



def check_token_not_revoked(user, token) -> None:
    """Refuse ``token`` if it was issued before the user's revocation cutoff."""
    cutoff = getattr(user, "tokens_valid_after", None)
    if cutoff is None:
        return
    issued_at = token.get("iat")
    # ``iat`` is whole seconds; a token minted in the same second as the cutoff
    # (the fresh pair returned by a password change) stays valid.
    if issued_at is None or int(issued_at) < int(cutoff.timestamp()):
        raise AuthenticationFailed(SESSION_REVOKED, code="token_revoked")


def revoke_all_sessions(user) -> None:
    """Invalidate every token issued to ``user`` so far (all devices)."""
    user.tokens_valid_after = timezone.now()
    user.save(update_fields=["tokens_valid_after"])


def issue_tokens(user) -> dict:
    """A fresh refresh/access pair, e.g. to keep the current device signed in."""
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class SessionAwareJWTAuthentication(JWTAuthentication):
    """``JWTAuthentication`` that also honours ``User.tokens_valid_after``."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        check_token_not_revoked(user, validated_token)
        return user


class SessionAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Refresh only for an active user whose sessions were not revoked."""

    def validate(self, attrs):
        from users.models import User

        token = RefreshToken(attrs["refresh"])
        user = User.objects.filter(
            **{api_settings.USER_ID_FIELD: token.get(api_settings.USER_ID_CLAIM)}
        ).first()
        if user is None or not user.is_active:
            raise AuthenticationFailed(SESSION_REVOKED, code="user_inactive")
        check_token_not_revoked(user, token)
        return super().validate(attrs)
