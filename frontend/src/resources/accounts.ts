import { createElement } from "react";
import { BarChart3, BookOpen, FileSpreadsheet, FileText, Landmark, Library, Scale, ScrollText, TrendingUp, Wallet } from "lucide-react";
import i18n from "i18next";
import { apiClient } from "@/api/client";
import { JournalBalanceBanner, negDec, totalsOf } from "@/components/accounts/BalanceBanner";
import { sumDec } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import type {
  ChildColumnDef,
  FieldDef,
  LinkDef,
  LinkRecord,
  NavLink,
  ResourceConfig,
  SelectOption,
} from "@/types";
import { companyField, docNoField } from "./selling";
import { accountGstFields, gstOptionCell } from "./gst";

/* ---- option lists (values are the backend's TextChoices) ---- */

const opt = (value: string): SelectOption => ({
  value,
  labelKey: `acc.opt.${value.toLowerCase().replace(/\s+/g, "_")}`,
});

const ROOT_TYPES = ["Asset", "Liability", "Equity", "Income", "Expense"].map(opt);
const ACCOUNT_TYPES = ["Receivable", "Payable", "Bank", "Cash", "Stock", "Tax", "Cost of Goods Sold"].map(opt);
const PAYMENT_TYPES = ["Receive", "Pay", "Internal Transfer"].map(opt);
const PARTY_TYPES = ["Customer", "Supplier"].map(opt);
// accounts.JournalEntryType (ERPNext's Journal Entry voucher_type).
const JE_TYPES = [
  "Journal Entry",
  "Bank Entry",
  "Cash Entry",
  "Contra Entry",
  "Credit Note",
  "Debit Note",
  "Opening Entry",
  "Depreciation Entry",
  "Write Off Entry",
].map(opt);
// reference_doctype is stored as "<app>.<Model>" (see accounts/mapping.py build_payment_entry).
const REFERENCE_DOCTYPES: SelectOption[] = [
  { value: "selling.SalesInvoice", labelKey: "acc.opt.sales_invoice" },
  { value: "buying.PurchaseInvoice", labelKey: "acc.opt.purchase_invoice" },
];

/** Translate a raw TextChoices value coming from the API (list cells). */
const optionCell = (value: any): string =>
  value ? i18n.t(`acc.opt.${String(value).toLowerCase().replace(/\s+/g, "_")}`, { defaultValue: String(value) }) : "";

/* ---- link definitions ---- */

/** Postable (leaf, active) account of the document's company. */
export const leafAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => [r.account_type, r.root_type].filter(Boolean).join(" - ") || undefined,
  display: (v) => v.account_name,
  filters: (_row, form) => ({ company: form.company, is_group: false, is_active: true }),
};

const parentAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.root_type,
  display: (v) => v.parent_name,
  filters: (_row, form) => ({ company: form.company, is_group: true }),
  // A child inherits its parent's root type (as in ERPNext).
  fill: (r) => ({ root_type: r.root_type }),
};

/**
 * Party picker over customers + suppliers (party_name is a free-text name on the
 * backend, so the stored value is the party's name). Either endpoint may be
 * forbidden for the user; whatever is readable is offered.
 */
async function fetchParties(search: string): Promise<LinkRecord[]> {
  const load = async (url: string, partyType: string): Promise<LinkRecord[]> => {
    const res = await apiClient.get(url, { params: { search, page_size: 20 } });
    const rows: LinkRecord[] = Array.isArray(res.data) ? res.data : (res.data.results ?? []);
    return rows.map((r) => ({ name: r.name, party_type: partyType }));
  };
  const settled = await Promise.allSettled([
    load("/selling/customers/", "Customer"),
    load("/buying/suppliers/", "Supplier"),
  ]);
  return settled.flatMap((s) => (s.status === "fulfilled" ? s.value : []));
}

const partyLink: LinkDef = {
  fetcher: fetchParties,
  valueField: "name",
  labelField: "name",
  subLabel: (r) => (r.party_type ? i18n.t(`acc.opt.${String(r.party_type).toLowerCase()}`, { defaultValue: r.party_type }) : undefined),
  display: (v) => v.party_name || undefined,
  fill: (r) => ({ party_type: r.party_type }),
};

/* ---- Chart of Accounts ---- */

const accountsMaster: ResourceConfig = {
  key: "accounts",
  module: "accounts",
  endpoint: "/accounts/accounts/",
  titleKey: "acc.res.accounts",
  singularKey: "acc.res.account",
  icon: BookOpen,
  titleField: "name",
  ordering: "name",
  tree: { parentKey: "parent" },
  columns: [
    { key: "name", labelKey: "acc.fields.account_name" },
    { key: "account_number", labelKey: "acc.fields.account_number" },
    { key: "parent_name", labelKey: "acc.fields.parent_account" },
    { key: "root_type", labelKey: "acc.fields.root_type", render: optionCell },
    { key: "account_type", labelKey: "acc.fields.account_type", render: optionCell },
    { key: "gst_direction", labelKey: "gst.fields.gst_direction", render: gstOptionCell },
    { key: "gst_component", labelKey: "gst.fields.gst_component" },
    { key: "company_name", labelKey: "acc.fields.company" },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "acc.fields.account_name", type: "text", required: true },
    { ...companyField, clears: ["parent"] },
    {
      key: "parent",
      labelKey: "acc.fields.parent_account",
      type: "link",
      link: parentAccountLink,
      helpKey: "acc.help.parent",
    },
    { key: "root_type", labelKey: "acc.fields.root_type", type: "select", required: true, options: ROOT_TYPES },
    { key: "account_type", labelKey: "acc.fields.account_type", type: "select", options: ACCOUNT_TYPES },
    { key: "account_number", labelKey: "acc.fields.account_number", type: "text" },
    ...accountGstFields(),
    { key: "is_group", labelKey: "fields.is_group", type: "boolean", defaultValue: false },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

/* ---- Journal Entry ---- */

const dateField = (key: string): FieldDef => ({
  key,
  labelKey: "fields.posting_date",
  type: "date",
  required: true,
  defaultValue: todayISO,
});

const partyTypeColumn: ChildColumnDef = {
  key: "party_type",
  labelKey: "acc.fields.party_type",
  type: "select",
  options: PARTY_TYPES,
  widthClass: "w-32",
};

const journalLines = (v: Record<string, any>): Record<string, any>[] => (v.lines as Record<string, any>[]) ?? [];

const journalEntries: ResourceConfig = {
  key: "journal-entries",
  module: "accounts",
  endpoint: "/accounts/journal-entries/",
  titleKey: "acc.res.journal_entries",
  singularKey: "acc.res.journal_entry",
  icon: FileSpreadsheet,
  submittable: true,
  ordering: "-posting_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "voucher_type", labelKey: "acc.fields.je_voucher_type", render: optionCell },
    { key: "user_remark", labelKey: "acc.fields.user_remark" },
    { key: "total_debit", labelKey: "acc.fields.total_debit", type: "money" },
    { key: "total_credit", labelKey: "acc.fields.total_credit", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    companyField,
    dateField("posting_date"),
    { key: "voucher_type", labelKey: "acc.fields.je_voucher_type", type: "select", options: JE_TYPES, defaultValue: "Journal Entry" },
    { key: "cheque_no", labelKey: "acc.fields.cheque_no", type: "text" },
    { key: "cheque_date", labelKey: "acc.fields.cheque_date", type: "date" },
    { key: "bill_no", labelKey: "acc.fields.bill_no", type: "text" },
    { key: "bill_date", labelKey: "acc.fields.bill_date", type: "date" },
    { key: "user_remark", labelKey: "acc.fields.user_remark", type: "textarea" },
    // Display-only previews; after a save the server totals (total_debit / total_credit) are shown.
    {
      key: "total_debit",
      labelKey: "acc.fields.total_debit",
      type: "decimal",
      money: true,
      readOnly: true,
      preview: (v) => totalsOf(journalLines(v)).debit,
    },
    {
      key: "total_credit",
      labelKey: "acc.fields.total_credit",
      type: "decimal",
      money: true,
      readOnly: true,
      preview: (v) => totalsOf(journalLines(v)).credit,
    },
    {
      key: "difference",
      labelKey: "acc.fields.difference",
      type: "decimal",
      money: true,
      readOnly: true,
      preview: (v) => totalsOf(journalLines(v)).diff,
    },
  ],
  childTables: [
    {
      key: "lines",
      titleKey: "acc.res.lines",
      columns: [
        {
          key: "account",
          labelKey: "acc.fields.account",
          type: "link",
          required: true,
          link: leafAccountLink,
          widthClass: "min-w-[16rem]",
        },
        { key: "debit", labelKey: "acc.fields.debit", type: "decimal", widthClass: "w-36" },
        { key: "credit", labelKey: "acc.fields.credit", type: "decimal", widthClass: "w-36" },
        partyTypeColumn,
        { key: "party", labelKey: "acc.fields.party", type: "text", widthClass: "min-w-[10rem]" },
      ],
      newRow: () => ({ debit: "", credit: "" }),
    },
  ],
  renderSummary: ({ values }) => createElement(JournalBalanceBanner, { lines: journalLines(values) }),
};

/* ---- Payment Entry ---- */

const referencesOf = (v: Record<string, any>): Record<string, any>[] => (v.references as Record<string, any>[]) ?? [];
const allocatedTotal = (v: Record<string, any>) => sumDec(referencesOf(v).map((r) => r.allocated_amount), 2);

const paymentEntries: ResourceConfig = {
  key: "payment-entries",
  module: "accounts",
  endpoint: "/accounts/payment-entries/",
  titleKey: "acc.res.payment_entries",
  singularKey: "acc.res.payment_entry",
  icon: Wallet,
  submittable: true,
  ordering: "-posting_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "payment_type", labelKey: "acc.fields.payment_type", render: optionCell },
    { key: "party_name", labelKey: "acc.fields.party" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "paid_amount", labelKey: "acc.fields.paid_amount", type: "money" },
    { key: "reference_no", labelKey: "acc.fields.reference_no" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["paid_from", "paid_to"] },
    {
      key: "payment_type",
      labelKey: "acc.fields.payment_type",
      type: "select",
      required: true,
      options: PAYMENT_TYPES,
      defaultValue: "Receive",
    },
    dateField("posting_date"),
    { key: "party_type", labelKey: "acc.fields.party_type", type: "select", options: PARTY_TYPES },
    { key: "party_name", labelKey: "acc.fields.party", type: "link", link: partyLink, helpKey: "acc.help.party" },
    { key: "paid_from", labelKey: "acc.fields.paid_from", type: "link", required: true, link: leafAccountLink },
    { key: "paid_to", labelKey: "acc.fields.paid_to", type: "link", required: true, link: leafAccountLink },
    { key: "paid_amount", labelKey: "acc.fields.paid_amount", type: "decimal", required: true },
    { key: "received_amount", labelKey: "acc.fields.received_amount", type: "decimal" },
    { key: "reference_no", labelKey: "acc.fields.reference_no", type: "text" },
    {
      key: "total_allocated",
      labelKey: "acc.fields.total_allocated",
      type: "decimal",
      money: true,
      readOnly: true,
      preview: allocatedTotal,
    },
    {
      key: "unallocated",
      labelKey: "acc.fields.unallocated",
      type: "decimal",
      money: true,
      readOnly: true,
      preview: (v) => sumDec([v.paid_amount, negDec(allocatedTotal(v))], 2),
    },
  ],
  childTables: [
    {
      key: "references",
      titleKey: "acc.res.references",
      columns: [
        {
          key: "reference_doctype",
          labelKey: "acc.fields.reference_doctype",
          type: "select",
          required: true,
          options: REFERENCE_DOCTYPES,
          widthClass: "w-48",
        },
        {
          key: "reference_name",
          labelKey: "acc.fields.reference_name",
          type: "text",
          required: true,
          widthClass: "min-w-[12rem]",
        },
        {
          key: "allocated_amount",
          labelKey: "acc.fields.allocated_amount",
          type: "decimal",
          required: true,
          widthClass: "w-40",
        },
      ],
      newRow: () => ({ reference_doctype: "selling.SalesInvoice" }),
    },
  ],
};

export const accountsResources: ResourceConfig[] = [accountsMaster, journalEntries, paymentEntries];

/** Sidebar links to the hand-written Accounts pages (GL Entries list + the four reports). */
export const accountsLinks: NavLink[] = [
  { path: "/accounts/gl-entries", labelKey: "acc.nav.gl_entries", icon: Library },
  { path: "/accounts/reports/trial-balance", labelKey: "acc.nav.trial_balance", icon: Scale },
  { path: "/accounts/reports/general-ledger", labelKey: "acc.nav.general_ledger", icon: BarChart3 },
  { path: "/accounts/reports/profit-and-loss", labelKey: "acc.nav.profit_loss", icon: TrendingUp },
  { path: "/accounts/reports/balance-sheet", labelKey: "acc.nav.balance_sheet", icon: Landmark },
  { path: "/accounts/reports/sales-register", labelKey: "registers.nav.sales_register", icon: FileText },
  { path: "/accounts/reports/purchase-register", labelKey: "registers.nav.purchase_register", icon: FileText },
  { path: "/accounts/reports/day-book", labelKey: "registers.nav.day_book", icon: BookOpen },
  { path: "/accounts/reports/cash-book", labelKey: "registers.nav.cash_book", icon: Wallet },
  { path: "/accounts/reports/party-statement", labelKey: "registers.nav.party_statement", icon: ScrollText },
];
