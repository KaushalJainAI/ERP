from django.urls import include, path
from rest_framework.routers import DefaultRouter

from hr.views import (
    AttendanceViewSet,
    BranchViewSet,
    DepartmentViewSet,
    DesignationViewSet,
    EmployeeAdvanceViewSet,
    EmployeePaymentViewSet,
    EmployeeViewSet,
    EmployeeWageRateViewSet,
    HolidayListViewSet,
    HRSettingsViewSet,
    PayrollEntryViewSet,
    SalaryComponentViewSet,
    SalarySlipViewSet,
    LeaveTypeViewSet,
    LeaveAllocationViewSet,
    LeaveApplicationViewSet,
    leave_balance_report_view,
    salary_register_view,
    tds_register_view,
    wage_register_view,
)

from hr.portal_admin_views import (
    AdvanceRequestViewSet,
    EmployeeIssueViewSet,
    PortalOverviewView,
)
from hr.portal_views import (
    PortalAdvanceListView,
    PortalAdvanceRequestListCreateView,
    PortalAdvanceRequestWithdrawView,
    PortalAttendanceView,
    PortalChangePinView,
    PortalIssueListCreateView,
    PortalLoginView,
    PortalMeView,
    PortalPaymentListView,
    PortalSalarySlipDetailView,
    PortalSalarySlipListView,
    PortalSummaryView,
)

app_name = "hr"

router = DefaultRouter()
router.register("departments", DepartmentViewSet, basename="department")
router.register("designations", DesignationViewSet, basename="designation")
router.register("branches", BranchViewSet, basename="branch")
router.register("holiday-lists", HolidayListViewSet, basename="holidaylist")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("wage-rates", EmployeeWageRateViewSet, basename="wagerate")
router.register("attendance", AttendanceViewSet, basename="attendance")
router.register("salary-components", SalaryComponentViewSet, basename="salarycomponent")
router.register("settings", HRSettingsViewSet, basename="hrsettings")
router.register("salary-slips", SalarySlipViewSet, basename="salaryslip")
router.register("payroll-entries", PayrollEntryViewSet, basename="payrollentry")
router.register("employee-advances", EmployeeAdvanceViewSet, basename="employeeadvance")
router.register("employee-payments", EmployeePaymentViewSet, basename="employeepayment")
router.register("advance-requests", AdvanceRequestViewSet, basename="advancerequest")
router.register("employee-issues", EmployeeIssueViewSet, basename="employeeissue")
router.register("leave-types", LeaveTypeViewSet, basename="leavetype")
router.register("leave-allocations", LeaveAllocationViewSet, basename="leaveallocation")
router.register("leave-applications", LeaveApplicationViewSet, basename="leaveapplication")

# Employee self-service portal (own data only; see hr/portal_views.py).
portal_patterns = [
    path("login/", PortalLoginView.as_view(), name="portal-login"),
    path("change-pin/", PortalChangePinView.as_view(), name="portal-change-pin"),
    path("me/", PortalMeView.as_view(), name="portal-me"),
    path("summary/", PortalSummaryView.as_view(), name="portal-summary"),
    path("attendance/", PortalAttendanceView.as_view(), name="portal-attendance"),
    path(
        "salary-slips/",
        PortalSalarySlipListView.as_view(),
        name="portal-salary-slips",
    ),
    path(
        "salary-slips/<str:name>/",
        PortalSalarySlipDetailView.as_view(),
        name="portal-salary-slip",
    ),
    path("payments/", PortalPaymentListView.as_view(), name="portal-payments"),
    path("advances/", PortalAdvanceListView.as_view(), name="portal-advances"),
    path(
        "advance-requests/",
        PortalAdvanceRequestListCreateView.as_view(),
        name="portal-advance-requests",
    ),
    path(
        "advance-requests/<int:pk>/withdraw/",
        PortalAdvanceRequestWithdrawView.as_view(),
        name="portal-advance-request-withdraw",
    ),
    path("issues/", PortalIssueListCreateView.as_view(), name="portal-issues"),
]

urlpatterns = [
    path("portal/", include(portal_patterns)),
    path("portal-overview/", PortalOverviewView.as_view(), name="portal-overview"),
    path("reports/leave-balance/", leave_balance_report_view, name="report-leave-balance"),
    path("reports/salary-register/", salary_register_view, name="report-salary-register"),
    path("reports/wage-register/", wage_register_view, name="report-wage-register"),
    path("reports/tds-register/", tds_register_view, name="report-tds-register"),
    path("", include(router.urls)),
]
