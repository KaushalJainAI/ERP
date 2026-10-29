from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from users.models import User, Role, UserRole, RolePermission
from users.permissions import user_has_model_permission


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    readable_models = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "language",
            "is_active",
            "is_superuser",
            "is_staff",
            "roles",
            "readable_models",
        )
        # Self-service profile (PATCH /api/auth/me/): only the four personal
        # fields below are writable. Everything else -- notably is_active,
        # is_superuser and the derived roles/readable_models -- is
        # server-controlled, so a user can never deactivate/elevate themselves
        # through their own profile.
        read_only_fields = (
            "id",
            "username",
            "is_active",
            "is_superuser",
            "is_staff",
            "roles",
            "readable_models",
        )

    def get_roles(self, obj):
        return list(
            UserRole.objects.filter(user=obj).values_list("role__name", flat=True)
        )

    def get_readable_models(self, obj):
        """Model labels the caller may read, for permission-aware navigation.

        Derived from the same ``RolePermission`` rows the API enforces; ``["*"]``
        means a superuser (who bypasses RBAC everywhere). Keeping this on the
        profile lets the UI hide workspaces the user cannot open without
        duplicating the role matrix in the frontend.
        """
        if obj.is_superuser:
            return ["*"]
        return sorted(
            set(
                RolePermission.objects.filter(
                    role__role_users__user=obj, can_read=True
                ).values_list("model_label", flat=True)
            )
        )


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = "__all__"


class RolePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RolePermission
        fields = "__all__"

    def validate_model_label(self, value):
        """A grant on a label that is not an installed model grants authority on
        nothing and hides typos -- refuse it (seed_defaults enforces the same)."""
        try:
            django_apps.get_model(value)
        except (LookupError, ValueError):
            raise serializers.ValidationError(
                _("'%(label)s' is not an installed model (use app_label.ModelName).")
                % {"label": value}
            )
        return value


def _password_errors(password, user):
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        return list(exc.messages)
    return []


class UserAdminSerializer(serializers.ModelSerializer):
    """User management (``/api/auth/users/``), the equivalent of ERPNext's User
    doctype plus role assignment.

    * ``password`` is write-only and accepted on **create only**; later changes
      go through ``set-password`` (admin) or ``me/change-password`` (self).
    * ``is_superuser`` / ``is_staff`` are read-only here -- they can only be
      granted from the Django admin / shell, never over the API.
    * ``role_ids`` (writable, list of ``Role`` ids) replaces the user's
      ``UserRole`` rows; ``roles`` is the read-only list of role names.
    * ``username`` is immutable after creation (audit rows and tokens reference
      the user).
    """

    password = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
        trim_whitespace=False,
    )
    roles = serializers.SerializerMethodField()
    role_ids = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), many=True, required=False
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "language",
            "is_active",
            "is_superuser",
            "is_staff",
            "last_login",
            "date_joined",
            "modified_at",
            "password",
            "roles",
            "role_ids",
        )
        read_only_fields = (
            "id",
            "is_superuser",
            "is_staff",
            "last_login",
            "date_joined",
            "modified_at",
        )

    # -- representation ----------------------------------------------------
    def get_roles(self, obj):
        return sorted(ur.role.name for ur in obj.user_roles.all())

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["role_ids"] = sorted(ur.role_id for ur in instance.user_roles.all())
        return data

    # -- validation --------------------------------------------------------
    @property
    def _actor(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def validate_username(self, value):
        if self.instance is not None and value != self.instance.username:
            raise serializers.ValidationError(_("The username cannot be changed."))
        return value

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if not value:
            raise serializers.ValidationError(_("This field may not be blank."))
        # The model lower-cases on save, so uniqueness must be case-insensitive
        # (the auto UniqueValidator is exact-match and would let "A@x" through
        # to an IntegrityError).
        clash = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                _("A user with that email already exists.")
            )
        return value

    def validate_language(self, value):
        if value not in {code for code, _name in settings.LANGUAGES}:
            raise serializers.ValidationError(_("Unsupported language."))
        return value

    def validate(self, attrs):
        actor = self._actor
        creating = self.instance is None

        if creating:
            password = attrs.get("password")
            if not password:
                raise serializers.ValidationError(
                    {"password": _("This field is required.")}
                )
            probe = User(
                username=attrs.get("username", ""),
                email=attrs.get("email", ""),
                first_name=attrs.get("first_name", ""),
                last_name=attrs.get("last_name", ""),
            )
            errors = _password_errors(password, probe)
            if errors:
                raise serializers.ValidationError({"password": errors})
        elif "password" in attrs:
            raise serializers.ValidationError(
                {"password": _("Use the set-password action to change a password.")}
            )

        is_self = not creating and self.instance.pk == getattr(actor, "pk", None)
        if is_self and attrs.get("is_active") is False:
            raise serializers.ValidationError(
                {"is_active": _("You cannot deactivate yourself.")}
            )

        if "role_ids" in attrs:
            new_ids = {r.pk for r in attrs["role_ids"]}
            current = (
                set()
                if creating
                else set(self.instance.user_roles.values_list("role_id", flat=True))
            )
            if new_ids != current:
                if is_self:
                    raise serializers.ValidationError(
                        {"role_ids": _("You cannot change your own roles.")}
                    )
                # Assigning roles is its own authority (users.UserRole), separate
                # from editing the profile (users.User).
                if not user_has_model_permission(actor, "users.UserRole", "can_write"):
                    raise PermissionDenied(
                        _("You do not have permission to assign roles.")
                    )
        return attrs

    # -- persistence -------------------------------------------------------
    @transaction.atomic
    def create(self, validated_data):
        roles = validated_data.pop("role_ids", [])
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        self._set_roles(user, roles)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        roles = validated_data.pop("role_ids", None)
        validated_data.pop("username", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if roles is not None:
            self._set_roles(instance, roles)
        return instance

    @staticmethod
    def _set_roles(user, roles):
        wanted = {r.pk for r in roles}
        user.user_roles.exclude(role_id__in=wanted).delete()
        have = set(user.user_roles.values_list("role_id", flat=True))
        UserRole.objects.bulk_create(
            [UserRole(user=user, role_id=rid) for rid in wanted - have]
        )


class SetPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_new_password(self, value):
        errors = _password_errors(value, self.context["target"])
        if errors:
            raise serializers.ValidationError(errors)
        return value


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError(_("The old password is incorrect."))
        return value

    def validate(self, attrs):
        errors = _password_errors(attrs["new_password"], self.context["request"].user)
        if errors:
            raise serializers.ValidationError({"new_password": errors})
        if attrs["new_password"] == attrs["old_password"]:
            raise serializers.ValidationError(
                {"new_password": _("The new password must differ from the old one.")}
            )
        return attrs
