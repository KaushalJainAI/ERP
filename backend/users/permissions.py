"""RBAC enforcement.

The unit of authority is ``(model_label, action)``: a ``RolePermission`` row
says "this role may ``can_read``/``can_create``/... the model ``stock.Item"``.
A request is allowed when *any* role the user holds grants the flag the
request's HTTP verb maps to, on the model the target view operates on.

Two rules are deliberate and load-bearing:

1. **Only ``is_superuser`` bypasses RBAC.** ``is_staff`` does not. In Django
   ``is_staff`` only means "may sign in to the admin site"; treating it as an
   all-power bypass handed every staff-flagged operator the whole company's
   books, and let staff grant themselves any permission via the role API.
   Staff keeps admin-site access via ``ModelAdmin``; API authority comes from
   roles.
2. **Fail closed.** If a view cannot be tied to a model, access is *denied*.
   The previous behaviour allowed the request, which silently made any
   function-based view world-readable to every logged-in user.
"""

from rest_framework import permissions
from users.models import RolePermission, UserRole

#: The permission flags a `RolePermission` row can carry.
PERMISSION_FLAGS = (
    "can_read",
    "can_create",
    "can_write",
    "can_submit",
    "can_cancel",
    "can_delete",
)


def resolve_model_label(view):
    """Resolve the ``app_label.ObjectName`` a view operates on, or ``None``.

    Resolution order:
      1. an explicit ``model_label`` attribute on the view
      2. the view's ``model`` attribute
      3. ``view.queryset`` (or its ``.model``)
      4. ``view.get_queryset()`` (or its ``.model``)

    Returns ``None`` when the view exposes no model. Callers must then *deny*
    — a view with no declared model has no declared authority.
    """
    label = getattr(view, "model_label", None)
    if label:
        return str(label)

    model = getattr(view, "model", None)

    # NOTE: these must be `is None` checks, never truthiness checks. A Django
    # QuerySet evaluates its query to decide truthiness, so an *empty* queryset
    # is falsy -- and `if not queryset` would discard the very queryset that
    # carries the model. On a table with no rows yet, the view would fail to
    # resolve and the request would be denied (or, before this was fixed,
    # allowed outright).
    queryset = getattr(view, "queryset", None)
    if model is None and queryset is not None:
        model = queryset

    if model is None and hasattr(view, "get_queryset"):
        try:
            model = view.get_queryset()
        except Exception:
            # A view whose queryset cannot be built has no resolvable model.
            model = None

    if model is None:
        return None

    # `queryset`/`get_queryset()` may hand back a Manager or a QuerySet; both
    # expose `.model`. A bare model class does not, and falls through to
    # `_meta` below.
    if hasattr(model, "model"):
        model = model.model

    if hasattr(model, "_meta"):
        return f"{model._meta.app_label}.{model._meta.object_name}"
    return str(model)


def resolve_action_flag(request, view):
    """Map an HTTP request onto the ``RolePermission`` flag it requires."""
    if request.method in permissions.SAFE_METHODS:
        return "can_read"
    if request.method == "POST":
        action_name = getattr(view, "action", None)
        if action_name in ("submit_doc", "submit"):
            return "can_submit"
        if action_name in ("cancel_doc", "cancel"):
            return "can_cancel"
        return "can_create"
    if request.method in ("PUT", "PATCH"):
        return "can_write"
    if request.method == "DELETE":
        return "can_delete"
    return "can_read"


def user_has_model_permission(user, model_label, flag="can_read"):
    """True if `user` holds `flag` on `model_label` through any of their roles.

    Superusers bypass. Staff do not (see the module docstring).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
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
    ``HasRolePermission`` cannot infer a ``model_label`` for them and (now that
    it fails closed) would deny every request. These reports read
    financial/stock data and must be gated explicitly — otherwise either every
    logged-in user reads every company's books, or nobody can read the books
    at all. Declare the model they serve and the flag they need.
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
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        # Superusers bypass RBAC. `is_staff` deliberately does NOT: in Django it
        # only means "may open the admin site", and treating it as an all-power
        # bypass handed every staff-flagged operator the whole company's books.
        if user.is_superuser:
            return True

        model_label = resolve_model_label(view)
        if not model_label:
            # Fail closed. A view we cannot map to a model has no declared
            # authority, so no role may reach it. Declare `model_label` on the
            # view, or use `require_model_permission(...)` for function views.
            return False

        action_flag = resolve_action_flag(request, view)

        # Check if user has any role granting this permission flag for model_label
        user_roles = UserRole.objects.filter(user=user).values_list(
            "role_id", flat=True
        )
        if not user_roles:
            return False

        filter_kwargs = {
            "role_id__in": user_roles,
            "model_label": model_label,
            action_flag: True,
        }
        return RolePermission.objects.filter(**filter_kwargs).exists()
