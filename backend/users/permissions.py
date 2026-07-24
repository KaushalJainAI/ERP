from rest_framework import permissions
from users.models import RolePermission, UserRole


def user_has_model_permission(user, model_label, flag="can_read"):
    """True if `user` holds `flag` on `model_label` through any of their roles.

    Superusers and staff are always allowed (mirrors HasRolePermission).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    role_ids = UserRole.objects.filter(user=user).values_list("role_id", flat=True)
    if not role_ids:
        return False
    return RolePermission.objects.filter(
        role_id__in=role_ids, model_label=model_label, **{flag: True}
    ).exists()


def require_model_permission(model_label, flag="can_read"):
    """Build a DRF permission class enforcing `flag` on `model_label`.

    Function-based report endpoints (`@api_view`) expose no queryset/model, so
    ``HasRolePermission`` can't infer a ``model_label`` and fails *open* (returns
    True) for them. These reports read financial/stock data and must be gated
    explicitly — otherwise any authenticated user reads every company's books.
    """

    class _RequireModelPermission(permissions.BasePermission):
        def has_permission(self, request, view):
            return user_has_model_permission(request.user, model_label, flag)

    return _RequireModelPermission


class HasRolePermission(permissions.BasePermission):
    """
    Granular RBAC permission class reading RolePermission per model and action.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser or request.user.is_staff:
            return True

        model_label = getattr(view, "model_label", None)
        if not model_label:
            model = getattr(view, "model", None)
            if not model and hasattr(view, "queryset") and view.queryset is not None:
                model = view.queryset.model
            if not model and hasattr(view, "get_queryset"):
                try:
                    model = view.get_queryset().model
                except Exception:
                    pass

            if not model:
                # If no model is associated with the view, allow authenticated access
                return True

            if hasattr(model, "_meta"):
                model_label = f"{model._meta.app_label}.{model._meta.object_name}"
            else:
                model_label = str(model)

        # Map HTTP method / action to permission flag
        action_flag = "can_read"
        if request.method in permissions.SAFE_METHODS:
            action_flag = "can_read"
        elif request.method == "POST":
            # Check for custom submit/cancel actions
            action_name = getattr(view, "action", None)
            if action_name in ["submit_doc", "submit"]:
                action_flag = "can_submit"
            elif action_name in ["cancel_doc", "cancel"]:
                action_flag = "can_cancel"
            else:
                action_flag = "can_create"
        elif request.method in ["PUT", "PATCH"]:
            action_flag = "can_write"
        elif request.method == "DELETE":
            action_flag = "can_delete"

        # Check if user has any role granting this permission flag for model_label
        user_roles = UserRole.objects.filter(user=request.user).values_list("role_id", flat=True)
        if not user_roles:
            return False

        filter_kwargs = {
            "role_id__in": user_roles,
            "model_label": model_label,
            action_flag: True,
        }
        return RolePermission.objects.filter(**filter_kwargs).exists()
