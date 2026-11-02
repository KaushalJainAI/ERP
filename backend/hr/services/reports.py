"""HR Reports: Salary Register and Wage Register."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import (
    ComponentType,
    PayType,
    SalarySlip,
    SystemComponent,
)
from hr.services.wages import wage_rate_for


def salary_register_report(
    company_id,
    from_date,
    to_date,
    department_id=None,
    branch_id=None,
    employee_id=None,
    pay_type=None,
):
    """Salary register: one row per submitted salary slip in the period."""
    if not from_date or not to_date:
        raise ValidationError(_("Both 'from' and 'to' dates are required."))
    if to_date < from_date:
        raise ValidationError(_("'to' date cannot be before 'from' date."))

    slips = (
        SalarySlip.objects.filter(
            company_id=company_id,
            docstatus=DocStatus.SUBMITTED,
            posting_date__gte=from_date,
            posting_date__lte=to_date,
        )
        .select_related("employee__department", "employee__designation")
        .prefetch_related("details__component")
        .order_by("posting_date", "employee__name")
    )

    if department_id:
        slips = slips.filter(employee__department_id=department_id)
    if branch_id:
        slips = slips.filter(employee__branch_id=branch_id)
    if employee_id:
        slips = slips.filter(employee_id=employee_id)
    if pay_type:
        slips = slips.filter(employee__pay_type=pay_type)

    rows = []
    tot_gross = Decimal("0.00")
    tot_deduction = Decimal("0.00")
    tot_net = Decimal("0.00")
    tot_days = Decimal("0.00")
    tot_ot = Decimal("0.00")

    for s in slips:
        tot_gross += s.gross_pay
        tot_deduction += s.total_deduction
        tot_net += s.net_pay
        tot_days += s.payment_days
        tot_ot += s.ot_hours

        rows.append(
            {
                "slip_name": s.name,
                "posting_date": str(s.posting_date),
                "employee_id": s.employee.name,
                "employee_name": s.employee.employee_name,
                "department": (
                    s.employee.department.name if s.employee.department else "-"
                ),
                "designation": (
                    s.employee.designation.name if s.employee.designation else "-"
                ),
                "pay_type": s.employee.pay_type,
                "start_date": str(s.start_date),
                "end_date": str(s.end_date),
                "payment_days": str(s.payment_days),
                "ot_hours": str(s.ot_hours),
                "gross_pay": str(s.gross_pay),
                "total_deduction": str(s.total_deduction),
                "net_pay": str(s.net_pay),
                "bank_account_no": s.employee.bank_account_no or "-",
                "bank_ifsc_code": s.employee.ifsc_code or "-",
            }
        )

    return {
        "rows": rows,
        "total_gross_pay": str(quantize_money(tot_gross)),
        "total_deduction": str(quantize_money(tot_deduction)),
        "total_net_pay": str(quantize_money(tot_net)),
        "total_payment_days": str(tot_days.quantize(Decimal("0.01"))),
        "total_ot_hours": str(tot_ot.quantize(Decimal("0.01"))),
    }


def wage_register_report(
    company_id,
    from_date,
    to_date,
    department_id=None,
    branch_id=None,
    employee_id=None,
):
    """Wage register: statutory muster roll wage register for daily wage workers."""
    if not from_date or not to_date:
        raise ValidationError(_("Both 'from' and 'to' dates are required."))
    if to_date < from_date:
        raise ValidationError(_("'to' date cannot be before 'from' date."))

    slips = (
        SalarySlip.objects.filter(
            company_id=company_id,
            docstatus=DocStatus.SUBMITTED,
            employee__pay_type=PayType.DAILY,
            posting_date__gte=from_date,
            posting_date__lte=to_date,
        )
        .select_related("employee__department", "employee__designation")
        .prefetch_related("details__component")
        .order_by("posting_date", "employee__name")
    )

    if department_id:
        slips = slips.filter(employee__department_id=department_id)
    if branch_id:
        slips = slips.filter(employee__branch_id=branch_id)
    if employee_id:
        slips = slips.filter(employee_id=employee_id)

    rows = []
    tot_days = Decimal("0.00")
    tot_ot = Decimal("0.00")
    tot_basic = Decimal("0.00")
    tot_overtime = Decimal("0.00")
    tot_gross = Decimal("0.00")
    tot_advance = Decimal("0.00")
    tot_other_ded = Decimal("0.00")
    tot_deduction = Decimal("0.00")
    tot_net = Decimal("0.00")

    for s in slips:
        rate = wage_rate_for(s.employee, s.start_date)
        daily_rate_val = (
            rate.daily_rate if rate and rate.daily_rate is not None else None
        )

        wages = Decimal("0.00")
        overtime = Decimal("0.00")
        advance_ded = Decimal("0.00")
        other_ded = Decimal("0.00")

        for d in s.details.all():
            comp = d.component
            if comp.system_key == SystemComponent.WAGES:
                wages += d.amount
            elif comp.system_key == SystemComponent.OVERTIME:
                overtime += d.amount
            elif comp.system_key == SystemComponent.ADVANCE_RECOVERY:
                advance_ded += d.amount
            elif comp.component_type == ComponentType.DEDUCTION:
                other_ded += d.amount

        tot_days += s.payment_days
        tot_ot += s.ot_hours
        tot_basic += wages
        tot_overtime += overtime
        tot_gross += s.gross_pay
        tot_advance += advance_ded
        tot_other_ded += other_ded
        tot_deduction += s.total_deduction
        tot_net += s.net_pay

        rows.append(
            {
                "slip_name": s.name,
                "posting_date": str(s.posting_date),
                "employee_id": s.employee.name,
                "employee_name": s.employee.employee_name,
                "department": (
                    s.employee.department.name if s.employee.department else "-"
                ),
                "daily_rate": (
                    str(quantize_money(daily_rate_val))
                    if daily_rate_val is not None
                    else "-"
                ),
                "start_date": str(s.start_date),
                "end_date": str(s.end_date),
                "payment_days": str(s.payment_days),
                "ot_hours": str(s.ot_hours),
                "basic_wages": str(quantize_money(wages)),
                "overtime_wages": str(quantize_money(overtime)),
                "gross_wages": str(s.gross_pay),
                "advance_deduction": str(quantize_money(advance_ded)),
                "other_deductions": str(quantize_money(other_ded)),
                "total_deductions": str(s.total_deduction),
                "net_wages": str(s.net_pay),
                "bank_account_no": s.employee.bank_account_no or "-",
            }
        )

    return {
        "rows": rows,
        "total_payment_days": str(tot_days.quantize(Decimal("0.01"))),
        "total_ot_hours": str(tot_ot.quantize(Decimal("0.01"))),
        "total_basic_wages": str(quantize_money(tot_basic)),
        "total_overtime_wages": str(quantize_money(tot_overtime)),
        "total_gross_wages": str(quantize_money(tot_gross)),
        "total_advance_deduction": str(quantize_money(tot_advance)),
        "total_other_deductions": str(quantize_money(tot_other_ded)),
        "total_deductions": str(quantize_money(tot_deduction)),
        "total_net_wages": str(quantize_money(tot_net)),
    }
