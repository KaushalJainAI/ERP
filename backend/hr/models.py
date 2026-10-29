"""HR masters, attendance and daily-wage payroll.

Design (see ``docs/erpnext_divergences.md``, "HR and payroll"):

* ERPNext core only ships the employee masters (Employee, Department,
  Designation, Branch, Holiday List); payroll lives in the separate hrms app, so
  everything from :class:`EmployeeWageRate` onwards is our own design.
* Pay is **per employee and effective-dated** (:class:`EmployeeWageRate`), not a
  salary structure, and a :class:`SalarySlip` may cover **any date range** -- the
  admin pays whenever they choose.
* Money fields follow CLAUDE.md: money ``(18,2)``, rates ``(18,6)``, quantities
  of hours ``(x,2)``.  Slip totals are *derived* from the detail rows and are
  rebuilt on every draft save; submitted slips are immutable.
* The ledgers are written only through ``accounts.services.gl`` (slip, advance
  and payment ``on_submit`` / ``on_cancel``).  Advance outstanding, recovered
  amounts and the wages-payable balance are **derived** from the slips and the GL
  -- nothing is stored that cannot be rebuilt.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.models import DocStatus, SubmittableDocument, TimeStampedModel

ZERO = Decimal("0.00")


# --------------------------------------------------------------------- choices
class Gender(models.TextChoices):
    MALE = "Male", _("Male")
    FEMALE = "Female", _("Female")
    OTHER = "Other", _("Other")


class EmployeeStatus(models.TextChoices):
    ACTIVE = "Active", _("Active")
    INACTIVE = "Inactive", _("Inactive")
    LEFT = "Left", _("Left")


class PayType(models.TextChoices):
    DAILY = "Daily", _("Daily")
    MONTHLY = "Monthly", _("Monthly")


class AttendanceStatus(models.TextChoices):
    PRESENT = "Present", _("Present")
    ABSENT = "Absent", _("Absent")
    HALF_DAY = "Half Day", _("Half Day")
    PAID_LEAVE = "Paid Leave", _("Paid Leave")


class ComponentType(models.TextChoices):
    EARNING = "Earning", _("Earning")
    DEDUCTION = "Deduction", _("Deduction")


class SystemComponent(models.TextChoices):
    """The salary components the compute service owns."""

    WAGES = "wages", _("Wages")
    OVERTIME = "overtime", _("Overtime")
    ADVANCE_RECOVERY = "advance_recovery", _("Advance Recovery")


class AdvanceRequestStatus(models.TextChoices):
    PENDING = "Pending", _("Pending")
    APPROVED = "Approved", _("Approved")
    REJECTED = "Rejected", _("Rejected")
    WITHDRAWN = "Withdrawn", _("Withdrawn")


class IssueCategory(models.TextChoices):
    ATTENDANCE = "Attendance", _("Attendance")
    PAYMENT = "Payment", _("Payment")
    ADVANCE = "Advance", _("Advance")
    SALARY_SLIP = "Salary Slip", _("Salary Slip")
    OTHER = "Other", _("Other")


class IssueStatus(models.TextChoices):
    OPEN = "Open", _("Open")
    RESOLVED = "Resolved", _("Resolved")
    REJECTED = "Rejected", _("Rejected")


class PaymentMode(models.TextChoices):
    CASH = "Cash", _("Cash")
    BANK = "Bank", _("Bank")
    UPI = "UPI", _("UPI")


# --------------------------------------------------------------------- masters
class Department(TimeStampedModel):
    name = models.CharField(_("name"), max_length=140, unique=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Designation(TimeStampedModel):
    name = models.CharField(_("name"), max_length=140, unique=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Branch(TimeStampedModel):
    name = models.CharField(_("name"), max_length=140, unique=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name_plural = "Branches"
        ordering = ["name"]

    def __str__(self):
        return self.name


class HolidayList(TimeStampedModel):
    name = models.CharField(_("name"), max_length=140, unique=True)
    from_date = models.DateField(_("from date"))
    to_date = models.DateField(_("to date"))

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                check=Q(to_date__gte=F("from_date")),
                name="check_holidaylist_dates",
            )
        ]

    def __str__(self):
        return self.name


class Holiday(models.Model):
    holiday_list = models.ForeignKey(
        HolidayList, on_delete=models.CASCADE, related_name="holidays"
    )
    date = models.DateField(_("date"))
    description = models.CharField(_("description"), max_length=240, blank=True)
    weekly_off = models.BooleanField(_("weekly off"), default=False)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["holiday_list", "date"], name="unique_holiday_per_list_date"
            )
        ]

    def __str__(self):
        return f"{self.holiday_list.name} {self.date}"


class Employee(TimeStampedModel):
    """An employee (ERPNext ``Employee``, trimmed to what this business uses).

    ``aadhaar`` is stored in full; the API masks it unless the caller holds the
    ``hr.Employee`` write permission (see ``hr.serializers``).  ``uan`` and
    ``esic_no`` are placeholders for the later PF / ESI modules.
    """

    name = models.CharField(max_length=60, unique=True, db_index=True)
    first_name = models.CharField(_("first name"), max_length=100)
    last_name = models.CharField(_("last name"), max_length=100, blank=True, default="")
    employee_name = models.CharField(
        _("employee name"), max_length=210, editable=False, db_index=True
    )
    gender = models.CharField(
        _("gender"), max_length=10, choices=Gender.choices, blank=True, default=""
    )
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    # Unique when set: it is the future portal login identity.
    mobile_no = models.CharField(
        _("mobile number"), max_length=20, null=True, blank=True
    )

    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="employees"
    )
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    designation = models.ForeignKey(
        Designation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    holiday_list = models.ForeignKey(
        HolidayList,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )

    status = models.CharField(
        _("status"),
        max_length=10,
        choices=EmployeeStatus.choices,
        default=EmployeeStatus.ACTIVE,
        db_index=True,
    )
    date_of_joining = models.DateField(_("date of joining"))
    relieving_date = models.DateField(_("relieving date"), null=True, blank=True)
    pay_type = models.CharField(
        _("pay type"), max_length=10, choices=PayType.choices, default=PayType.DAILY
    )
    standard_hours_per_day = models.DecimalField(
        _("standard hours per day"),
        max_digits=4,
        decimal_places=2,
        default=Decimal("8.00"),
    )

    bank_name = models.CharField(_("bank name"), max_length=140, blank=True, default="")
    bank_account_no = models.CharField(
        _("bank account number"), max_length=40, blank=True, default=""
    )
    ifsc_code = models.CharField(_("IFSC code"), max_length=11, blank=True, default="")
    pan = models.CharField(_("PAN"), max_length=10, blank=True, default="")
    aadhaar = models.CharField(_("Aadhaar"), max_length=12, blank=True, default="")
    uan = models.CharField(_("UAN"), max_length=20, blank=True, default="")
    esic_no = models.CharField(_("ESIC number"), max_length=20, blank=True, default="")

    # The synthetic portal login user (``hr.services.portal.ensure_portal_user``);
    # created when the admin first sets a portal PIN.  It has no roles.
    user = models.OneToOneField(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                check=Q(relieving_date__isnull=True)
                | Q(relieving_date__gte=F("date_of_joining")),
                name="check_employee_relieving_after_joining",
            ),
            models.CheckConstraint(
                check=~Q(status="Left") | Q(relieving_date__isnull=False),
                name="check_employee_left_has_relieving_date",
            ),
            models.CheckConstraint(
                check=Q(standard_hours_per_day__gt=0)
                & Q(standard_hours_per_day__lte=24),
                name="check_employee_standard_hours",
            ),
            models.UniqueConstraint(
                fields=["mobile_no"],
                condition=Q(mobile_no__isnull=False),
                name="unique_employee_mobile_no",
            ),
            # A blank mobile must be stored as NULL, or two blanks would collide.
            models.CheckConstraint(
                check=Q(mobile_no__isnull=True) | ~Q(mobile_no=""),
                name="check_employee_mobile_not_blank",
            ),
        ]

    def save(self, *args, **kwargs):
        self.employee_name = f"{self.first_name} {self.last_name}".strip()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and (
            "first_name" in update_fields or "last_name" in update_fields
        ):
            kwargs["update_fields"] = {*update_fields, "employee_name"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.employee_name}"


class EmployeeWageRate(TimeStampedModel):
    """Effective-dated pay for one employee, set individually by the admin.

    The rate for a date is the latest row with ``from_date <= date``.
    ``daily_rate`` applies to Daily employees, ``monthly_salary`` to Monthly
    ones; ``ot_hourly_rate`` to both.  A row that a submitted slip already
    depends on is frozen (corrections add a new, later-dated row).
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="wage_rates"
    )
    from_date = models.DateField(_("effective from"))
    daily_rate = models.DecimalField(
        _("daily rate"), max_digits=18, decimal_places=6, null=True, blank=True
    )
    monthly_salary = models.DecimalField(
        _("monthly salary"), max_digits=18, decimal_places=2, null=True, blank=True
    )
    ot_hourly_rate = models.DecimalField(
        _("overtime hourly rate"),
        max_digits=18,
        decimal_places=6,
        default=Decimal("0.000000"),
    )

    class Meta:
        ordering = ["employee_id", "-from_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "from_date"], name="unique_wage_rate_per_date"
            ),
            models.CheckConstraint(
                check=(Q(daily_rate__isnull=True) | Q(daily_rate__gte=0))
                & (Q(monthly_salary__isnull=True) | Q(monthly_salary__gte=0))
                & Q(ot_hourly_rate__gte=0),
                name="check_wage_rate_non_negative",
            ),
        ]

    def _check_unfrozen(self, from_date):
        if SalarySlip.objects.filter(
            employee_id=self.employee_id,
            docstatus=DocStatus.SUBMITTED,
            end_date__gte=from_date,
        ).exists():
            raise ValidationError(
                _(
                    "A submitted salary slip already covers dates from %(date)s; "
                    "add a new wage rate effective after it instead of changing this one."
                )
                % {"date": from_date}
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Employee.objects.select_for_update().get(pk=self.employee_id)
            self._check_unfrozen(self.from_date)
            if self.pk:
                old = type(self).objects.filter(pk=self.pk).values("from_date").first()
                if old and old["from_date"] != self.from_date:
                    self._check_unfrozen(old["from_date"])
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            Employee.objects.select_for_update().get(pk=self.employee_id)
            self._check_unfrozen(self.from_date)
            return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_id} from {self.from_date}"


# ------------------------------------------------------------------ attendance
class Attendance(TimeStampedModel):
    """One employee's attendance for one day.

    A plain record (not submittable) that is **locked** once a submitted salary
    slip of the employee covers its date: create / update / delete are refused
    (``ValidationError``; the API turns it into a 400).  Cancelling the slip
    unlocks it.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="attendance"
    )
    date = models.DateField(_("date"), db_index=True)
    status = models.CharField(
        _("status"), max_length=12, choices=AttendanceStatus.choices
    )
    ot_hours = models.DecimalField(
        _("overtime hours"), max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    remarks = models.CharField(_("remarks"), max_length=240, blank=True, default="")

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"], name="unique_attendance_per_day"
            ),
            models.CheckConstraint(
                check=Q(ot_hours__gte=0) & Q(ot_hours__lte=24),
                name="check_attendance_ot_hours",
            ),
            models.CheckConstraint(
                check=~Q(status="Absent") | Q(ot_hours=0),
                name="check_attendance_absent_no_ot",
            ),
        ]

    @staticmethod
    def locking_slip(employee_id, date):
        """The submitted slip of ``employee_id`` covering ``date``, if any."""
        return SalarySlip.objects.filter(
            employee_id=employee_id,
            docstatus=DocStatus.SUBMITTED,
            start_date__lte=date,
            end_date__gte=date,
        ).first()

    @classmethod
    def assert_unlocked(cls, employee_id, date):
        slip = cls.locking_slip(employee_id, date)
        if slip is not None:
            raise ValidationError(
                _("Attendance on %(date)s is locked by submitted salary slip %(slip)s.")
                % {"date": date, "slip": slip.name}
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Serialise against a slip submission of the same employee, which
            # takes the same row lock before it checks for overlaps.
            Employee.objects.select_for_update().get(pk=self.employee_id)
            self.assert_unlocked(self.employee_id, self.date)
            if self.pk:
                old = (
                    type(self)
                    .objects.filter(pk=self.pk)
                    .values("employee_id", "date")
                    .first()
                )
                if old and (
                    old["employee_id"] != self.employee_id or old["date"] != self.date
                ):
                    self.assert_unlocked(old["employee_id"], old["date"])
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            Employee.objects.select_for_update().get(pk=self.employee_id)
            self.assert_unlocked(self.employee_id, self.date)
            return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_id} {self.date} {self.status}"


# -------------------------------------------------------------- payroll setup
class SalaryComponent(TimeStampedModel):
    """A line type on a salary slip.  Deductions are components too, so PF / ESI
    can later plug in as further components without touching the slip.

    The three ``is_system`` components (Wages, Overtime, Advance Recovery) are
    created by ``ensure_hr_setup`` and are the ones the compute service owns.
    """

    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="salary_components"
    )
    name = models.CharField(_("name"), max_length=140)
    component_type = models.CharField(
        _("type"), max_length=10, choices=ComponentType.choices
    )
    account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    is_system = models.BooleanField(_("system component"), default=False)
    system_key = models.CharField(
        max_length=20,
        choices=SystemComponent.choices,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        ordering = ["company_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_salary_component_name"
            ),
            models.UniqueConstraint(
                fields=["company", "system_key"],
                condition=Q(system_key__isnull=False),
                name="unique_salary_component_system_key",
            ),
            models.CheckConstraint(
                check=(Q(is_system=False) & Q(system_key__isnull=True))
                | (Q(is_system=True) & Q(system_key__isnull=False)),
                name="check_salary_component_system_key",
            ),
        ]

    def __str__(self):
        return self.name


class HRSettings(TimeStampedModel):
    """Per-company HR accounts (instead of ERPNext's Company fields)."""

    company = models.OneToOneField(
        "core.Company", on_delete=models.PROTECT, related_name="hr_settings"
    )
    wage_expense_account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    payroll_payable_account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    employee_advance_account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    default_payment_account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        verbose_name = "HR Settings"
        verbose_name_plural = "HR Settings"

    #: Accounts whose change would orphan existing postings.
    LOCKED_ONCE_POSTED = ("payroll_payable_account", "employee_advance_account")

    def has_postings(self):
        return (
            SalarySlip.objects.filter(
                company_id=self.company_id, docstatus=DocStatus.SUBMITTED
            ).exists()
            or EmployeeAdvance.objects.filter(
                company_id=self.company_id, docstatus=DocStatus.SUBMITTED
            ).exists()
            or EmployeePayment.objects.filter(
                company_id=self.company_id, docstatus=DocStatus.SUBMITTED
            ).exists()
        )

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).first()
            if old is not None and any(
                getattr(old, f + "_id") != getattr(self, f + "_id")
                for f in self.LOCKED_ONCE_POSTED
            ):
                if self.has_postings():
                    raise ValidationError(
                        _(
                            "The payroll payable and employee advance accounts cannot "
                            "be changed once salary slips, advances or payments are submitted."
                        )
                    )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"HR Settings - {self.company}"


# ------------------------------------------------------------------ documents
class EmployeeAdvance(SubmittableDocument):
    """Cash advanced to an employee, recovered through salary slips.

    ``recovered_amount`` / ``outstanding`` are derived from the submitted slips'
    recovery rows (``hr.services.advances``), never stored.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="advances"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="employee_advances"
    )
    posting_date = models.DateField(_("posting date"))
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    purpose = models.CharField(_("purpose"), max_length=240, blank=True, default="")
    instalment_amount = models.DecimalField(
        _("instalment amount"),
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    paid_from = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-posting_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0), name="check_employee_advance_amount_positive"
            ),
            models.CheckConstraint(
                check=Q(instalment_amount__isnull=True)
                | (Q(instalment_amount__gt=0) & Q(instalment_amount__lte=F("amount"))),
                name="check_employee_advance_instalment",
            ),
        ]

    def validate_before_submit(self):
        from hr.services import advances

        advances.validate_advance(self)

    def on_submit(self):
        from hr.services import advances

        advances.post_advance(self)

    def on_cancel(self):
        from hr.services import advances

        advances.cancel_advance(self)

    def __str__(self):
        return f"{self.name} ({self.employee_id})"


class EmployeePayment(SubmittableDocument):
    """Wages paid to an employee: Dr wages payable, Cr cash / bank."""

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="payments"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="employee_payments"
    )
    posting_date = models.DateField(_("posting date"))
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    paid_from = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    mode = models.CharField(
        _("mode"), max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH
    )
    reference_no = models.CharField(
        _("reference number"), max_length=140, blank=True, default=""
    )
    remarks = models.CharField(_("remarks"), max_length=240, blank=True, default="")
    # Set by PayrollEntry "make payments"; informational.
    salary_slip = models.ForeignKey(
        "hr.SalarySlip",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    class Meta:
        ordering = ["-posting_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0), name="check_employee_payment_amount_positive"
            )
        ]

    def validate_before_submit(self):
        from hr.services import payments

        payments.validate_payment(self)

    def on_submit(self):
        from hr.services import payments

        payments.post_payment(self)

    def on_cancel(self):
        from accounts.services import gl as gl_service

        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} ({self.employee_id})"


class PayrollEntry(SubmittableDocument):
    """Bulk salary slips for a date range and optional department / branch."""

    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="payroll_entries"
    )
    posting_date = models.DateField(_("posting date"))
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-posting_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__gte=F("start_date")),
                name="check_payroll_entry_dates",
            )
        ]

    def _copy_child_rows_to(self, amendment):
        # Employees are copied, but not the slip link: the amendment makes its own.
        for row in self.employees.all():
            PayrollEntryEmployee.objects.create(
                payroll_entry=amendment, employee=row.employee
            )

    def validate_before_submit(self):
        from hr.services import payroll

        payroll.validate_payroll_entry(self)

    def on_submit(self):
        from hr.services import payroll

        payroll.submit_payroll_entry_slips(self)

    def on_cancel(self):
        from hr.services import payroll

        payroll.cancel_payroll_entry_slips(self)

    def __str__(self):
        return self.name


class PayrollEntryEmployee(models.Model):
    payroll_entry = models.ForeignKey(
        PayrollEntry, on_delete=models.CASCADE, related_name="employees"
    )
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="+")
    salary_slip = models.ForeignKey(
        "hr.SalarySlip",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_entry", "employee"],
                name="unique_payroll_entry_employee",
            )
        ]

    def __str__(self):
        return f"{self.payroll_entry_id} {self.employee_id}"


class SalarySlip(SubmittableDocument):
    """Wages of one employee for any date range.

    ``gross_pay`` / ``total_deduction`` / ``net_pay`` and the day / hour counts
    are derived: the header totals are rebuilt from the detail rows on every
    draft save (``hr.services.payroll.refresh_slip_totals``).  The ``amend()``
    hook names (``recompute_totals`` ...) are deliberately not used here because
    the core amend path would assume journal-entry fields.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="salary_slips"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="salary_slips"
    )
    payroll_entry = models.ForeignKey(
        PayrollEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="salary_slips",
    )
    posting_date = models.DateField(_("posting date"))
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    payment_days = models.DecimalField(
        _("payment days"), max_digits=7, decimal_places=2, default=Decimal("0.00")
    )
    ot_hours = models.DecimalField(
        _("overtime hours"), max_digits=9, decimal_places=2, default=Decimal("0.00")
    )
    gross_pay = models.DecimalField(
        _("gross pay"), max_digits=18, decimal_places=2, default=ZERO
    )
    total_deduction = models.DecimalField(
        _("total deduction"), max_digits=18, decimal_places=2, default=ZERO
    )
    net_pay = models.DecimalField(
        _("net pay"), max_digits=18, decimal_places=2, default=ZERO
    )
    remarks = models.CharField(_("remarks"), max_length=240, blank=True, default="")

    class Meta:
        ordering = ["-posting_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__gte=F("start_date")),
                name="check_salary_slip_dates",
            ),
            models.CheckConstraint(
                check=Q(gross_pay__gte=0) & Q(total_deduction__gte=0),
                name="check_salary_slip_totals_non_negative",
            ),
            # A draft may be negative while the admin is still editing; a posted
            # slip never is (net pay is a credit to wages payable).
            models.CheckConstraint(
                check=Q(docstatus=0) | Q(net_pay__gte=0),
                name="check_salary_slip_posted_net_non_negative",
            ),
        ]
        indexes = [models.Index(fields=["employee", "start_date", "end_date"])]

    def validate_before_submit(self):
        from hr.services import payroll

        payroll.validate_slip(self)

    def on_submit(self):
        from hr.services import payroll

        payroll.post_slip(self)

    def on_cancel(self):
        from hr.services import payroll

        payroll.cancel_slip(self)

    def __str__(self):
        return f"{self.name} ({self.employee_id})"


class SalarySlipDetail(models.Model):
    """One component line of a slip.  ``is_manual`` rows were entered by a person
    (bonus, fine, an edited recovery) and survive a recompute; the others are
    generated from attendance and wage rates."""

    slip = models.ForeignKey(
        SalarySlip, on_delete=models.CASCADE, related_name="details"
    )
    idx = models.PositiveIntegerField(default=0)
    component = models.ForeignKey(
        SalaryComponent, on_delete=models.PROTECT, related_name="slip_details"
    )
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    is_manual = models.BooleanField(_("manual"), default=True)
    advance = models.ForeignKey(
        EmployeeAdvance,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recoveries",
    )
    remarks = models.CharField(_("remarks"), max_length=240, blank=True, default="")

    class Meta:
        ordering = ["idx", "pk"]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gte=0), name="check_salary_slip_detail_amount"
            ),
            models.UniqueConstraint(
                fields=["slip", "advance"],
                condition=Q(advance__isnull=False),
                name="unique_slip_detail_advance",
            ),
        ]

    def __str__(self):
        return f"{self.slip_id} {self.component_id} {self.amount}"


# ----------------------------------------------------------- employee portal
class EmployeePortalAccess(TimeStampedModel):
    """Self-service portal credentials of one employee (mobile number + PIN).

    Only a Django password hash of the PIN is stored (``pin_hash``); the raw PIN
    is never stored, logged or returned.  The linked ``Employee.user`` is a
    synthetic, role-less user that exists only to carry a JWT -- see
    ``docs/erpnext_divergences.md`` ("Employee self-service portal").
    ``failed_attempts`` / ``locked_until`` implement the brute-force lockout.
    """

    employee = models.OneToOneField(
        Employee, on_delete=models.CASCADE, related_name="portal_access"
    )
    portal_enabled = models.BooleanField(_("portal enabled"), default=False)
    pin_hash = models.CharField(max_length=255, blank=True, default="", editable=False)
    pin_must_change = models.BooleanField(_("PIN must be changed"), default=True)
    pin_set_at = models.DateTimeField(null=True, blank=True, editable=False)
    pin_set_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # An enabled portal always has a PIN to check against.
            models.CheckConstraint(
                check=Q(portal_enabled=False) | ~Q(pin_hash=""),
                name="check_portal_enabled_has_pin",
            )
        ]

    def __str__(self):
        return f"Portal access of {self.employee_id}"


class AdvanceRequest(TimeStampedModel):
    """An advance asked for by the employee through the portal.

    Approval creates a **draft** :class:`EmployeeAdvance` (linked in ``advance``)
    that the admin then submits to pay out; nothing is posted by the request
    itself.  At most one request per employee can be Pending.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="advance_requests"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="advance_requests"
    )
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    reason = models.CharField(_("reason"), max_length=240)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=AdvanceRequestStatus.choices,
        default=AdvanceRequestStatus.PENDING,
        db_index=True,
    )
    response = models.CharField(_("response"), max_length=500, blank=True, default="")
    reviewed_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    advance = models.OneToOneField(
        EmployeeAdvance,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="request",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0), name="check_advance_request_amount_positive"
            ),
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(status="Pending"),
                name="unique_pending_advance_request_per_employee",
            ),
            # Approved <=> the draft advance exists.
            models.CheckConstraint(
                check=(Q(status="Approved") & Q(advance__isnull=False))
                | (~Q(status="Approved") & Q(advance__isnull=True)),
                name="check_advance_request_advance_iff_approved",
            ),
            # A decision (approve / reject) is always stamped.
            models.CheckConstraint(
                check=~Q(status__in=["Approved", "Rejected"])
                | Q(reviewed_at__isnull=False),
                name="check_advance_request_decision_stamped",
            ),
        ]

    def __str__(self):
        return f"Advance request {self.pk} ({self.employee_id}, {self.status})"


class EmployeeIssue(TimeStampedModel):
    """A problem flagged by the employee (wrong attendance / payment ...).

    Purely the communication record: the admin fixes the underlying data through
    the normal screens (cancel + amend a slip, correct attendance ...) and then
    resolves or rejects the issue with a reply.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="issues"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="employee_issues"
    )
    category = models.CharField(
        _("category"), max_length=20, choices=IssueCategory.choices
    )
    reference_date = models.DateField(_("reference date"), null=True, blank=True)
    reference_doc = models.CharField(
        _("reference document"), max_length=60, null=True, blank=True
    )
    message = models.CharField(_("message"), max_length=1000)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=IssueStatus.choices,
        default=IssueStatus.OPEN,
        db_index=True,
    )
    response = models.CharField(_("response"), max_length=1000, blank=True, default="")
    resolved_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=~Q(message=""), name="check_employee_issue_message_not_blank"
            ),
            # Open <=> not yet closed by an admin.
            models.CheckConstraint(
                check=(Q(status="Open") & Q(resolved_at__isnull=True))
                | (~Q(status="Open") & Q(resolved_at__isnull=False)),
                name="check_employee_issue_closed_iff_resolved_at",
            ),
        ]

    def __str__(self):
        return f"Issue {self.pk} ({self.employee_id}, {self.status})"
