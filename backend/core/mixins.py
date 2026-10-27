import logging
from decimal import Decimal

from django.db import models as dj_models, transaction
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


class DraftNestedWriteMixin:
    """Serializer mixin: create / update a document together with its child rows.

    Declare ``children_field`` (the nested ``many=True`` field, e.g. ``"items"``)
    and ``name_prefix`` (the ``NamingSeries`` prefix used when no name is sent).
    The child model and the FK back to the parent are derived from the nested
    serializer, so there is a single implementation for every document type.

    ``update()`` is only legal on a **draft**: the row is re-read under a lock and
    a non-draft raises Django's ``ValidationError`` before anything is touched
    (the view layer turns that into a 409). When the children are sent, the draft's
    child set is replaced atomically (child rows of a draft are plain rows, not
    ledger facts) and every derived total is recomputed server-side -- the same
    path ``create()`` uses. If the children are omitted (header-only PATCH / a
    PaymentEntry PUT without ``references``) they are left untouched.
    """

    children_field = "items"
    name_prefix = ""

    def get_extra_kwargs(self):
        # ``name`` is optional on create: when omitted it is auto-numbered from
        # the NamingSeries (``name_prefix``). A client may still supply one.
        kwargs = super().get_extra_kwargs()
        kwargs["name"] = {**kwargs.get("name", {}), "required": False}
        return kwargs

    # ----------------------------------------------------------- introspection

    def _child_model(self):
        return self.fields[self.children_field].child.Meta.model

    def _parent_fk_name(self):
        parent_model = self.Meta.model
        for field in self._child_model()._meta.fields:
            if (
                isinstance(field, dj_models.ForeignKey)
                and field.related_model is parent_model
            ):
                return field.name
        raise RuntimeError(f"{self._child_model()} has no FK to {parent_model}")

    # -------------------------------------------------------------- validation

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # On a partial update DRF skips "required" for nested rows too, so a PATCH
        # replacing the children could slip an incomplete row through to the
        # database (a 500). A replacement child set must be complete, like create.
        if self.partial and self.children_field in attrs:
            child_fields = self.fields[self.children_field].child.fields
            required = [
                name
                for name, f in child_fields.items()
                if f.required and not f.read_only
            ]
            for index, row in enumerate(attrs[self.children_field]):
                missing = [name for name in required if name not in row]
                if missing:
                    raise DRFValidationError(
                        {
                            self.children_field: {
                                index: {
                                    name: ["This field is required."]
                                    for name in missing
                                }
                            }
                        }
                    )
        return attrs

    # -------------------------------------------------------------- persistence

    def _replace_children(self, instance, rows):
        child_model = self._child_model()
        fk = self._parent_fk_name()
        child_model.objects.filter(**{fk: instance}).delete()
        for row in rows:
            child_model.objects.create(**{fk: instance}, **row)
        # The viewset prefetches children; drop the stale cache.
        instance._prefetched_objects_cache = {}

    def refresh_totals(self, instance):
        """Recompute derived header totals from the persisted child rows."""
        if hasattr(instance, "recompute_totals"):
            instance.recompute_totals()
            instance.save(update_fields=["total_debit", "total_credit"])
        elif hasattr(instance, "grand_total"):
            child_model = self._child_model()
            fk = self._parent_fk_name()
            total = sum(
                (c.amount for c in child_model.objects.filter(**{fk: instance})),
                Decimal("0.00"),
            )
            instance.grand_total = total
            instance.save(update_fields=["grand_total"])

    def create(self, validated_data):
        from core.models import NamingSeries

        rows = validated_data.pop(self.children_field, [])
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next(self.name_prefix)
            instance = self.Meta.model.objects.create(**validated_data)
            self._replace_children(instance, rows)
            self.refresh_totals(instance)
        return instance

    def update(self, instance, validated_data):
        rows = validated_data.pop(self.children_field, None)
        model = self.Meta.model
        with transaction.atomic():
            locked = model.objects.select_for_update().get(pk=instance.pk)
            if locked.docstatus != DocStatus.DRAFT:
                raise DjangoValidationError(
                    "Submitted or cancelled documents cannot be modified."
                )
            instance = super().update(instance, validated_data)
            if rows is not None:
                self._replace_children(instance, rows)
            self.refresh_totals(instance)
        return instance


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


class MappedDocumentActionMixin:
    """ViewSet mixin for ``make-*`` actions that map one document to a draft of another.

    Mirrors ERPNext's mapped-document permissions: the caller needs **read** on the
    source (declared via ``action_permission_flags``, enforced by
    ``HasRolePermission``) and **create** on the target (checked here). The mapping
    function runs atomically and returns a *draft*; a ``ValidationError`` it
    raises becomes a 400 ``{"error": ...}``.
    """

    action_permission_flags = {}

    def map_document(self, request, builder, target_label, target_serializer_class):
        from rest_framework.exceptions import PermissionDenied
        from users.permissions import user_has_model_permission

        if not user_has_model_permission(request.user, target_label, "can_create"):
            raise PermissionDenied(
                f"You do not have permission to create {target_label}."
            )
        source = self.get_object()
        try:
            doc = builder(source)
        except (DjangoValidationError, DRFValidationError) as exc:
            msg = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else (exc.messages if hasattr(exc, "messages") else str(exc))
            )
            if isinstance(msg, list) and len(msg) == 1:
                msg = msg[0]
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)
        data = target_serializer_class(doc, context=self.get_serializer_context()).data
        record_audit_log(request.user, "create", doc, changes=data)
        return Response(data, status=status.HTTP_201_CREATED)


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
