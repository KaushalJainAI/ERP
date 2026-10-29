"""Employee summary: a pure function of the database, reusable by the portal.

``employee_summary(employee, from_date, to_date)`` has no request / user
dependency.  Period figures use the attendance dates in the range and the
submitted slips / payments *posted* in the range; the wages-payable balance and
the advance outstanding are as of now.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum

from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import (
    Attendance,
    AttendanceStatus,
    EmployeeAdvance,
    EmployeePayment,
    SalarySlip,
)
from hr.services import advances as advance_service
from hr.services.balances import payable_balance

ZERO = Decimal("0.00")
RECENT = 5


def employee_summary(employee, from_date=None, to_date=None):
    if to_date is None:
        to_date = datetime.date.today()
    if from_date is None:
        from_date = to_date.replace(day=1)

    counts = {status: 0 for status in AttendanceStatus.values}
    ot_hours = ZERO
    attendance = Attendance.objects.filter(
        employee=employee, date__gte=from_date, date__lte=to_date
    )
    for row in attendance.values("status").annotate(n=Count("id"), ot=Sum("ot_hours")):
        counts[row["status"]] = row["n"]
        ot_hours += row["ot"] or ZERO
    ot_hours = ot_hours.quantize(Decimal("0.01"))

    slips = SalarySlip.objects.filter(
        employee=employee,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    totals = slips.aggregate(
        gross=Sum("gross_pay"), deduction=Sum("total_deduction"), net=Sum("net_pay")
    )
    payments = EmployeePayment.objects.filter(
        employee=employee,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )

    try:
        wages_payable = payable_balance(employee)
    except ValidationError:  # HR not set up for the company: nothing posted
        wages_payable = ZERO
    advance_outstanding = ZERO
    for advance in EmployeeAdvance.objects.filter(
        employee=employee, docstatus=DocStatus.SUBMITTED
    ):
        advance_outstanding += advance_service.outstanding(advance)

    return {
        "employee": employee.name,
        "employee_name": employee.employee_name,
        "from_date": from_date,
        "to_date": to_date,
        "days_present": counts[AttendanceStatus.PRESENT],
        "days_half_day": counts[AttendanceStatus.HALF_DAY],
        "days_absent": counts[AttendanceStatus.ABSENT],
        "days_paid_leave": counts[AttendanceStatus.PAID_LEAVE],
        "ot_hours": ot_hours,
        "gross_earned": quantize_money(totals["gross"] or ZERO),
        "total_deductions": quantize_money(totals["deduction"] or ZERO),
        "net_pay": quantize_money(totals["net"] or ZERO),
        "total_paid": quantize_money(payments.aggregate(t=Sum("amount"))["t"] or ZERO),
        "wages_payable": wages_payable,
        "advance_outstanding": advance_outstanding,
        "recent_slips": [
            {
                "name": s.name,
                "posting_date": s.posting_date,
                "start_date": s.start_date,
                "end_date": s.end_date,
                "gross_pay": s.gross_pay,
                "total_deduction": s.total_deduction,
                "net_pay": s.net_pay,
            }
            for s in SalarySlip.objects.filter(
                employee=employee, docstatus=DocStatus.SUBMITTED
            ).order_by("-posting_date", "-id")[:RECENT]
        ],
        "recent_payments": [
            {
                "name": p.name,
                "posting_date": p.posting_date,
                "amount": p.amount,
                "mode": p.mode,
                "reference_no": p.reference_no,
            }
            for p in EmployeePayment.objects.filter(
                employee=employee, docstatus=DocStatus.SUBMITTED
            ).order_by("-posting_date", "-id")[:RECENT]
        ],
    }
