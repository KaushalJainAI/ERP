import { CalendarCheck, FileUp, Landmark, Scale, CheckCheck } from "lucide-react";
import type { LinkDef, NavLink, ResourceConfig } from "@/types";
import { companyField, docNoField } from "./selling";

/** Bank GL accounts of the document's company. */
const bankGlLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  display: (v) => v.account_name,
  filters: (_row, form) => ({ company: form.company, account_type: "Bank", is_group: false, is_active: true }),
};

const fiscalYearLink: LinkDef = {
  endpoint: "/accounts/fiscal-years/",
  labelField: "name",
  subLabel: (r) => `${r.start_date} - ${r.end_date}`,
  display: (v) => v.fiscal_year_name,
  // Picking a year proposes its last day as the period end.
  fill: (r) => ({ period_end_date: r.end_date }),
};

const closingHeadLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.root_type,
  display: (v) => v.closing_account_head_name,
  filters: (_row, form) => ({ company: form.company, is_group: false, is_active: true }),
};

const bankAccounts: ResourceConfig = {
  key: "bank-accounts",
  module: "accounts",
  endpoint: "/accounts/bank-accounts/",
  titleKey: "bank.res.bank_accounts",
  singularKey: "bank.res.bank_account",
  icon: Landmark,
  ordering: "bank_name",
  titleField: "bank_name",
  columns: [
    { key: "bank_name", labelKey: "bank.fields.bank_name" },
    { key: "account_name", labelKey: "bank.fields.account" },
    { key: "account_number_last4", labelKey: "bank.fields.last4" },
    { key: "ifsc_code", labelKey: "bank.fields.ifsc" },
    { key: "is_default", labelKey: "bank.fields.is_default", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    companyField,
    { key: "account", labelKey: "bank.fields.account", type: "link", required: true, link: bankGlLink },
    { key: "bank_name", labelKey: "bank.fields.bank_name", type: "text", required: true },
    { key: "account_number", labelKey: "bank.fields.account_number", type: "text", helpKey: "bank.help.account_number" },
    { key: "ifsc_code", labelKey: "bank.fields.ifsc", type: "text" },
    { key: "reconciled_up_to", labelKey: "bank.fields.reconciled_up_to", type: "date", helpKey: "bank.help.reconciled_up_to" },
    { key: "is_default", labelKey: "bank.fields.is_default", type: "boolean", defaultValue: false },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

const periodClosingVouchers: ResourceConfig = {
  key: "period-closing-vouchers",
  module: "accounts",
  endpoint: "/accounts/period-closing-vouchers/",
  titleKey: "bank.res.period_closings",
  singularKey: "bank.res.period_closing",
  icon: CalendarCheck,
  submittable: true,
  ordering: "-period_end_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "fiscal_year_name", labelKey: "bank.fields.fiscal_year" },
    { key: "period_end_date", labelKey: "bank.fields.period_end", type: "date" },
    { key: "closing_account_head_name", labelKey: "bank.fields.closing_head" },
    { key: "net_profit", labelKey: "bank.fields.net_profit", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["closing_account_head"] },
    { key: "fiscal_year", labelKey: "bank.fields.fiscal_year", type: "link", required: true, readOnlyAfterCreate: true, link: fiscalYearLink },
    { key: "period_end_date", labelKey: "bank.fields.period_end", type: "date", helpKey: "bank.help.period_end" },
    { key: "closing_account_head", labelKey: "bank.fields.closing_head", type: "link", link: closingHeadLink, helpKey: "bank.help.closing_head" },
    { key: "remarks", labelKey: "bank.fields.remarks", type: "textarea" },
    {
      key: "net_profit",
      labelKey: "bank.fields.net_profit",
      type: "decimal",
      money: true,
      readOnly: true,
      hidden: (_v, isNew) => isNew,
    },
  ],
};

export const bankResources: ResourceConfig[] = [bankAccounts, periodClosingVouchers];

/** Sidebar links to the hand-written bank pages. */
export const bankLinks: NavLink[] = [
  { path: "/accounts/bank-reconciliation", labelKey: "bank.nav.reconcile", icon: CheckCheck },
  { path: "/accounts/bank-import", labelKey: "bank.nav.import", icon: FileUp },
  { path: "/accounts/reports/bank-reconciliation-statement", labelKey: "bank.nav.brs", icon: Scale },
];
