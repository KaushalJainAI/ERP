"""Read-only dashboard summary.

Every figure is *derived* on read from the documents and the immutable ledgers
(nothing here is stored or writable). Each section is only computed -- and only
returned -- when the caller holds ``can_read`` on the model(s) it is built from,
so a Sales Manager sees sales numbers but not the books or the stock value; a
section the caller may not see is ``null``. A caller who may read none of them
gets 403.
"""

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse
from django.utils.translation import gettext as _
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from accounts.models import AccountType, GLEntry, JournalEntry, PaymentEntry
from buying.models import PurchaseInvoice, PurchaseOrder
from core import attachments as att_rules
from core.company_context import (
    companies_for,
    object_company_id,
    resolve_active_company,
)
from core.mixins import record_audit_log
from core.models import Attachment, Company, DocStatus, State, SystemHealthCheck
from selling.models import SalesInvoice, SalesOrder
from stock.models import Bin, StockEntry
from users.permissions import HasRolePermission, user_has_model_permission

ZERO = Decimal("0.00")
INTEGRITY_CHECK_NAME = "ledger_and_stock_integrity"
MAX_INTEGRITY_ERRORS = 10

#: Every model label a dashboard section is built from.
DASHBOARD_LABELS = (
    "selling.SalesInvoice",
    "selling.SalesOrder",
    "buying.PurchaseInvoice",
    "buying.PurchaseOrder",
    "accounts.GLEntry",
    "accounts.JournalEntry",
    "accounts.PaymentEntry",
    "stock.Bin",
    "stock.StockEntry",
    "core.SystemHealthCheck",
)

#: key in ``draft_documents`` -> (model, label)
DRAFT_SOURCES = {
    "sales_orders": (SalesOrder, "selling.SalesOrder"),
    "sales_invoices": (SalesInvoice, "selling.SalesInvoice"),
    "purchase_orders": (PurchaseOrder, "buying.PurchaseOrder"),
    "purchase_invoices": (PurchaseInvoice, "buying.PurchaseInvoice"),
    "stock_entries": (StockEntry, "stock.StockEntry"),
    "journal_entries": (JournalEntry, "accounts.JournalEntry"),
    "payment_entries": (PaymentEntry, "accounts.PaymentEntry"),
}


class CanReadSomeDashboardSection(permissions.BasePermission):
    """Allow a caller who can read at least one model the dashboard summarises."""

    def has_permission(self, request, view):
        user = request.user
        return any(user_has_model_permission(user, label) for label in DASHBOARD_LABELS)


def _money(value):
    return str((value or ZERO).quantize(Decimal("0.01")))


def _resolve_company(request):
    """The request's active company (see ``core.company_context``).

    Kept as a thin wrapper because the dashboard is an ``@api_view`` and several
    helpers already call this name. The client cannot choose a company through
    ``?company=`` unless it is a superuser; ordinary users are pinned to their
    membership/default.
    """
    return resolve_active_company(request)


def _ledger_balance(company, account_type, receivable):
    """Net open balance of all accounts of ``account_type`` from live GL rows."""
    agg = GLEntry.objects.filter(
        company=company, account__account_type=account_type, is_cancelled=False
    ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    dr, cr = agg["dr"] or ZERO, agg["cr"] or ZERO
    return dr - cr if receivable else cr - dr


def _open_sales_orders(company):
    """Submitted orders not yet fully invoiced (by submitted invoice net value; GST is
    a pass-through, so order and invoices are compared before tax)."""
    billed = Coalesce(
        Sum("invoices__net_total", filter=Q(invoices__docstatus=DocStatus.SUBMITTED)),
        Value(ZERO),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    orders = (
        SalesOrder.objects.filter(company=company, docstatus=DocStatus.SUBMITTED)
        .annotate(billed=billed)
        .annotate(
            remaining=ExpressionWrapper(
                F("net_total") - F("billed"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        )
        .filter(remaining__gt=0)
    )
    return {
        "count": orders.count(),
        "value": _money(sum((o.remaining for o in orders), ZERO)),
    }


def _integrity():
    row = (
        SystemHealthCheck.objects.filter(check_name=INTEGRITY_CHECK_NAME)
        .order_by("-checked_at", "-id")
        .first()
        or SystemHealthCheck.objects.order_by("-checked_at", "-id").first()
    )
    if row is None:
        return {
            "status": "never_run",
            "check_name": INTEGRITY_CHECK_NAME,
            "checked_at": None,
            "details": {},
        }
    details = dict(row.details or {})
    errors = details.get("errors")
    if isinstance(errors, list):
        details["error_count"] = len(errors)
        details["errors"] = errors[:MAX_INTEGRITY_ERRORS]
    return {
        "status": row.status,
        "check_name": row.check_name,
        "checked_at": row.checked_at.isoformat(),
        "details": details,
    }


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSomeDashboardSection])
def dashboard(request):
    """Headline numbers for one company (``?company=<id>``, default: first active)."""
    user = request.user
    can = lambda label: user_has_model_permission(user, label)  # noqa: E731

    company = _resolve_company(request)
    payload = {
        "company": (
            {
                "id": company.id,
                "name": company.name,
                "currency": company.default_currency,
            }
            if company
            else None
        ),
        "total_sales": None,
        "total_purchases": None,
        "receivables_outstanding": None,
        "payables_outstanding": None,
        "open_sales_orders": None,
        "stock_value": None,
        "draft_documents": None,
        "integrity": None,
    }
    if company is None:
        return Response(payload)

    submitted = DocStatus.SUBMITTED
    if can("selling.SalesInvoice"):
        total = SalesInvoice.objects.filter(
            company=company, docstatus=submitted
        ).aggregate(t=Sum("net_total"))["t"]
        payload["total_sales"] = _money(total)
    if can("buying.PurchaseInvoice"):
        total = PurchaseInvoice.objects.filter(
            company=company, docstatus=submitted
        ).aggregate(t=Sum("net_total"))["t"]
        payload["total_purchases"] = _money(total)
    if can("accounts.GLEntry"):
        payload["receivables_outstanding"] = _money(
            _ledger_balance(company, AccountType.RECEIVABLE, True)
        )
        payload["payables_outstanding"] = _money(
            _ledger_balance(company, AccountType.PAYABLE, False)
        )
    if can("selling.SalesOrder"):
        payload["open_sales_orders"] = _open_sales_orders(company)
    if can("stock.Bin"):
        total = Bin.objects.filter(warehouse__company=company).aggregate(
            t=Sum("stock_value")
        )["t"]
        payload["stock_value"] = _money(total)

    drafts = {
        key: model.objects.filter(company=company, docstatus=DocStatus.DRAFT).count()
        for key, (model, label) in DRAFT_SOURCES.items()
        if can(label)
    }
    if drafts:
        drafts["total"] = sum(drafts.values())
        payload["draft_documents"] = drafts

    if can("core.SystemHealthCheck"):
        payload["integrity"] = _integrity()

    return Response(payload)


class CompanySerializer(serializers.ModelSerializer):
    is_default = serializers.SerializerMethodField()
    can_write = serializers.SerializerMethodField()
    state_name = serializers.CharField(source="state.name", read_only=True)

    class Meta:
        model = Company
        fields = (
            "id",
            "name",
            "legal_name",
            "abbr",
            "default_currency",
            "country",
            "gstin",
            "is_active",
            "is_default",
            "can_write",
            "email",
            "phone_no",
            "website",
            "address_line1",
            "address_line2",
            "city",
            "pincode",
            "pan",
            "cin",
            "date_of_incorporation",
            "state",
            "state_name",
            "gst_category",
            "hsn_min_digits",
            "enable_perpetual_inventory",
            "default_inventory_account",
            "stock_adjustment_account",
            "default_expense_account",
            "default_selling_price_list",
            "default_buying_price_list",
            "enable_rounded_total",
            "round_off_account",
            "round_off_gst_values",
        )
        read_only_fields = (
            "id",
            "is_default",
            "can_write",
            "state_name",
        )

    def get_is_default(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_superuser:
            return False
        return obj.user_memberships.filter(
            user=user, is_default=True, is_active=True
        ).exists()

    def get_can_write(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user_has_model_permission(user, "core.Company", "can_write")
        )

    def update(self, instance, validated_data):
        """Apply a profile edit through the model's full cleaner.

        ``ModelSerializer`` alone skips ``Company.clean()``; GSTIN, PAN, CIN,
        PIN-code and the round-off-account ownership rule must still be checked
        server-side. Assign first, then ``full_clean`` before persisting so a
        failed validation cannot leave a half-updated row.
        """
        for field, value in validated_data.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            errors = getattr(exc, "message_dict", None) or {"detail": exc.messages}
            raise serializers.ValidationError(errors)
        instance.save()
        return instance


class CanEditCompany(permissions.BasePermission):
    """Read is membership-only; writing a company needs ``core.Company.can_write``."""

    message = "You do not have permission to edit company details."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return user_has_model_permission(request.user, "core.Company", "can_write")


class CompanyViewSet(viewsets.ReadOnlyModelViewSet, mixins.UpdateModelMixin):
    """Companies the caller may enter (never the whole table).

    Deliberately authenticated-only, not RBAC-gated: every user needs to resolve
    their own company before any model permission can be checked. The queryset
    itself is the boundary — a user only ever sees their memberships.
    """

    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated, CanEditCompany]
    filterset_fields = ["is_active"]
    search_fields = ["name", "abbr", "gstin", "pan"]
    ordering_fields = ["name"]

    def get_queryset(self):
        return companies_for(self.request.user).order_by("name")

    @action(detail=False, methods=["get"], url_path="current")
    def current(self, request):
        """The company the rest of the API resolves for this session."""
        company = resolve_active_company(request)
        return Response(self.get_serializer(company).data)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)
        record_audit_log(
            self.request.user,
            "update",
            serializer.instance,
            changes={"source": "company_profile"},
            ip_address=self.request.META.get("REMOTE_ADDR"),
        )


class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ("code", "name", "abbr", "is_union_territory", "is_active")
        read_only_fields = fields


class StateViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only GST state / union-territory list (``code`` is the primary key)."""

    queryset = State.objects.all().order_by("code")
    serializer_class = StateSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["is_active", "is_union_territory"]
    search_fields = ["code", "name", "abbr"]
    ordering_fields = ["code", "name"]


# --------------------------------------------------------------------------- #
# Attachments (proof documents)
# --------------------------------------------------------------------------- #


class AttachmentSerializer(serializers.ModelSerializer):
    """Read-only view of an attachment. ``can_delete`` is computed for the caller
    so the UI never offers a delete the server would refuse."""

    uploaded_by_name = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    previewable = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = (
            "id",
            "model_label",
            "object_id",
            "original_filename",
            "content_type",
            "size",
            "sha256",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
            "can_delete",
            "previewable",
        )
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        user = obj.uploaded_by
        return user.get_full_name() or user.email or user.username

    def get_can_delete(self, obj):
        parent = self.context.get("parent")
        if parent is None:
            return False
        user = self.context["request"].user
        return att_rules.delete_verdict(user, obj, parent) is None

    def get_previewable(self, obj):
        return obj.content_type in att_rules.INLINE_CONTENT_TYPES


class AttachmentViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Proof documents on any document or master.

    There is no model-level RBAC row for attachments: authority is *inherited from
    the parent record* (read to list/download, write/submit/create to upload,
    uploader-or-manager to remove) -- see ``core.attachments``. Files are never
    exposed under MEDIA_URL; the only way to read the bytes is ``download``.
    """

    queryset = Attachment.objects.select_related("uploaded_by")
    serializer_class = AttachmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = None
    filter_backends: list = []

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _error(exc):
        return Response({"error": exc.message, "code": exc.code}, status=exc.status)

    @staticmethod
    def _forbidden():
        return att_rules.AttachmentError(
            "attachment_forbidden", _("You do not have permission to do that."), 403
        )

    @staticmethod
    def _not_found():
        return att_rules.AttachmentError(
            "attachment_not_found", _("Attachment not found."), 404
        )

    def _parent_ref(self, data):
        label = att_rules.resolve_parent_label(
            data.get("model", ""), data.get("endpoint", "")
        )
        object_id = str(data.get("object_id", "")).strip()
        if not object_id:
            raise att_rules.AttachmentError(
                "attachment_parent_invalid",
                _("The record to attach to is missing."),
                400,
            )
        return label, object_id

    def _load_readable(self, pk):
        """The attachment and its parent, after the parent's read check."""
        att = Attachment.objects.select_related("uploaded_by").filter(pk=pk).first()
        if att is None:
            raise self._not_found()
        if not att_rules.can_read(self.request.user, att.model_label):
            raise self._forbidden()
        parent = att_rules.get_parent(att.model_label, att.object_id)
        if parent is None:
            raise self._not_found()
        self._check_parent_company(parent)
        return att, parent

    def _check_parent_company(self, parent):
        """An attachment may only be reached through the active company's parent."""
        company_id = object_company_id(parent)
        if company_id is not None and company_id != resolve_active_company(self.request).pk:
            raise self._not_found()

    # -- endpoints --------------------------------------------------------- #

    def list(self, request, *args, **kwargs):
        try:
            label, object_id = self._parent_ref(request.query_params)
            if not att_rules.can_read(request.user, label):
                raise self._forbidden()
            parent = att_rules.get_parent(label, object_id)
            if parent is None:
                raise self._not_found()
            self._check_parent_company(parent)
        except att_rules.AttachmentError as exc:
            return self._error(exc)
        rows = Attachment.objects.select_related("uploaded_by").filter(
            model_label=label, object_id=object_id
        )
        data = AttachmentSerializer(
            rows, many=True, context={"request": request, "parent": parent}
        ).data
        return Response(
            {
                "results": data,
                "max_bytes": att_rules.max_bytes(),
                "allowed_extensions": att_rules.allowed_extensions(),
                "can_upload": att_rules.can_upload(request.user, label),
            }
        )

    def create(self, request, *args, **kwargs):
        try:
            label, object_id = self._parent_ref(request.data)
            if not att_rules.can_upload(request.user, label):
                raise self._forbidden()
            parent = att_rules.get_parent(label, object_id)
            if parent is None:
                raise self._not_found()
            self._check_parent_company(parent)
            upload = request.FILES.get("file")
            checked = att_rules.validate_upload(upload)
            description = str(request.data.get("description", "")).strip()[:240]
            if Attachment.objects.filter(
                model_label=label, object_id=object_id, sha256=checked.sha256
            ).exists():
                raise self._duplicate()
            with transaction.atomic():
                att = Attachment(
                    model_label=label,
                    object_id=object_id,
                    original_filename=checked.filename,
                    content_type=checked.content_type,
                    size=checked.size,
                    sha256=checked.sha256,
                    description=description,
                    uploaded_by=request.user,
                )
                att.file.save(checked.filename, upload, save=False)
                try:
                    att.save()
                except IntegrityError:
                    att.file.storage.delete(att.file.name)
                    raise self._duplicate()
                record_audit_log(
                    request.user,
                    "attachment_add",
                    parent,
                    changes={
                        "attachment_id": att.pk,
                        "filename": att.original_filename,
                        "content_type": att.content_type,
                        "size": att.size,
                        "sha256": att.sha256,
                    },
                )
        except att_rules.AttachmentError as exc:
            return self._error(exc)
        data = AttachmentSerializer(
            att, context={"request": request, "parent": parent}
        ).data
        return Response(data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _duplicate():
        return att_rules.AttachmentError(
            "attachment_duplicate",
            _("This file is already attached to this record."),
            409,
        )

    def destroy(self, request, *args, **kwargs):
        try:
            att, parent = self._load_readable(kwargs["pk"])
            verdict = att_rules.delete_verdict(request.user, att, parent)
            if verdict is not None:
                raise verdict
            changes = {
                "attachment_id": att.pk,
                "filename": att.original_filename,
                "sha256": att.sha256,
            }
            with transaction.atomic():
                att.delete()
                record_audit_log(
                    request.user, "attachment_delete", parent, changes=changes
                )
        except att_rules.AttachmentError as exc:
            return self._error(exc)
        except DjangoValidationError as exc:
            return self._error(
                att_rules.AttachmentError(
                    "attachment_locked", " ".join(exc.messages), 409
                )
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        """Stream the file. ``?inline=1`` asks the browser to render it (images and
        PDF only); anything else is always a forced download."""
        try:
            att, _parent = self._load_readable(pk)
        except att_rules.AttachmentError as exc:
            return self._error(exc)
        try:
            handle = att.file.open("rb")
        except (OSError, ValueError):
            return self._error(
                att_rules.AttachmentError(
                    "attachment_file_missing", _("The stored file is missing."), 404
                )
            )
        inline = (
            request.query_params.get("inline") == "1"
            and att.content_type in att_rules.INLINE_CONTENT_TYPES
        )
        response = FileResponse(
            handle,
            content_type=att.content_type,
            as_attachment=not inline,
            filename=att.original_filename,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, no-store"
        return response
