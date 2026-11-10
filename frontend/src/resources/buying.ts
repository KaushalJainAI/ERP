import { createElement } from "react";
import { FilePlus2, Receipt, RefreshCw, ShoppingBag, Truck, Undo2, Users, Wallet } from "lucide-react";
import { LandedCostPreview } from "@/components/doc/LandedCostPreview";
import { isDecimal } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import { DocStatus, type ChildColumnDef, type ChildTableDef, type LinkDef, type PreviewDef, type ResourceConfig } from "@/types";
import { accountLink, buyingPriceListLink, companyField, docNoField, itemsTable } from "./selling";
import { invoiceStatusColumns, invoiceStatusFields } from "./invoiceStatus";
import { returnColumns, returnHeaderFields, withReturnColumns } from "./returns";
import {
  gstOptionCell,
  partyGstFields,
  renderGstSummary,
  taxHeaderFields,
  taxPreview,
  taxTotalFields,
  taxesTable,
} from "./gst";

/* ---- reusable link definitions (other modules may import these) ---- */

export const supplierLink: LinkDef = {
  endpoint: "/buying/suppliers/",
  labelField: "name",
  subLabel: (r) => [r.gstin, r.supplier_group_name].filter(Boolean).join(" - ") || undefined,
  display: (v) => v.supplier_name,
  filters: (_row, form) => ({ company: form.company, is_active: true }),
  fill: (r) => ({ supplier_name: r.name, gst_category: r.gst_category ?? "", party_gstin: r.gstin ?? "" }),
};

const supplierGroupLink: LinkDef = {
  endpoint: "/buying/supplier-groups/",
  labelField: "name",
  display: (v) => v.supplier_group_name,
};

/* ---- line tables ---- */

// A Purchase Order does not move stock, so it carries no batch.
const purchaseOrderItemsTable = {
  ...itemsTable,
  columns: itemsTable.columns.filter((c) => c.key !== "batch"),
};

// A Purchase Invoice with update_stock may receive into an existing batch or
// create a new one from the three new-batch fields (server-side at submit).
const newBatchColumns: ChildColumnDef[] = [
  {
    key: "supplier_batch_no",
    labelKey: "bs.fields.supplier_batch_no",
    type: "text",
    widthClass: "w-36",
    hidden: (row, form) => form.update_stock === false || !row._has_batch_no || !!row.batch,
  },
  {
    key: "manufacturing_date",
    labelKey: "bs.fields.manufacturing_date",
    type: "date",
    widthClass: "w-36",
    hidden: (row, form) => form.update_stock === false || !row._has_batch_no || !!row.batch,
  },
  {
    key: "expiry_date",
    labelKey: "bs.fields.expiry_date",
    type: "date",
    widthClass: "w-36",
    hidden: (row, form) => form.update_stock === false || !row._has_batch_no || !!row.batch,
  },
];

// Server-owned: the line's cost once the bill's extra charges are spread over it.
const landedRateColumn: ChildColumnDef = {
  key: "landed_rate",
  labelKey: "chg.landed_rate",
  type: "decimal",
  decimals: 6,
  readOnly: true,
  widthClass: "w-32",
  hidden: (row) => !row.landed_rate,
};

const purchaseInvoiceItemsTable = withReturnColumns(
  {
    ...itemsTable,
    columns: [...itemsTable.columns, ...newBatchColumns, landedRateColumn],
  },
  "purchase_invoice_item"
);

/* ---- extra charges (freight, hamali / loading, unloading, cartage) ---- */

const chargeTemplateLink: LinkDef = {
  endpoint: "/buying/charge-templates/",
  labelField: "name",
  filters: (_row, form) => ({ company: form.company, is_active: true }),
  // Picking a preset fills the row; every value stays editable.
  fill: (r) => ({
    description: r.name,
    account: r.account ?? "",
    account_name: r.account_name ?? "",
    distribute_by: r.distribute_by,
    included_in_bill: !!r.included_in_bill,
  }),
};

const DISTRIBUTE_OPTIONS = [
  { value: "Amount", labelKey: "chg.by_amount" },
  { value: "Qty", labelKey: "chg.by_qty" },
];

const chargesTable: ChildTableDef = {
  key: "charges",
  titleKey: "chg.table",
  newRow: () => ({ distribute_by: "Amount", included_in_bill: false }),
  columns: [
    // UI-only picker: never sent (the serializer has no such field).
    { key: "_template", labelKey: "chg.from_template", type: "link", link: chargeTemplateLink, widthClass: "min-w-[12rem]" },
    { key: "description", labelKey: "chg.description", type: "text", required: true, widthClass: "min-w-[12rem]" },
    { key: "amount", labelKey: "chg.amount", type: "decimal", money: true, required: true, widthClass: "w-32" },
    { key: "distribute_by", labelKey: "chg.distribute_by", type: "select", required: true, options: DISTRIBUTE_OPTIONS, widthClass: "w-36" },
    { key: "included_in_bill", labelKey: "chg.in_bill", type: "boolean", widthClass: "w-28" },
    {
      key: "account",
      labelKey: "chg.account",
      type: "link",
      link: { ...accountLink("Expense"), display: (v) => v.account_name },
      widthClass: "min-w-[14rem]",
      // Only a charge on the supplier's bill is booked to an expense account.
      hidden: (row) => !row.included_in_bill,
    },
  ],
};

/** The dry run also needs the charges that sit on the bill: they raise the total. */
const basePurchasePreview = taxPreview("buying.PurchaseInvoice", "supplier");
const purchaseInvoicePreview: PreviewDef = {
  ...basePurchasePreview,
  buildRequest: (v, manual) => {
    const body = basePurchasePreview.buildRequest(v, manual);
    if (!body) return body;
    const rows = ((v.charges as Record<string, any>[]) ?? []).filter((c) => isDecimal(c.amount));
    body.charges = rows.map((c) => ({ amount: c.amount, included_in_bill: !!c.included_in_bill }));
    return body;
  },
};

/** GST summary plus the live landed cost per stock line. */
const renderPurchaseSummary: NonNullable<ResourceConfig["renderSummary"]> = (ctx) =>
  createElement(
    "div",
    { className: "space-y-3" },
    renderGstSummary("supplier")(ctx),
    createElement(LandedCostPreview, { values: ctx.values })
  );

/* ---- resources ---- */

const supplierGroups: ResourceConfig = {
  key: "supplier-groups",
  module: "buying",
  endpoint: "/buying/supplier-groups/",
  titleKey: "bs.res.supplier_groups",
  singularKey: "bs.res.supplier_group",
  icon: Users,
  titleField: "name",
  ordering: "name",
  tree: { parentKey: "parent" },
  columns: [
    { key: "name", labelKey: "bs.fields.supplier_group" },
    { key: "parent_name", labelKey: "fields.parent_group" },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "bs.fields.supplier_group", type: "text", required: true },
    {
      key: "parent",
      labelKey: "fields.parent_group",
      type: "link",
      link: { endpoint: "/buying/supplier-groups/", labelField: "name", display: (v) => v.parent_name },
    },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean", defaultValue: true },
  ],
};

const suppliers: ResourceConfig = {
  key: "suppliers",
  module: "buying",
  endpoint: "/buying/suppliers/",
  titleKey: "bs.res.suppliers",
  singularKey: "bs.res.supplier",
  icon: Truck,
  titleField: "name",
  ordering: "name",
  // The party's documents, Tally-era (historical) ones included.
  actions: [
    {
      key: "purchase_invoices",
      labelKey: "bs.res.purchase_invoices",
      icon: Receipt,
      kind: "navigate",
      navigate: (d) => ({ to: `/buying/purchase-invoices?supplier=${d.id}` }),
    },
    {
      key: "payments",
      labelKey: "acc.res.payment_entries",
      icon: Wallet,
      kind: "navigate",
      navigate: (d) => ({
        to: `/accounts/payment-entries?party_type=Supplier&party_name=${encodeURIComponent(String(d.name ?? ""))}`,
      }),
    },
  ],
  columns: [
    { key: "name", labelKey: "bs.fields.supplier" },
    { key: "supplier_group_name", labelKey: "bs.fields.supplier_group" },
    { key: "gstin", labelKey: "fields.gstin" },
    { key: "gst_category", labelKey: "gst.fields.gst_category", render: gstOptionCell },
    { key: "state_name", labelKey: "gst.fields.state" },
    { key: "phone", labelKey: "fields.phone" },
    { key: "email", labelKey: "fields.email" },
    { key: "default_price_list_name", labelKey: "pl.fields.price_list" },
    { key: "credit_days", labelKey: "fields.credit_days", type: "number" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "bs.fields.supplier", type: "text", required: true },
    { ...companyField, clears: ["supplier_group"] },
    {
      key: "supplier_group",
      labelKey: "bs.fields.supplier_group",
      type: "link",
      required: true,
      link: supplierGroupLink,
    },
    ...partyGstFields(),
    { key: "tax_id", labelKey: "fields.tax_id", type: "text" },
    { key: "email", labelKey: "fields.email", type: "email" },
    { key: "phone", labelKey: "fields.phone", type: "text" },
    { key: "default_price_list", labelKey: "pl.fields.price_list", type: "link", link: buyingPriceListLink, helpKey: "pl.help.supplier_list" },
    { key: "credit_days", labelKey: "fields.credit_days", type: "number", helpKey: "fields.credit_days_help" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

const purchaseOrders: ResourceConfig = {
  key: "purchase-orders",
  module: "buying",
  endpoint: "/buying/purchase-orders/",
  titleKey: "bs.res.purchase_orders",
  singularKey: "bs.res.purchase_order",
  icon: ShoppingBag,
  submittable: true,
  hideInSidebar: true,
  ordering: "-transaction_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "supplier_name", labelKey: "bs.fields.supplier" },
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date" },
    { key: "schedule_date", labelKey: "bs.fields.schedule_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["supplier", "taxes_and_charges", "place_of_supply"] },
    { key: "supplier", labelKey: "bs.fields.supplier", type: "link", required: true, link: supplierLink, clears: ["place_of_supply", "taxes_and_charges"] },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: buyingPriceListLink, helpKey: "pl.help.document_list", advanced: true },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date", required: true, defaultValue: todayISO },
    { key: "schedule_date", labelKey: "bs.fields.schedule_date", type: "date" },
    ...taxHeaderFields("Purchase"),
    ...taxTotalFields(),
  ],
  childTables: [purchaseOrderItemsTable, taxesTable],
  preview: taxPreview("buying.PurchaseOrder", "supplier"),
  renderSummary: renderGstSummary("supplier"),
  actions: [
    {
      key: "make_purchase_invoice",
      labelKey: "bs.res.make_purchase_invoice",
      icon: FilePlus2,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/buying/purchase-orders/${d.id}/make-purchase-invoice/`,
      resultTo: (r) => `/buying/purchase-invoices/${r.id}`,
    },
    {
      key: "update_rates",
      labelKey: "pl.actions.update_rates",
      icon: RefreshCw,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.DRAFT,
      url: (d) => `/buying/purchase-orders/${d.id}/update-rates/`,
    },
  ],
};

const purchaseInvoices: ResourceConfig = {
  key: "purchase-invoices",
  module: "buying",
  endpoint: "/buying/purchase-invoices/",
  titleKey: "bs.res.purchase_invoices",
  singularKey: "bs.res.purchase_invoice",
  icon: Receipt,
  submittable: true,
  ordering: "-posting_date,-created_at",
  urlFilters: ["supplier"],
  historicalFilter: true,
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "supplier_name", labelKey: "bs.fields.supplier" },
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "bill_no", labelKey: "chg.bill_no" },
    { key: "bill_date", labelKey: "chg.bill_date", type: "date" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
    ...invoiceStatusColumns,
    ...returnColumns("debit", "/buying/purchase-invoices"),
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["supplier", "credit_to", "expense_account", "purchase_order", "taxes_and_charges", "place_of_supply"] },
    { key: "supplier", labelKey: "bs.fields.supplier", type: "link", required: true, link: supplierLink, clears: ["place_of_supply", "taxes_and_charges"] },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: buyingPriceListLink, helpKey: "pl.help.document_list", advanced: true },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    // The supplier's own invoice number / date: the reference the accountant needs in Tally.
    { key: "bill_no", labelKey: "chg.bill_no", type: "text", helpKey: "chg.bill_no_help" },
    { key: "bill_date", labelKey: "chg.bill_date", type: "date" },
    { key: "due_date", labelKey: "fields.due_date", type: "date", advanced: true },
    {
      key: "purchase_order",
      advanced: true,
      labelKey: "bs.fields.purchase_order",
      type: "link",
      link: {
        endpoint: "/buying/purchase-orders/",
        labelField: "name",
        subLabel: (r) => r.supplier_name,
        filters: (_row, form) => ({ company: form.company, supplier: form.supplier, docstatus: DocStatus.SUBMITTED }),
      },
    },
    { key: "credit_to", labelKey: "bs.fields.credit_to", type: "link", link: accountLink("Liability"), helpKey: "doc.help.default_account", advanced: true },
    { key: "expense_account", labelKey: "bs.fields.expense_account", type: "link", link: accountLink("Expense"), helpKey: "doc.help.default_account", advanced: true },
    { key: "update_stock", labelKey: "fields.update_stock", type: "boolean", defaultValue: true, advanced: true },
    { key: "itc_eligible", labelKey: "fields.itc_eligible", type: "boolean", defaultValue: true, advanced: true },
    ...returnHeaderFields("debit", "/buying/purchase-invoices", "/buying/purchase-invoices/", "supplier", "supplier_name"),
    ...taxHeaderFields("Purchase"),
    ...taxTotalFields(),
    ...invoiceStatusFields,
  ],
  childTables: [purchaseInvoiceItemsTable, taxesTable, chargesTable],
  preview: purchaseInvoicePreview,
  renderSummary: renderPurchaseSummary,
  actions: [
    {
      key: "make_payment_entry",
      labelKey: "plain.paid",
      icon: Wallet,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/buying/purchase-invoices/${d.id}/make-payment-entry/`,
      // Opened in the Accounts module's Payment Entry document page.
      resultTo: (r) => `/accounts/payment-entries/${r.id}`,
    },
    {
      key: "make_purchase_return",
      labelKey: "ret.create_debit_note",
      icon: Undo2,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED && !d.is_return,
      url: (d) => `/buying/purchase-invoices/${d.id}/make-purchase-return/`,
      resultTo: (r) => `/buying/purchase-invoices/${r.id}`,
    },
    {
      key: "update_rates",
      labelKey: "pl.actions.update_rates",
      icon: RefreshCw,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.DRAFT,
      url: (d) => `/buying/purchase-invoices/${d.id}/update-rates/`,
    },
  ],
};

export const expenseCategoryLink: LinkDef = {
  endpoint: "/buying/expense-categories/",
  labelField: "name",
  display: (v) => v.expense_category_name ?? v.name,
  filters: (_row, form) => ({ company: form.company, is_active: true }),
};

export const expenseCategories: ResourceConfig = {
  key: "expense-categories",
  module: "buying",
  endpoint: "/buying/expense-categories/",
  titleKey: "bs.res.expense_categories",
  singularKey: "bs.res.expense_category",
  icon: Receipt,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "fields.name" },
    { key: "account_name", labelKey: "bs.fields.account" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    companyField,
    { key: "name", labelKey: "fields.name", type: "text", required: true },
    {
      key: "account",
      labelKey: "bs.fields.account",
      type: "link",
      required: true,
      link: accountLink("Expense"),
      helpKey: "doc.help.default_account",
    },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

export const buyingResources: ResourceConfig[] = [
  suppliers,
  supplierGroups,
  purchaseOrders,
  purchaseInvoices,
  expenseCategories,
];
