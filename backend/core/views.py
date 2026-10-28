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
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from accounts.models import AccountType, GLEntry, JournalEntry, PaymentEntry
from buying.models import PurchaseInvoice, PurchaseOrder
from core.models import Company, DocStatus, State, SystemHealthCheck
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
    raw = request.query_params.get("company")
    if raw in (None, ""):
        return Company.objects.filter(is_active=True).order_by("id").first()
    try:
        company_id = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"company": "Must be a company id."})
    try:
        return Company.objects.get(pk=company_id)
    except Company.DoesNotExist:
        raise NotFound("Company not found.")


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
    class Meta:
        model = Company
        fields = (
            "id",
            "name",
            "abbr",
            "default_currency",
            "country",
            "gstin",
            "is_active",
            "enable_perpetual_inventory",
            "default_inventory_account",
            "stock_adjustment_account",
            "default_expense_account",
            "state",
            "gst_category",
            "hsn_min_digits",
            "enable_rounded_total",
            "round_off_account",
            "round_off_gst_values",
        )
        read_only_fields = fields


class CompanyViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only company list/detail (for company pickers and link fields)."""

    queryset = Company.objects.all().order_by("name")
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["is_active"]
    search_fields = ["name", "abbr"]
    ordering_fields = ["name"]


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
