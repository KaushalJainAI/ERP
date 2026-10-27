from decimal import Decimal
from django.db.models import Sum
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from rest_framework import viewsets, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from accounts.models import Account, GLEntry, JournalEntry, PaymentEntry
from accounts.serializers import (
    AccountSerializer,
    GLEntrySerializer,
    JournalEntrySerializer,
    PaymentEntrySerializer,
)
from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from users.permissions import HasRolePermission, require_model_permission


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _q(value):
    """Quantize a money Decimal to 2dp (SQL SUM() may return un-scaled values)."""
    return (value or ZERO).quantize(CENT)


def _money(value):
    return str(_q(value))


def _date_param(request, name):
    """Parse an optional ISO (YYYY-MM-DD) query param; 400 on malformed input."""
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise ValidationError({name: _("Enter a valid date (YYYY-MM-DD).")})
    return parsed


def _root_balances(qs, root_type, debit_normal):
    """Per-account balances for one root type, signed to the account's normal side."""
    rows = (
        qs.filter(account__root_type=root_type)
        .values("account_id", "account__name", "account__root_type")
        .annotate(dr=Sum("debit"), cr=Sum("credit"))
        .order_by("account__name", "account_id")
    )
    out = []
    total = ZERO
    for r in rows:
        dr = r["dr"] or ZERO
        cr = r["cr"] or ZERO
        bal = _q(dr - cr if debit_normal else cr - dr)
        total += bal
        if dr == 0 and cr == 0:
            continue
        out.append(
            {
                "account_id": r["account_id"],
                "account_name": r["account__name"],
                "root_type": r["account__root_type"],
                "balance": str(bal),
            }
        )
    return out, _q(total)


class AccountViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Account.objects.all().select_related("company", "parent")
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "root_type", "account_type", "is_group", "is_active"]
    search_fields = ["name", "account_number"]
    ordering_fields = ["name", "created_at"]


class GLEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """ReadOnlyModelViewSet for immutable GLEntry facts."""

    queryset = GLEntry.objects.all().select_related("account", "company")
    serializer_class = GLEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = {
        "company": ["exact"],
        "account": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
        "voucher_type": ["exact"],
        "voucher_no": ["exact"],
        "is_cancelled": ["exact"],
    }
    search_fields = ["voucher_no", "party", "remarks"]
    ordering_fields = ["posting_date", "created_at"]


class JournalEntryViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalEntry.objects.all().prefetch_related("lines")
    serializer_class = JournalEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "posting_date", "docstatus"]
    search_fields = ["name", "user_remark"]
    ordering_fields = ["posting_date", "created_at"]


class PaymentEntryViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = PaymentEntry.objects.all().prefetch_related("references")
    serializer_class = PaymentEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "payment_type", "posting_date", "docstatus"]
    search_fields = ["name", "party_name", "reference_no"]
    ordering_fields = ["posting_date", "created_at"]


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def trial_balance(request):
    """Trial Balance report endpoint. Money values are quantized to 2dp."""
    company_id = request.query_params.get("company")
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    accounts = Account.objects.filter(is_group=False)
    if company_id:
        accounts = accounts.filter(company_id=company_id)

    report_rows = []
    total_debit_sum = ZERO
    total_credit_sum = ZERO

    for acc in accounts:
        qs = GLEntry.objects.filter(account=acc, is_cancelled=False)
        if company_id:
            qs = qs.filter(company_id=company_id)
        if from_date:
            qs = qs.filter(posting_date__gte=from_date)
        if to_date:
            qs = qs.filter(posting_date__lte=to_date)

        agg = qs.aggregate(sum_dr=Sum("debit"), sum_cr=Sum("credit"))
        dr = _q(agg["sum_dr"])
        cr = _q(agg["sum_cr"])

        if dr == 0 and cr == 0:
            continue

        net = dr - cr
        total_debit_sum += dr
        total_credit_sum += cr

        report_rows.append(
            {
                "account_id": acc.id,
                "account_name": acc.name,
                "root_type": acc.root_type,
                "debit": str(dr),
                "credit": str(cr),
                "net_balance": str(net),
            }
        )

    return Response(
        {
            "rows": report_rows,
            "total_debit": str(total_debit_sum),
            "total_credit": str(total_credit_sum),
            "is_balanced": total_debit_sum == total_credit_sum,
        }
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def general_ledger(request):
    """General Ledger.

    Mirrors ERPNext: with an ``account`` filter the report carries an
    ``opening_balance`` (net debit-credit of everything *before* ``from_date``;
    0.00 when no ``from_date``) and every row's ``balance`` is a running balance
    that starts from it. ``closing_balance`` = opening + the period's net.

    Without an ``account`` filter the rows span several accounts, so a running
    balance would be meaningless: ``opening_balance``, each row's ``balance`` and
    ``closing_balance`` are all ``null``.
    """
    company_id = request.query_params.get("company")
    account_id = request.query_params.get("account")
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    base = GLEntry.objects.filter(is_cancelled=False)
    if company_id:
        base = base.filter(company_id=company_id)
    if account_id:
        base = base.filter(account_id=account_id)

    qs = base.select_related("account", "company").order_by("posting_date", "id")
    if from_date:
        qs = qs.filter(posting_date__gte=from_date)
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)

    with_balance = bool(account_id)
    opening = None
    running = None
    if with_balance:
        opening = ZERO
        if from_date:
            agg = base.filter(posting_date__lt=from_date).aggregate(
                dr=Sum("debit"), cr=Sum("credit")
            )
            opening = _q((agg["dr"] or ZERO) - (agg["cr"] or ZERO))
        running = opening

    entries = []
    for row in qs:
        if with_balance:
            running += row.debit - row.credit
        entries.append(
            {
                "id": row.id,
                "posting_date": str(row.posting_date),
                "account_name": row.account.name,
                "voucher_type": row.voucher_type,
                "voucher_no": row.voucher_no,
                "party": row.party,
                "debit": _money(row.debit),
                "credit": _money(row.credit),
                "balance": _money(running) if with_balance else None,
            }
        )

    return Response(
        {
            "entries": entries,
            "opening_balance": _money(opening) if with_balance else None,
            "closing_balance": _money(running) if with_balance else None,
        }
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def profit_and_loss(request):
    """Profit & Loss for the period ``from_date``..``to_date`` (both optional).

    No params = all time. ``accounts`` lists the per-account breakdown
    (Income balance = credit-debit, Expense balance = debit-credit).
    """
    company_id = request.query_params.get("company")
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    qs = GLEntry.objects.filter(is_cancelled=False)
    if company_id:
        qs = qs.filter(company_id=company_id)
    if from_date:
        qs = qs.filter(posting_date__gte=from_date)
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)

    income_rows, total_income = _root_balances(qs, "Income", debit_normal=False)
    expense_rows, total_expense = _root_balances(qs, "Expense", debit_normal=True)
    net_profit = total_income - total_expense

    return Response(
        {
            "total_income": str(total_income),
            "total_expense": str(total_expense),
            "net_profit": str(net_profit),
            "accounts": income_rows + expense_rows,
        }
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def balance_sheet(request):
    """Balance Sheet as of ``to_date`` (everything up to and including it).

    No ``to_date`` = all time. ``accounts`` lists the per-account breakdown
    (Asset = debit-credit; Liability/Equity = credit-debit).
    """
    company_id = request.query_params.get("company")
    to_date = _date_param(request, "to_date")

    qs = GLEntry.objects.filter(is_cancelled=False)
    if company_id:
        qs = qs.filter(company_id=company_id)
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)

    asset_rows, total_assets = _root_balances(qs, "Asset", debit_normal=True)
    liab_rows, total_liabilities = _root_balances(qs, "Liability", debit_normal=False)
    eq_rows, total_equity = _root_balances(qs, "Equity", debit_normal=False)

    return Response(
        {
            "total_assets": str(total_assets),
            "total_liabilities": str(total_liabilities),
            "total_equity": str(total_equity),
            "accounts": asset_rows + liab_rows + eq_rows,
        }
    )
