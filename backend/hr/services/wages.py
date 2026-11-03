"""Wage calculation from attendance and effective-dated wage rates (pure reads).

Rules (docs/erpnext_divergences.md, "HR and payroll"):

* Day units: Present = 1, Half Day = 0.5, Paid Leave = 1, Absent / no record = 0.
* Daily employee: units x that day's ``daily_rate``.
* Monthly employee: units x ``monthly_salary`` / days in that calendar month.
  A holiday (any row of the employee's holiday list, weekly offs included) with no
  attendance record counts as one paid day -- **Monthly staff only**.
* Overtime: ``ot_hours`` x that day's ``ot_hourly_rate``.
* Days before the joining date or after the relieving date earn nothing.
* The rate for a date is the latest ``EmployeeWageRate`` with ``from_date <= date``.

Amounts are accumulated unrounded and rounded once per component.
"""

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.services.gl import quantize_money
from hr.models import (
    Attendance,
    AttendanceStatus,
    EmployeeWageRate,
    Holiday,
    PayType,
)

DAY_UNITS = {
    AttendanceStatus.PRESENT: Decimal("1"),
    AttendanceStatus.HALF_DAY: Decimal("0.5"),
    AttendanceStatus.PAID_LEAVE: Decimal("1"),
    AttendanceStatus.ABSENT: Decimal("0"),
}

MAX_SLIP_DAYS = 366


@dataclass
class WageResult:
    wages: Decimal = Decimal("0.00")
    overtime: Decimal = Decimal("0.00")
    payment_days: Decimal = Decimal("0.00")
    ot_hours: Decimal = Decimal("0.00")
    daily: list = field(default_factory=list)


def daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def rate_on(rates, date):
    """Latest rate with ``from_date <= date`` from ``rates`` (ascending), or None."""
    chosen = None
    for rate in rates:
        if rate.from_date <= date:
            chosen = rate
        else:
            break
    return chosen


def wage_rate_for(employee, date):
    """The ``EmployeeWageRate`` effective for ``employee`` on ``date`` (or None)."""
    return (
        EmployeeWageRate.objects.filter(employee=employee, from_date__lte=date)
        .order_by("-from_date")
        .first()
    )


def compute_wages(employee, start_date, end_date):
    """Wages and overtime of ``employee`` for ``start_date..end_date`` inclusive."""
    if end_date < start_date:
        raise ValidationError(_("End date cannot be before start date."))
    if (end_date - start_date).days + 1 > MAX_SLIP_DAYS:
        raise ValidationError(
            _("A salary slip cannot cover more than %(days)s days.")
            % {"days": MAX_SLIP_DAYS}
        )

    rates = list(
        EmployeeWageRate.objects.filter(
            employee=employee, from_date__lte=end_date
        ).order_by("from_date")
    )
    attendance = {
        a.date: a
        for a in Attendance.objects.filter(
            employee=employee, date__gte=start_date, date__lte=end_date
        )
    }
    monthly = employee.pay_type == PayType.MONTHLY
    holidays = set()
    if monthly and employee.holiday_list_id:
        holidays = set(
            Holiday.objects.filter(
                holiday_list_id=employee.holiday_list_id,
                date__gte=start_date,
                date__lte=end_date,
            ).values_list("date", flat=True)
        )

    result = WageResult()
    wages = Decimal("0")
    overtime = Decimal("0")
    for day in daterange(start_date, end_date):
        if day < employee.date_of_joining:
            continue
        if employee.employment_end_date and day > employee.employment_end_date:
            continue
        record = attendance.get(day)
        if record is not None:
            units = DAY_UNITS[record.status]
            ot_hours = record.ot_hours
        else:
            units = Decimal("1") if (monthly and day in holidays) else Decimal("0")
            ot_hours = Decimal("0")
        if units == 0 and ot_hours == 0:
            continue

        rate = rate_on(rates, day)
        if rate is None:
            raise ValidationError(
                _("%(employee)s has no wage rate effective on %(date)s.")
                % {"employee": employee.employee_name, "date": day}
            )
        day_wage = Decimal("0")
        if units:
            if monthly:
                if rate.monthly_salary is None:
                    raise ValidationError(
                        _(
                            "The wage rate of %(employee)s effective %(date)s has no "
                            "monthly salary."
                        )
                        % {"employee": employee.employee_name, "date": rate.from_date}
                    )
                days_in_month = calendar.monthrange(day.year, day.month)[1]
                day_wage = units * rate.monthly_salary / Decimal(days_in_month)
            else:
                if rate.daily_rate is None:
                    raise ValidationError(
                        _(
                            "The wage rate of %(employee)s effective %(date)s has no "
                            "daily rate."
                        )
                        % {"employee": employee.employee_name, "date": rate.from_date}
                    )
                day_wage = units * rate.daily_rate
        day_ot = ot_hours * rate.ot_hourly_rate
        wages += day_wage
        overtime += day_ot
        result.payment_days += units
        result.ot_hours += ot_hours
        result.daily.append(
            {
                "date": day,
                "units": units,
                "ot_hours": ot_hours,
                "wage": quantize_money(day_wage),
                "overtime": quantize_money(day_ot),
            }
        )

    result.wages = quantize_money(wages)
    result.overtime = quantize_money(overtime)
    result.payment_days = result.payment_days.quantize(Decimal("0.01"))
    result.ot_hours = result.ot_hours.quantize(Decimal("0.01"))
    return result
