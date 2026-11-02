"""Employee expense claims and reimbursements (ERPNext "Expense Claim").

An expense the staff paid from their own pocket is recorded on an
:class:`~hr.models.ExpenseClaim`, with the receipt attached as proof.  Submitting
posts Dr each line's category expense account (sanctioned amount) / Cr the claim's
payable account (party: the employee).  The company later pays the employee back
with an :class:`~hr.models.ExpenseReimbursement`: Dr the payable account (party:
the employee) / Cr cash or bank.

``outstanding_amount`` on the claim is a stored projection: sanctioned less the live
reimbursements.  It is written only by :func:`rebuild_claim`, always with the claim
row locked; ``hr.services.integrity`` checks it and ``repost_hr`` rebuilds it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import AccountRootType
from accounts.services import gl as gl_service
from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import (
    EmployeeStatus,
    ExpenseClaim,
    ExpenseClaimDetail,
    ExpenseReimbursement,
)
from hr.services.advances import validate_paid_from
from hr.services.setup import get_settings

ZERO = Decimal("0.00")
CLAIM_PREFIX = "EXP-CLM-"
REIMBURSEMENT_PREFIX = "EXP-REIMB-"


class ClaimStatus:
    """Derived status of a claim (plain keys; the client translates them)."""

    DRAFT = "Draft"
    UNPAID = "Unpaid"
    PARTLY_PAID = "Partly Paid"
    PAID = "Paid"
    CANCELLED = "Cancelled"


def status_of(claim) -> str:
    if claim.docstatus == DocStatus.DRAFT:
        return ClaimStatus.DRAFT
    if claim.docstatus == DocStatus.CANCELLED:
        return ClaimStatus.CANCELLED
    if claim.outstanding_amount <= 0:
        return ClaimStatus.PAID
    if claim.outstanding_amount < claim.total_sanctioned:
        return ClaimStatus.PARTLY_PAID
    return ClaimStatus.UNPAID


# ----------------------------------------------------------------- derived figures


def line_totals(claim):
    """``(claimed, sanctioned)`` summed from the claim's rows in the database."""
    claimed = sanctioned = ZERO
    for line in ExpenseClaimDetail.objects.filter(claim=claim):
        claimed += line.amount
        sanctioned += line.sanctioned_amount
    return quantize_money(claimed), quantize_money(sanctioned)


def live_reimbursed(claim) -> Decimal:
    """Sum of the submitted reimbursements of the claim."""
    total = ExpenseReimbursement.objects.filter(
        expense_claim=claim, docstatus=DocStatus.SUBMITTED
    ).aggregate(t=Sum("amount"))["t"]
    return quantize_money(total or ZERO)


def rebuild_claim(claim):
    """Rewrite the stored totals and outstanding of ``claim`` from its lines and its
    live reimbursements, and refresh the in-memory instance.

    Totals are kept on drafts too; outstanding is only non-zero while submitted.
    The caller holds the claim's row lock when the claim is submitted.
    """
    claimed, sanctioned = line_totals(claim)
    outstanding = ZERO
    if claim.docstatus == DocStatus.SUBMITTED:
        outstanding = quantize_money(sanctioned - live_reimbursed(claim))
    ExpenseClaim.objects.filter(pk=claim.pk).update(
        total_claimed=claimed,
        total_sanctioned=sanctioned,
        outstanding_amount=outstanding,
    )
    claim.total_claimed = claimed
    claim.total_sanctioned = sanctioned
    claim.outstanding_amount = outstanding
    return outstanding


def repost_claims(company_id=None) -> int:
    """Rebuild every claim's stored figures (optionally of one company).  Returns
    the number of claims whose stored figures actually changed."""
    qs = ExpenseClaim.objects.all().order_by("pk")
    if company_id is not None:
        qs = qs.filter(company_id=company_id)
    changed = 0
    for pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            claim = ExpenseClaim.objects.select_for_update().get(pk=pk)
            before = (
                claim.total_claimed,
                claim.total_sanctioned,
                claim.outstanding_amount,
            )
            rebuild_claim(claim)
            after = (
                claim.total_claimed,
                claim.total_sanctioned,
                claim.outstanding_amount,
            )
            if before != after:
                changed += 1
    return changed


def owed_by_employee(company, employee_id=None) -> list:
    """Per employee: what the submitted claims ask for, what has been paid back and
    what is still owed.  Money is ``Decimal``; the API formats it."""
    claims = ExpenseClaim.objects.filter(company=company, docstatus=DocStatus.SUBMITTED)
    reimbursements = ExpenseReimbursement.objects.filter(
        company=company, docstatus=DocStatus.SUBMITTED
    )
    if employee_id is not None:
        claims = claims.filter(employee_id=employee_id)
        reimbursements = reimbursements.filter(employee_id=employee_id)

    rows = {}
    grouped = (
        claims.values("employee_id", "employee__employee_name")
        .annotate(
            claimed=Sum("total_claimed"),
            sanctioned=Sum("total_sanctioned"),
            outstanding=Sum("outstanding_amount"),
        )
        .order_by("employee__employee_name", "employee_id")
    )
    for row in grouped:
        rows[row["employee_id"]] = {
            "employee": row["employee_id"],
            "employee_name": row["employee__employee_name"],
            "claimed": quantize_money(row["claimed"] or ZERO),
            "sanctioned": quantize_money(row["sanctioned"] or ZERO),
            "reimbursed": ZERO,
            "outstanding": quantize_money(row["outstanding"] or ZERO),
        }
    for row in reimbursements.values("employee_id").annotate(paid=Sum("amount")):
        if row["employee_id"] in rows:
            rows[row["employee_id"]]["reimbursed"] = quantize_money(row["paid"] or ZERO)
    return sorted(rows.values(), key=lambda r: (r["employee_name"], r["employee"]))


# ------------------------------------------------------------------ validation


def check_header(company, employee, payable_account):
    """Rules on the claim's header that hold for a draft and a submitted claim."""
    if employee.company_id != company.pk:
        raise ValidationError(
            {"employee": _("The employee belongs to a different company.")}
        )
    if employee.status != EmployeeStatus.ACTIVE:
        raise ValidationError({"employee": _("The employee is not active.")})
    if payable_account.company_id != company.pk:
        raise ValidationError(
            {"payable_account": _("Account must belong to the company.")}
        )
    if payable_account.is_group:
        raise ValidationError(
            {"payable_account": _("Account must not be a group account.")}
        )
    if payable_account.root_type != AccountRootType.LIABILITY:
        raise ValidationError({"payable_account": _("Account must be a liability.")})


def check_lines(claim, lines):
    """Rules on each expense row (draft and submitted)."""
    for line in lines:
        if line.expense_date > claim.posting_date:
            raise ValidationError(
                {
                    "lines": _(
                        "Expense date %(date)s is after the posting date of the claim."
                    )
                    % {"date": line.expense_date}
                }
            )
        if line.expense_category.company_id != claim.company_id:
            raise ValidationError(
                {
                    "lines": _(
                        "Expense category %(category)s belongs to another company."
                    )
                    % {"category": line.expense_category.name}
                }
            )
        if line.amount <= 0:
            raise ValidationError({"lines": _("Amounts must be greater than zero.")})
        if not (ZERO <= line.sanctioned_amount <= line.amount):
            raise ValidationError(
                {
                    "lines": _(
                        "The sanctioned amount must be between zero and the claimed amount."
                    )
                }
            )


def _lines_of(claim):
    return list(
        ExpenseClaimDetail.objects.filter(claim=claim).select_related(
            "expense_category"
        )
    )


def refresh_draft(claim):
    """Validate a draft claim's rows and rebuild its totals (every draft save)."""
    check_lines(claim, _lines_of(claim))
    rebuild_claim(claim)


def validate_claim(claim):
    """Everything a claim needs before it is submitted (called under its lock)."""
    from core.attachments import has_proof

    get_settings(claim.company)  # clear error when HR is not set up
    check_header(claim.company, claim.employee, claim.payable_account)
    lines = _lines_of(claim)
    if not lines:
        raise ValidationError(
            {"lines": _("Add at least one expense before submitting.")}
        )
    check_lines(claim, lines)
    if sum((line.sanctioned_amount for line in lines), ZERO) <= 0:
        raise ValidationError(
            {"lines": _("The sanctioned total must be greater than zero.")}
        )
    if not has_proof(claim):
        raise ValidationError(_("Proof attachment is required before submission."))


def check_reimbursement_links(company, employee, claim):
    """A reimbursement pays one submitted claim of the same company and employee."""
    if employee.company_id != company.pk:
        raise ValidationError(
            {"employee": _("The employee belongs to a different company.")}
        )
    if claim.company_id != company.pk:
        raise ValidationError(
            {"expense_claim": _("The expense claim belongs to a different company.")}
        )
    if claim.employee_id != employee.pk:
        raise ValidationError(
            {"expense_claim": _("The expense claim belongs to another employee.")}
        )
    if claim.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            {"expense_claim": _("Only a submitted expense claim can be reimbursed.")}
        )


def validate_reimbursement(reimbursement):
    """Submit-time rules.  Locks the claim row, so two reimbursements cannot both
    pass the outstanding check."""
    claim = ExpenseClaim.objects.select_for_update().get(
        pk=reimbursement.expense_claim_id
    )
    check_reimbursement_links(reimbursement.company, reimbursement.employee, claim)
    get_settings(reimbursement.company)  # clear error when HR is not set up
    validate_paid_from(reimbursement.paid_from, reimbursement.company)
    if reimbursement.amount > claim.outstanding_amount:
        raise ValidationError(
            {
                "amount": _(
                    "Amount %(amount)s exceeds the outstanding amount %(outstanding)s "
                    "of expense claim %(claim)s."
                )
                % {
                    "amount": reimbursement.amount,
                    "outstanding": claim.outstanding_amount,
                    "claim": claim.name,
                }
            }
        )


# ------------------------------------------------------------------- posting


def post_claim(claim):
    """Dr each category's expense account (sanctioned) / Cr payable (party Employee)."""
    by_account = {}
    lines = (
        ExpenseClaimDetail.objects.filter(claim=claim)
        .select_related("expense_category__account")
        .order_by("idx", "pk")
    )
    for line in lines:
        if line.sanctioned_amount == 0:
            continue
        account = line.expense_category.account
        entry = by_account.setdefault(account.pk, [account, ZERO])
        entry[1] += line.sanctioned_amount
    total = sum((amount for _account, amount in by_account.values()), ZERO)
    entries = [
        {"account": account, "debit": amount} for account, amount in by_account.values()
    ]
    entries.append(
        {
            "account": claim.payable_account,
            "credit": total,
            "party_type": "Employee",
            "party": claim.employee.name,
        }
    )
    gl_service.post(
        claim,
        entries,
        posting_date=claim.posting_date,
        remarks=claim.remarks or None,
    )
    rebuild_claim(claim)


def cancel_claim(claim):
    """Refused while a submitted reimbursement is live; else reverse the GL."""
    if ExpenseReimbursement.objects.filter(
        expense_claim=claim, docstatus=DocStatus.SUBMITTED
    ).exists():
        raise ValidationError(
            _(
                "This expense claim has reimbursements in submitted payments; cancel "
                "those first."
            )
        )
    gl_service.reverse(claim)
    rebuild_claim(claim)


def post_reimbursement(reimbursement):
    """Dr the claim's payable account (party Employee) / Cr paid_from."""
    # validate_reimbursement locked the claim row in this same transaction.
    claim = ExpenseClaim.objects.get(pk=reimbursement.expense_claim_id)
    gl_service.post(
        reimbursement,
        [
            {
                "account": claim.payable_account,
                "debit": reimbursement.amount,
                "party_type": "Employee",
                "party": reimbursement.employee.name,
            },
            {"account": reimbursement.paid_from, "credit": reimbursement.amount},
        ],
        posting_date=reimbursement.posting_date,
        remarks=reimbursement.reference_no or None,
    )
    rebuild_claim(claim)


def cancel_reimbursement(reimbursement):
    """Reverse the GL and restore the claim's outstanding."""
    claim = ExpenseClaim.objects.select_for_update().get(
        pk=reimbursement.expense_claim_id
    )
    gl_service.reverse(reimbursement)
    rebuild_claim(claim)
