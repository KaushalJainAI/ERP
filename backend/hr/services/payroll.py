"""Salary slip compute / validate / post, and Payroll Entry bulk actions.

Slip rows are of two kinds:

* generated (``is_manual=False``): Wages and Overtime from attendance and wage
  rates, and proposed Advance Recovery rows.  Wages / Overtime are rebuilt on
  **every** draft save, so the slip never drifts from the attendance behind it.
* manual (``is_manual=True``): anything a person typed (bonus, fine, an edited
  recovery).  These survive a recompute.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils.translation import gettext_lazy as _

from accounts.services import gl as gl_service
from core.models import DocStatus, NamingSeries
from hr.models import (
    ComponentType,
    Employee,
    EmployeeAdvance,
    EmployeePayment,
    EmployeeStatus,
    PayrollEntryEmployee,
    SalarySlip,
    SalarySlipDetail,
    SystemComponent,
)
from hr.services import advances as advance_service
from hr.services import tds as tds_service
from hr.services.balances import payable_balance
from hr.services.setup import get_settings, system_components
from hr.services.wages import compute_wages

ZERO = Decimal("0.00")
SLIP_PREFIX = "SAL-"
PAYMENT_PREFIX = "EMP-PAY-"
GENERATED_KEYS = (SystemComponent.WAGES, SystemComponent.OVERTIME)


def _messages(exc):
    return "; ".join(str(m) for m in exc.messages)


# ------------------------------------------------------------------ compute
def overlapping_slip(employee_id, start_date, end_date, exclude_pk=None):
    """A submitted slip of the employee whose range intersects the given one."""
    qs = SalarySlip.objects.filter(
        employee_id=employee_id,
        docstatus=DocStatus.SUBMITTED,
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.order_by("start_date").first()


def regenerate_system_rows(slip):
    """Rebuild the Wages / Overtime rows and the day / hour counts of a draft slip."""
    components = system_components(slip.company)
    result = compute_wages(slip.employee, slip.start_date, slip.end_date)
    SalarySlipDetail.objects.filter(
        slip=slip, component__system_key__in=GENERATED_KEYS
    ).delete()
    if result.wages > 0:
        SalarySlipDetail.objects.create(
            slip=slip,
            idx=0,
            component=components[SystemComponent.WAGES],
            amount=result.wages,
            is_manual=False,
            remarks=f"{result.payment_days} days",
        )
    if result.overtime > 0:
        SalarySlipDetail.objects.create(
            slip=slip,
            idx=0,
            component=components[SystemComponent.OVERTIME],
            amount=result.overtime,
            is_manual=False,
            remarks=f"{result.ot_hours} h",
        )
    slip.payment_days = result.payment_days
    slip.ot_hours = result.ot_hours
    return result


def _sum_by_type(slip):
    totals = {ComponentType.EARNING: ZERO, ComponentType.DEDUCTION: ZERO}
    for row in slip.details.select_related("component"):
        totals[row.component.component_type] += row.amount
    return totals


def refresh_slip_totals(slip):
    """Rebuild ``gross_pay`` / ``total_deduction`` / ``net_pay`` from the rows."""
    totals = _sum_by_type(slip)
    slip.gross_pay = totals[ComponentType.EARNING]
    slip.total_deduction = totals[ComponentType.DEDUCTION]
    slip.net_pay = slip.gross_pay - slip.total_deduction
    slip.save(
        update_fields=[
            "payment_days",
            "ot_hours",
            "gross_pay",
            "total_deduction",
            "net_pay",
            "modified_at",
        ]
    )
    slip._prefetched_objects_cache = {}


def refresh_draft_slip(slip):
    """Called on every draft save: regenerate Wages / Overtime and the totals."""
    if slip.docstatus != DocStatus.DRAFT:
        raise ValidationError(_("Only draft salary slips can be recomputed."))
    regenerate_system_rows(slip)
    tds_service.refresh_tds(slip)
    refresh_slip_totals(slip)


def propose_advance_recovery(slip):
    """Add recovery rows for open advances: the instalment (or the whole balance
    when there is no instalment), capped by what is left of the pay so net >= 0.
    Advances that already have a row on the slip are left alone."""
    components = system_components(slip.company)
    recovery = components[SystemComponent.ADVANCE_RECOVERY]
    totals = _sum_by_type(slip)
    available = totals[ComponentType.EARNING] - totals[ComponentType.DEDUCTION]
    if available <= 0:
        return
    present = set(
        slip.details.exclude(advance__isnull=True).values_list("advance_id", flat=True)
    )
    for advance, left in advance_service.open_advances(
        slip.employee, slip.company, slip.end_date
    ):
        if advance.pk in present:
            continue
        amount = min(advance.instalment_amount or left, left, available)
        if amount <= 0:
            continue
        SalarySlipDetail.objects.create(
            slip=slip,
            idx=900,
            component=recovery,
            amount=amount,
            is_manual=False,
            advance=advance,
            remarks=advance.name,
        )
        available -= amount


def recompute_slip(slip):
    """Draft-only full recompute: drop every generated row, rebuild Wages /
    Overtime from attendance, re-propose recoveries; manual rows are kept."""
    if slip.docstatus != DocStatus.DRAFT:
        raise ValidationError(_("Only draft salary slips can be recomputed."))
    with transaction.atomic():
        SalarySlip.objects.select_for_update().get(pk=slip.pk)
        slip.details.filter(is_manual=False).delete()
        regenerate_system_rows(slip)
        refresh_slip_totals(slip)
        tds_service.propose_tds(slip)
        propose_advance_recovery(slip)
        refresh_slip_totals(slip)
    return slip


def create_draft_slip(
    employee, start_date, end_date, posting_date, payroll_entry=None, user=None
):
    """A computed draft slip (used by Payroll Entry ``create-slips``)."""
    with transaction.atomic():
        slip = SalarySlip.objects.create(
            name=NamingSeries.next(SLIP_PREFIX),
            employee=employee,
            company=employee.company,
            payroll_entry=payroll_entry,
            posting_date=posting_date,
            start_date=start_date,
            end_date=end_date,
            created_by=user if getattr(user, "pk", None) else None,
        )
        refresh_draft_slip(slip)  # also proposes the TDS row
        propose_advance_recovery(slip)
        refresh_slip_totals(slip)
    return slip


# ------------------------------------------------------------------ submit
def _stored_sum(slip, key):
    return (
        slip.details.filter(component__system_key=key).aggregate(t=Sum("amount"))["t"]
        or ZERO
    )


def validate_slip(slip):
    """Everything that must hold before a slip may post (runs in the submit
    transaction, on a draft row)."""
    settings_row = get_settings(slip.company)  # clear error when HR is not set up
    # Lock the employee: it serialises concurrent slip submissions (so the
    # overlap check below cannot be raced past) and attendance writes, which take
    # the same lock before they check for a covering slip.
    employee = Employee.objects.select_for_update().get(pk=slip.employee_id)
    if employee.company_id != slip.company_id:
        raise ValidationError(_("The employee belongs to a different company."))

    clash = overlapping_slip(employee.pk, slip.start_date, slip.end_date, slip.pk)
    if clash is not None:
        raise ValidationError(
            _(
                "Salary slip %(slip)s (%(start)s to %(end)s) already covers part of "
                "this period for %(employee)s."
            )
            % {
                "slip": clash.name,
                "start": clash.start_date,
                "end": clash.end_date,
                "employee": employee.employee_name,
            }
        )

    # The generated rows must still match the attendance and rates they came from.
    fresh = compute_wages(employee, slip.start_date, slip.end_date)
    if (
        _stored_sum(slip, SystemComponent.WAGES) != fresh.wages
        or _stored_sum(slip, SystemComponent.OVERTIME) != fresh.overtime
    ):
        raise ValidationError(
            _(
                "Attendance or wage rates changed since this slip was calculated; "
                "recompute it before submitting."
            )
        )
    slip.payment_days = fresh.payment_days
    slip.ot_hours = fresh.ot_hours
    tds_service.check_tds(slip, employee, settings_row)

    rows = list(slip.details.select_related("component__account", "advance"))
    for row in rows:
        component = row.component
        if component.company_id != slip.company_id:
            raise ValidationError(
                _("Component %(c)s belongs to a different company.")
                % {"c": component.name}
            )
        if (
            component.account.company_id != slip.company_id
            or component.account.is_group
        ):
            raise ValidationError(
                _("The account of component %(c)s is not postable for this company.")
                % {"c": component.name}
            )
        is_recovery = component.system_key == SystemComponent.ADVANCE_RECOVERY
        if is_recovery != (row.advance_id is not None):
            raise ValidationError(
                _(
                    "Advance recovery rows must reference an employee advance, and "
                    "only they may."
                )
            )

    refresh_slip_totals(slip)
    if slip.gross_pay <= 0:
        raise ValidationError(_("A salary slip with no earnings cannot be submitted."))
    if slip.net_pay < 0:
        raise ValidationError(_("Net pay cannot be negative."))

    # Recoveries: lock each advance, then check the cumulative recovery.
    per_advance = {}
    for row in rows:
        if row.advance_id:
            per_advance[row.advance_id] = (
                per_advance.get(row.advance_id, ZERO) + row.amount
            )
    for advance in (
        EmployeeAdvance.objects.select_for_update()
        .filter(pk__in=per_advance)
        .order_by("pk")
    ):
        if (
            advance.docstatus != DocStatus.SUBMITTED
            or advance.employee_id != slip.employee_id
            or advance.company_id != slip.company_id
        ):
            raise ValidationError(
                _("Advance %(a)s cannot be recovered on this salary slip.")
                % {"a": advance.name}
            )
        left = advance_service.outstanding(advance, exclude_slip=slip)
        if per_advance[advance.pk] > left:
            raise ValidationError(
                _(
                    "Recovery of %(amount)s exceeds the outstanding %(left)s of "
                    "advance %(a)s."
                )
                % {"amount": per_advance[advance.pk], "left": left, "a": advance.name}
            )


def post_slip(slip):
    """Dr each earning component's account (gross); Cr wages payable (net, party
    Employee); Cr employee advances (party Employee) for recoveries; Cr the
    component account for any other deduction."""
    settings_row = get_settings(slip.company)
    party = {"party_type": "Employee", "party": slip.employee.name}
    entries = []
    for row in slip.details.select_related("component__account", "advance"):
        component = row.component
        if component.component_type == ComponentType.EARNING:
            entries.append({"account": component.account, "debit": row.amount})
        elif row.advance_id:
            entries.append(
                {
                    "account": settings_row.employee_advance_account,
                    "credit": row.amount,
                    "against_voucher_type": "hr.EmployeeAdvance",
                    "against_voucher_no": row.advance.name,
                    **party,
                }
            )
        elif component.system_key == SystemComponent.TDS:
            # The TDS payable account is HR Settings', not the component's own
            # account, so changing it there takes effect on the next slip.
            entries.append(
                {"account": settings_row.tds_payable_account, "credit": row.amount}
            )
        else:
            entries.append({"account": component.account, "credit": row.amount})
    entries.append(
        {
            "account": settings_row.payroll_payable_account,
            "credit": slip.net_pay,
            **party,
        }
    )
    gl_service.post(
        slip,
        entries,
        posting_date=slip.posting_date,
        remarks=f"Wages {slip.start_date} to {slip.end_date}",
    )


def cancel_slip(slip):
    """Reverse the postings -- unless payments already consumed the wages."""
    employee = Employee.objects.select_for_update().get(pk=slip.employee_id)
    balance = payable_balance(employee, slip.company)
    if balance - slip.net_pay < 0:
        raise ValidationError(
            _(
                "Wages of %(net)s from this slip have already been paid out; cancel "
                "the payments first."
            )
            % {"net": slip.net_pay}
        )
    gl_service.reverse(slip)


# ------------------------------------------------------------ payroll entry
def eligible_employees(entry):
    qs = Employee.objects.filter(
        company=entry.company,
        status=EmployeeStatus.ACTIVE,
        date_of_joining__lte=entry.end_date,
    ).filter(Q(relieving_date__isnull=True) | Q(relieving_date__gte=entry.start_date))
    if entry.department_id:
        qs = qs.filter(department_id=entry.department_id)
    if entry.branch_id:
        qs = qs.filter(branch_id=entry.branch_id)
    return qs.order_by("name")


def get_employees(entry):
    """Add the matching active employees to a draft entry (existing rows stay)."""
    if entry.docstatus != DocStatus.DRAFT:
        raise ValidationError(_("Only a draft payroll entry can be changed."))
    have = set(entry.employees.values_list("employee_id", flat=True))
    with transaction.atomic():
        for employee in eligible_employees(entry):
            if employee.pk not in have:
                PayrollEntryEmployee.objects.create(
                    payroll_entry=entry, employee=employee
                )
    return entry.employees.select_related("employee")


def create_slips(entry, user=None):
    """Draft slips for every row without one.  Employees that would overlap a
    submitted slip (or cannot be computed, e.g. no wage rate) are skipped and
    reported; the rest are created."""
    if entry.docstatus != DocStatus.DRAFT:
        raise ValidationError(_("Only a draft payroll entry can create slips."))
    created, skipped = [], []
    existing = {
        s.employee_id: s
        for s in entry.salary_slips.exclude(docstatus=DocStatus.CANCELLED)
    }
    rows = entry.employees.select_related("employee__company").filter(
        salary_slip__isnull=True
    )
    for row in rows:
        employee = row.employee
        reason = None
        if employee.pk in existing:  # row link lost by an employees edit: re-link
            row.salary_slip = existing[employee.pk]
            row.save(update_fields=["salary_slip"])
            continue
        if employee.company_id != entry.company_id:
            reason = _("The employee belongs to a different company.")
        else:
            clash = overlapping_slip(employee.pk, entry.start_date, entry.end_date)
            if clash is not None:
                reason = _("Overlaps submitted salary slip %(slip)s.") % {
                    "slip": clash.name
                }
        if reason is None:
            try:
                with transaction.atomic():
                    slip = create_draft_slip(
                        employee,
                        entry.start_date,
                        entry.end_date,
                        entry.posting_date,
                        payroll_entry=entry,
                        user=user,
                    )
            except ValidationError as exc:
                reason = _messages(exc)
            else:
                row.salary_slip = slip
                row.save(update_fields=["salary_slip"])
                created.append(slip)
                continue
        skipped.append(
            {
                "employee": employee.name,
                "employee_name": employee.employee_name,
                "reason": str(reason),
            }
        )
    return created, skipped


def validate_payroll_entry(entry):
    if not entry.salary_slips.exclude(docstatus=DocStatus.CANCELLED).exists():
        raise ValidationError(
            _("Create salary slips for this payroll entry before submitting it.")
        )


def _slip_error(slip, exc):
    return ValidationError(
        _("Salary slip %(slip)s (%(employee)s): %(error)s")
        % {
            "slip": slip.name,
            "employee": slip.employee.employee_name,
            "error": _messages(exc),
        }
    )


def submit_payroll_entry_slips(entry):
    """Submit every draft slip of the entry, all or nothing (the surrounding
    ``submit()`` transaction rolls back if any slip is refused)."""
    for slip in entry.salary_slips.filter(docstatus=DocStatus.DRAFT).order_by("pk"):
        try:
            slip.submit(user=entry.modified_by)
        except ValidationError as exc:
            raise _slip_error(slip, exc)


def cancel_payroll_entry_slips(entry):
    for slip in entry.salary_slips.filter(docstatus=DocStatus.SUBMITTED).order_by(
        "-pk"
    ):
        try:
            slip.cancel(user=entry.modified_by)
        except ValidationError as exc:
            raise _slip_error(slip, exc)


def make_payments(entry, paid_from, mode, posting_date, reference_no="", user=None):
    """One submitted EmployeePayment per submitted slip's net pay, one transaction.
    Slips that already have a submitted payment are skipped."""
    if entry.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(_("Submit the payroll entry before paying it."))
    payments = []
    with transaction.atomic():
        slips = entry.salary_slips.filter(docstatus=DocStatus.SUBMITTED).order_by("pk")
        for slip in slips:
            if slip.net_pay <= 0:
                continue
            if slip.payments.filter(docstatus=DocStatus.SUBMITTED).exists():
                continue
            payment = EmployeePayment.objects.create(
                name=NamingSeries.next(PAYMENT_PREFIX),
                employee=slip.employee,
                company=slip.company,
                posting_date=posting_date,
                amount=slip.net_pay,
                paid_from=paid_from,
                mode=mode,
                reference_no=reference_no,
                remarks=slip.name,
                salary_slip=slip,
                created_by=user if getattr(user, "pk", None) else None,
            )
            try:
                payment.submit(user=user)
            except ValidationError as exc:
                raise ValidationError(
                    _("Payment for %(employee)s: %(error)s")
                    % {"employee": slip.employee.employee_name, "error": _messages(exc)}
                )
            payments.append(payment)
    return payments
