from hr.services import leave as leave_service
from hr.services import reports as reports_service
from hr.services import tds_register as tds_register_service
"""HR API: masters, employees, attendance / muster roll, payroll documents."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q, Sum
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from accounts.models import Account
from core.company_context import CompanyScopedMixin, resolve_active_company, active_company_id
from core.mixins import AuditLogMixin, SubmittableViewSetMixin, record_audit_log
from core.models import AuditLog, DocStatus
from hr.models import (
    Attendance,
    Branch,
    Department,
    Designation,
    Employee,
    EmployeeAdvance,
    EmployeePayment,
    EmployeeWageRate,
    HolidayList,
    HRSettings,
    PaymentMode,
    PayrollEntry,
    SalaryComponent,
    SalarySlip,
    LeaveType,
    LeaveAllocation,
    LeaveApplication,
)
from hr.serializers import (
    AttendanceSerializer,
    BranchSerializer,
    DepartmentSerializer,
    DesignationSerializer,
    EmployeeAdvanceSerializer,
    EmployeePaymentSerializer,
    EmployeeSerializer,
    EmployeeWageRateSerializer,
    HolidayListSerializer,
    HRSettingsSerializer,
    MusterUpsertSerializer,
    PayrollEntrySerializer,
    SalaryComponentSerializer,
    SalarySlipSerializer,
    LeaveTypeSerializer,
    LeaveAllocationSerializer,
    LeaveApplicationSerializer,
)
from hr.portal_serializers import SetPortalPinSerializer
from hr.services import attendance as attendance_service
from hr.services import payroll
from hr.services import portal as portal_service
from hr.services.setup import ensure_hr_setup
from hr.services.summary import employee_summary
from users.permissions import HasRolePermission, require_model_permission, user_has_model_permission

PERMS = [permissions.IsAuthenticated, HasRolePermission]


def _jsonable(obj):
    """Decimals as strings (the report convention)."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _date_param(request, name, required=False, default=None):
    raw = request.query_params.get(name)
    if not raw:
        if required:
            raise DRFValidationError({name: _("This query parameter is required.")})
        return default
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise DRFValidationError({name: _("Use YYYY-MM-DD.")})


def _int_param(request, name):
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise DRFValidationError({name: _("Must be an integer.")})


def _require(request, label, flag):
    if not user_has_model_permission(request.user, label, flag):
        raise PermissionDenied(
            _("You do not have %(flag)s permission on %(label)s.")
            % {"flag": flag, "label": label}
        )


def _error_response(exc):
    msg = exc.messages if hasattr(exc, "messages") else str(exc)
    return Response(
        {"error": [str(m) for m in msg]}, status=status.HTTP_400_BAD_REQUEST
    )


class HRMasterViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Base: RBAC + clean errors for a delete that is refused (409) or locked (400)."""

    permission_classes = PERMS

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError):
            return Response(
                {"error": _("This record is in use and cannot be deleted.")},
                status=status.HTTP_409_CONFLICT,
            )
        except DjangoValidationError as exc:
            return _error_response(exc)


class DepartmentViewSet(HRMasterViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class DesignationViewSet(HRMasterViewSet):
    queryset = Designation.objects.all()
    serializer_class = DesignationSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class BranchViewSet(HRMasterViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class HolidayListViewSet(HRMasterViewSet):
    queryset = HolidayList.objects.all().prefetch_related("holidays")
    serializer_class = HolidayListSerializer
    search_fields = ["name"]


class EmployeeViewSet(CompanyScopedMixin, HRMasterViewSet):
    queryset = Employee.objects.all().select_related(
        "department", "designation", "branch", "portal_access"
    )
    serializer_class = EmployeeSerializer
    # The portal actions edit credentials: the class-level check only needs
    # ``can_write`` on the employee; the real gate (HR Manager) is the explicit
    # ``hr.EmployeePortalAccess`` check inside each action.
    action_permission_flags = {"set_portal_pin": "can_write", "disable_portal": "can_write"}
    filterset_fields = [
        "company",
        "department",
        "designation",
        "branch",
        "status",
        "pay_type",
    ]
    search_fields = ["name", "employee_name", "mobile_no"]
    ordering_fields = ["name", "employee_name", "date_of_joining"]

    # The audit trail must never carry the full Aadhaar, whoever the actor is.
    def _audit(self, action_name, serializer):
        instance = serializer.instance
        masked = EmployeeSerializer(
            instance, context={"request": self.request, "mask_pii": True}
        ).data
        record_audit_log(self.request.user, action_name, instance, changes=masked)

    def perform_create(self, serializer):
        serializer.validated_data["company"] = self.active_company
        serializer.save()
        self._audit("create", serializer)

    def perform_update(self, serializer):
        serializer.save()
        self._audit("update", serializer)

    @action(detail=True, methods=["post"], url_path="set-portal-pin")
    def set_portal_pin(self, request, pk=None):
        """Enable the employee portal with a PIN (must be changed at first login).

        Body ``{"pin": "4-6 digits"}``.  HR Manager only; the PIN is never stored
        in clear, logged or returned."""
        _require(request, "hr.EmployeePortalAccess", "can_write")
        employee = self.get_object()
        ser = SetPortalPinSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            access = portal_service.set_portal_pin(
                employee, ser.validated_data["pin"], request.user
            )
        except DjangoValidationError as exc:
            return _error_response(exc)
        return Response(
            {
                "portal_enabled": access.portal_enabled,
                "pin_must_change": access.pin_must_change,
            }
        )

    @action(detail=True, methods=["post"], url_path="disable-portal")
    def disable_portal(self, request, pk=None):
        """Switch the employee portal off (existing tokens stop working at once)."""
        _require(request, "hr.EmployeePortalAccess", "can_write")
        employee = self.get_object()
        portal_service.disable_portal(employee, request.user)
        return Response({"portal_enabled": False})

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        """Attendance, earnings, payments, payable and advance for a period."""
        employee = self.get_object()
        from_date = _date_param(request, "from")
        to_date = _date_param(request, "to")
        if from_date and to_date and to_date < from_date:
            raise DRFValidationError({"to": _("'to' cannot be before 'from'.")})
        return Response(_jsonable(employee_summary(employee, from_date, to_date)))


class EmployeeWageRateViewSet(CompanyScopedMixin, HRMasterViewSet):
    company_lookup = "employee__company"
    queryset = EmployeeWageRate.objects.all().select_related("employee")
    serializer_class = EmployeeWageRateSerializer
    filterset_fields = ["employee"]
    ordering_fields = ["from_date"]


class AttendanceViewSet(CompanyScopedMixin, HRMasterViewSet):
    company_lookup = "employee__company"
    queryset = Attendance.objects.all().select_related("employee")
    serializer_class = AttendanceSerializer
    filterset_fields = {
        "employee": ["exact"],
        "status": ["exact"],
        "date": ["exact", "gte", "lte"],
    }
    search_fields = ["employee__name", "employee__employee_name"]
    ordering_fields = ["date", "employee"]

    @action(detail=False, methods=["get", "post"], url_path="muster-roll")
    def muster_roll(self, request):
        """GET: all active employees with their attendance for ``?date=``.
        POST: upsert many rows for one date, all or nothing."""
        if request.method == "GET":
            date = _date_param(request, "date", required=True)
            rows = attendance_service.muster_roll(
                resolve_active_company(request).pk, date
            )
            return Response(_jsonable({"date": date, "rows": rows}))

        # POST creates and updates rows, so both rights are needed.
        _require(request, "hr.Attendance", "can_write")
        ser = MusterUpsertSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            saved = attendance_service.upsert_muster(
                resolve_active_company(request).pk,
                data["date"],
                data["rows"],
                user=request.user,
            )
        except DjangoValidationError as exc:
            return _error_response(exc)
        AuditLog.objects.create(
            user=request.user,
            action="muster_roll",
            model_label="hr.Attendance",
            object_id=str(data["date"]),
            changes={
                "date": str(data["date"]),
                "employees": [r.employee_id for r in saved],
            },
        )
        return Response(
            AttendanceSerializer(saved, many=True).data, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"], url_path="muster-roll-report")
    def muster_roll_report(self, request):
        """Employee x day grid for ``?from=&to=`` (max 93 days)."""
        from_date = _date_param(request, "from", required=True)
        to_date = _date_param(request, "to", required=True)
        try:
            report = attendance_service.muster_report(
                resolve_active_company(request).pk,
                from_date,
                to_date,
                _int_param(request, "department"),
            )
        except DjangoValidationError as exc:
            return _error_response(exc)
        return Response(_jsonable(report))


class SalaryComponentViewSet(CompanyScopedMixin, HRMasterViewSet):
    queryset = SalaryComponent.objects.all().select_related("account")
    serializer_class = SalaryComponentSerializer
    filterset_fields = ["company", "component_type", "is_system"]
    search_fields = ["name"]

    def destroy(self, request, *args, **kwargs):
        if self.get_object().is_system:
            return Response(
                {"error": _("System components cannot be deleted.")},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class HRSettingsViewSet(CompanyScopedMixin, HRMasterViewSet):
    queryset = HRSettings.objects.all()
    serializer_class = HRSettingsSerializer
    filterset_fields = ["company"]
    # Deleting the settings would orphan every posting.
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    @action(detail=False, methods=["post"], url_path="ensure-setup")
    def ensure_setup(self, request):
        """Create the HR accounts, settings and system components for the active company."""
        company = resolve_active_company(request)
        row = ensure_hr_setup(company)
        return Response(HRSettingsSerializer(row).data, status=status.HTTP_200_OK)


class SalarySlipViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = (
        SalarySlip.objects.all()
        .select_related("employee", "company")
        .prefetch_related("details__component", "details__advance")
    )
    serializer_class = SalarySlipSerializer
    permission_classes = PERMS
    filterset_fields = {
        "employee": ["exact"],
        "company": ["exact"],
        "docstatus": ["exact"],
        "payroll_entry": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
    }
    search_fields = ["name", "employee__name", "employee__employee_name"]
    ordering_fields = ["posting_date", "name", "net_pay"]
    action_permission_flags = {"recompute": "can_write"}

    @action(detail=True, methods=["post"])
    def recompute(self, request, pk=None):
        """Draft only: rebuild Wages / Overtime from attendance and re-propose
        advance recoveries; manual rows are kept."""
        slip = self.get_object()
        try:
            payroll.recompute_slip(slip)
        except DjangoValidationError as exc:
            return _error_response(exc)
        slip = self.get_queryset().get(pk=slip.pk)
        return Response(self.get_serializer(slip).data)


class PayrollEntryViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = (
        PayrollEntry.objects.all()
        .select_related("company")
        .prefetch_related("employees__employee", "employees__salary_slip")
    )
    serializer_class = PayrollEntrySerializer
    permission_classes = PERMS
    filterset_fields = ["company", "docstatus", "department", "branch"]
    search_fields = ["name"]
    ordering_fields = ["posting_date", "name"]
    action_permission_flags = {
        "get_employees": "can_write",
        "create_slips": "can_write",
        "make_payments": "can_submit",
    }

    def _fresh(self, entry):
        return self.get_serializer(self.get_queryset().get(pk=entry.pk)).data

    @action(detail=True, methods=["post"], url_path="get-employees")
    def get_employees(self, request, pk=None):
        """Draft only: add the active employees matching the filters and dates."""
        entry = self.get_object()
        try:
            payroll.get_employees(entry)
        except DjangoValidationError as exc:
            return _error_response(exc)
        return Response(self._fresh(entry))

    @action(detail=True, methods=["post"], url_path="create-slips")
    def create_slips(self, request, pk=None):
        """Draft only: a draft slip per employee row. Employees that would overlap
        a submitted slip (or cannot be computed) are skipped and listed."""
        _require(request, "hr.SalarySlip", "can_create")
        entry = self.get_object()
        try:
            created, skipped = payroll.create_slips(entry, user=request.user)
        except DjangoValidationError as exc:
            return _error_response(exc)
        return Response(
            {
                "created": [s.name for s in created],
                "skipped": skipped,
                "payroll_entry": self._fresh(entry),
            }
        )

    @action(detail=True, methods=["post"], url_path="make-payments")
    def make_payments(self, request, pk=None):
        """Submitted entry: pay every slip's net pay (one payment each), atomically.

        Body: ``{"paid_from": <account id, default the HR default payment
        account>, "mode": "Cash|Bank|UPI", "posting_date": "...", "reference_no": ""}``.
        """
        _require(request, "hr.EmployeePayment", "can_create")
        _require(request, "hr.EmployeePayment", "can_submit")
        entry = self.get_object()
        data = request.data
        from hr.services.setup import get_settings

        try:
            account = None
            if data.get("paid_from"):
                account = Account.objects.filter(pk=data["paid_from"]).first()
                if account is None:
                    raise DRFValidationError({"paid_from": _("Unknown account.")})
            else:
                account = get_settings(entry.company).default_payment_account
            mode = data.get("mode", PaymentMode.CASH)
            if mode not in PaymentMode.values:
                raise DRFValidationError({"mode": _("Invalid payment mode.")})
            try:
                posting_date = (
                    datetime.date.fromisoformat(data["posting_date"])
                    if data.get("posting_date")
                    else datetime.date.today()
                )
            except ValueError:
                raise DRFValidationError({"posting_date": _("Use YYYY-MM-DD.")})
            payments = payroll.make_payments(
                entry,
                account,
                mode,
                posting_date,
                reference_no=str(data.get("reference_no", "")),
                user=request.user,
            )
        except DjangoValidationError as exc:
            return _error_response(exc)
        for payment in payments:
            record_audit_log(request.user, "create", payment)
            record_audit_log(request.user, "submit", payment)
        return Response(
            EmployeePaymentSerializer(payments, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class EmployeeAdvanceViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    serializer_class = EmployeeAdvanceSerializer
    permission_classes = PERMS
    filterset_fields = ["employee", "company", "docstatus"]
    search_fields = ["name", "employee__name", "employee__employee_name"]
    ordering_fields = ["posting_date", "name"]

    def get_queryset(self):
        return (
            EmployeeAdvance.objects.filter(company=self.active_company)
            .select_related("employee")
            .annotate(
                recovered=Sum(
                    "recoveries__amount",
                    filter=Q(recoveries__slip__docstatus=DocStatus.SUBMITTED),
                    default=Decimal("0.00"),
                )
            )
        )


class EmployeePaymentViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = EmployeePayment.objects.all().select_related("employee")
    serializer_class = EmployeePaymentSerializer
    permission_classes = PERMS
    filterset_fields = {
        "employee": ["exact"],
        "company": ["exact"],
        "docstatus": ["exact"],
        "mode": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
    }
    search_fields = [
        "name",
        "employee__name",
        "employee__employee_name",
        "reference_no",
    ]
    ordering_fields = ["posting_date", "name", "amount"]


# --------------------------------------------------------------------- leave
class LeaveTypeViewSet(CompanyScopedMixin, HRMasterViewSet):
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    filterset_fields = ["company", "is_active"]
    search_fields = ["name"]


class LeaveAllocationViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = LeaveAllocation.objects.all().select_related(
        "employee", "leave_type", "company"
    )
    serializer_class = LeaveAllocationSerializer
    permission_classes = PERMS
    filterset_fields = {
        "employee": ["exact"],
        "leave_type": ["exact"],
        "docstatus": ["exact"],
        "company": ["exact"],
    }
    search_fields = ["name", "employee__name", "employee__employee_name"]
    ordering_fields = ["from_date", "name"]


class LeaveApplicationViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = LeaveApplication.objects.all().select_related(
        "employee", "leave_type", "company"
    )
    serializer_class = LeaveApplicationSerializer
    permission_classes = PERMS
    filterset_fields = {
        "employee": ["exact"],
        "leave_type": ["exact"],
        "docstatus": ["exact"],
        "company": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
    }
    search_fields = ["name", "employee__name", "employee__employee_name"]
    ordering_fields = ["posting_date", "from_date", "name"]


# --------------------------------------------------------------------- reports
@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("hr.LeaveApplication")]
)
def leave_balance_report_view(request):
    """Leave balance report endpoint: allocations, leaves taken, and balance."""
    company_id = active_company_id(request)
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")
    employee_id = request.query_params.get("employee")
    department_id = request.query_params.get("department")

    try:
        report = leave_service.leave_balance_report(
            company_id=company_id,
            from_date=from_date or None,
            to_date=to_date or None,
            employee_id=employee_id or None,
            department_id=department_id or None,
        )
    except DjangoValidationError as exc:
        return _error_response(exc)
    return Response(report)


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("hr.SalarySlip")]
)
def salary_register_view(request):
    """Salary register report endpoint."""
    company_id = active_company_id(request)
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")
    department_id = request.query_params.get("department")
    branch_id = request.query_params.get("branch")
    employee_id = request.query_params.get("employee")
    pay_type = request.query_params.get("pay_type")

    if not from_date or not to_date:
        return Response(
            {"detail": _("Both from_date and to_date are required.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        from_d = datetime.date.fromisoformat(from_date)
        to_d = datetime.date.fromisoformat(to_date)
    except ValueError:
        return Response(
            {"detail": _("Invalid date format. Use YYYY-MM-DD.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        report = reports_service.salary_register_report(
            company_id=company_id,
            from_date=from_d,
            to_date=to_d,
            department_id=department_id or None,
            branch_id=branch_id or None,
            employee_id=employee_id or None,
            pay_type=pay_type or None,
        )
    except DjangoValidationError as exc:
        return _error_response(exc)
    return Response(report)


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("hr.SalarySlip")]
)
def wage_register_view(request):
    """Wage register report endpoint: muster-roll wage register for daily wagers."""
    company_id = active_company_id(request)
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")
    department_id = request.query_params.get("department")
    branch_id = request.query_params.get("branch")
    employee_id = request.query_params.get("employee")

    if not from_date or not to_date:
        return Response(
            {"detail": _("Both from_date and to_date are required.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        from_d = datetime.date.fromisoformat(from_date)
        to_d = datetime.date.fromisoformat(to_date)
    except ValueError:
        return Response(
            {"detail": _("Invalid date format. Use YYYY-MM-DD.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        report = reports_service.wage_register_report(
            company_id=company_id,
            from_date=from_d,
            to_date=to_d,
            department_id=department_id or None,
            branch_id=branch_id or None,
            employee_id=employee_id or None,
        )
    except DjangoValidationError as exc:
        return _error_response(exc)
    return Response(report)


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("hr.SalarySlip")]
)
def tds_register_view(request):
    """TDS register for one financial-year quarter, ``?quarter=2026-27-Q1``."""
    company_id = active_company_id(request)
    try:
        report = tds_register_service.tds_register(
            company_id=company_id,
            label=request.query_params.get("quarter", ""),
        )
    except DjangoValidationError as exc:
        return _error_response(exc)
    return Response(report)
