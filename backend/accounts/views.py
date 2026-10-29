from decimal import Decimal
import django_filters
from django.db.models import Q, Sum
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from rest_framework import viewsets, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from accounts.models import (
    Account,
    GLEntry,
    ItemTaxTemplate,
    JournalEntry,
    PaymentEntry,
    TaxTemplate,
)
from accounts.serializers import (
    AccountSerializer,
    GLEntrySerializer,
    ItemTaxTemplateSerializer,
    JournalEntrySerializer,
    PaymentEntrySerializer,
    TaxCalculateSerializer,
    TaxTemplateSerializer,
)
from accounts.reports.tally import tally_lookup
from accounts.services import outstanding as outstanding_service
from core.company_context import (
    CompanyScopedMixin,
    active_company_id,
    resolve_active_company,
)
from core.models import State
from gst.constants import GSTSupplyType
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


class AccountViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = Account.objects.all().select_related("company", "parent")
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "root_type",
        "account_type",
        "is_group",
        "is_active",
        "gst_direction",
        "gst_component",
    ]
    search_fields = ["name", "account_number"]
    ordering_fields = ["name", "created_at"]


class ItemTaxTemplateViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Global item tax templates (GST rate sets) with nested ``rows``."""

    queryset = ItemTaxTemplate.objects.all().prefetch_related("rows").order_by("name")
    serializer_class = ItemTaxTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["gst_treatment", "is_active", "is_legacy"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class TaxTemplateFilter(django_filters.FilterSet):
    """``?supply_kind=INTRA|INTER`` returns the templates of that supply type **and**
    the generic (blank supply type) ones, which fit any supply."""

    supply_kind = django_filters.ChoiceFilter(
        choices=[("INTRA", "Intra-state"), ("INTER", "Inter-state")],
        method="filter_supply_kind",
    )

    class Meta:
        model = TaxTemplate
        fields = [
            "company",
            "template_type",
            "gst_supply_type",
            "is_reverse_charge",
            "is_default",
            "disabled",
        ]

    def filter_supply_kind(self, queryset, name, value):
        supply_type = (
            GSTSupplyType.INTRA_STATE if value == "INTRA" else GSTSupplyType.INTER_STATE
        )
        return queryset.filter(Q(gst_supply_type=supply_type) | Q(gst_supply_type=""))


class TaxTemplateViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    """Per-company Sales / Purchase Taxes and Charges templates with nested ``rows``."""

    queryset = (
        TaxTemplate.objects.all()
        .select_related("company")
        .prefetch_related("rows__account")
        .order_by("company_id", "template_type", "title")
    )
    serializer_class = TaxTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_class = TaxTemplateFilter
    search_fields = ["title"]
    ordering_fields = ["title", "created_at"]


class GLEntryViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
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


class JournalEntryViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = JournalEntry.objects.all().prefetch_related("lines")
    serializer_class = JournalEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "posting_date", "docstatus"]
    search_fields = ["name", "user_remark"]
    ordering_fields = ["posting_date", "created_at"]


class PaymentEntryViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
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
    company_id = active_company_id(request)
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    accounts = Account.objects.filter(is_group=False, company_id=company_id)

    report_rows = []
    total_debit_sum = ZERO
    total_credit_sum = ZERO

    for acc in accounts:
        qs = GLEntry.objects.filter(
            account=acc, company_id=company_id, is_cancelled=False
        )
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
    company_id = active_company_id(request)
    account_id = request.query_params.get("account")
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    base = GLEntry.objects.filter(company_id=company_id, is_cancelled=False)
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
                "tally": None,
                "party": row.party,
                "debit": _money(row.debit),
                "credit": _money(row.credit),
                "balance": _money(running) if with_balance else None,
            }
        )

    tally = tally_lookup(company_id, [e["voucher_no"] for e in entries])
    if tally:
        for e in entries:
            e["tally"] = tally.get(e["voucher_no"])

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
    company_id = active_company_id(request)
    from_date = _date_param(request, "from_date")
    to_date = _date_param(request, "to_date")

    qs = GLEntry.objects.filter(company_id=company_id, is_cancelled=False)
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
    company_id = active_company_id(request)
    to_date = _date_param(request, "to_date")

    qs = GLEntry.objects.filter(company_id=company_id, is_cancelled=False)
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


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def ageing(request):
    """Accounts receivable / payable ageing.

    ``party_type`` (``Customer`` | ``Supplier``, required), ``company``, ``as_on``
    (date, default today) and ``party`` (exact party name). One row per submitted
    invoice with an outstanding amount posted on/before ``as_on``, bucketed by days
    past due (0-30 / 31-60 / 61-90 / 90+), with totals per party and a grand total.
    The invoice's *current* outstanding is used, not its balance on ``as_on``.
    """
    party_type = request.query_params.get("party_type")
    if party_type not in ("Customer", "Supplier"):
        raise ValidationError(
            {"party_type": _("party_type must be 'Customer' or 'Supplier'.")}
        )
    company_id = active_company_id(request)
    return Response(
        outstanding_service.ageing_report(
            party_type,
            as_on=_date_param(request, "as_on"),
            company_id=company_id,
            party=request.query_params.get("party"),
        )
    )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def calculate_taxes(request):
    """Stateless dry run of the tax engine (``POST /api/accounts/taxes/calculate/``).

    Takes what a Sales/Purchase Order or Invoice form holds (company, party, place
    of supply, flags, template, lines and optional tax rows) and returns the same
    supply decision, per-line GST and totals a subsequent create would produce --
    nothing is written. As on create, omitting ``taxes`` expands ``taxes_and_charges``
    or, with neither, auto-selects the company's default template. Needs
    ``can_create`` on the target document type. Tax-input problems are a 400.
    """
    from django.apps import apps
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import PermissionDenied
    from gst.services import supply
    from users.permissions import user_has_model_permission

    payload = (
        request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
    )
    company = resolve_active_company(request)
    # The active company is authoritative; a tampered body cannot price another
    # company's tax. The field stays in the payload because the serializer expects it.
    payload["company"] = company.pk

    serializer = TaxCalculateSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    label = data["doctype"]
    if not user_has_model_permission(request.user, label, "can_create"):
        raise PermissionDenied(f"You do not have permission to create {label}.")

    doc_model = apps.get_model(label)
    party_model = doc_model._meta.get_field(doc_model.party_field).related_model
    party = party_model.objects.filter(pk=data["party"]).first()
    if party is None:
        raise ValidationError({"party": _("Party not found.")})
    if party.company_id != company.pk:
        raise ValidationError({"party": _("Party must belong to the company.")})

    doc = doc_model(
        company=company,
        place_of_supply=data.get("place_of_supply"),
        taxes_and_charges=data.get("taxes_and_charges"),
        is_export_with_gst=data["is_export_with_gst"],
        is_reverse_charge=data["is_reverse_charge"],
    )
    setattr(doc, doc_model.party_field, party)
    lines = []
    for row in data["items"]:
        line = doc_model.item_model()(
            item=row["item"], qty=row["qty"], rate=row["rate"]
        )
        line.amount = line.compute_amount()
        lines.append(line)
    rows_supplied = "taxes" in data
    tax_model = doc_model.tax_model()
    rows = [
        tax_model(
            account=r["account"],
            rate=r["rate"],
            charge_type=r["charge_type"],
            add_deduct_tax=r["add_deduct_tax"],
            category=r["category"],
            description=r.get("description", ""),
        )
        for r in data.get("taxes", [])
    ]
    try:
        outcome = supply.compute(
            doc,
            lines,
            rows,
            resolve_lines=True,
            expand_template=(
                not rows_supplied and doc.taxes_and_charges_id is not None
            ),
            auto_select=(not rows_supplied and doc.taxes_and_charges_id is None),
        )
    except DjangoValidationError as exc:
        raise ValidationError(
            exc.message_dict
            if hasattr(exc, "error_dict")
            else {"non_field_errors": exc.messages}
        )

    ctx = outcome.ctx
    return Response(
        {
            "supply": {
                "kind": ctx.kind,
                "place_of_supply": ctx.place_of_supply,
                "gst_category": ctx.gst_category,
                "party_gstin": ctx.party_gstin,
                "company_gstin": ctx.company_gstin,
                "allowed_components": list(ctx.allowed_components),
                "reason": ctx.reason,
            },
            "taxes_and_charges": doc.taxes_and_charges_id,
            "taxes_and_charges_name": (
                doc.taxes_and_charges.title if doc.taxes_and_charges_id else None
            ),
            "place_of_supply": ctx.place_of_supply,
            "place_of_supply_name": (
                State.objects.filter(pk=ctx.place_of_supply)
                .values_list("name", flat=True)
                .first()
            ),
            "items": [
                {
                    "item": line.item_id,
                    "qty": str(line.qty),
                    "rate": str(line.rate),
                    "amount": str(line.amount),
                    "gst_treatment": line.gst_treatment,
                    "gst_hsn_code": line.gst_hsn_code,
                    "item_tax_rate": line.item_tax_rate,
                    "gst_rate": str(line.gst_rate),
                    "cgst_amount": str(line.cgst_amount),
                    "sgst_amount": str(line.sgst_amount),
                    "igst_amount": str(line.igst_amount),
                }
                for line in outcome.lines
            ],
            "taxes": [
                {
                    "idx": row.idx,
                    "charge_type": row.charge_type,
                    "account": row.account_id,
                    "account_name": row.account.name,
                    "gst_direction": row.account.gst_direction,
                    "gst_component": row.account.gst_component,
                    "description": row.description,
                    "rate": str(row.rate),
                    "add_deduct_tax": row.add_deduct_tax,
                    "category": row.category,
                    "net_amount": str(row.net_amount),
                    "tax_amount": str(row.tax_amount),
                    "total": str(row.total),
                }
                for row in outcome.rows
            ],
            "net_total": str(doc.net_total),
            "total_taxes_and_charges": str(doc.total_taxes_and_charges),
            "grand_total": str(doc.grand_total),
            "rounding_adjustment": str(doc.rounding_adjustment),
            "rounded_total": str(doc.rounded_total),
        }
    )
