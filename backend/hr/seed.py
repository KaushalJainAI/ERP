"""RBAC seed data for the HR roles (imported by ``seed_defaults``)."""

READ = ("can_read",)
READ_CREATE_WRITE_DELETE = ("can_read", "can_create", "can_write", "can_delete")
READ_CREATE_WRITE = ("can_read", "can_create", "can_write")
FULL = ("can_read", "can_create", "can_write", "can_submit", "can_cancel", "can_delete")

HR_LABELS = (
    "hr.Department",
    "hr.Designation",
    "hr.Branch",
    "hr.HolidayList",
    "hr.Employee",
    "hr.EmployeeWageRate",
    "hr.Attendance",
    "hr.SalaryComponent",
    "hr.HRSettings",
    "hr.SalarySlip",
    "hr.PayrollEntry",
    "hr.EmployeeAdvance",
    "hr.EmployeePayment",
    "hr.EmployeePortalAccess",
    "hr.AdvanceRequest",
    "hr.EmployeeIssue",
    "hr.LeaveType",
    "hr.LeaveAllocation",
    "hr.LeaveApplication",
)

HR_ROLES = [
    (
        "HR User",
        "Employees, attendance and draft salary slips; cannot submit or see pay rates",
    ),
    (
        "HR Manager",
        "Full permission over HR masters, attendance, wage rates and payroll",
    ),
]

_MASTERS = ("hr.Department", "hr.Designation", "hr.Branch", "hr.HolidayList", "hr.LeaveType")

HR_ROLE_MATRIX = {
    "HR User": {
        **{label: READ for label in _MASTERS},
        "hr.SalaryComponent": READ,
        "hr.Employee": READ_CREATE_WRITE,
        "hr.Attendance": READ_CREATE_WRITE_DELETE,
        "hr.SalarySlip": READ_CREATE_WRITE_DELETE,
        "hr.PayrollEntry": READ_CREATE_WRITE_DELETE,
        "hr.EmployeeAdvance": READ_CREATE_WRITE_DELETE,
        "hr.EmployeePayment": READ,
        "hr.LeaveAllocation": READ,
        "hr.LeaveApplication": READ_CREATE_WRITE_DELETE,
        # Portal: read advance requests, read + resolve issues.  Approving /
        # rejecting requests and managing portal PINs are HR Manager only.
        "hr.AdvanceRequest": READ,
        "hr.EmployeeIssue": ("can_read", "can_write"),
        "core.Company": READ,
    },
    "HR Manager": {
        **{label: FULL for label in HR_LABELS},
        "core.Company": READ,
        "accounts.Account": READ,
    },
}
