import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from core.models import AuditLog, DocStatus

logger = logging.getLogger(__name__)


class PositiveQtyLineItemMixin:
    """Serializer mixin: reject non-positive quantities on transactional lines.

    Every document type here moves stock/value in one direction (abs(qty) is
    taken in the posting services), so a zero or negative line is always a data
    error — it produces a meaningless or value-destroying document. Returns/credit
    notes, when added, will be their own document types, not negative lines.
    """

    def validate_qty(self, value):
        if value is None or value <= 0:
            raise DRFValidationError("Quantity must be greater than zero.")
        return value


class NonNegativeRateLineItemMixin:
    """Serializer mixin: reject negative unit rates on transactional lines."""

    def validate_rate(self, value):
        if value is not None and value < 0:
            raise DRFValidationError("Rate cannot be negative.")
        return value


def record_audit_log(user, action_name, instance, changes=None, ip_address=None):
    """Utility function to log an operation to AuditLog."""
    try:
        model_label = f"{instance._meta.app_label}.{instance._meta.object_name}"
        obj_id = str(getattr(instance, "name", getattr(instance, "id", "")))
        AuditLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            action=action_name,
            model_label=model_label,
            object_id=obj_id,
            changes=changes or {},
            ip_address=ip_address,
        )
    except Exception:
        # A failed audit write must never be swallowed silently — the audit log
        # is a mandate. Surface it loudly so the drift is caught, without
        # tearing down the caller's transaction from inside this helper.
        logger.exception(
            "Failed to record audit log: action=%s instance=%r", action_name, instance
        )


class AuditLogMixin:
    """Mixin for ModelViewSet to automatically record CRUD operations in AuditLog."""

    def perform_create(self, serializer):
        instance = serializer.save()
        user = getattr(self.request, "user", None)
        record_audit_log(user, "create", instance, changes=serializer.data)

    def perform_update(self, serializer):
        instance = serializer.save()
        user = getattr(self.request, "user", None)
        record_audit_log(user, "update", instance, changes=serializer.data)

    def perform_destroy(self, instance):
        user = getattr(self.request, "user", None)
        # Delete first, then audit: recording "delete" before the delete would
        # leave a phantom audit entry if the delete is refused (e.g. the model
        # blocks deleting a non-draft submittable). The name/id read for the log
        # survive in memory after the row is gone.
        instance.delete()
        record_audit_log(user, "delete", instance)


class SubmittableViewSetMixin(AuditLogMixin):
    """
    ViewSet mixin for SubmittableDocument resources.
    Provides /submit/ and /cancel/ POST endpoints and blocks edit/delete on non-draft documents.
    """

    def update(self, request, *args, **kwargs):
        """Translate the model-layer immutability guard into a clean 409 instead
        of letting Django's ValidationError surface as an uncaught 500."""
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return Response(
                {"error": exc.messages if hasattr(exc, "messages") else str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if getattr(instance, "docstatus", None) != DocStatus.DRAFT:
            return Response(
                {
                    "error": "Only draft documents can be deleted; cancel and amend instead."
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit_doc(self, request, pk=None):
        doc = self.get_object()
        try:
            doc.submit(user=request.user)
            record_audit_log(request.user, "submit", doc)
            serializer = self.get_serializer(doc)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except (DjangoValidationError, DRFValidationError) as exc:
            msg = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else (exc.messages if hasattr(exc, "messages") else str(exc))
            )
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel_doc(self, request, pk=None):
        doc = self.get_object()
        try:
            doc.cancel(user=request.user)
            record_audit_log(request.user, "cancel", doc)
            serializer = self.get_serializer(doc)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except (DjangoValidationError, DRFValidationError) as exc:
            msg = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else (exc.messages if hasattr(exc, "messages") else str(exc))
            )
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="amend")
    def amend_doc(self, request, pk=None):
        doc = self.get_object()
        try:
            new_draft = doc.amend()
            new_draft.save()
            record_audit_log(request.user, "amend", new_draft)
            serializer = self.get_serializer(new_draft)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except (DjangoValidationError, DRFValidationError) as exc:
            msg = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else (exc.messages if hasattr(exc, "messages") else str(exc))
            )
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)


def get_active_language(request):
    """Resolve active language: User.language -> Accept-Language header -> 'en'."""
    if not request:
        return "en"
    user = getattr(request, "user", None)
    if user and user.is_authenticated and getattr(user, "language", None):
        return user.language
    accept_lang = (
        request.META.get("HTTP_ACCEPT_LANGUAGE", "") if hasattr(request, "META") else ""
    )
    if accept_lang:
        code = accept_lang.split(",")[0].split("-")[0].strip().lower()
        if code:
            return code
    return "en"


class TranslatedSerializerMixin:
    """
    Serializer mixin to dynamically translate declared translatable fields
    using FieldTranslation records based on request active language.
    """

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        request = self.context.get("request")
        lang_code = get_active_language(request)
        if lang_code == "en":
            return ret

        translatable_fields = getattr(self.Meta, "translatable_fields", [])
        if not translatable_fields or not hasattr(instance, "pk") or not instance.pk:
            return ret

        model_label = f"{instance._meta.app_label}.{instance._meta.object_name}"
        from core.models import FieldTranslation

        translations = FieldTranslation.objects.filter(
            model_label=model_label,
            object_id=instance.pk,
            field_name__in=translatable_fields,
            language=lang_code,
        )
        trans_dict = {t.field_name: t.value for t in translations}
        for field in translatable_fields:
            if field in trans_dict and field in ret:
                ret[field] = trans_dict[field]

        return ret
