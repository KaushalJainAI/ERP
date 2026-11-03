"""Muster roll: the whole workforce's attendance for a day, and a range grid."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from hr.models import (
    Attendance,
    AttendanceStatus,
    Employee,
    EmployeeStatus,
    Holiday,
    SalarySlip,
)
from core.models import DocStatus
from hr.services.wages import daterange

MAX_REPORT_DAYS = 93
STATUS_CODE = {
    AttendanceStatus.PRESENT: "P",
    AttendanceStatus.ABSENT: "A",
    AttendanceStatus.HALF_DAY: "H",
    AttendanceStatus.PAID_LEAVE: "L",
}


def active_employees(company_id, date):
    """Employees on the roll on ``date`` (active, joined, not yet relieved)."""
    qs = Employee.objects.filter(
        status=EmployeeStatus.ACTIVE, date_of_joining__lte=date
    ).filter(Q(relieving_date__isnull=True) | Q(relieving_date__gte=date))
    qs = qs.filter(Q(contract_end_date__isnull=True) | Q(contract_end_date__gte=date))
    if company_id:
        qs = qs.filter(company_id=company_id)
    return qs.select_related("department", "designation")


def muster_roll(company_id, date):
    """Every active employee with their attendance for ``date`` (or ``None``)."""
    employees = list(active_employees(company_id, date).order_by("name"))
    records = {
        a.employee_id: a
        for a in Attendance.objects.filter(
            date=date, employee_id__in=[e.pk for e in employees]
        )
    }
    locked = set(
        SalarySlip.objects.filter(
            docstatus=DocStatus.SUBMITTED,
            start_date__lte=date,
            end_date__gte=date,
            employee_id__in=[e.pk for e in employees],
        ).values_list("employee_id", flat=True)
    )
    holiday_lists = {e.holiday_list_id for e in employees if e.holiday_list_id}
    holiday = {
        h.holiday_list_id: h
        for h in Holiday.objects.filter(holiday_list_id__in=holiday_lists, date=date)
    }
    rows = []
    for e in employees:
        record = records.get(e.pk)
        h = holiday.get(e.holiday_list_id)
        rows.append(
            {
                "employee": e.pk,
                "employee_id": e.name,
                "employee_name": e.employee_name,
                "department": e.department.name if e.department_id else None,
                "pay_type": e.pay_type,
                "is_holiday": h is not None,
                "holiday_description": h.description if h else None,
                "locked": e.pk in locked,
                "attendance": (
                    {
                        "id": record.pk,
                        "status": record.status,
                        "ot_hours": record.ot_hours,
                        "remarks": record.remarks,
                    }
                    if record
                    else None
                ),
            }
        )
    return rows


def upsert_muster(company_id, date, rows, user=None):
    """Create / update the attendance of many employees for one date, atomically.

    ``rows`` are validated dicts (``employee`` a model instance, ``status``,
    ``ot_hours``, ``remarks``).  Any refused row -- locked by a submitted slip,
    not on the roll that day, duplicated -- rejects the whole batch.
    """
    seen = set()
    for row in rows:
        emp = row["employee"]
        if emp.pk in seen:
            raise ValidationError(
                _("%(employee)s appears more than once.") % {"employee": emp.name}
            )
        seen.add(emp.pk)
    on_roll = set(active_employees(company_id, date).values_list("pk", flat=True))

    saved = []
    with transaction.atomic():
        for row in sorted(rows, key=lambda r: r["employee"].pk):
            emp = row["employee"]
            if emp.pk not in on_roll:
                raise ValidationError(
                    _("%(employee)s is not on the active roll on %(date)s.")
                    % {"employee": emp.name, "date": date}
                )
            ot_hours = row.get("ot_hours", Decimal("0.00"))
            if row["status"] == AttendanceStatus.ABSENT and ot_hours:
                raise ValidationError(
                    _("%(employee)s is absent and cannot have overtime hours.")
                    % {"employee": emp.name}
                )
            record = Attendance.objects.filter(employee=emp, date=date).first()
            if record is None:
                record = Attendance(employee=emp, date=date, created_by=user)
            record.status = row["status"]
            record.ot_hours = ot_hours
            record.remarks = row.get("remarks", "")
            if user is not None and getattr(user, "pk", None):
                record.modified_by = user
            record.save()  # raises if a submitted slip covers the date
            saved.append(record)
    return saved


def muster_report(company_id, from_date, to_date, department_id=None):
    """Employee x day grid for ``from_date..to_date`` (inclusive)."""
    if to_date < from_date:
        raise ValidationError(_("'to' cannot be before 'from'."))
    if (to_date - from_date).days + 1 > MAX_REPORT_DAYS:
        raise ValidationError(
            _("The range cannot exceed %(n)s days.") % {"n": MAX_REPORT_DAYS}
        )
    employees = Employee.objects.filter(
        date_of_joining__lte=to_date,
    ).filter(Q(relieving_date__isnull=True) | Q(relieving_date__gte=from_date))
    employees = employees.filter(
        Q(contract_end_date__isnull=True) | Q(contract_end_date__gte=from_date)
    )
    employees = employees.exclude(status=EmployeeStatus.INACTIVE)
    if company_id:
        employees = employees.filter(company_id=company_id)
    if department_id:
        employees = employees.filter(department_id=department_id)
    employees = list(employees.select_related("department").order_by("name"))

    records = {}
    for a in Attendance.objects.filter(
        employee_id__in=[e.pk for e in employees],
        date__gte=from_date,
        date__lte=to_date,
    ):
        records[(a.employee_id, a.date)] = a

    days = list(daterange(from_date, to_date))
    rows = []
    for e in employees:
        cells = {}
        counts = {s: Decimal("0") for s in AttendanceStatus.values}
        ot = Decimal("0.00")
        for day in days:
            a = records.get((e.pk, day))
            if a is None:
                cells[day.isoformat()] = None
                continue
            cells[day.isoformat()] = STATUS_CODE[a.status]
            counts[a.status] += 1
            ot += a.ot_hours
        rows.append(
            {
                "employee": e.pk,
                "employee_id": e.name,
                "employee_name": e.employee_name,
                "department": e.department.name if e.department_id else None,
                "days": cells,
                "present": counts[AttendanceStatus.PRESENT],
                "half_day": counts[AttendanceStatus.HALF_DAY],
                "absent": counts[AttendanceStatus.ABSENT],
                "paid_leave": counts[AttendanceStatus.PAID_LEAVE],
                "ot_hours": ot,
            }
        )
    return {
        "from_date": from_date,
        "to_date": to_date,
        "dates": [d.isoformat() for d in days],
        "legend": {v: k for k, v in STATUS_CODE.items()},
        "rows": rows,
    }
