import { FilePlus2, Receipt, RefreshCw, ShoppingBag, Truck, Users, Wallet } from "lucide-react";
import { todayISO } from "@/lib/format";
import { DocStatus, type ChildColumnDef, type LinkDef, type ResourceConfig } from "@/types";
import { accountLink, companyField, docNoField, itemsTable, priceListLink } from "./selling";
import { invoiceStatusColumns, invoiceStatusFields } from "./invoiceStatus";
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

const purchaseInvoiceItemsTable = {
  ...itemsTable,
  columns: [...itemsTable.columns, ...newBatchColumns],
};

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
  columns: [
    { key: "name", labelKey: "bs.fields.supplier" },
    { key: "supplier_group_name", labelKey: "bs.fields.supplier_group" },
    { key: "gstin", labelKey: "fields.gstin" },
    { key: "gst_category", labelKey: "gst.fields.gst_category", render: gstOptionCell },
    { key: "state_name", labelKey: "gst.fields.state" },
    { key: "phone", labelKey: "fields.phone" },
    { key: "email", labelKey: "fields.email" },
    { key: "default_price_list_name", labelKey: "pl.fields.price_list" },
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
    { key: "default_price_list", labelKey: "pl.fields.price_list", type: "link", link: priceListLink, helpKey: "pl.help.supplier_list" },
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
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: priceListLink, helpKey: "pl.help.document_list" },
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
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "supplier_name", labelKey: "bs.fields.supplier" },
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    { key: "grand_total", labelKey: "fields.grand_total", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
    ...invoiceStatusColumns,
  ],
  fields: [
    docNoField,
    { ...companyField, clears: ["supplier", "credit_to", "expense_account", "purchase_order", "taxes_and_charges", "place_of_supply"] },
    { key: "supplier", labelKey: "bs.fields.supplier", type: "link", required: true, link: supplierLink, clears: ["place_of_supply", "taxes_and_charges"] },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", link: priceListLink, helpKey: "pl.help.document_list" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    { key: "due_date", labelKey: "fields.due_date", type: "date" },
    {
      key: "purchase_order",
      labelKey: "bs.fields.purchase_order",
      type: "link",
      link: {
        endpoint: "/buying/purchase-orders/",
        labelField: "name",
        subLabel: (r) => r.supplier_name,
        filters: (_row, form) => ({ company: form.company, supplier: form.supplier, docstatus: DocStatus.SUBMITTED }),
      },
    },
    { key: "credit_to", labelKey: "bs.fields.credit_to", type: "link", required: true, link: accountLink("Liability") },
    { key: "expense_account", labelKey: "bs.fields.expense_account", type: "link", required: true, link: accountLink("Expense") },
    { key: "update_stock", labelKey: "fields.update_stock", type: "boolean", defaultValue: true },
    ...taxHeaderFields("Purchase"),
    ...taxTotalFields(),
    ...invoiceStatusFields,
  ],
  childTables: [purchaseInvoiceItemsTable, taxesTable],
  preview: taxPreview("buying.PurchaseInvoice", "supplier"),
  renderSummary: renderGstSummary("supplier"),
  actions: [
    {
      key: "make_payment_entry",
      labelKey: "res.make_payment_entry",
      icon: Wallet,
      kind: "request",
      showWhen: (d) => d.docstatus === DocStatus.SUBMITTED,
      url: (d) => `/buying/purchase-invoices/${d.id}/make-payment-entry/`,
      // Opened in the Accounts module's Payment Entry document page.
      resultTo: (r) => `/accounts/payment-entries/${r.id}`,
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

export const buyingResources: ResourceConfig[] = [suppliers, supplierGroups, purchaseOrders, purchaseInvoices];
