from decimal import Decimal
from django.db.models import Sum
from rest_framework import viewsets, permissions
from rest_framework.decorators import api_view, permission_classes
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
    filterset_fields = ["company", "account", "posting_date", "voucher_type", "voucher_no", "is_cancelled"]
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
@permission_classes([permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")])
def trial_balance(request):
    """Trial Balance report endpoint."""
    company_id = request.query_params.get("company")
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")

    accounts = Account.objects.filter(is_group=False)
    if company_id:
        accounts = accounts.filter(company_id=company_id)

    report_rows = []
    total_debit_sum = Decimal("0.00")
    total_credit_sum = Decimal("0.00")

    for acc in accounts:
        qs = GLEntry.objects.filter(account=acc, is_cancelled=False)
        if company_id:
            qs = qs.filter(company_id=company_id)
        if from_date:
            qs = qs.filter(posting_date__gte=from_date)
        if to_date:
            qs = qs.filter(posting_date__lte=to_date)

        agg = qs.aggregate(sum_dr=Sum("debit"), sum_cr=Sum("credit"))
        dr = agg["sum_dr"] or Decimal("0.00")
        cr = agg["sum_cr"] or Decimal("0.00")

        if dr == 0 and cr == 0:
            continue

        net = dr - cr
        total_debit_sum += dr
        total_credit_sum += cr

        report_rows.append({
            "account_id": acc.id,
            "account_name": acc.name,
            "root_type": acc.root_type,
            "debit": str(dr),
            "credit": str(cr),
            "net_balance": str(net),
        })

    return Response({
        "rows": report_rows,
        "total_debit": str(total_debit_sum),
        "total_credit": str(total_credit_sum),
        "is_balanced": total_debit_sum == total_credit_sum,
    })


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")])
def general_ledger(request):
    company_id = request.query_params.get("company")
    account_id = request.query_params.get("account")
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")

    qs = GLEntry.objects.filter(is_cancelled=False).select_related("account", "company").order_by("posting_date", "id")
    if company_id:
        qs = qs.filter(company_id=company_id)
    if account_id:
        qs = qs.filter(account_id=account_id)
    if from_date:
        qs = qs.filter(posting_date__gte=from_date)
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)

    entries = []
    running_balance = Decimal("0.00")
    for row in qs:
        running_balance += (row.debit - row.credit)
        entries.append({
            "id": row.id,
            "posting_date": str(row.posting_date),
            "account_name": row.account.name,
            "voucher_type": row.voucher_type,
            "voucher_no": row.voucher_no,
            "party": row.party,
            "debit": str(row.debit),
            "credit": str(row.credit),
            "balance": str(running_balance),
        })

    return Response({"entries": entries, "closing_balance": str(running_balance)})


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")])
def profit_and_loss(request):
    company_id = request.query_params.get("company")
    qs = GLEntry.objects.filter(is_cancelled=False)
    if company_id:
        qs = qs.filter(company_id=company_id)

    income_agg = qs.filter(account__root_type="Income").aggregate(dr=Sum("debit"), cr=Sum("credit"))
    expense_agg = qs.filter(account__root_type="Expense").aggregate(dr=Sum("debit"), cr=Sum("credit"))

    total_income = (income_agg["cr"] or Decimal("0.00")) - (income_agg["dr"] or Decimal("0.00"))
    total_expense = (expense_agg["dr"] or Decimal("0.00")) - (expense_agg["cr"] or Decimal("0.00"))
    net_profit = total_income - total_expense

    return Response({
        "total_income": str(total_income),
        "total_expense": str(total_expense),
        "net_profit": str(net_profit),
    })


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")])
def balance_sheet(request):
    company_id = request.query_params.get("company")
    qs = GLEntry.objects.filter(is_cancelled=False)
    if company_id:
        qs = qs.filter(company_id=company_id)

    asset_agg = qs.filter(account__root_type="Asset").aggregate(dr=Sum("debit"), cr=Sum("credit"))
    liab_agg = qs.filter(account__root_type="Liability").aggregate(dr=Sum("debit"), cr=Sum("credit"))
    eq_agg = qs.filter(account__root_type="Equity").aggregate(dr=Sum("debit"), cr=Sum("credit"))

    total_assets = (asset_agg["dr"] or Decimal("0.00")) - (asset_agg["cr"] or Decimal("0.00"))
    total_liabilities = (liab_agg["cr"] or Decimal("0.00")) - (liab_agg["dr"] or Decimal("0.00"))
    total_equity = (eq_agg["cr"] or Decimal("0.00")) - (eq_agg["dr"] or Decimal("0.00"))

    return Response({
        "total_assets": str(total_assets),
        "total_liabilities": str(total_liabilities),
        "total_equity": str(total_equity),
    })

