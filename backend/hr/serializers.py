"""HR serializers.

* ``Employee.aadhaar`` is stored in full and returned masked (``XXXX-XXXX-1234``)
  unless the caller holds the ``hr.Employee`` **write** permission (superusers
  count).  That is the whole PII rule -- kept deliberately simple.
* Slip totals, payment days, OT hours and the Wages / Overtime rows are server
  owned: they are rebuilt on every draft save and ignored on input.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError

from accounts.models import AccountRootType
from accounts.services.gl import quantize_money
from core.mixins import (
    DraftNestedWriteMixin,
    NestedMasterWriteMixin,
    TranslatedSerializerMixin,
)
from core.models import DocStatus, NamingSeries
from django.utils.translation import gettext_lazy as _
from hr.models import (
    Attendance,
    AttendanceStatus,
    Branch,
    Department,
    Designation,
    Employee,
    EmployeeAdvance,
    EmployeePayment,
    EmployeeStatus,
    EmployeeWageRate,
    ExpenseClaim,
    ExpenseClaimDetail,
    ExpenseReimbursement,
    Holiday,
    HolidayList,
    HRSettings,
    PayrollEntry,
    PayrollEntryEmployee,
    PayType,
    SalaryComponent,
    SalarySlip,
    SalarySlipDetail,
    SystemComponent,
    LeaveType,
    LeaveAllocation,
    LeaveApplication,
)
from hr.services import advances as advance_service
from hr.services import expense_claims as claim_service
from hr.services import leave as leave_service
from hr.services import payroll
from hr.services.setup import get_settings
from hr.services.wages import MAX_SLIP_DAYS
from users.permissions import user_has_model_permission

MOBILE_RE = re.compile(r"^\+?[0-9]{10,15}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def as_drf_error(exc):
    """A Django ``ValidationError`` as a DRF one (field dict kept when present)."""
    if hasattr(exc, "error_dict"):
        return DRFValidationError(
            {k: [str(m) for m in v] for k, v in exc.message_dict.items()}
        )
    return DRFValidationError({"non_field_errors": [str(m) for m in exc.messages]})


class ModelErrorMixin:
    """Turn model-level ``ValidationError`` (locks, frozen rows) into a 400."""

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)


class AutoNameMixin:
    """Auto-number a submittable document that has no child table."""

    name_prefix = ""

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        kwargs["name"] = {**kwargs.get("name", {}), "required": False}
        return kwargs

    def create(self, validated_data):
        with transaction.atomic():
            if not validated_data.get("name"):
                validated_data["name"] = NamingSeries.next(self.name_prefix)
            return super().create(validated_data)


# --------------------------------------------------------------------- masters
class DepartmentSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ("id", "name", "is_active")
        translatable_fields = ["name"]


class DesignationSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Designation
        fields = ("id", "name", "is_active")
        translatable_fields = ["name"]


class BranchSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ("id", "name", "is_active")
        translatable_fields = ["name"]


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ("id", "date", "description", "weekly_off")
        read_only_fields = ("id",)
        # (holiday_list, date) uniqueness is checked on the whole list below.
        validators = []


class HolidayListSerializer(NestedMasterWriteMixin, serializers.ModelSerializer):
    children_field = "holidays"
    holidays = HolidaySerializer(many=True, required=False)

    class Meta:
        model = HolidayList
        fields = ("id", "name", "from_date", "to_date", "holidays")

    def validate(self, attrs):
        inst = self.instance
        start = attrs.get("from_date", inst.from_date if inst else None)
        end = attrs.get("to_date", inst.to_date if inst else None)
        if start and end and end < start:
            raise DRFValidationError(
                {"to_date": _("To date cannot be before from date.")}
            )
        if "holidays" in attrs:
            seen = set()
            for row in attrs["holidays"]:
                if row["date"] in seen:
                    raise DRFValidationError(
                        {"holidays": _("%(d)s is listed twice.") % {"d": row["date"]}}
                    )
                seen.add(row["date"])
                if start and end and not (start <= row["date"] <= end):
                    raise DRFValidationError(
                        {
                            "holidays": _("%(d)s is outside the list's date range.")
                            % {"d": row["date"]}
                        }
                    )
        return super().validate(attrs)


def mask_aadhaar(value):
    if not value:
        return value
    return f"XXXX-XXXX-{value[-4:]}"


class EmployeeSerializer(serializers.ModelSerializer):
    # Free-form input (spaces / dashes) is normalised in validate_aadhaar, so the
    # model's 12-character limit must not reject it first.
    aadhaar = serializers.CharField(max_length=20, required=False, allow_blank=True)
    department_name = serializers.CharField(
        source="department.name", read_only=True, default=None
    )
    designation_name = serializers.CharField(
        source="designation.name", read_only=True, default=None
    )
    branch_name = serializers.CharField(
        source="branch.name", read_only=True, default=None
    )
    # Portal status only (never the PIN hash); managed via set-portal-pin /
    # disable-portal.
    portal_enabled = serializers.SerializerMethodField()
    portal_pin_must_change = serializers.SerializerMethodField()
    portal_locked = serializers.SerializerMethodField()
    portal_last_login_at = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = (
            "id",
            "name",
            "first_name",
            "last_name",
            "employee_name",
            "gender",
            "date_of_birth",
            "mobile_no",
            "company",
            "department",
            "department_name",
            "designation",
            "designation_name",
            "branch",
            "branch_name",
            "holiday_list",
            "status",
            "date_of_joining",
            "relieving_date",
            "pay_type",
            "standard_hours_per_day",
            "bank_name",
            "bank_account_no",
            "ifsc_code",
            "pan",
            "tax_regime",
            "tds_per_month",
            "aadhaar",
            "uan",
            "esic_no",
            "portal_enabled",
            "portal_pin_must_change",
            "portal_locked",
            "portal_last_login_at",
            "modified_at",
        )
        read_only_fields = ("id", "name", "employee_name", "modified_at")
        # mobile_no uniqueness is checked in validate_mobile_no (conditional constraint).
        validators = []

    # --------------------------------------------------------------- portal
    @staticmethod
    def _portal(obj):
        return getattr(obj, "portal_access", None)

    def get_portal_enabled(self, obj):
        access = self._portal(obj)
        return bool(access and access.portal_enabled)

    def get_portal_pin_must_change(self, obj):
        access = self._portal(obj)
        return bool(access and access.portal_enabled and access.pin_must_change)

    def get_portal_locked(self, obj):
        access = self._portal(obj)
        return bool(
            access
            and access.locked_until is not None
            and access.locked_until > timezone.now()
        )

    def get_portal_last_login_at(self, obj):
        access = self._portal(obj)
        return access.last_login_at if access else None

    # ----------------------------------------------------------------- PII
    def _may_see_pii(self):
        if self.context.get("mask_pii"):
            return False
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user) and user_has_model_permission(
            user, "hr.Employee", "can_write"
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._may_see_pii():
            data["aadhaar"] = mask_aadhaar(data.get("aadhaar") or "")
        return data

    # ---------------------------------------------------------- validation
    def validate_mobile_no(self, value):
        value = (value or "").replace(" ", "").replace("-", "") or None
        if value is None:
            return None
        if not MOBILE_RE.match(value):
            raise serializers.ValidationError(_("Enter a valid mobile number."))
        clash = Employee.objects.filter(mobile_no=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                _("Another employee already uses this mobile number.")
            )
        return value

    def validate_pan(self, value):
        value = (value or "").strip().upper()
        if value and not PAN_RE.match(value):
            raise serializers.ValidationError(_("Enter a valid PAN."))
        return value

    def validate_tds_per_month(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError(
                _("The fixed monthly TDS cannot be negative.")
            )
        return value

    def validate_ifsc_code(self, value):
        value = (value or "").strip().upper()
        if value and not IFSC_RE.match(value):
            raise serializers.ValidationError(_("Enter a valid IFSC code."))
        return value

    def validate_aadhaar(self, value):
        value = (value or "").replace(" ", "").replace("-", "")
        if value and not (value.isdigit() and len(value) == 12):
            raise serializers.ValidationError(_("Aadhaar must be 12 digits."))
        return value

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        joining, relieving = get("date_of_joining"), get("relieving_date")
        if joining and relieving and relieving < joining:
            raise DRFValidationError(
                {
                    "relieving_date": _(
                        "Relieving date cannot be before the joining date."
                    )
                }
            )
        if get("status") == EmployeeStatus.LEFT and not relieving:
            raise DRFValidationError(
                {
                    "relieving_date": _(
                        "A relieving date is required when the employee has left."
                    )
                }
            )
        if inst and "company" in attrs and attrs["company"] != inst.company:
            raise DRFValidationError(
                {"company": _("The company of an employee cannot be changed.")}
            )
        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            validated_data["name"] = NamingSeries.next("EMP-")
            return super().create(validated_data)


class EmployeeWageRateSerializer(ModelErrorMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )

    class Meta:
        model = EmployeeWageRate
        fields = (
            "id",
            "employee",
            "employee_name",
            "from_date",
            "daily_rate",
            "monthly_salary",
            "ot_hourly_rate",
        )
        read_only_fields = ("id",)
        validators = []  # (employee, from_date) uniqueness checked below

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee = get("employee")
        if inst and employee != inst.employee:
            raise DRFValidationError({"employee": _("The employee cannot be changed.")})
        if employee.pay_type == PayType.DAILY and get("daily_rate") is None:
            raise DRFValidationError(
                {"daily_rate": _("A daily rate is required for a Daily employee.")}
            )
        if employee.pay_type == PayType.MONTHLY and get("monthly_salary") is None:
            raise DRFValidationError(
                {
                    "monthly_salary": _(
                        "A monthly salary is required for a Monthly employee."
                    )
                }
            )
        for field in ("daily_rate", "monthly_salary", "ot_hourly_rate"):
            value = get(field)
            if value is not None and value < 0:
                raise DRFValidationError({field: _("Cannot be negative.")})
        clash = EmployeeWageRate.objects.filter(
            employee=employee, from_date=get("from_date")
        )
        if inst:
            clash = clash.exclude(pk=inst.pk)
        if clash.exists():
            raise DRFValidationError(
                {"from_date": _("A wage rate already starts on this date.")}
            )
        return attrs


class AttendanceSerializer(ModelErrorMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )

    class Meta:
        model = Attendance
        fields = (
            "id",
            "employee",
            "employee_name",
            "date",
            "status",
            "ot_hours",
            "remarks",
        )
        read_only_fields = ("id",)
        validators = []  # (employee, date) uniqueness checked below

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee, date = get("employee"), get("date")
        if get("ot_hours") is not None and not (0 <= get("ot_hours") <= 24):
            raise DRFValidationError(
                {"ot_hours": _("Overtime must be between 0 and 24 hours.")}
            )
        if get("status") == AttendanceStatus.ABSENT and get("ot_hours"):
            raise DRFValidationError(
                {"ot_hours": _("An absent employee cannot have overtime.")}
            )
        if date < employee.date_of_joining or (
            employee.relieving_date and date > employee.relieving_date
        ):
            raise DRFValidationError(
                {"date": _("The date is outside the employee's period of employment.")}
            )
        clash = Attendance.objects.filter(employee=employee, date=date)
        if inst:
            clash = clash.exclude(pk=inst.pk)
        if clash.exists():
            raise DRFValidationError(
                {
                    "date": _(
                        "Attendance is already recorded for this employee and date."
                    )
                }
            )
        return attrs


class MusterRowSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    ot_hours = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        default=Decimal("0.00"),
        min_value=Decimal("0"),
        max_value=Decimal("24"),
    )
    remarks = serializers.CharField(
        max_length=240, required=False, allow_blank=True, default=""
    )


class MusterUpsertSerializer(serializers.Serializer):
    date = serializers.DateField()
    company = serializers.IntegerField(required=False, allow_null=True)
    rows = MusterRowSerializer(many=True, allow_empty=False)


# ------------------------------------------------------------ payroll setup
def _check_account(account, company, root_type, field):
    if account.company_id != company.pk:
        raise DRFValidationError({field: _("Account must belong to the company.")})
    if account.is_group:
        raise DRFValidationError({field: _("Account must not be a group account.")})
    if root_type is not None and account.root_type != root_type:
        raise DRFValidationError(
            {field: _("Account must be of type %(t)s.") % {"t": root_type}}
        )


class SalaryComponentSerializer(ModelErrorMixin, serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = SalaryComponent
        fields = (
            "id",
            "company",
            "name",
            "component_type",
            "account",
            "account_name",
            "is_system",
            "system_key",
        )
        read_only_fields = ("id", "is_system", "system_key")
        validators = []

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        company = get("company")
        if inst and company != inst.company:
            raise DRFValidationError({"company": _("The company cannot be changed.")})
        _check_account(get("account"), company, None, "account")
        if (
            inst
            and "component_type" in attrs
            and attrs["component_type"] != inst.component_type
        ):
            if inst.is_system or inst.slip_details.exists():
                raise DRFValidationError(
                    {
                        "component_type": _(
                            "The type of a component in use cannot be changed."
                        )
                    }
                )
        clash = SalaryComponent.objects.filter(company=company, name=get("name"))
        if inst:
            clash = clash.exclude(pk=inst.pk)
        if clash.exists():
            raise DRFValidationError({"name": _("A component with this name exists.")})
        return attrs


class HRSettingsSerializer(ModelErrorMixin, serializers.ModelSerializer):
    class Meta:
        model = HRSettings
        fields = (
            "id",
            "company",
            "wage_expense_account",
            "payroll_payable_account",
            "employee_advance_account",
            "expense_claim_payable_account",
            "default_payment_account",
            "tds_payable_account",
        )
        read_only_fields = ("id",)
        validators = []

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        company = get("company")
        if inst and company != inst.company:
            raise DRFValidationError({"company": _("The company cannot be changed.")})
        if not inst and HRSettings.objects.filter(company=company).exists():
            raise DRFValidationError(
                {"company": _("HR Settings already exist for this company.")}
            )
        _check_account(
            get("wage_expense_account"),
            company,
            AccountRootType.EXPENSE,
            "wage_expense_account",
        )
        _check_account(
            get("payroll_payable_account"),
            company,
            AccountRootType.LIABILITY,
            "payroll_payable_account",
        )
        _check_account(
            get("employee_advance_account"),
            company,
            AccountRootType.ASSET,
            "employee_advance_account",
        )
        _check_account(
            get("expense_claim_payable_account"),
            company,
            AccountRootType.LIABILITY,
            "expense_claim_payable_account",
        )
        _check_account(
            get("default_payment_account"),
            company,
            AccountRootType.ASSET,
            "default_payment_account",
        )
        if get("tds_payable_account") is not None:
            _check_account(
                get("tds_payable_account"),
                company,
                AccountRootType.LIABILITY,
                "tds_payable_account",
            )
        return attrs


# ------------------------------------------------------------------ documents
class SalarySlipDetailSerializer(serializers.ModelSerializer):
    component_name = serializers.CharField(source="component.name", read_only=True)
    component_type = serializers.CharField(
        source="component.component_type", read_only=True
    )
    system_key = serializers.CharField(source="component.system_key", read_only=True)
    advance_name = serializers.CharField(
        source="advance.name", read_only=True, default=None
    )

    class Meta:
        model = SalarySlipDetail
        fields = (
            "id",
            "idx",
            "component",
            "component_name",
            "component_type",
            "system_key",
            "amount",
            "is_manual",
            "advance",
            "advance_name",
            "remarks",
        )
        read_only_fields = ("id", "idx", "is_manual")


class SalarySlipSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "details"
    name_prefix = payroll.SLIP_PREFIX

    details = SalarySlipDetailSerializer(many=True, required=False)
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    # Write-only switch: on create, propose recovery rows for open advances.
    propose_recovery = serializers.BooleanField(
        write_only=True, required=False, default=True
    )

    class Meta:
        model = SalarySlip
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "company",
            "employee",
            "employee_name",
            "payroll_entry",
            "posting_date",
            "start_date",
            "end_date",
            "payment_days",
            "ot_hours",
            "gross_pay",
            "total_deduction",
            "net_pay",
            "remarks",
            "details",
            "propose_recovery",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "amended_from",
            "payroll_entry",
            "payment_days",
            "ot_hours",
            "gross_pay",
            "total_deduction",
            "net_pay",
            "modified_at",
        )

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee, company = get("employee"), get("company")
        if inst and "employee" in attrs and attrs["employee"] != inst.employee:
            raise DRFValidationError(
                {"employee": _("The employee of a slip cannot be changed.")}
            )
        if employee.company_id != company.pk:
            raise DRFValidationError(
                {"employee": _("The employee belongs to a different company.")}
            )
        start, end = get("start_date"), get("end_date")
        if end < start:
            raise DRFValidationError(
                {"end_date": _("End date cannot be before start date.")}
            )
        if (end - start).days + 1 > MAX_SLIP_DAYS:
            raise DRFValidationError(
                {
                    "end_date": _("A salary slip cannot cover more than %(n)s days.")
                    % {"n": MAX_SLIP_DAYS}
                }
            )
        if "details" in attrs:
            attrs["details"] = self._clean_rows(attrs["details"], employee, company)
        return super().validate(attrs)

    def _clean_rows(self, rows, employee, company):
        kept, seen_advances = [], set()
        for row in rows:
            component = row["component"]
            if component.system_key in (
                SystemComponent.WAGES,
                SystemComponent.OVERTIME,
                SystemComponent.TDS,
            ):
                continue  # server owned: regenerated from attendance and rates
            if component.company_id != company.pk:
                raise DRFValidationError(
                    {
                        "details": _("Component %(c)s belongs to another company.")
                        % {"c": component.name}
                    }
                )
            if row["amount"] <= 0:
                raise DRFValidationError(
                    {"details": _("Amounts must be greater than zero.")}
                )
            advance = row.get("advance")
            if component.system_key == SystemComponent.ADVANCE_RECOVERY:
                if advance is None:
                    raise DRFValidationError(
                        {"details": _("Advance recovery rows must name an advance.")}
                    )
                if (
                    advance.employee_id != employee.pk
                    or advance.company_id != company.pk
                    or advance.docstatus != DocStatus.SUBMITTED
                ):
                    raise DRFValidationError(
                        {
                            "details": _(
                                "Advance %(a)s cannot be recovered on this slip."
                            )
                            % {"a": advance.name}
                        }
                    )
                if advance.pk in seen_advances:
                    raise DRFValidationError(
                        {
                            "details": _("Advance %(a)s appears twice.")
                            % {"a": advance.name}
                        }
                    )
                seen_advances.add(advance.pk)
            elif advance is not None:
                raise DRFValidationError(
                    {"details": _("Only advance recovery rows may name an advance.")}
                )
            kept.append(row)
        return kept

    # The mixin calls this after every create / update.
    def refresh_totals(self, instance):
        try:
            payroll.refresh_draft_slip(instance)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)

    def create(self, validated_data):
        propose = validated_data.pop("propose_recovery", True)
        with transaction.atomic():
            slip = super().create(validated_data)
            if propose:
                payroll.propose_advance_recovery(slip)
                payroll.refresh_slip_totals(slip)
        return slip

    def update(self, instance, validated_data):
        validated_data.pop("propose_recovery", None)
        return super().update(instance, validated_data)


class PayrollEntryEmployeeSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    employee_id = serializers.CharField(source="employee.name", read_only=True)
    salary_slip_name = serializers.CharField(
        source="salary_slip.name", read_only=True, default=None
    )

    class Meta:
        model = PayrollEntryEmployee
        fields = (
            "id",
            "employee",
            "employee_id",
            "employee_name",
            "salary_slip",
            "salary_slip_name",
        )
        read_only_fields = ("id", "salary_slip")
        validators = []


class PayrollEntrySerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "employees"
    name_prefix = "PAY-ENT-"

    employees = PayrollEntryEmployeeSerializer(many=True, required=False)

    class Meta:
        model = PayrollEntry
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "company",
            "posting_date",
            "start_date",
            "end_date",
            "department",
            "branch",
            "employees",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "amended_from", "modified_at")

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        if get("end_date") < get("start_date"):
            raise DRFValidationError(
                {"end_date": _("End date cannot be before start date.")}
            )
        company = get("company")
        seen = set()
        for row in attrs.get("employees", []):
            employee = row["employee"]
            if employee.company_id != company.pk:
                raise DRFValidationError(
                    {
                        "employees": _("%(e)s belongs to a different company.")
                        % {"e": employee.name}
                    }
                )
            if employee.pk in seen:
                raise DRFValidationError(
                    {"employees": _("%(e)s is listed twice.") % {"e": employee.name}}
                )
            seen.add(employee.pk)
        return super().validate(attrs)


class EmployeeAdvanceSerializer(AutoNameMixin, serializers.ModelSerializer):
    name_prefix = "EMP-ADV-"

    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    recovered_amount = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeAdvance
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "employee",
            "employee_name",
            "company",
            "posting_date",
            "amount",
            "purpose",
            "instalment_amount",
            "paid_from",
            "recovered_amount",
            "outstanding",
            "status",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "amended_from", "modified_at")

    def _recovered(self, obj):
        # The viewset annotates ``recovered`` (one query for a list page).
        value = getattr(obj, "recovered", None)
        if value is None:
            value = advance_service.recovered_amount(obj)
        return quantize_money(value)

    def get_recovered_amount(self, obj):
        return (
            str(self._recovered(obj))
            if obj.docstatus == DocStatus.SUBMITTED
            else "0.00"
        )

    def get_outstanding(self, obj):
        if obj.docstatus != DocStatus.SUBMITTED:
            return "0.00"
        return str(obj.amount - self._recovered(obj))

    def get_status(self, obj):
        recovered = (
            self._recovered(obj) if obj.docstatus == DocStatus.SUBMITTED else None
        )
        return advance_service.status_of(obj, recovered)

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee, company = get("employee"), get("company")
        if employee.company_id != company.pk:
            raise DRFValidationError(
                {"employee": _("The employee belongs to a different company.")}
            )
        if get("amount") <= 0:
            raise DRFValidationError({"amount": _("Amount must be greater than zero.")})
        instalment = get("instalment_amount")
        if instalment is not None and not (0 < instalment <= get("amount")):
            raise DRFValidationError(
                {
                    "instalment_amount": _(
                        "The instalment must be between zero and the advance amount."
                    )
                }
            )
        try:
            advance_service.validate_paid_from(get("paid_from"), company)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)
        return attrs


class EmployeePaymentSerializer(AutoNameMixin, serializers.ModelSerializer):
    name_prefix = "EMP-PAY-"

    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )

    class Meta:
        model = EmployeePayment
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "employee",
            "employee_name",
            "company",
            "posting_date",
            "amount",
            "paid_from",
            "mode",
            "reference_no",
            "remarks",
            "salary_slip",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "amended_from",
            "salary_slip",
            "modified_at",
        )

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee, company = get("employee"), get("company")
        if employee.company_id != company.pk:
            raise DRFValidationError(
                {"employee": _("The employee belongs to a different company.")}
            )
        if get("amount") <= 0:
            raise DRFValidationError({"amount": _("Amount must be greater than zero.")})
        try:
            advance_service.validate_paid_from(get("paid_from"), company)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)
        return attrs


# --------------------------------------------------------------------- leave
# ------------------------------------------------------------ expense claims
class ExpenseClaimDetailSerializer(serializers.ModelSerializer):
    expense_category_name = serializers.CharField(
        source="expense_category.name", read_only=True
    )

    class Meta:
        model = ExpenseClaimDetail
        fields = (
            "id",
            "idx",
            "expense_date",
            "expense_category",
            "expense_category_name",
            "description",
            "amount",
            "sanctioned_amount",
        )
        read_only_fields = ("id", "idx")
        extra_kwargs = {"sanctioned_amount": {"required": False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("sanctioned_amount") is None and "amount" in attrs:
            attrs["sanctioned_amount"] = attrs["amount"]
        amount = attrs.get("amount")
        sanctioned = attrs.get("sanctioned_amount")
        if amount is not None and amount <= 0:
            raise DRFValidationError({"amount": _("Amount must be greater than zero.")})
        if (
            amount is not None
            and sanctioned is not None
            and not (0 <= sanctioned <= amount)
        ):
            raise DRFValidationError(
                {
                    "sanctioned_amount": _(
                        "The sanctioned amount must be between zero and the claimed amount."
                    )
                }
            )
        return attrs


class ExpenseClaimSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "lines"
    name_prefix = claim_service.CLAIM_PREFIX

    lines = ExpenseClaimDetailSerializer(many=True, required=False)
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    status = serializers.SerializerMethodField()

    class Meta:
        model = ExpenseClaim
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "company",
            "employee",
            "employee_name",
            "posting_date",
            "payable_account",
            "remarks",
            "total_claimed",
            "total_sanctioned",
            "outstanding_amount",
            "status",
            "lines",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "docstatus",
            "amended_from",
            "total_claimed",
            "total_sanctioned",
            "outstanding_amount",
            "modified_at",
        )
        extra_kwargs = {"payable_account": {"required": False}}

    def get_status(self, obj):
        return claim_service.status_of(obj)

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        company, employee = get("company"), get("employee")
        payable = get("payable_account")
        try:
            if payable is None:
                # Default: the company's expense claim payable account.
                payable = get_settings(company).expense_claim_payable_account
                attrs["payable_account"] = payable
            claim_service.check_header(company, employee, payable)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)
        return super().validate(attrs)

    # The mixin calls this after every create / update.
    def refresh_totals(self, instance):
        try:
            claim_service.refresh_draft(instance)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)


class ExpenseReimbursementSerializer(AutoNameMixin, serializers.ModelSerializer):
    name_prefix = claim_service.REIMBURSEMENT_PREFIX

    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    expense_claim_name = serializers.CharField(
        source="expense_claim.name", read_only=True
    )

    class Meta:
        model = ExpenseReimbursement
        fields = (
            "id",
            "name",
            "docstatus",
            "amended_from",
            "company",
            "employee",
            "employee_name",
            "expense_claim",
            "expense_claim_name",
            "posting_date",
            "amount",
            "mode",
            "paid_from",
            "reference_no",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "amended_from", "modified_at")

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        company, employee = get("company"), get("employee")
        claim = get("expense_claim")
        try:
            claim_service.check_reimbursement_links(company, employee, claim)
            get_settings(company)  # clear error when HR is not set up
            advance_service.validate_paid_from(get("paid_from"), company)
        except DjangoValidationError as exc:
            raise as_drf_error(exc)
        if get("amount") is not None and get("amount") <= 0:
            raise DRFValidationError({"amount": _("Amount must be greater than zero.")})
        return attrs


class LeaveTypeSerializer(
    TranslatedSerializerMixin, ModelErrorMixin, serializers.ModelSerializer
):
    class Meta:
        model = LeaveType
        fields = (
            "id",
            "company",
            "name",
            "max_days_allowed",
            "is_carry_forward",
            "is_active",
        )
        translatable_fields = ["name"]


class LeaveAllocationSerializer(
    AutoNameMixin, ModelErrorMixin, serializers.ModelSerializer
):
    name_prefix = leave_service.LEAVE_ALLOCATION_PREFIX

    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)

    class Meta:
        model = LeaveAllocation
        fields = (
            "id",
            "name",
            "docstatus",
            "company",
            "employee",
            "employee_name",
            "leave_type",
            "leave_type_name",
            "from_date",
            "to_date",
            "total_leaves_allocated",
            "notes",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "modified_at")

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee = get("employee")
        company = get("company")
        leave_type = get("leave_type")

        if employee and company and employee.company_id != company.pk:
            raise DRFValidationError(
                {"employee": _("The employee belongs to a different company.")}
            )
        if leave_type and company and leave_type.company_id != company.pk:
            raise DRFValidationError(
                {"leave_type": _("The leave type belongs to a different company.")}
            )
        from_date = get("from_date")
        to_date = get("to_date")
        if from_date and to_date and to_date < from_date:
            raise DRFValidationError(
                {"to_date": _("End date cannot be before start date.")}
            )
        alloc = get("total_leaves_allocated")
        if alloc is not None and alloc < 0:
            raise DRFValidationError(
                {"total_leaves_allocated": _("Allocated leaves cannot be negative.")}
            )
        return attrs


class LeaveApplicationSerializer(
    AutoNameMixin, ModelErrorMixin, serializers.ModelSerializer
):
    name_prefix = leave_service.LEAVE_APPLICATION_PREFIX

    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)

    class Meta:
        model = LeaveApplication
        fields = (
            "id",
            "name",
            "docstatus",
            "company",
            "employee",
            "employee_name",
            "leave_type",
            "leave_type_name",
            "posting_date",
            "from_date",
            "to_date",
            "total_leave_days",
            "reason",
            "modified_at",
        )
        read_only_fields = ("id", "docstatus", "modified_at")

    def validate(self, attrs):
        inst = self.instance

        def get(name):
            return attrs.get(name, getattr(inst, name, None) if inst else None)

        employee = get("employee")
        company = get("company")
        leave_type = get("leave_type")

        if employee and company and employee.company_id != company.pk:
            raise DRFValidationError(
                {"employee": _("The employee belongs to a different company.")}
            )
        if leave_type and company and leave_type.company_id != company.pk:
            raise DRFValidationError(
                {"leave_type": _("The leave type belongs to a different company.")}
            )
        from_date = get("from_date")
        to_date = get("to_date")
        if from_date and to_date and to_date < from_date:
            raise DRFValidationError(
                {"to_date": _("End date cannot be before start date.")}
            )
        total_days = get("total_leave_days")
        if total_days is not None and total_days <= 0:
            raise DRFValidationError(
                {"total_leave_days": _("Total leave days must be greater than zero.")}
            )
        if from_date and to_date and total_days is not None:
            days_span = (to_date - from_date).days + 1
            if total_days > Decimal(days_span):
                raise DRFValidationError(
                    {
                        "total_leave_days": _(
                            "Total leave days (%(days)s) cannot exceed the date range span (%(span)s)."
                        )
                        % {"days": total_days, "span": days_span}
                    }
                )
        return attrs
