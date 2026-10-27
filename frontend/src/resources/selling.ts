import { FilePlus2, Receipt, ShoppingCart, UserSquare2, Users, Wallet } from "lucide-react";
import { fetchCompanies } from "@/api/lookups";
import { mulDec, roundDec, sumDec } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import { DocStatus, type ChildColumnDef, type FieldDef, type LinkDef, type ResourceConfig } from "@/types";

/* ---- reusable link definitions (other modules may import these) ---- */

export const companyLink: LinkDef = { fetcher: fetchCompanies, labelField: "name" };

export const customerLink: LinkDef = {
  endpoint: "/selling/customers/",
  labelField: "name",
  subLabel: (r) => [r.gstin, r.customer_group_name].filter(Boolean).join(" - ") || undefined,
  display: (v) => v.customer_name,
  filters: (_row, form) => ({ company: form.company, is_active: true }),
  fill: (r) => ({ customer_name: r.name }),
};

export const itemLink: LinkDef = {
  endpoint: "/stock/items/",
  label: (r) => `${r.code} - ${r.name}`,
  subLabel: (r) => r.item_group_name,
  display: (row) => (row.item_code ? `${row.item_code} - ${row.item_name}` : undefined),
  filters: () => ({ is_active: true }),
  // Auto-fill the rate from the item's standard rate (rounded to the 2dp the line stores).
  fill: (r, row) => ({
    item_code: r.code,
    item_name: r.name,
    rate: roundDec(r.standard_rate, 2) ?? row.rate ?? "",
    qty: row.qty ? row.qty : "1",
  }),
};

export const warehouseLink: LinkDef = {
  endpoint: "/stock/warehouses/",
  labelField: "name",
  filters: (_row, form) => ({ company: form.company, is_group: false }),
};

export const accountLink = (rootType: string): LinkDef => ({
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.account_type || r.root_type,
  filters: (_row, form) => ({ company: form.company, root_type: rootType, is_group: false, is_active: true }),
});

/* ---- line items (shared by Sales Order and Sales Invoice) ---- */

const lineAmount = (row: Record<string, any>) => mulDec(row.qty, row.rate, 2);

const itemColumns: ChildColumnDef[] = [
  { key: "item", labelKey: "fields.item", type: "link", required: true, link: itemLink, widthClass: "min-w-[16rem]" },
  { key: "warehouse", labelKey: "fields.warehouse", type: "link", link: warehouseLink, widthClass: "min-w-[11rem]" },
  { key: "qty", labelKey: "fields.qty", type: "number", required: true, widthClass: "w-28" },
  { key: "rate", labelKey: "fields.rate", type: "decimal", required: true, widthClass: "w-32" },
  {
    key: "amount",
    labelKey: "fields.amount",
    type: "decimal",
    money: true,
    readOnly: true,
    preview: lineAmount, // display-only estimate; the server value replaces it after save
    dependsOn: ["qty", "rate"],
    widthClass: "w-36",
  },
];

export const itemsTable = {
  key: "items",
  titleKey: "res.items_table",
  columns: itemColumns,
  newRow: () => ({ qty: "1" }),
  inheritFromPrevious: ["warehouse"],
};

export const docNoField: FieldDef = {
  key: "name",
  labelKey: "fields.docno",
  type: "text",
  readOnlyAfterCreate: true,
  helpKey: "fields.docno_help",
};

export const companyField: FieldDef = {
  key: "company",
  labelKey: "fields.company",
  type: "link",
  required: true,
  readOnlyAfterCreate: true,
  link: companyLink,
};

export const grandTotal = (itemsKey = "items"): FieldDef => ({
  key: "grand_total",
  labelKey: "fields.grand_total",
  type: "decimal",
  money: true,
  readOnly: true,
  preview: (v) => sumDec(((v[itemsKey] as Record<string, any>[]) ?? []).map(lineAmount), 2),
});

/* ---- resources ---- */

const customerGroups: ResourceConfig = {
  key: "customer-groups",
  module: "selling",
  endpoint: "/selling/customer-groups/",
  titleKey: "res.customer_groups",
  singularKey: "res.customer_group",
  icon: Users,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "fields.customer_group" },
    { key: "parent_name", labelKey: "fields.parent_group" },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "fields.customer_group", type: "text", required: true },
    {
      key: "parent",
      labelKey: "fields.parent_group",
      type: "link",
      link: { endpoint: "/selling/customer-groups/", labelField: "name", display: (v) => v.parent_name },
    },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean", defaultValue: true },
  ],
};

const customers: ResourceConfig = {
  key: "customers",
  module: "selling",
  endpoint: "/selling/customers/",
  titleKey: "res.customers",
  singularKey: "res.customer",
  icon: UserSquare2,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "fields.customer" },
    { key: "customer_group_name", labelKey: "fields.customer_group" },
    { key: "gstin", labelKey: "fields.gstin" },
    { key: "phone", labelKey: "fields.phone" },
    { key: "email", labelKey: "fields.email" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "fields.customer", type: "text", required: true },
    { ...companyField, readOnlyAfterCreate: true, clears: ["customer_group"] },
    {
      key: "customer_group",
      labelKey: "fields.customer_group",
      type: "link",
      required: true,
      link: { endpoint: "/selling/customer-groups/", labelField: "name", display: (v) => v.customer_group_name },
    },
    { key: "gstin", labelKey: "fields.gstin", type: "text" },
    { key: "tax_id", labelKey: "fields.tax_id", type: "text" },
    { key: "email", labelKey: "fields.email", type: "email" },
    { key: "phone", labelKey: "fields.phone", type: "text" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

const salesOrders: ResourceConfig = {
  key: "sales-orders",
  module: "selling",
  endpoint: "/selling/sales-orders/",
  titleKey: "res.sales_orders",
  singularKey: "res.sales_order",
  icon: ShoppingCart,
  submittable: true,
  ordering: "-transaction_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "customer_name", labelKey: "fields.customer" },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date" },
    { key: "delivery_date", labelKey: "fields.delivery_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["customer"] },
    { key: "customer", labelKey: "fields.customer", type: "link", required: true, link: customerLink },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date", required: true, defaultValue: todayISO },
    { key: "delivery_date", labelKey: "fields.delivery_date", type: "date" },
    grandTotal(),
  ],
  childTables: [itemsTable],
  actions: [
    {
      key: "make_sales_invoice",
      labelKey: "res.make_sales_invoice",
      icon: FilePlus2,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/selling/sales-orders/${d.id}/make-sales-invoice/`,
      resultTo: (r) => `/selling/sales-invoices/${r.id}`,
    },
  ],
};

const salesInvoices: ResourceConfig = {
  key: "sales-invoices",
  module: "selling",
  endpoint: "/selling/sales-invoices/",
  titleKey: "res.sales_invoices",
  singularKey: "res.sales_invoice",
  icon: Receipt,
  submittable: true,
  ordering: "-posting_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "customer_name", labelKey: "fields.customer" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["customer", "debit_to", "income_account", "sales_order"] },
    { key: "customer", labelKey: "fields.customer", type: "link", required: true, link: customerLink },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    {
      key: "sales_order",
      labelKey: "fields.sales_order",
      type: "link",
      link: {
        endpoint: "/selling/sales-orders/",
        labelField: "name",
        subLabel: (r) => r.customer_name,
        filters: (_row, form) => ({ company: form.company, customer: form.customer, docstatus: DocStatus.SUBMITTED }),
      },
    },
    { key: "debit_to", labelKey: "fields.debit_to", type: "link", required: true, link: accountLink("Asset") },
    { key: "income_account", labelKey: "fields.income_account", type: "link", required: true, link: accountLink("Income") },
    { key: "update_stock", labelKey: "fields.update_stock", type: "boolean", defaultValue: true },
    grandTotal(),
  ],
  childTables: [itemsTable],
  actions: [
    {
      key: "make_payment_entry",
      labelKey: "res.make_payment_entry",
      icon: Wallet,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/selling/sales-invoices/${d.id}/make-payment-entry/`,
      // Opened in the Accounts module's Payment Entry document page.
      resultTo: (r) => `/accounts/payment-entries/${r.id}`,
    },
  ],
};

export const sellingResources: ResourceConfig[] = [customers, customerGroups, salesOrders, salesInvoices];
