"""Leave management service: allocations, applications, balance calculations and attendance sync."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from core.models import DocStatus
from hr.models import (
    Attendance,
    AttendanceStatus,
    Employee,
    EmployeeStatus,
    LeaveAllocation,
    LeaveApplication,
    LeaveType,
)
from hr.services.wages import daterange

LEAVE_ALLOCATION_PREFIX = "HR-LAL-"
LEAVE_APPLICATION_PREFIX = "HR-LAP-"


def validate_allocation(allocation):
    """Validate leave allocation dates and relations before submission."""
    if allocation.to_date < allocation.from_date:
        raise ValidationError(_("End date cannot be before start date."))
    if allocation.employee.company_id != allocation.company_id:
        raise ValidationError(_("The employee belongs to a different company."))
    if allocation.leave_type.company_id != allocation.company_id:
        raise ValidationError(_("The leave type belongs to a different company."))
    if allocation.total_leaves_allocated < 0:
        raise ValidationError(_("Allocated leaves cannot be negative."))


def cancel_allocation(allocation):
    """Cancel allocation if no leaves have been taken against it that would cause negative balance."""
    taken = LeaveApplication.objects.filter(
        employee=allocation.employee,
        leave_type=allocation.leave_type,
        docstatus=DocStatus.SUBMITTED,
        from_date__gte=allocation.from_date,
        to_date__lte=allocation.to_date,
    ).aggregate(s=Sum("total_leave_days"))["s"] or Decimal("0.00")
    other_alloc = LeaveAllocation.objects.filter(
        employee=allocation.employee,
        leave_type=allocation.leave_type,
        docstatus=DocStatus.SUBMITTED,
        from_date__lte=allocation.to_date,
        to_date__gte=allocation.from_date,
    ).exclude(pk=allocation.pk).aggregate(s=Sum("total_leaves_allocated"))[
        "s"
    ] or Decimal(
        "0.00"
    )
    if other_alloc < taken:
        raise ValidationError(
            _(
                "Cannot cancel leave allocation: %(taken)s days of leave have already "
                "been taken against it."
            )
            % {"taken": taken}
        )


def get_leave_balance(
    employee, leave_type, from_date=None, to_date=None, exclude_application_id=None
):
    """Calculate remaining leave balance for employee and leave type."""
    alloc_qs = LeaveAllocation.objects.filter(
        employee=employee,
        leave_type=leave_type,
        docstatus=DocStatus.SUBMITTED,
    )
    if from_date:
        alloc_qs = alloc_qs.filter(to_date__gte=from_date)
    if to_date:
        alloc_qs = alloc_qs.filter(from_date__lte=to_date)

    if not alloc_qs.exists():
        return Decimal("0.00")

    alloc_start = (
        alloc_qs.order_by("from_date").values_list("from_date", flat=True).first()
    )
    alloc_end = alloc_qs.order_by("-to_date").values_list("to_date", flat=True).first()
    allocated = alloc_qs.aggregate(s=Sum("total_leaves_allocated"))["s"] or Decimal(
        "0.00"
    )

    app_qs = LeaveApplication.objects.filter(
        employee=employee,
        leave_type=leave_type,
        docstatus=DocStatus.SUBMITTED,
        from_date__gte=alloc_start,
        to_date__lte=alloc_end,
    )
    if exclude_application_id:
        app_qs = app_qs.exclude(pk=exclude_application_id)
    taken = app_qs.aggregate(s=Sum("total_leave_days"))["s"] or Decimal("0.00")

    return allocated - taken


def validate_application(application):
    """Validate leave application dates, relations, locks, overlaps, and balances."""
    if application.to_date < application.from_date:
        raise ValidationError(_("End date cannot be before start date."))
    if application.employee.company_id != application.company_id:
        raise ValidationError(_("The employee belongs to a different company."))
    if application.leave_type.company_id != application.company_id:
        raise ValidationError(_("The leave type belongs to a different company."))
    if application.total_leave_days <= 0:
        raise ValidationError(_("Total leave days must be greater than zero."))

    days_in_span = (application.to_date - application.from_date).days + 1
    if application.total_leave_days > Decimal(days_in_span):
        raise ValidationError(
            _(
                "Total leave days (%(days)s) cannot exceed the date range span (%(span)s)."
            )
            % {"days": application.total_leave_days, "span": days_in_span}
        )

    # Check for overlaps with already submitted applications
    overlap = (
        LeaveApplication.objects.filter(
            employee=application.employee,
            docstatus=DocStatus.SUBMITTED,
            from_date__lte=application.to_date,
            to_date__gte=application.from_date,
        )
        .exclude(pk=application.pk)
        .first()
    )
    if overlap:
        raise ValidationError(
            _(
                "Leave dates overlap with submitted leave application %(name)s "
                "(%(from)s to %(to)s)."
            )
            % {
                "name": overlap.name,
                "from": overlap.from_date,
                "to": overlap.to_date,
            }
        )

    # Check that attendance dates are unlocked
    for day in daterange(application.from_date, application.to_date):
        Attendance.assert_unlocked(application.employee_id, day)

    # Check available leave balance
    balance = get_leave_balance(
        application.employee,
        application.leave_type,
        from_date=application.from_date,
        to_date=application.to_date,
        exclude_application_id=application.pk,
    )
    if balance < application.total_leave_days:
        raise ValidationError(
            _(
                "Insufficient leave balance for %(leave_type)s. Available: %(avail)s, "
                "Requested: %(req)s."
            )
            % {
                "leave_type": application.leave_type.name,
                "avail": balance,
                "req": application.total_leave_days,
            }
        )


def submit_application(application):
    """Mark Paid Leave attendance for approved leave application dates."""
    with transaction.atomic():
        # Lock employee record
        Employee.objects.select_for_update().get(pk=application.employee_id)
        for day in daterange(application.from_date, application.to_date):
            Attendance.assert_unlocked(application.employee_id, day)
            att = Attendance.objects.filter(
                employee=application.employee, date=day
            ).first()
            if att:
                att.status = AttendanceStatus.PAID_LEAVE
                att.remarks = (
                    f"Leave: {application.name} ({application.leave_type.name})"
                )
                if application.modified_by:
                    att.modified_by = application.modified_by
                att.save()
            else:
                Attendance.objects.create(
                    employee=application.employee,
                    date=day,
                    status=AttendanceStatus.PAID_LEAVE,
                    remarks=f"Leave: {application.name} ({application.leave_type.name})",
                    created_by=application.modified_by or application.created_by,
                )


def cancel_application(application):
    """Revert attendance created by leave application."""
    with transaction.atomic():
        Employee.objects.select_for_update().get(pk=application.employee_id)
        for day in daterange(application.from_date, application.to_date):
            Attendance.assert_unlocked(application.employee_id, day)

        for day in daterange(application.from_date, application.to_date):
            att = Attendance.objects.filter(
                employee=application.employee, date=day
            ).first()
            if att and application.name in (att.remarks or ""):
                att.delete()


def leave_balance_report(
    company_id, from_date=None, to_date=None, employee_id=None, department_id=None
):
    """Report on allocated, taken, and remaining leave balances per employee & leave type."""
    if from_date and isinstance(from_date, str):
        from_date = datetime.date.fromisoformat(from_date)
    if to_date and isinstance(to_date, str):
        to_date = datetime.date.fromisoformat(to_date)
    employees = Employee.objects.filter(
        company_id=company_id,
        status=EmployeeStatus.ACTIVE,
    )
    if employee_id:
        employees = employees.filter(pk=employee_id)
    if department_id:
        employees = employees.filter(department_id=department_id)

    employees = list(employees.select_related("department").order_by("name"))
    leave_types = list(
        LeaveType.objects.filter(company_id=company_id, is_active=True).order_by("name")
    )

    rows = []
    total_alloc = Decimal("0.00")
    total_taken = Decimal("0.00")
    total_balance = Decimal("0.00")

    for emp in employees:
        for lt in leave_types:
            alloc_qs = LeaveAllocation.objects.filter(
                employee=emp,
                leave_type=lt,
                docstatus=DocStatus.SUBMITTED,
            )
            if from_date:
                alloc_qs = alloc_qs.filter(to_date__gte=from_date)
            if to_date:
                alloc_qs = alloc_qs.filter(from_date__lte=to_date)
            allocated = alloc_qs.aggregate(s=Sum("total_leaves_allocated"))[
                "s"
            ] or Decimal("0.00")

            app_qs = LeaveApplication.objects.filter(
                employee=emp,
                leave_type=lt,
                docstatus=DocStatus.SUBMITTED,
            )
            if from_date:
                app_qs = app_qs.filter(to_date__gte=from_date)
            if to_date:
                app_qs = app_qs.filter(from_date__lte=to_date)
            taken = app_qs.aggregate(s=Sum("total_leave_days"))["s"] or Decimal("0.00")

            balance = allocated - taken
            if allocated == 0 and taken == 0:
                continue

            total_alloc += allocated
            total_taken += taken
            total_balance += balance

            rows.append(
                {
                    "employee_id": emp.name,
                    "employee_name": emp.employee_name,
                    "department": emp.department.name if emp.department else "-",
                    "leave_type": lt.name,
                    "allocated": str(allocated.quantize(Decimal("0.01"))),
                    "taken": str(taken.quantize(Decimal("0.01"))),
                    "balance": str(balance.quantize(Decimal("0.01"))),
                }
            )

    return {
        "rows": rows,
        "total_allocated": str(total_alloc),
        "total_taken": str(total_taken),
        "total_balance": str(total_balance),
    }
