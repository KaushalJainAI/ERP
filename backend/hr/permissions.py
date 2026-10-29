"""Permission class of the employee self-service portal.

Portal routes are *not* RBAC model routes: they are used by role-less synthetic
users (see ``hr.services.portal``) and every queryset is filtered to the signed-in
employee.  ``IsPortalEmployee`` is therefore set *instead of* ``HasRolePermission``
on those views, and it fails closed: any user that is not an Active employee with
the portal enabled is denied.
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from hr.models import Employee, EmployeeStatus


class IsPortalEmployee(permissions.BasePermission):
    """Authenticated user linked to an Active employee whose portal is enabled.

    Sets ``request.portal_employee`` / ``request.portal_access`` for the view.
    While ``pin_must_change`` is set, every view except those declaring
    ``allow_pin_change_pending = True`` (``me/`` and ``change-pin/``) answers
    ``403 {"code": "pin_change_required"}``.
    """

    message = _("Portal access is not available for this account.")

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user.is_active:
            return False
        employee = (
            Employee.objects.select_related(
                "company", "department", "designation", "branch", "portal_access"
            )
            .filter(user=user)
            .first()
        )
        if employee is None or employee.status != EmployeeStatus.ACTIVE:
            return False
        access = getattr(employee, "portal_access", None)
        if access is None or not access.portal_enabled:
            return False
        request.portal_employee = employee
        request.portal_access = access
        if access.pin_must_change and not getattr(
            view, "allow_pin_change_pending", False
        ):
            raise PermissionDenied(
                {
                    "detail": _("You must change your PIN before continuing."),
                    "code": "pin_change_required",
                }
            )
        return True
