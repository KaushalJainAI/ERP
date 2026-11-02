"""Employee self-service portal API (``/api/hr/portal/...``).

Read-mostly, **own data only**: every queryset is filtered to
``request.portal_employee`` (set by :class:`hr.permissions.IsPortalEmployee`) and
no id / employee parameter is ever honoured, so there is nothing to tamper with
(no IDOR).  These views use ``IsPortalEmployee`` *instead of* ``HasRolePermission``.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q, Sum
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.models import DocStatus
from hr.models import (
    AdvanceRequest,
    Attendance,
    EmployeeAdvance,
    EmployeeIssue,
    EmployeePayment,
    SalarySlip,
)
from hr.permissions import IsPortalEmployee
from hr.portal_serializers import (
    PortalAdvanceRequestCreateSerializer,
    PortalAdvanceRequestSerializer,
    PortalAdvanceSerializer,
    PortalAttendanceSerializer,
    PortalChangePinSerializer,
    PortalIssueCreateSerializer,
    PortalIssueSerializer,
    PortalLoginSerializer,
    PortalPaymentSerializer,
    PortalSlipDetailSerializer,
    PortalSlipSerializer,
)
from hr.services import portal as portal_service
from hr.services import portal_requests
from hr.services.summary import employee_summary
from hr.services.wages import wage_rate_for
from hr.views import _date_param, _jsonable

#: Longest attendance window one call may return.
MAX_ATTENDANCE_DAYS = 366
#: Lists are newest-first and capped, so a call is always cheap.
LIST_LIMIT = 200

GENERIC_LOGIN_ERROR = _("Invalid mobile number or PIN.")
LOCKED_MESSAGE = _("Too many failed attempts. Please try again later.")


def _validation_response(exc):
    """A Django ``ValidationError`` as a 400 in the API's usual shape."""
    if hasattr(exc, "message_dict"):
        body = {k: [str(m) for m in v] for k, v in exc.message_dict.items()}
    else:
        body = {"error": [str(m) for m in exc.messages]}
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def _rate(value):
    """A wage rate as a string: 2dp, more only when the rate really has them."""
    if value is None:
        return None
    cents = value.quantize(Decimal("0.01"))
    return str(cents if cents == value else value.normalize())


def _month_range(request):
    today = datetime.date.today()
    from_date = _date_param(request, "from", default=today.replace(day=1))
    to_date = _date_param(request, "to", default=today)
    if to_date < from_date:
        raise DRFValidationError({"to": _("'to' cannot be before 'from'.")})
    return from_date, to_date


class PortalView(APIView):
    """Base of every authenticated portal view."""

    permission_classes = [IsPortalEmployee]

    @property
    def employee(self):
        return self.request.portal_employee


# ------------------------------------------------------------------ auth
class PortalLoginView(APIView):
    """POST ``/api/hr/portal/login/`` -- mobile number + PIN -> JWT pair.

    Public and throttled per IP; no authentication class runs (a stale token in
    the header must not turn a login into a 401).  Every failure carries the same
    message, so the response never reveals whether a number is registered.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portal_login"

    def post(self, request):
        ser = PortalLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        outcome, employee = portal_service.authenticate(
            ser.validated_data["mobile_no"], ser.validated_data["pin"]
        )
        if outcome == portal_service.LOCKED:
            return Response(
                {"detail": LOCKED_MESSAGE, "code": "locked"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if outcome != portal_service.OK:
            return Response(
                {"detail": GENERIC_LOGIN_ERROR, "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        tokens = portal_service.issue_tokens(employee.user)
        return Response(
            {
                **tokens,
                "pin_must_change": employee.portal_access.pin_must_change,
                "employee": {
                    "name": employee.name,
                    "employee_name": employee.employee_name,
                    "company": employee.company.name,
                },
            }
        )


class PortalChangePinView(PortalView):
    """POST ``/api/hr/portal/change-pin/`` ``{current_pin, new_pin}``."""

    allow_pin_change_pending = True
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portal_login"

    def post(self, request):
        ser = PortalChangePinSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            outcome = portal_service.change_pin(
                self.employee,
                request.portal_access,
                ser.validated_data["current_pin"],
                ser.validated_data["new_pin"],
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        if outcome == portal_service.LOCKED:
            return Response(
                {"detail": LOCKED_MESSAGE, "code": "locked"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if outcome != portal_service.OK:
            return Response(
                {"current_pin": [_("The current PIN is incorrect.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"detail": _("PIN changed."), "pin_must_change": False})


# --------------------------------------------------------------- read-only
class PortalMeView(PortalView):
    """GET ``/api/hr/portal/me/`` -- the employee's own profile (no PII)."""

    allow_pin_change_pending = True

    def get(self, request):
        emp = self.employee
        rate = wage_rate_for(emp, datetime.date.today())
        return Response(
            {
                "name": emp.name,
                "employee_name": emp.employee_name,
                "mobile_no": emp.mobile_no,
                "department": emp.department.name if emp.department_id else None,
                "designation": emp.designation.name if emp.designation_id else None,
                "branch": emp.branch.name if emp.branch_id else None,
                "date_of_joining": emp.date_of_joining,
                "pay_type": emp.pay_type,
                "current_daily_rate": _rate(rate.daily_rate) if rate else None,
                "current_monthly_salary": (
                    _rate(rate.monthly_salary) if rate else None
                ),
                "current_ot_hourly_rate": _rate(rate.ot_hourly_rate) if rate else None,
                "pin_must_change": request.portal_access.pin_must_change,
            }
        )


class PortalSummaryView(PortalView):
    """GET ``/api/hr/portal/summary/?from=&to=`` -- same shape as
    ``/api/hr/employees/{id}/summary/`` (default: current month)."""

    def get(self, request):
        from_date, to_date = _month_range(request)
        return Response(_jsonable(employee_summary(self.employee, from_date, to_date)))


class PortalAttendanceView(PortalView):
    """GET ``/api/hr/portal/attendance/?from=&to=`` -- default current month."""

    def get(self, request):
        from_date, to_date = _month_range(request)
        if (to_date - from_date).days >= MAX_ATTENDANCE_DAYS:
            raise DRFValidationError({"to": _("The range is too long.")})
        rows = Attendance.objects.filter(
            employee=self.employee, date__gte=from_date, date__lte=to_date
        ).order_by("date")
        return Response(PortalAttendanceSerializer(rows, many=True).data)


class _SubmittedSlips:
    def slips(self):
        return SalarySlip.objects.filter(
            employee=self.employee, docstatus=DocStatus.SUBMITTED
        )


class PortalSalarySlipListView(_SubmittedSlips, PortalView):
    """GET ``/api/hr/portal/salary-slips/`` -- own submitted slips, newest first."""

    def get(self, request):
        rows = self.slips().order_by("-posting_date", "-id")[:LIST_LIMIT]
        return Response(PortalSlipSerializer(rows, many=True).data)


class PortalSalarySlipDetailView(_SubmittedSlips, PortalView):
    """GET ``/api/hr/portal/salary-slips/{name}/`` -- with the component rows."""

    def get(self, request, name):
        slip = (
            self.slips()
            .prefetch_related("details__component")
            .filter(name=name)
            .first()
        )
        if slip is None:
            raise NotFound()
        return Response(PortalSlipDetailSerializer(slip).data)


class PortalPaymentListView(PortalView):
    """GET ``/api/hr/portal/payments/`` -- own submitted wage payments."""

    def get(self, request):
        rows = EmployeePayment.objects.filter(
            employee=self.employee, docstatus=DocStatus.SUBMITTED
        ).order_by("-posting_date", "-id")[:LIST_LIMIT]
        return Response(PortalPaymentSerializer(rows, many=True).data)


class PortalAdvanceListView(PortalView):
    """GET ``/api/hr/portal/advances/`` -- own submitted advances with what is
    still to be recovered (derived from the submitted slips, never stored)."""

    def get(self, request):
        rows = (
            EmployeeAdvance.objects.filter(
                employee=self.employee, docstatus=DocStatus.SUBMITTED
            )
            .annotate(
                recovered=Sum(
                    "recoveries__amount",
                    filter=Q(recoveries__slip__docstatus=DocStatus.SUBMITTED),
                    default=Decimal("0.00"),
                )
            )
            .order_by("-posting_date", "-id")[:LIST_LIMIT]
        )
        return Response(PortalAdvanceSerializer(rows, many=True).data)


# ------------------------------------------------------- requests and issues
class PortalAdvanceRequestListCreateView(PortalView):
    """GET / POST ``/api/hr/portal/advance-requests/``."""

    def get(self, request):
        rows = (
            AdvanceRequest.objects.filter(employee=self.employee)
            .select_related("advance")
            .order_by("-created_at", "-id")[:LIST_LIMIT]
        )
        return Response(PortalAdvanceRequestSerializer(rows, many=True).data)

    def post(self, request):
        ser = PortalAdvanceRequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            req = portal_requests.create_advance_request(
                self.employee,
                ser.validated_data["amount"],
                ser.validated_data["reason"].strip(),
                request.user,
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(
            PortalAdvanceRequestSerializer(req).data, status=status.HTTP_201_CREATED
        )


class PortalAdvanceRequestWithdrawView(PortalView):
    """POST ``/api/hr/portal/advance-requests/{id}/withdraw/`` -- Pending only."""

    def post(self, request, pk):
        req = AdvanceRequest.objects.filter(employee=self.employee, pk=pk).first()
        if req is None:
            raise NotFound()
        try:
            req = portal_requests.withdraw_advance_request(req, request.user)
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(PortalAdvanceRequestSerializer(req).data)


class PortalIssueListCreateView(PortalView):
    """GET / POST ``/api/hr/portal/issues/``."""

    def get(self, request):
        rows = EmployeeIssue.objects.filter(employee=self.employee).order_by(
            "-created_at", "-id"
        )[:LIST_LIMIT]
        return Response(PortalIssueSerializer(rows, many=True).data)

    def post(self, request):
        ser = PortalIssueCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            issue = portal_requests.create_issue(
                self.employee,
                data["category"],
                data["message"],
                request.user,
                reference_date=data.get("reference_date"),
                reference_doc=(data.get("reference_doc") or "").strip() or None,
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(
            PortalIssueSerializer(issue).data, status=status.HTTP_201_CREATED
        )
