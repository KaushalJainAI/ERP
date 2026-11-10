import datetime
import logging
from decimal import Decimal

from django.db import models as dj_models, transaction
from django.utils.translation import gettext as _
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
            raise DRFValidationError(_("Quantity must be greater than zero."))
        return value


class NonNegativeRateLineItemMixin:
    """Serializer mixin: reject negative unit rates on transactional lines."""

    def validate_rate(self, value):
        if value is not None and value < 0:
            raise DRFValidationError(_("Rate cannot be negative."))
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
    #: Further nested child tables (e.g. ``("taxes",)`` on taxable documents),
    #: written after ``children_field``.
    extra_children_fields = ()
    name_prefix = ""
    #: Preferred over ``name_prefix``: a series code such as ``"SO"``. The number is
    #: ``<code>-<FY>-NNNN`` (``SO-2728-0001``), FY being the financial year of
    #: ``name_date_field`` (today when blank); see ``accounts.mapping``.
    name_series_prefix = ""
    name_date_field = "posting_date"

    def get_extra_kwargs(self):
        # ``name`` is optional on create: when omitted it is auto-numbered from
        # the NamingSeries (``name_prefix``). A client may still supply one.
        kwargs = super().get_extra_kwargs()
        kwargs["name"] = {**kwargs.get("name", {}), "required": False}
        return kwargs

    # ----------------------------------------------------------- introspection

    def _all_child_fields(self):
        return (self.children_field, *self.extra_children_fields)

    def _child_model(self, field=None):
        return self.fields[field or self.children_field].child.Meta.model

    def _parent_fk_name(self, field=None):
        parent_model = self.Meta.model
        child_model = self._child_model(field)
        for model_field in child_model._meta.fields:
            if (
                isinstance(model_field, dj_models.ForeignKey)
                and model_field.related_model is parent_model
            ):
                return model_field.name
        raise RuntimeError(f"{child_model} has no FK to {parent_model}")

    # -------------------------------------------------------------- validation

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # A template item (``has_variants``) is a grouping master, never a line:
        # the line must name one of its variants.
        for field in self._all_child_fields():
            for row in attrs.get(field) or []:
                line_item = row.get("item") if hasattr(row, "get") else None
                if getattr(line_item, "has_variants", False):

                    raise DRFValidationError(
                        {
                            field: _(
                                "Item '%(code)s' is a template with variants: "
                                "select one of its variants instead."
                            )
                            % {"code": line_item.code}
                        }
                    )
        # On a partial update DRF skips "required" for nested rows too, so a PATCH
        # replacing the children could slip an incomplete row through to the
        # database (a 500). A replacement child set must be complete, like create.
        if self.partial:
            for field in self._all_child_fields():
                if field not in attrs:
                    continue
                child_fields = self.fields[field].child.fields
                required = [
                    name
                    for name, f in child_fields.items()
                    if f.required and not f.read_only
                ]
                for index, row in enumerate(attrs[field]):
                    missing = [name for name in required if name not in row]
                    if missing:
                        raise DRFValidationError(
                            {
                                field: {
                                    index: {
                                        name: [_("This field is required.")]
                                        for name in missing
                                    }
                                }
                            }
                        )
        return attrs

    # -------------------------------------------------------------- persistence

    def _replace_children(self, instance, rows, field=None):
        child_model = self._child_model(field)
        fk = self._parent_fk_name(field)
        has_idx = any(f.name == "idx" for f in child_model._meta.fields)
        child_model.objects.filter(**{fk: instance}).delete()
        for position, row in enumerate(rows, start=1):
            row = dict(row)
            if has_idx:
                row["idx"] = position
            child_model.objects.create(**{fk: instance}, **row)
        # The viewset prefetches children; drop the stale cache.
        instance._prefetched_objects_cache = {}

    def refresh_totals(self, instance):
        """Recompute derived header totals from the persisted child rows."""
        if hasattr(instance, "recompute_totals"):
            instance.recompute_totals()
            instance.save(update_fields=["total_debit", "total_credit"])
        elif hasattr(instance, "calculate_taxes_and_totals"):
            # Taxable documents: the tax engine is the source of truth. Input
            # problems become a 400 (and roll the surrounding atomic block back),
            # not the 409 the view maps a bare Django ValidationError to.
            flags = getattr(instance, "_tax_flags", {})
            try:
                try:
                    instance.calculate_taxes_and_totals(
                        resolve_lines=True,
                        expand_template=flags.get("expand_template", False),
                        auto_select=flags.get("auto_select", False),
                    )
                except DjangoValidationError as exc:
                    # ERPNext re-applies the taxes when the party changes. The
                    # stored rows no longer fit the supply kind: drop them and
                    # re-select the default template (none when GST does not apply).
                    if not (
                        flags.get("reconcile")
                        and hasattr(exc, "error_dict")
                        and "taxes" in exc.error_dict
                    ):
                        raise
                    self._rederive_taxes(instance)
            except DjangoValidationError as exc:
                raise DRFValidationError(
                    exc.message_dict
                    if hasattr(exc, "error_dict")
                    else {"non_field_errors": exc.messages}
                )
        elif hasattr(instance, "grand_total"):
            child_model = self._child_model()
            fk = self._parent_fk_name()
            total = sum(
                (c.amount for c in child_model.objects.filter(**{fk: instance})),
                Decimal("0.00"),
            )
            instance.grand_total = total
            instance.save(update_fields=["grand_total"])

    def _rederive_taxes(self, instance):
        """Replace stale tax rows by the default template for the new supply kind."""
        instance.tax_model().objects.filter(
            **{instance.tax_fk_name(): instance}
        ).delete()
        instance.taxes_and_charges = None
        instance.save(update_fields=["taxes_and_charges"])
        instance._prefetched_objects_cache = {}
        instance.calculate_taxes_and_totals(resolve_lines=True, auto_select=True)

    #: Header fields whose change can make stored GST rows stale.
    _SUPPLY_FIELDS = ("place_of_supply", "is_export_with_gst", "is_reverse_charge")

    def _pop_children(self, validated_data):
        """Pop every nested child set out of ``validated_data`` (field -> rows)."""
        return {
            field: validated_data.pop(field)
            for field in self._all_child_fields()
            if field in validated_data
        }

    def _tax_flags_for(self, validated_data, supplied, is_new):
        """How the tax engine should treat the tax table on this save.

        Explicit ``taxes`` (even ``[]``) are used as sent. When they are omitted a
        chosen ``taxes_and_charges`` template is expanded (on create, or when it is
        part of the update, i.e. an explicit "re-apply the template"); a new
        document with neither gets the company's default template.
        """
        if "taxes" in supplied:
            return {}
        template = validated_data.get("taxes_and_charges")
        if template is not None and (is_new or "taxes_and_charges" in validated_data):
            return {"expand_template": True}
        if is_new and template is None:
            return {"auto_select": True}
        party_field = getattr(self.Meta.model, "party_field", "")
        if not is_new and any(
            f in validated_data for f in (party_field, *self._SUPPLY_FIELDS) if f
        ):
            return {"reconcile": True}
        return {}

    def _auto_name(self, validated_data):
        """Next name from the NamingSeries: FY series when ``name_series_prefix`` is set."""
        from core.models import NamingSeries

        if not self.name_series_prefix:
            return NamingSeries.next(self.name_prefix)
        from accounts.mapping import financial_year_series

        posting = validated_data.get(self.name_date_field) or datetime.date.today()
        return NamingSeries.next(
            financial_year_series(self.name_series_prefix, posting)
        )

    def create(self, validated_data):
        supplied = self._pop_children(validated_data)
        flags = (
            self._tax_flags_for(validated_data, supplied, True)
            if self.extra_children_fields
            else {}
        )
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = self._auto_name(validated_data)
            instance = self.Meta.model.objects.create(**validated_data)
            for field in self._all_child_fields():
                if field in supplied:
                    self._replace_children(instance, supplied[field], field)
            instance._tax_flags = flags
            self.refresh_totals(instance)
        return instance

    def update(self, instance, validated_data):
        supplied = self._pop_children(validated_data)
        flags = (
            self._tax_flags_for(validated_data, supplied, False)
            if self.extra_children_fields
            else {}
        )
        model = self.Meta.model
        with transaction.atomic():
            locked = model.objects.select_for_update().get(pk=instance.pk)
            if locked.docstatus != DocStatus.DRAFT:
                raise DjangoValidationError(
                    _("Submitted or cancelled documents cannot be modified.")
                )
            instance = super().update(instance, validated_data)
            for field in self._all_child_fields():
                if field in supplied:
                    self._replace_children(instance, supplied[field], field)
            instance._tax_flags = flags
            self.refresh_totals(instance)
        return instance


class NestedMasterWriteMixin:
    """Serializer mixin: create / update a *master* together with its child rows.

    Like :class:`DraftNestedWriteMixin` but for non-document masters (tax templates):
    no docstatus guard, no naming series, no derived totals. Declare
    ``children_field`` (the nested ``many=True`` field, e.g. ``"rows"``). When the
    children are sent, the existing set is replaced atomically; when omitted
    (header-only PATCH) they are left untouched. A child model with an ``idx``
    field gets ``idx = position + 1`` (client-supplied ``idx`` is ignored -- make
    it read-only on the child serializer).
    """

    children_field = "rows"

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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # A replacement child set must be complete, like on create (DRF skips
        # "required" for nested rows on a partial update).
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

    def _replace_children(self, instance, rows):
        child_model = self._child_model()
        fk = self._parent_fk_name()
        has_idx = any(f.name == "idx" for f in child_model._meta.fields)
        child_model.objects.filter(**{fk: instance}).delete()
        for position, row in enumerate(rows, start=1):
            row = dict(row)
            if has_idx:
                row["idx"] = position
            child_model.objects.create(**{fk: instance}, **row)
        instance._prefetched_objects_cache = {}

    def create(self, validated_data):
        rows = validated_data.pop(self.children_field, [])
        with transaction.atomic():
            instance = self.Meta.model.objects.create(**validated_data)
            self._replace_children(instance, rows)
        return instance

    def update(self, instance, validated_data):
        rows = validated_data.pop(self.children_field, None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if rows is not None:
                self._replace_children(instance, rows)
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
        user = getattr(self.request, "user", None)
        proposal_hdr = None
        if hasattr(self, "request") and self.request:
            proposal_hdr = self.request.headers.get("X-Assistant-Proposal")
        # The document and any proof carried over from an assistant bill photo commit
        # together: if the copy fails, the document is not saved either.
        with transaction.atomic():
            instance = serializer.save()
            changes = dict(serializer.data) if isinstance(serializer.data, dict) else {}
            if proposal_hdr and user and getattr(user, "is_authenticated", False):
                changes = self._tag_assistant_save(
                    instance, user, proposal_hdr, changes
                )
        record_audit_log(user, "create", instance, changes=changes)

    @staticmethod
    def _tag_assistant_save(instance, user, proposal_hdr, changes):
        """Mark the assistant proposal saved; return the audit changes to record."""
        try:
            import uuid
            from assistant.models import AssistantProposal

            prop_id = uuid.UUID(str(proposal_hdr).strip())
            proposal = (
                AssistantProposal.objects.filter(
                    id=prop_id,
                    status__in=["proposed", "opened", "abandoned"],
                )
                .select_related("message__conversation")
                .first()
            )
            if not (
                proposal
                and proposal.message
                and proposal.message.conversation.user_id == user.id
            ):
                return changes
            proposal.status = "saved"
            proposal.saved_model_label = (
                f"{instance._meta.app_label}.{instance._meta.object_name}"
            )
            proposal.saved_object_id = str(
                getattr(instance, "name", getattr(instance, "pk", ""))
            )
            proposal.save(
                update_fields=[
                    "status",
                    "saved_model_label",
                    "saved_object_id",
                    "updated_at",
                ]
            )
        except Exception:
            logger.exception(
                "Failed to tag assistant proposal %s on create", proposal_hdr
            )
            return changes

        # Outside the try: a failed proof copy must roll the save back, not vanish.
        from assistant.bill_photo import carry_proof

        carry_proof(proposal, instance, user)
        return {**changes, "source": "assistant", "proposal": str(proposal.id)}

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

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        from core.deletion import handle_unified_destroy
        return handle_unified_destroy(self, request, instance)


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
                    "error": _(
                        "Only draft documents can be deleted; cancel and amend instead."
                    )
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
            data = serializer.data
            # Informational messages raised while submitting (e.g. a return that
            # fell back to its own outstanding amount).
            notices = getattr(doc, "notices", None)
            if notices:
                data = {**data, "notices": notices}
            return Response(data, status=status.HTTP_200_OK)
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
                _("You do not have permission to create %(target)s.")
                % {"target": target_label}
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
