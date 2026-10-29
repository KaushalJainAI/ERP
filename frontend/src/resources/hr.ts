import { createElement } from "react";
import {
  Layers,
  Briefcase,
  Building2,
  CalendarCheck,
  CalendarDays,
  ClipboardCheck,
  FileText,
  HandCoins,
  Inbox,
  IndianRupee,
  Network,
  RefreshCw,
  Settings,
  Table2,
  UserRound,
  Wallet,
} from "lucide-react";
import type { LinkDef, NavLink, ResourceConfig, SelectOption } from "@/types";
import { todayISO } from "@/lib/format";
import { companyField } from "./selling";
import { optCell, optKey } from "./hrOptions";
import { EmployeePanels } from "@/components/hr/EmployeePanels";
import { HrSettingsPanel, PayrollEntryPanel, SalarySlipPanel } from "@/components/hr/PayrollPanels";

/* ---- option helpers ---- */

const options = (values: string[]): SelectOption[] => values.map((value) => ({ value, labelKey: optKey(value) }));


const GENDERS = options(["Male", "Female", "Other"]);
const EMPLOYEE_STATUS = options(["Active", "Inactive", "Left"]);
const PAY_TYPES = options(["Daily", "Monthly"]);
const ATTENDANCE_STATUS = options(["Present", "Absent", "Half Day", "Paid Leave"]);
const COMPONENT_TYPES = options(["Earning", "Deduction"]);
const PAYMENT_MODES = options(["Cash", "Bank", "UPI"]);

/* ---- reusable links ---- */

export const employeeLink: LinkDef = {
  endpoint: "/hr/employees/",
  labelField: "name",
  label: (r) => `${r.name} - ${r.employee_name}`,
  subLabel: (r) => [r.designation_name, r.department_name].filter(Boolean).join(" - ") || undefined,
  display: (v) => (v.employee_name ? `${v.employee_name}` : undefined),
  filters: (_row, form) => ({ company: form.company }),
  // Picking an employee on a document also sets the company (and shows the name at once).
  fill: (r) => ({ company: r.company, employee_name: r.employee_name, pay_type: r.pay_type }),
};

/** Employee picker inside a child row (never changes the parent's company). */
const rowEmployeeLink: LinkDef = { ...employeeLink, fill: (r) => ({ employee_name: r.employee_name }) };

/** Employee picker that does not filter by company or status (masters, attendance). */
const anyEmployeeLink: LinkDef = { ...employeeLink, filters: () => ({}) };

const simpleLink = (endpoint: string, displayKey: string): LinkDef => ({
  endpoint,
  labelField: "name",
  display: (v) => v[displayKey] || undefined,
  filters: () => ({ is_active: true }),
});

const departmentLink = simpleLink("/hr/departments/", "department_name");
const designationLink = simpleLink("/hr/designations/", "designation_name");
const branchLink = simpleLink("/hr/branches/", "branch_name");

const holidayListLink: LinkDef = { endpoint: "/hr/holiday-lists/", labelField: "name" };

/** Ledger account of the form's company, optionally of one root type. */
const hrAccountLink = (rootType?: string, displayKey?: string): LinkDef => ({
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.account_type || r.root_type,
  display: displayKey ? (v) => v[displayKey] || undefined : undefined,
  filters: (_row, form) => ({
    company: form.company,
    ...(rootType ? { root_type: rootType } : {}),
    is_group: false,
    is_active: true,
  }),
});

const salaryComponentLink: LinkDef = {
  endpoint: "/hr/salary-components/",
  labelField: "name",
  subLabel: (r) => optCell(r.component_type),
  display: (v) => v.component_name || undefined,
  filters: (_row, form) => ({ company: form.company }),
};

const advanceLink: LinkDef = {
  endpoint: "/hr/employee-advances/",
  labelField: "name",
  subLabel: (r) => `${r.outstanding} / ${r.amount}`,
  display: (v) => v.advance_name || undefined,
  // Only submitted advances of the slip's employee can be recovered.
  filters: (_row, form) => ({ employee: form.employee, company: form.company, docstatus: 1 }),
};

const dateToday = () => todayISO();

/* ---- masters ---- */

const nameActiveMaster = (
  key: string,
  endpoint: string,
  titleKey: string,
  singularKey: string,
  icon: ResourceConfig["icon"]
): ResourceConfig => ({
  key,
  module: "hr",
  endpoint,
  titleKey,
  singularKey,
  icon,
  ordering: "name",
  columns: [
    { key: "name", labelKey: "hrm.fields.name" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "hrm.fields.name", type: "text", required: true },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
});

const departments = nameActiveMaster("departments", "/hr/departments/", "hrm.res.departments", "hrm.res.department", Building2);
const designations = nameActiveMaster("designations", "/hr/designations/", "hrm.res.designations", "hrm.res.designation", Briefcase);
const branches = nameActiveMaster("branches", "/hr/branches/", "hrm.res.branches", "hrm.res.branch", Network);

const holidayLists: ResourceConfig = {
  key: "holiday-lists",
  module: "hr",
  endpoint: "/hr/holiday-lists/",
  titleKey: "hrm.res.holiday_lists",
  singularKey: "hrm.res.holiday_list",
  icon: CalendarDays,
  ordering: "name",
  columns: [
    { key: "name", labelKey: "hrm.fields.name" },
    { key: "from_date", labelKey: "hrm.fields.from_date", type: "date" },
    { key: "to_date", labelKey: "hrm.fields.to_date", type: "date" },
  ],
  fields: [
    { key: "name", labelKey: "hrm.fields.name", type: "text", required: true },
    { key: "from_date", labelKey: "hrm.fields.from_date", type: "date", required: true },
    { key: "to_date", labelKey: "hrm.fields.to_date", type: "date", required: true },
  ],
  childTables: [
    {
      key: "holidays",
      titleKey: "hrm.res.holidays",
      newRow: () => ({ weekly_off: false }),
      columns: [
        { key: "date", labelKey: "hrm.fields.holiday_date", type: "date", required: true, widthClass: "w-44" },
        { key: "description", labelKey: "hrm.fields.description", type: "text", widthClass: "min-w-[14rem]" },
        { key: "weekly_off", labelKey: "hrm.fields.weekly_off", type: "boolean", widthClass: "w-28" },
      ],
    },
  ],
};

/* ---- employees ---- */

const employees: ResourceConfig = {
  key: "employees",
  module: "hr",
  endpoint: "/hr/employees/",
  titleKey: "hrm.res.employees",
  singularKey: "hrm.res.employee",
  icon: UserRound,
  titleField: "employee_name",
  ordering: "employee_name",
  columns: [
    { key: "name", labelKey: "hrm.fields.employee" },
    { key: "employee_name", labelKey: "hrm.fields.employee_name" },
    { key: "mobile_no", labelKey: "hrm.fields.mobile_no" },
    { key: "department_name", labelKey: "hrm.fields.department" },
    { key: "designation_name", labelKey: "hrm.fields.designation" },
    { key: "pay_type", labelKey: "hrm.fields.pay_type", render: optCell },
    { key: "status", labelKey: "hrm.fields.status", render: optCell },
    { key: "date_of_joining", labelKey: "hrm.fields.date_of_joining", type: "date" },
  ],
  fields: [
    { key: "name", labelKey: "hrm.fields.employee", type: "text", readOnly: true, hidden: (_v, isNew) => isNew },
    { ...companyField },
    { key: "first_name", labelKey: "hrm.fields.first_name", type: "text", required: true },
    { key: "last_name", labelKey: "hrm.fields.last_name", type: "text" },
    { key: "gender", labelKey: "hrm.fields.gender", type: "select", options: GENDERS, required: true, defaultValue: "Male" },
    { key: "date_of_birth", labelKey: "hrm.fields.date_of_birth", type: "date" },
    { key: "mobile_no", labelKey: "hrm.fields.mobile_no", type: "text", helpKey: "hrm.help.mobile" },
    { key: "department", labelKey: "hrm.fields.department", type: "link", link: departmentLink },
    { key: "designation", labelKey: "hrm.fields.designation", type: "link", link: designationLink },
    { key: "branch", labelKey: "hrm.fields.branch", type: "link", link: branchLink },
    { key: "holiday_list", labelKey: "hrm.fields.holiday_list", type: "link", link: holidayListLink },
    { key: "status", labelKey: "hrm.fields.status", type: "select", options: EMPLOYEE_STATUS, defaultValue: "Active" },
    { key: "date_of_joining", labelKey: "hrm.fields.date_of_joining", type: "date", required: true, defaultValue: dateToday },
    { key: "relieving_date", labelKey: "hrm.fields.relieving_date", type: "date" },
    { key: "pay_type", labelKey: "hrm.fields.pay_type", type: "select", options: PAY_TYPES, defaultValue: "Daily" },
    {
      key: "standard_hours_per_day",
      labelKey: "hrm.fields.standard_hours",
      type: "decimal",
      required: true,
      defaultValue: "8",
      helpKey: "hrm.help.standard_hours",
    },
    { key: "bank_name", labelKey: "hrm.fields.bank_name", type: "text" },
    { key: "bank_account_no", labelKey: "hrm.fields.bank_account_no", type: "text" },
    { key: "ifsc_code", labelKey: "hrm.fields.ifsc_code", type: "text" },
    { key: "pan", labelKey: "hrm.fields.pan", type: "text" },
    { key: "aadhaar", labelKey: "hrm.fields.aadhaar", type: "text", helpKey: "hrm.help.aadhaar" },
    { key: "uan", labelKey: "hrm.fields.uan", type: "text" },
    { key: "esic_no", labelKey: "hrm.fields.esic_no", type: "text" },
  ],
  renderSummary: ({ values, dirty }) => createElement(EmployeePanels, { employee: values, dirty }),
};

const wageRates: ResourceConfig = {
  key: "wage-rates",
  module: "hr",
  endpoint: "/hr/wage-rates/",
  titleKey: "hrm.res.wage_rates",
  singularKey: "hrm.res.wage_rate",
  icon: IndianRupee,
  titleField: "employee_name",
  ordering: "-from_date",
  columns: [
    { key: "employee_name", labelKey: "hrm.fields.employee" },
    { key: "from_date", labelKey: "hrm.fields.from_date", type: "date" },
    { key: "daily_rate", labelKey: "hrm.fields.daily_rate", type: "money" },
    { key: "monthly_salary", labelKey: "hrm.fields.monthly_salary", type: "money" },
    { key: "ot_hourly_rate", labelKey: "hrm.fields.ot_hourly_rate", type: "money" },
  ],
  fields: [
    { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, readOnlyAfterCreate: true, link: anyEmployeeLink },
    { key: "from_date", labelKey: "hrm.fields.from_date", type: "date", required: true, defaultValue: dateToday },
    { key: "daily_rate", labelKey: "hrm.fields.daily_rate", type: "decimal", money: true, hidden: (v) => v.pay_type === "Monthly" },
    { key: "monthly_salary", labelKey: "hrm.fields.monthly_salary", type: "decimal", money: true, hidden: (v) => v.pay_type === "Daily" },
    { key: "ot_hourly_rate", labelKey: "hrm.fields.ot_hourly_rate", type: "decimal", money: true, required: true, defaultValue: "0" },
  ],
};

/* ---- attendance ---- */

const attendance: ResourceConfig = {
  key: "attendance",
  module: "hr",
  endpoint: "/hr/attendance/",
  titleKey: "hrm.res.attendance",
  singularKey: "hrm.res.attendance_one",
  icon: CalendarCheck,
  titleField: "employee_name",
  ordering: "-date",
  columns: [
    { key: "date", labelKey: "hrm.fields.date", type: "date" },
    { key: "employee_name", labelKey: "hrm.fields.employee" },
    { key: "status", labelKey: "hrm.fields.attendance_status", render: optCell },
    { key: "ot_hours", labelKey: "hrm.fields.ot_hours", type: "number" },
    { key: "remarks", labelKey: "hrm.fields.remarks" },
  ],
  fields: [
    { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, readOnlyAfterCreate: true, link: anyEmployeeLink },
    { key: "date", labelKey: "hrm.fields.date", type: "date", required: true, defaultValue: dateToday },
    { key: "status", labelKey: "hrm.fields.attendance_status", type: "select", options: ATTENDANCE_STATUS, required: true, defaultValue: "Present" },
    { key: "ot_hours", labelKey: "hrm.fields.ot_hours", type: "decimal", required: true, defaultValue: "0" },
    { key: "remarks", labelKey: "hrm.fields.remarks", type: "text", span: 2 },
  ],
};

/* ---- payroll setup ---- */

const salaryComponents: ResourceConfig = {
  key: "salary-components",
  module: "hr",
  endpoint: "/hr/salary-components/",
  titleKey: "hrm.res.salary_components",
  singularKey: "hrm.res.salary_component",
  icon: Layers,
  ordering: "name",
  columns: [
    { key: "name", labelKey: "hrm.fields.name" },
    { key: "component_type", labelKey: "hrm.fields.component_type", render: optCell },
    { key: "account_name", labelKey: "hrm.fields.account" },
    { key: "is_system", labelKey: "hrm.fields.is_system", type: "boolean" },
  ],
  fields: [
    { ...companyField },
    { key: "name", labelKey: "hrm.fields.name", type: "text", required: true },
    { key: "component_type", labelKey: "hrm.fields.component_type", type: "select", options: COMPONENT_TYPES, required: true, defaultValue: "Earning" },
    { key: "account", labelKey: "hrm.fields.account", type: "link", required: true, link: hrAccountLink(undefined, "account_name") },
  ],
};

const hrSettings: ResourceConfig = {
  key: "settings",
  module: "hr",
  endpoint: "/hr/settings/",
  titleKey: "hrm.res.settings",
  singularKey: "hrm.res.settings_one",
  icon: Settings,
  titleField: "id",
  ordering: "id",
  noDelete: true,
  columns: [{ key: "company", labelKey: "fields.company" }],
  fields: [
    { ...companyField },
    { key: "wage_expense_account", labelKey: "hrm.fields.wage_expense_account", type: "link", required: true, link: hrAccountLink("Expense") },
    { key: "payroll_payable_account", labelKey: "hrm.fields.payroll_payable_account", type: "link", required: true, link: hrAccountLink("Liability") },
    { key: "employee_advance_account", labelKey: "hrm.fields.employee_advance_account", type: "link", required: true, link: hrAccountLink("Asset") },
    { key: "default_payment_account", labelKey: "hrm.fields.default_payment_account", type: "link", required: true, link: hrAccountLink("Asset") },
  ],
  renderSummary: ({ values }) => createElement(HrSettingsPanel, { values }),
};

/* ---- documents ---- */

const salarySlips: ResourceConfig = {
  key: "salary-slips",
  module: "hr",
  endpoint: "/hr/salary-slips/",
  titleKey: "hrm.res.salary_slips",
  singularKey: "hrm.res.salary_slip",
  icon: FileText,
  submittable: true,
  titleField: "name",
  ordering: "-posting_date",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "employee_name", labelKey: "hrm.fields.employee" },
    { key: "start_date", labelKey: "hrm.fields.start_date", type: "date" },
    { key: "end_date", labelKey: "hrm.fields.end_date", type: "date" },
    { key: "gross_pay", labelKey: "hrm.fields.gross_pay", type: "money" },
    { key: "total_deduction", labelKey: "hrm.fields.total_deduction", type: "money" },
    { key: "net_pay", labelKey: "hrm.fields.net_pay", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "fields.docno_help" },
    { ...companyField, clears: ["employee"] },
    { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, readOnlyAfterCreate: true, link: employeeLink },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: dateToday },
    { key: "start_date", labelKey: "hrm.fields.start_date", type: "date", required: true },
    { key: "end_date", labelKey: "hrm.fields.end_date", type: "date", required: true },
    {
      key: "propose_recovery",
      labelKey: "hrm.fields.propose_recovery",
      type: "boolean",
      defaultValue: true,
      hidden: (_v, isNew) => !isNew,
      helpKey: "hrm.help.propose_recovery",
    },
    { key: "remarks", labelKey: "hrm.fields.remarks", type: "textarea" },
    { key: "payment_days", labelKey: "hrm.fields.payment_days", type: "number", readOnly: true, hidden: (_v, isNew) => isNew },
    { key: "ot_hours", labelKey: "hrm.fields.ot_hours", type: "number", readOnly: true, hidden: (_v, isNew) => isNew },
    { key: "gross_pay", labelKey: "hrm.fields.gross_pay", type: "decimal", money: true, readOnly: true, hidden: (_v, isNew) => isNew },
    { key: "total_deduction", labelKey: "hrm.fields.total_deduction", type: "decimal", money: true, readOnly: true, hidden: (_v, isNew) => isNew },
    { key: "net_pay", labelKey: "hrm.fields.net_pay", type: "decimal", money: true, readOnly: true, hidden: (_v, isNew) => isNew },
  ],
  childTables: [
    {
      key: "details",
      titleKey: "hrm.res.slip_rows",
      newRow: () => ({}),
      columns: [
        { key: "component", labelKey: "hrm.fields.component", type: "link", required: true, link: salaryComponentLink, widthClass: "min-w-[14rem]" },
        { key: "amount", labelKey: "hrm.fields.amount", type: "decimal", money: true, required: true, widthClass: "w-36" },
        { key: "advance", labelKey: "hrm.fields.advance", type: "link", link: advanceLink, widthClass: "min-w-[12rem]" },
        { key: "remarks", labelKey: "hrm.fields.remarks", type: "text", widthClass: "min-w-[12rem]" },
      ],
    },
  ],
  actions: [
    {
      key: "recompute",
      labelKey: "hrm.actions.recompute",
      icon: RefreshCw,
      showWhen: (doc) => doc.docstatus === 0,
      kind: "request",
      url: (doc) => `/hr/salary-slips/${doc.id}/recompute/`,
    },
  ],
  renderSummary: ({ values, dirty }) => createElement(SalarySlipPanel, { slip: values, dirty }),
};

const payrollEntries: ResourceConfig = {
  key: "payroll-entries",
  module: "hr",
  endpoint: "/hr/payroll-entries/",
  titleKey: "hrm.res.payroll_entries",
  singularKey: "hrm.res.payroll_entry",
  icon: ClipboardCheck,
  submittable: true,
  titleField: "name",
  ordering: "-posting_date",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "start_date", labelKey: "hrm.fields.start_date", type: "date" },
    { key: "end_date", labelKey: "hrm.fields.end_date", type: "date" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "fields.docno_help" },
    { ...companyField },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: dateToday },
    { key: "start_date", labelKey: "hrm.fields.start_date", type: "date", required: true },
    { key: "end_date", labelKey: "hrm.fields.end_date", type: "date", required: true },
    { key: "department", labelKey: "hrm.fields.department", type: "link", link: departmentLink },
    { key: "branch", labelKey: "hrm.fields.branch", type: "link", link: branchLink },
  ],
  childTables: [
    {
      key: "employees",
      titleKey: "hrm.res.payroll_employees",
      newRow: () => ({}),
      columns: [
        { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, link: rowEmployeeLink, widthClass: "min-w-[16rem]" },
        { key: "salary_slip_name", labelKey: "hrm.cols.slip_no", type: "text", readOnly: true, widthClass: "w-40" },
      ],
    },
  ],
  renderSummary: ({ values, dirty }) => createElement(PayrollEntryPanel, { entry: values, dirty }),
};

const advances: ResourceConfig = {
  key: "employee-advances",
  module: "hr",
  endpoint: "/hr/employee-advances/",
  titleKey: "hrm.res.advances",
  singularKey: "hrm.res.advance",
  icon: HandCoins,
  submittable: true,
  titleField: "name",
  ordering: "-posting_date",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "employee_name", labelKey: "hrm.fields.employee" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "amount", labelKey: "hrm.fields.amount", type: "money" },
    { key: "outstanding", labelKey: "hrm.fields.outstanding", type: "money" },
    { key: "status", labelKey: "hrm.fields.status", render: optCell },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "fields.docno_help" },
    { ...companyField, clears: ["employee", "paid_from"] },
    { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, link: employeeLink },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: dateToday },
    { key: "amount", labelKey: "hrm.fields.amount", type: "decimal", money: true, required: true },
    { key: "instalment_amount", labelKey: "hrm.fields.instalment", type: "decimal", money: true, helpKey: "hrm.help.instalment" },
    { key: "paid_from", labelKey: "hrm.fields.paid_from", type: "link", required: true, link: hrAccountLink("Asset") },
    { key: "purpose", labelKey: "hrm.fields.purpose", type: "textarea" },
    { key: "recovered_amount", labelKey: "hrm.fields.recovered", type: "decimal", money: true, readOnly: true, hidden: (_v, isNew) => isNew },
    { key: "outstanding", labelKey: "hrm.fields.outstanding", type: "decimal", money: true, readOnly: true, hidden: (_v, isNew) => isNew },
  ],
};

const payments: ResourceConfig = {
  key: "employee-payments",
  module: "hr",
  endpoint: "/hr/employee-payments/",
  titleKey: "hrm.res.payments",
  singularKey: "hrm.res.payment",
  icon: Wallet,
  submittable: true,
  titleField: "name",
  ordering: "-posting_date",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "employee_name", labelKey: "hrm.fields.employee" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "amount", labelKey: "hrm.fields.amount", type: "money" },
    { key: "mode", labelKey: "hrm.fields.mode", render: optCell },
    { key: "reference_no", labelKey: "hrm.fields.reference_no" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "fields.docno_help" },
    { ...companyField, clears: ["employee", "paid_from"] },
    { key: "employee", labelKey: "hrm.fields.employee", type: "link", required: true, link: employeeLink },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: dateToday },
    { key: "amount", labelKey: "hrm.fields.amount", type: "decimal", money: true, required: true },
    { key: "paid_from", labelKey: "hrm.fields.paid_from", type: "link", required: true, link: hrAccountLink("Asset") },
    { key: "mode", labelKey: "hrm.fields.mode", type: "select", options: PAYMENT_MODES, required: true, defaultValue: "Cash" },
    { key: "reference_no", labelKey: "hrm.fields.reference_no", type: "text" },
    { key: "remarks", labelKey: "hrm.fields.remarks", type: "textarea" },
  ],
};

/** Attendance first (most used), then masters, then payroll documents. */
export const hrResources: ResourceConfig[] = [
  employees,
  attendance,
  salarySlips,
  payrollEntries,
  advances,
  payments,
  wageRates,
  departments,
  designations,
  branches,
  holidayLists,
  salaryComponents,
  hrSettings,
];

/** Hand-written pages of the HR sidebar section (shown above the resources). */
export const hrLinks: NavLink[] = [
  { path: "/hr/muster-roll", labelKey: "hrm.res.muster_roll", icon: ClipboardCheck },
  { path: "/hr/muster-report", labelKey: "hrm.res.muster_report", icon: Table2 },
  { path: "/hr/inbox", labelKey: "hrm.res.inbox", icon: Inbox, badge: "hr-inbox" },
];

