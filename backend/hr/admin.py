"""Django admin for HR.  Submittable documents keep ``docstatus`` read-only so
submit / cancel always go through the model (and the GL posting service)."""

from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from core.admin import SubmittableDocumentAdmin, TimeStampedAdminMixin
from hr.models import (
    AdvanceRequest,
    Attendance,
    Branch,
    Department,
    Designation,
    Employee,
    EmployeeAdvance,
    EmployeeIssue,
    EmployeePayment,
    EmployeePortalAccess,
    EmployeeWageRate,
    Holiday,
    HolidayList,
    HRSettings,
    IncomeTaxRateSet,
    IncomeTaxSlab,
    IncomeTaxSurcharge,
    PayrollEntry,
    PayrollEntryEmployee,
    SalaryComponent,
    SalarySlip,
    SalarySlipDetail,
    LeaveType,
    LeaveAllocation,
    LeaveApplication,
)
from hr.services.tax_rates import check_slab_rows

_RO = TimeStampedAdminMixin.readonly_timestamp_fields


@admin.register(Department, Designation, Branch)
class SimpleMasterAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)
    readonly_fields = _RO


class HolidayInline(admin.TabularInline):
    model = Holiday
    extra = 0


@admin.register(HolidayList)
class HolidayListAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "from_date", "to_date")
    search_fields = ("name",)
    inlines = (HolidayInline,)
    readonly_fields = _RO


@admin.register(Employee)
class EmployeeAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "employee_name",
        "company",
        "department",
        "status",
        "employment_type",
        "contract_end_date",
        "pay_type",
    )
    list_filter = (
        "company",
        "status",
        "employment_type",
        "pay_type",
        "department",
        "branch",
    )
    search_fields = ("name", "employee_name", "mobile_no")
    readonly_fields = ("employee_name", *_RO)


@admin.register(EmployeeWageRate)
class EmployeeWageRateAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "employee",
        "from_date",
        "daily_rate",
        "monthly_salary",
        "ot_hourly_rate",
    )
    search_fields = ("employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)
    readonly_fields = _RO


@admin.register(Attendance)
class AttendanceAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("employee", "date", "status", "ot_hours")
    list_filter = ("status",)
    date_hierarchy = "date"
    search_fields = ("employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)
    readonly_fields = _RO


@admin.register(SalaryComponent)
class SalaryComponentAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "component_type", "account", "is_system")
    list_filter = ("company", "component_type", "is_system")
    search_fields = ("name",)
    readonly_fields = ("is_system", "system_key", *_RO)


@admin.register(HRSettings)
class HRSettingsAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("company", "wage_expense_account", "payroll_payable_account")
    readonly_fields = _RO


class IncomeTaxSlabFormSet(BaseInlineFormSet):
    """Refuses a slab table with a gap, an overlap, or a missing zero start."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = [
            (form.cleaned_data["lower_limit"], form.cleaned_data.get("upper_limit"))
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        check_slab_rows(rows)


class IncomeTaxSlabInline(admin.TabularInline):
    model = IncomeTaxSlab
    formset = IncomeTaxSlabFormSet
    extra = 0


class IncomeTaxSurchargeInline(admin.TabularInline):
    model = IncomeTaxSurcharge
    extra = 0


@admin.register(IncomeTaxRateSet)
class IncomeTaxRateSetAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    """The versioned income-tax rates the monthly TDS is worked out from.  A new
    year is a new row with a later effective date; old rows stay for history."""

    list_display = ("label", "regime", "effective_from")
    list_filter = ("regime",)
    inlines = (IncomeTaxSlabInline, IncomeTaxSurchargeInline)
    readonly_fields = _RO


class SalarySlipDetailInline(admin.TabularInline):
    model = SalarySlipDetail
    extra = 0
    can_delete = False
    readonly_fields = ("component", "amount", "is_manual", "advance", "remarks")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SalarySlip)
class SalarySlipAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "employee",
        "start_date",
        "end_date",
        "net_pay",
        "docstatus",
    )
    list_filter = ("company", "docstatus")
    search_fields = ("name", "employee__name", "employee__employee_name")
    raw_id_fields = ("employee", "payroll_entry")
    inlines = (SalarySlipDetailInline,)
    readonly_fields = (
        *SubmittableDocumentAdmin.readonly_fields,
        "payment_days",
        "ot_hours",
        "gross_pay",
        "total_deduction",
        "net_pay",
    )


class PayrollEntryEmployeeInline(admin.TabularInline):
    model = PayrollEntryEmployee
    extra = 0
    raw_id_fields = ("employee", "salary_slip")


@admin.register(PayrollEntry)
class PayrollEntryAdmin(SubmittableDocumentAdmin):
    list_display = ("name", "company", "start_date", "end_date", "docstatus")
    list_filter = ("company", "docstatus")
    inlines = (PayrollEntryEmployeeInline,)


@admin.register(EmployeeAdvance)
class EmployeeAdvanceAdmin(SubmittableDocumentAdmin):
    list_display = ("name", "employee", "posting_date", "amount", "docstatus")
    list_filter = ("company", "docstatus")
    search_fields = ("name", "employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)


@admin.register(EmployeePayment)
class EmployeePaymentAdmin(SubmittableDocumentAdmin):
    list_display = ("name", "employee", "posting_date", "amount", "mode", "docstatus")
    list_filter = ("company", "docstatus", "mode")
    search_fields = (
        "name",
        "employee__name",
        "employee__employee_name",
        "reference_no",
    )
    raw_id_fields = ("employee", "salary_slip")


@admin.register(EmployeePortalAccess)
class EmployeePortalAccessAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    """Read-only: PINs are set through the API (hashed, audited), never here."""

    list_display = ("employee", "portal_enabled", "pin_must_change", "last_login_at")
    list_filter = ("portal_enabled",)
    search_fields = ("employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)
    exclude = ("pin_hash",)
    readonly_fields = (
        *_RO,
        "employee",
        "portal_enabled",
        "pin_must_change",
        "pin_set_at",
        "pin_set_by",
        "failed_attempts",
        "locked_until",
        "last_login_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdvanceRequest)
class AdvanceRequestAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("id", "employee", "amount", "status", "created_at")
    list_filter = ("company", "status")
    search_fields = ("employee__name", "employee__employee_name")
    raw_id_fields = ("employee", "advance")
    readonly_fields = _RO


@admin.register(EmployeeIssue)
class EmployeeIssueAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("id", "employee", "category", "status", "created_at")
    list_filter = ("company", "category", "status")
    search_fields = ("employee__name", "employee__employee_name", "message")
    raw_id_fields = ("employee",)
    readonly_fields = _RO


@admin.register(LeaveType)
class LeaveTypeAdmin(TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "max_days_allowed",
        "is_carry_forward",
        "is_active",
    )
    list_filter = ("company", "is_active", "is_carry_forward")
    search_fields = ("name",)
    readonly_fields = _RO


@admin.register(LeaveAllocation)
class LeaveAllocationAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "employee",
        "leave_type",
        "from_date",
        "to_date",
        "total_leaves_allocated",
        "docstatus",
    )
    list_filter = ("company", "docstatus", "leave_type")
    search_fields = ("name", "employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)
    readonly_fields = _RO + ("docstatus", "amended_from")


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(SubmittableDocumentAdmin):
    list_display = (
        "name",
        "employee",
        "leave_type",
        "posting_date",
        "from_date",
        "to_date",
        "total_leave_days",
        "docstatus",
    )
    list_filter = ("company", "docstatus", "leave_type")
    search_fields = ("name", "employee__name", "employee__employee_name")
    raw_id_fields = ("employee",)
    readonly_fields = _RO + ("docstatus", "amended_from")
