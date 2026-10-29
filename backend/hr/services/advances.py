"""Employee advances: derived recovery / outstanding and GL posting.

``recovered_amount`` is the sum of the recovery rows of *submitted* salary slips
that reference the advance; ``outstanding`` is ``amount - recovered``.  Neither
is stored, so there is nothing to drift (and nothing to repost).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from accounts.models import AccountRootType
from accounts.services import gl as gl_service
from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import Employee, EmployeeAdvance, SalarySlipDetail
from hr.services.setup import get_settings

ZERO = Decimal("0.00")


class AdvanceStatus:
    DRAFT = "Draft"
    CANCELLED = "Cancelled"
    PAID = "Paid"
    PARTLY_RECOVERED = "Partly Recovered"
    RECOVERED = "Recovered"


def recovered_amount(advance, exclude_slip=None):
    qs = SalarySlipDetail.objects.filter(
        advance=advance, slip__docstatus=DocStatus.SUBMITTED
    )
    if exclude_slip is not None:
        qs = qs.exclude(slip=exclude_slip)
    return quantize_money(qs.aggregate(t=Sum("amount"))["t"] or ZERO)


def outstanding(advance, exclude_slip=None):
    if advance.docstatus != DocStatus.SUBMITTED:
        return ZERO
    return advance.amount - recovered_amount(advance, exclude_slip)


def status_of(advance, recovered=None):
    if advance.docstatus == DocStatus.DRAFT:
        return AdvanceStatus.DRAFT
    if advance.docstatus == DocStatus.CANCELLED:
        return AdvanceStatus.CANCELLED
    if recovered is None:
        recovered = recovered_amount(advance)
    if recovered <= 0:
        return AdvanceStatus.PAID
    if recovered < advance.amount:
        return AdvanceStatus.PARTLY_RECOVERED
    return AdvanceStatus.RECOVERED


def open_advances(employee, company, up_to_date):
    """Submitted advances of the employee with something left to recover, oldest first."""
    result = []
    for advance in EmployeeAdvance.objects.filter(
        employee=employee,
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__lte=up_to_date,
    ).order_by("posting_date", "pk"):
        left = outstanding(advance)
        if left > 0:
            result.append((advance, left))
    return result


def validate_paid_from(account, company, field="paid_from"):
    """The cash / bank account used to pay out: an asset leaf of the company."""
    if account.company_id != company.pk:
        raise ValidationError({field: _("Account must belong to the company.")})
    if account.is_group:
        raise ValidationError({field: _("Account must not be a group account.")})
    if account.root_type != AccountRootType.ASSET:
        raise ValidationError({field: _("Account must be an asset (cash or bank).")})


def validate_advance(advance):
    if advance.employee.company_id != advance.company_id:
        raise ValidationError(_("The employee belongs to a different company."))
    validate_paid_from(advance.paid_from, advance.company)
    get_settings(advance.company)  # clear error when HR is not set up


def post_advance(advance):
    """Dr employee advances (party Employee) / Cr the cash or bank account."""
    settings_row = get_settings(advance.company)
    gl_service.post(
        advance,
        [
            {
                "account": settings_row.employee_advance_account,
                "debit": advance.amount,
                "party_type": "Employee",
                "party": advance.employee.name,
            },
            {"account": advance.paid_from, "credit": advance.amount},
        ],
        posting_date=advance.posting_date,
        remarks=advance.purpose or None,
    )


def cancel_advance(advance):
    """Refuse while submitted slips recover from it, else reverse the GL."""
    # Serialise with slip submission, which locks the advance rows it recovers.
    Employee.objects.select_for_update().get(pk=advance.employee_id)
    if recovered_amount(advance) > 0:
        raise ValidationError(
            _(
                "This advance has recoveries in submitted salary slips; cancel those "
                "slips first."
            )
        )
    gl_service.reverse(advance)
