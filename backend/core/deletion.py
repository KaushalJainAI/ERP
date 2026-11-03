"""Unified deletion service enforcing soft-delete for standard users and hard-delete for admins."""

import logging
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from core.models import DocStatus

logger = logging.getLogger(__name__)


def is_admin_user(user):
    """Return True if user is a superuser or holds an administrator role."""
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    try:
        from users.models import UserRole
        return UserRole.objects.filter(
            user=user,
            role__name__in=["System Manager", "Admin", "Administrator"],
        ).exists()
    except Exception:
        return False


def handle_unified_destroy(viewset, request, instance):
    """Handle DELETE request with soft-delete for non-admins and hard-delete for admins.
    
    Protected foreign key errors on hard delete are captured cleanly by the
    DRF exception handler and return HTTP 409 instead of 500.
    """
    from core.mixins import record_audit_log

    user = getattr(request, "user", None)
    is_admin = is_admin_user(user)

    # Allow hard delete only if user is admin and hasn't explicitly requested soft-delete
    allow_hard = is_admin and request.query_params.get("soft", "").lower() not in ("true", "1")

    if not allow_hard:
        # Non-admin or explicit soft-delete:
        # 1. Models with is_active
        if hasattr(instance, "is_active"):
            if instance.is_active:
                instance.is_active = False
                fields = ["is_active"]
                if hasattr(instance, "modified_at"):
                    fields.append("modified_at")
                if hasattr(instance, "modified_by") and user and user.is_authenticated:
                    instance.modified_by = user
                    fields.append("modified_by")
                instance.save(update_fields=fields)
                record_audit_log(user, "soft_delete", instance, changes={"is_active": False})
                return Response(
                    {
                        "message": _(
                            "Record soft-deleted (deactivated). Permanent deletion requires administrator privileges."
                        ),
                        "soft_deleted": True,
                        "id": instance.pk,
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {
                        "error": _(
                            "This record is already deactivated. Permanent deletion requires administrator privileges."
                        ),
                        "soft_deleted": True,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 2. Submittable documents (docstatus)
        elif hasattr(instance, "docstatus"):
            if instance.docstatus == DocStatus.DRAFT:
                instance.docstatus = DocStatus.CANCELLED
                fields = ["docstatus"]
                if hasattr(instance, "modified_at"):
                    fields.append("modified_at")
                if hasattr(instance, "modified_by") and user and user.is_authenticated:
                    instance.modified_by = user
                    fields.append("modified_by")
                instance.save(update_fields=fields)
                record_audit_log(
                    user, "soft_delete", instance, changes={"docstatus": DocStatus.CANCELLED}
                )
                return Response(
                    {
                        "message": _(
                            "Draft document cancelled (soft deleted). Permanent deletion requires administrator privileges."
                        ),
                        "soft_deleted": True,
                        "id": instance.pk,
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {"error": _("Only draft documents can be deleted or cancelled.")},
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            return Response(
                {
                    "error": _(
                        "Permanent deletion requires administrator privileges. Contact an administrator to delete this record."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

    # Admin: perform hard delete
    viewset.perform_destroy(instance)
    return Response(status=status.HTTP_204_NO_CONTENT)
