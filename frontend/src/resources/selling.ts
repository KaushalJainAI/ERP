import { apiClient } from "@/api/client";
import { createElement } from "react";
import { BarChart3, FilePlus2, Receipt, RefreshCw, ShoppingCart, Truck, Undo2, UserSquare2, Users, Wallet } from "lucide-react";
import { fetchCompanies } from "@/api/lookups";
import { getActiveCompanyId } from "@/lib/company";
import { CreditBanner } from "@/components/stock/CreditBanner";
import { mulDec, sumDec } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import {
  gstOptionCell,
  lineGstColumns,
  partyGstFields,
  renderGstSummary,
  taxHeaderFields,
  taxPreview,
  taxTotalFields,
  taxesTable,
} from "./gst";
import { DocStatus, type ChildColumnDef, type FieldDef, type LinkDef, type NavLink, type ResourceConfig } from "@/types";
import { hasReadAccess } from "./workspaces";
import { invoiceStatusColumns, invoiceStatusFields } from "./invoiceStatus";
import { returnColumns, returnHeaderFields, withReturnColumns } from "./returns";

/* ---- reusable link definitions (other modules may import these) ---- */

export const companyLink: LinkDef = { fetcher: fetchCompanies, labelField: "name" };

export const customerLink: LinkDef = {
  endpoint: "/selling/customers/",
  labelField: "name",
  subLabel: (r) => [r.gstin, r.customer_group_name].filter(Boolean).join(" - ") || undefined,
  display: (v) => v.customer_name,
  filters: (_row, form) => ({ company: form.company, is_active: true }),
  // The GST snapshot (category / GSTIN) is read-only on the document; shown at once, re-derived by the server.
  fill: (r) => ({ customer_name: r.name, gst_category: r.gst_category ?? "", party_gstin: r.gstin ?? "" }),
};

export const itemLink: LinkDef = {
  endpoint: "/stock/items/",
  label: (r) => `${r.code} - ${r.name}`,
  subLabel: (r) => r.item_group_name,
  display: (row) => (row.item_code ? `${row.item_code} - ${row.item_name}` : undefined),
  filters: () => ({ is_active: true }),
  // Item metadata only. The line rate stays blank so the server can resolve it
  // from the price list (falling back to standard_rate when no list applies).
  fill: (r, row) => ({
    item_code: r.code,
    item_name: r.name,
    gst_hsn_code: r.gst_hsn_code ?? "",
    qty: row.qty ? row.qty : "1",
    // UI-only: lets line columns show the batch picker for batch-tracked items.
    // Never sent to the server (buildPayload writes only declared columns).
    _has_batch_no: !!r.has_batch_no,
    // UI-only too: purchase-bill charges are spread over stock lines only.
    is_stock_item: r.is_stock_item !== false,
  }),
  // Pre-fill the rate the party last paid (price list first, then their last
  // bill incl. Tally history, then the standard rate). The server applies the
  // same chain on save when the rate is still blank.
  fillAsync: async (r, _row, form) => {
    const party = form.customer ? { type: "selling", customer: form.customer } : form.supplier ? { type: "buying", supplier: form.supplier } : null;
    if (!party) return {};
    const res = await apiClient.get("/stock/item-prices/rate/", {
      params: {
        item: r.id,
        ...party,
        company: form.company || undefined,
        price_list: form.price_list || undefined,
        transaction_date: form.posting_date || form.transaction_date || undefined,
      },
    });
    return res.data?.rate ? { rate: res.data.rate } : {};
  },
};

/** Price-list picker shared by selling/buying headers and the stock masters. */
export const priceListLink: LinkDef = {
  endpoint: "/stock/price-lists/",
  labelField: "name",
  subLabel: (r) =>
    [r.currency, r.selling ? "Selling" : null, r.buying ? "Buying" : null].filter(Boolean).join(" · ") || undefined,
  filters: () => ({ enabled: true }),
};

/** Direction-aware pickers: a document must use a list that supports its side. */
export const sellingPriceListLink: LinkDef = {
  ...priceListLink,
  filters: () => ({ enabled: true, selling: true }),
};
export const buyingPriceListLink: LinkDef = {
  ...priceListLink,
  filters: () => ({ enabled: true, buying: true }),
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

/** Batch (lot) picker for invoice / order lines; FEFO-ordered by the server. */
const lineBatchLink: LinkDef = {
  endpoint: "/stock/batches/",
  labelField: "batch_no",
  subLabel: (r) =>
    [r.expiry_date ? `exp ${r.expiry_date}` : null, r.available_qty != null ? `${Number(r.available_qty)} avail` : null]
      .filter(Boolean)
      .join(" · ") || undefined,
  filters: (row, form) => ({
    item: row.item,
    ...(row.warehouse ? { warehouse: row.warehouse, available: 1 } : {}),
    posting_date: form.posting_date ?? form.transaction_date,
  }),
};

const itemColumns: ChildColumnDef[] = [
  { key: "item", labelKey: "fields.item", type: "link", required: true, link: itemLink, widthClass: "min-w-[16rem]" },
  {
    key: "warehouse",
    labelKey: "fields.warehouse",
    type: "link",
    link: warehouseLink,
    placeholderKey: "org.fields.ready_warehouse",
    widthClass: "min-w-[11rem]",
  },
  {
    key: "batch",
    labelKey: "bs.fields.batch",
    type: "link",
    link: lineBatchLink,
    widthClass: "min-w-[12rem]",
    // Only for batch-tracked items, and only when the invoice moves stock (an
    // SI with update_stock off refuses batches: no false traceability).
    hidden: (row, form) => form.update_stock === false || (!row._has_batch_no && !row.batch),
  },
  { key: "qty", labelKey: "fields.qty", type: "number", required: true, widthClass: "w-28" },
  { key: "rate", labelKey: "fields.rate", type: "decimal", widthClass: "w-32" },
  {
    key: "price_list_rate",
    labelKey: "pl.fields.price_list_rate",
    type: "decimal",
    money: true,
    readOnly: true,
    widthClass: "w-32",
  },
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
  // HSN, GST rate, CGST / SGST / IGST: server-owned, shown from the saved doc or the dry-run preview.
  ...lineGstColumns,
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
  // The server numbers the document on save (the page title shows it), so the
  // operator never types one.
  hidden: () => true,
};

export const companyField: FieldDef = {
  key: "company",
  labelKey: "fields.company",
  type: "link",
  required: true,
  readOnlyAfterCreate: true,
  link: companyLink,
  // The company is chosen once on the home screen. The form still carries the id
  // so every dependent link (customer, account, warehouse, tax template) filters
  // to the same company, but the operator never edits it here.
  defaultValue: () => getActiveCompanyId() ?? "",
  hidden: () => true,
};

export const grandTotal = (itemsKey = "items"): FieldDef => ({
  key: "grand_total",
  labelKey: "fields.grand_total",
  type: "decimal",
  money: true,
  readOnly: true,
  preview: (v) => sumDec(((v[itemsKey] as Record<string, any>[]) ?? []).map(lineAmount), 2),
});

/** GST summary plus the customer's live credit exposure (design task 4). */
const renderSalesSummary: NonNullable<ResourceConfig["renderSummary"]> = (ctx) =>
  createElement(
    "div",
    { className: "space-y-3" },
    createElement(CreditBanner, { customerId: ctx.values.customer }),
    renderGstSummary("customer")(ctx)
  );

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
  tree: { parentKey: "parent" },
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
    { key: "gst_category", labelKey: "gst.fields.gst_category", render: gstOptionCell },
    { key: "state_name", labelKey: "gst.fields.state" },
    { key: "phone", labelKey: "fields.phone" },
    { key: "email", labelKey: "fields.email" },
    { key: "default_price_list_name", labelKey: "pl.fields.price_list" },
    { key: "credit_days", labelKey: "fields.credit_days", type: "number" },
    { key: "credit_limit", labelKey: "pl.fields.credit_limit", type: "money" },
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
    ...partyGstFields(),
    { key: "tax_id", labelKey: "fields.tax_id", type: "text" },
    { key: "email", labelKey: "fields.email", type: "email" },
    { key: "phone", labelKey: "fields.phone", type: "text" },
    { key: "default_price_list", labelKey: "pl.fields.price_list", type: "link", link: sellingPriceListLink, helpKey: "pl.help.customer_list" },
    { key: "credit_days", labelKey: "fields.credit_days", type: "number", helpKey: "fields.credit_days_help" },
    { key: "credit_limit", labelKey: "pl.fields.credit_limit", type: "decimal", defaultValue: "0.00", helpKey: "pl.help.credit_limit" },
    { key: "bypass_credit_limit_check", labelKey: "pl.fields.bypass_credit_limit", type: "boolean", defaultValue: false },
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
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date" },
    { key: "delivery_date", labelKey: "fields.delivery_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["customer", "taxes_and_charges", "place_of_supply"] },
    // A new party forgets the place of supply so the server re-defaults it from the party state.
    { key: "customer", labelKey: "fields.customer", type: "link", required: true, link: customerLink, clears: ["place_of_supply", "taxes_and_charges"] },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: sellingPriceListLink, helpKey: "pl.help.document_list", advanced: true },
    { key: "transaction_date", labelKey: "fields.transaction_date", type: "date", required: true, defaultValue: todayISO },
    { key: "delivery_date", labelKey: "fields.delivery_date", type: "date" },
    ...taxHeaderFields("Sales"),
    ...taxTotalFields(),
  ],
  childTables: [itemsTable, taxesTable],
  preview: taxPreview("selling.SalesOrder", "customer"),
  renderSummary: renderSalesSummary,
  actions: [
    {
      key: "make_sales_invoice",
      labelKey: "plain.bill",
      icon: FilePlus2,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/selling/sales-orders/${d.id}/make-sales-invoice/`,
      resultTo: (r) => `/selling/sales-invoices/${r.id}`,
    },
    {
      key: "update_rates",
      labelKey: "pl.actions.update_rates",
      icon: RefreshCw,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.DRAFT,
      url: (d) => `/selling/sales-orders/${d.id}/update-rates/`,
    },
  ],
};

// A Sales Invoice may also be a return (credit note): negative quantities, linked rows.
const salesInvoiceItemsTable = withReturnColumns(itemsTable, "sales_invoice_item");

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
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
    ...invoiceStatusColumns,
    ...returnColumns("credit", "/selling/sales-invoices"),
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["customer", "debit_to", "income_account", "sales_order", "taxes_and_charges", "place_of_supply"] },
    { key: "customer", labelKey: "fields.customer", type: "link", required: true, link: customerLink, clears: ["place_of_supply", "taxes_and_charges"] },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: sellingPriceListLink, helpKey: "pl.help.document_list", advanced: true },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    { key: "due_date", labelKey: "fields.due_date", type: "date", advanced: true },
    {
      key: "sales_order",
      advanced: true,
      labelKey: "fields.sales_order",
      type: "link",
      link: {
        endpoint: "/selling/sales-orders/",
        labelField: "name",
        subLabel: (r) => r.customer_name,
        filters: (_row, form) => ({ company: form.company, customer: form.customer, docstatus: DocStatus.SUBMITTED }),
      },
    },
    { key: "debit_to", labelKey: "fields.debit_to", type: "link", link: accountLink("Asset"), helpKey: "doc.help.default_account", advanced: true },
    { key: "income_account", labelKey: "fields.income_account", type: "link", link: accountLink("Income"), helpKey: "doc.help.default_account", advanced: true },
    { key: "update_stock", labelKey: "fields.update_stock", type: "boolean", defaultValue: true, advanced: true },
    ...returnHeaderFields("credit", "/selling/sales-invoices", "/selling/sales-invoices/", "customer", "customer_name"),
    ...taxHeaderFields("Sales"),
    ...taxTotalFields(),
    ...invoiceStatusFields,
  ],
  childTables: [salesInvoiceItemsTable, taxesTable],
  preview: taxPreview("selling.SalesInvoice", "customer"),
  renderSummary: renderSalesSummary,
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
    {
      key: "make_sales_return",
      labelKey: "ret.create_credit_note",
      icon: Undo2,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED && !d.is_return,
      url: (d) => `/selling/sales-invoices/${d.id}/make-sales-return/`,
      resultTo: (r) => `/selling/sales-invoices/${r.id}`,
    },
    {
      key: "update_rates",
      labelKey: "pl.actions.update_rates",
      icon: RefreshCw,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.DRAFT,
      url: (d) => `/selling/sales-invoices/${d.id}/update-rates/`,
    },
  ],
};

export const sellingResources: ResourceConfig[] = [customers, customerGroups, salesOrders, salesInvoices];

export const sellingLinks: NavLink[] = [
  {
    path: "/selling/pending-orders",
    labelKey: "pending_orders.nav_title",
    icon: Truck,
    group: "transactions",
    visible: (u) => hasReadAccess(u.readable_models, ["selling.SalesOrder"]),
  },
  {
    path: "/selling/analytics",
    labelKey: "selling_kpi.nav_title",
    icon: BarChart3,
    group: "reports",
    visible: (u) => hasReadAccess(u.readable_models, ["selling.SalesOrder", "selling.SalesInvoice", "accounts.GLEntry"]),
  },
];
