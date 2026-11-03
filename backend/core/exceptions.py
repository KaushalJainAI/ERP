"""Global exception handler for Django REST Framework.

Transforms unhandled database exceptions (ProtectedError, RestrictedError,
IntegrityError) and Django validation errors into informative, non-500 HTTP responses.
"""

from collections import Counter
import logging
from django.core.exceptions import (
    ObjectDoesNotExist,
    ValidationError as DjangoValidationError,
)
from django.db import IntegrityError
from django.db.models import ProtectedError, RestrictedError
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def format_protected_error(exc, target_name="record"):
    """Format a user-friendly error message from a Django ProtectedError."""
    protected_objects = getattr(exc, "protected_objects", set())
    if protected_objects:
        counts = Counter(
            getattr(obj._meta, "verbose_name", obj.__class__.__name__).title()
            for obj in protected_objects
        )
        details = ", ".join(f"{cnt} {name}" for name, cnt in counts.items())
        samples = [
            getattr(obj, "name", getattr(obj, "code", str(obj)))
            for obj in list(protected_objects)[:3]
        ]
        samples_str = f" (e.g. {', '.join(samples)})" if samples else ""
        return _(
            "Cannot delete %(target)s because it is referenced by %(details)s%(samples)s. "
            "Please delete or reassign those referenced records first, or deactivate it instead."
        ) % {
            "target": target_name,
            "details": details,
            "samples": samples_str,
        }
    return _(
        "Cannot delete %(target)s because it is referenced by other records."
    ) % {"target": target_name}


def format_restricted_error(exc, target_name="record"):
    """Format a user-friendly error message from a Django RestrictedError."""
    restricted_objects = getattr(exc, "restricted_objects", set())
    if restricted_objects:
        counts = Counter(
            getattr(obj._meta, "verbose_name", obj.__class__.__name__).title()
            for obj in restricted_objects
        )
        details = ", ".join(f"{cnt} {name}" for name, cnt in counts.items())
        return _(
            "Cannot delete %(target)s due to foreign key restriction: referenced by %(details)s. "
            "Please delete or reassign those records first."
        ) % {
            "target": target_name,
            "details": details,
        }
    return _("Cannot delete %(target)s due to foreign key restrictions.") % {
        "target": target_name
    }


def format_integrity_error(exc):
    """Format a user-friendly error message from an IntegrityError."""
    msg = str(exc)
    msg_lower = msg.lower()
    if "foreign key" in msg_lower or "violates foreign key constraint" in msg_lower:
        return _(
            "Foreign key constraint error: A referenced record was not found or is required by another record. "
            "Please check that all related IDs and foreign keys exist."
        )
    if "unique constraint" in msg_lower or "duplicate key" in msg_lower:
        return _(
            "A record with this identifier or unique combination already exists."
        )
    if "check constraint" in msg_lower:
        return _("The provided values violate a database validation check constraint.")
    return _("Database integrity error: %(msg)s") % {"msg": msg}


def custom_exception_handler(exc, context):
    """DRF custom exception handler preventing 500 crashes on DB and model errors."""
    # 1. Let DRF handle standard API exceptions first
    response = exception_handler(exc, context)
    if response is not None:
        return response

    # 2. Inspect context for target object name if available
    view = context.get("view")
    target_name = "this record"
    if view and hasattr(view, "get_object"):
        try:
            obj = view.get_object()
            verbose = getattr(obj._meta, "verbose_name", "record")
            ident = getattr(obj, "name", getattr(obj, "code", str(obj)))
            target_name = f"{verbose} '{ident}'"
        except Exception:
            pass

    # 3. Handle ProtectedError (on_delete=models.PROTECT)
    if isinstance(exc, ProtectedError):
        message = format_protected_error(exc, target_name)
        logger.warning("ProtectedError caught for %s: %s", target_name, message)
        samples = [
            getattr(obj, "name", getattr(obj, "code", str(obj)))
            for obj in list(getattr(exc, "protected_objects", []))[:10]
        ]
        return Response(
            {
                "error": message,
                "code": "foreign_key_protected",
                "details": samples,
            },
            status=status.HTTP_409_CONFLICT,
        )

    # 4. Handle RestrictedError (on_delete=models.RESTRICT)
    if isinstance(exc, RestrictedError):
        message = format_restricted_error(exc, target_name)
        logger.warning("RestrictedError caught for %s: %s", target_name, message)
        return Response(
            {
                "error": message,
                "code": "foreign_key_restricted",
            },
            status=status.HTTP_409_CONFLICT,
        )

    # 5. Handle IntegrityError (raw DB constraint or FK violations)
    if isinstance(exc, IntegrityError):
        message = format_integrity_error(exc)
        logger.warning("IntegrityError caught: %s", exc)
        return Response(
            {
                "error": message,
                "code": "database_integrity_error",
            },
            status=status.HTTP_409_CONFLICT,
        )

    # 6. Handle unhandled Django model ValidationError
    if isinstance(exc, DjangoValidationError):
        msg = (
            exc.message_dict
            if hasattr(exc, "message_dict")
            else (exc.messages if hasattr(exc, "messages") else str(exc))
        )
        return Response(
            {"error": msg, "code": "validation_error"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 7. Handle ObjectDoesNotExist
    if isinstance(exc, ObjectDoesNotExist):
        return Response(
            {"error": str(exc), "code": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Return None for true unexpected server errors (500)
    return None
