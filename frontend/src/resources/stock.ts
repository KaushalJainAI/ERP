import { ArrowLeftRight, BarChart3, CalendarClock, ClipboardCheck, FolderTree, Layers, ListChecks, Package, Ruler, ScrollText, Shapes, Tags, Warehouse } from "lucide-react";
import { createElement } from "react";
import { roundDec } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import type { LinkDef, ResourceConfig } from "@/types";
import type { ReportConfig } from "@/types/report";
import { companyField, companyLink, docNoField, itemLink, priceListLink, warehouseLink } from "./selling";
import { itemGstFields } from "./gst";
import { bomLink, workOrderLink } from "./manufacturingLinks";

/* ---- reusable link definitions (other modules may import these) ---- */

export const itemGroupLink: LinkDef = {
  endpoint: "/stock/item-groups/",
  labelField: "name",
  display: (v) => v.item_group_name,
};

export const uomLink: LinkDef = {
  endpoint: "/stock/uoms/",
  labelField: "name",
  display: (v) => v.stock_uom_name,
};

/** Item picker for stock-entry lines (seeds the valuation rate and the batch hint). */
const stockEntryItemLink: LinkDef = {
  ...itemLink,
  // A template (has variants) is never a line: pick one of its variants.
  filters: () => ({ is_active: true, has_variants: false }),
  fill: (r, row) => ({
    item_code: r.code,
    item_name: r.name,
    valuation_rate: roundDec(r.standard_rate, 6) ?? row.valuation_rate ?? "",
    qty: row.qty ? row.qty : "1",
    _has_batch_no: !!r.has_batch_no,
  }),
};

/**
 * Batch picker. Outward legs show only usable lots in FEFO order (the server
 * filters expired and empty batches and sorts by expiry); an inward leg may also
 * pick an existing lot, or leave it blank and fill the new-batch fields.
 */
export const batchLink: LinkDef = {
  endpoint: "/stock/batches/",
  labelField: "batch_no",
  subLabel: (r) =>
    [r.expiry_date ? `exp ${r.expiry_date}` : null, r.available_qty != null ? `${Number(r.available_qty)} avail` : null]
      .filter(Boolean)
      .join(" · ") || undefined,
  filters: (row, form) => ({
    item: row.item,
    ...(form.stock_entry_type === "Material Receipt" || !row.source_warehouse
      ? {}
      : { warehouse: row.source_warehouse, available: 1 }),
    posting_date: form.posting_date,
  }),
};

const fmt = (min: number, max: number) => (v: any) =>
  v === null || v === undefined || v === "" || isNaN(Number(v))
    ? ""
    : new Intl.NumberFormat("en-IN", { minimumFractionDigits: min, maximumFractionDigits: max }).format(Number(v));

/** Rates carry up to 6 decimals; show at least 2 (display only). */
const rateCell = fmt(2, 6);

/** Quantities carry up to 3 decimals. */
const qtyCell = fmt(0, 3);

const VALUATION_OPTIONS = [
  { value: "FIFO", labelKey: "bs.res.valuation_fifo" },
  { value: "Moving Average", labelKey: "bs.res.valuation_moving_average" },
];

const ENTRY_TYPE_OPTIONS = [
  { value: "Material Receipt", labelKey: "bs.res.entry_material_receipt" },
  { value: "Material Issue", labelKey: "bs.res.entry_material_issue" },
  { value: "Material Transfer", labelKey: "bs.res.entry_material_transfer" },
  { value: "Material Transfer for Manufacture", labelKey: "bs.res.entry_material_transfer_manufacture" },
  { value: "Manufacture", labelKey: "bs.res.entry_manufacture" },
  { value: "Repack", labelKey: "bs.res.entry_repack" },
];

/** Work-Order vouchers carry a Work Order / BOM; Repack carries neither. */
const WORK_ORDER_ENTRY_TYPES = ["Material Transfer for Manufacture", "Manufacture"];
const isWorkOrderEntry = (values: Record<string, any>) =>
  WORK_ORDER_ENTRY_TYPES.includes(values.stock_entry_type);
const isOutputEntry = (values: Record<string, any>) =>
  values.stock_entry_type === "Manufacture" || values.stock_entry_type === "Repack";

/* ---- masters ---- */

const uoms: ResourceConfig = {
  key: "uoms",
  module: "stock",
  endpoint: "/stock/uoms/",
  titleKey: "bs.res.uoms",
  singularKey: "bs.res.uom",
  icon: Ruler,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "bs.fields.uom" },
    { key: "gst_uqc", labelKey: "gst.fields.gst_uqc" },
  ],
  fields: [
    { key: "name", labelKey: "bs.fields.uom", type: "text", required: true },
    { key: "gst_uqc", labelKey: "gst.fields.gst_uqc", type: "text", helpKey: "gst.help.gst_uqc" },
  ],
};

const itemGroups: ResourceConfig = {
  key: "item-groups",
  module: "stock",
  endpoint: "/stock/item-groups/",
  titleKey: "bs.res.item_groups",
  singularKey: "bs.res.item_group",
  icon: FolderTree,
  titleField: "name",
  ordering: "name",
  tree: { parentKey: "parent" },
  columns: [
    { key: "name", labelKey: "bs.fields.item_group" },
    { key: "parent_name", labelKey: "fields.parent_group" },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "bs.fields.item_group", type: "text", required: true },
    {
      key: "parent",
      labelKey: "fields.parent_group",
      type: "link",
      link: { endpoint: "/stock/item-groups/", labelField: "name", display: (v) => v.parent_name },
    },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean", defaultValue: true },
  ],
};

const items: ResourceConfig = {
  key: "items",
  module: "stock",
  endpoint: "/stock/items/",
  titleKey: "bs.res.items",
  singularKey: "bs.res.item",
  icon: Package,
  titleField: "name",
  ordering: "code",
  columns: [
    { key: "code", labelKey: "bs.fields.item_code" },
    { key: "name", labelKey: "bs.fields.item_name" },
    { key: "item_group_name", labelKey: "bs.fields.item_group" },
    { key: "stock_uom_name", labelKey: "bs.fields.stock_uom" },
    { key: "variant_of_code", labelKey: "vr.fields.variant_of" },
    { key: "has_variants", labelKey: "vr.fields.has_variants", type: "boolean" },
    { key: "gst_hsn_code", labelKey: "gst.fields.hsn_code" },
    { key: "item_tax_template_name", labelKey: "gst.fields.item_tax_template" },
    { key: "valuation_method", labelKey: "bs.fields.valuation_method" },
    { key: "standard_rate", labelKey: "bs.fields.standard_rate", type: "number", render: rateCell },
    { key: "critical_stock", labelKey: "bs.fields.critical_stock", type: "number", render: qtyCell },
    { key: "has_batch_no", labelKey: "bs.fields.has_batch_no", type: "boolean" },
    { key: "is_stock_item", labelKey: "bs.fields.is_stock_item", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "code", labelKey: "bs.fields.item_code", type: "text", required: true },
    { key: "name", labelKey: "bs.fields.item_name", type: "text", required: true },
    { key: "item_group", labelKey: "bs.fields.item_group", type: "link", required: true, link: itemGroupLink },
    { key: "stock_uom", labelKey: "bs.fields.stock_uom", type: "link", required: true, link: uomLink },
    {
      key: "valuation_method",
      labelKey: "bs.fields.valuation_method",
      type: "select",
      required: true,
      options: VALUATION_OPTIONS,
      defaultValue: "FIFO",
    },
    { key: "standard_rate", labelKey: "bs.fields.standard_rate", type: "decimal", decimals: 6, defaultValue: "0" },
    { key: "critical_stock", labelKey: "bs.fields.critical_stock", type: "decimal", decimals: 3, defaultValue: "0" },
    // A template groups variants and is never stocked: the server forces this off.
    { key: "is_stock_item", labelKey: "bs.fields.is_stock_item", type: "boolean", defaultValue: true, hidden: (v) => !!v.has_variants },
    // Variants (ERPNext Item Variants): a template varies by attributes; a variant is made with "Create Variant".
    { key: "has_variants", labelKey: "vr.fields.has_variants", type: "boolean", defaultValue: false, helpKey: "vr.help.has_variants", hidden: (v) => !!v.variant_of },
    {
      key: "template_attributes",
      labelKey: "vr.fields.template_attributes",
      type: "multiselect",
      span: 3,
      helpKey: "vr.help.template_attributes",
      hidden: (v) => !v.has_variants,
      link: { endpoint: "/stock/item-attributes/", valueField: "id", labelField: "name" },
    },
    { key: "variant_of_code", labelKey: "vr.fields.variant_of", type: "text", readOnly: true, hidden: (v) => !v.variant_of },
    { key: "variant_label", labelKey: "vr.fields.variant_label", type: "text", readOnly: true, hidden: (v) => !v.variant_of },
    // Batch tracking is frozen by the server once the item has ledger entries.
    { key: "has_batch_no", labelKey: "bs.fields.has_batch_no", type: "boolean", defaultValue: false, helpKey: "bs.help.has_batch_no" },
    {
      key: "has_expiry_date",
      labelKey: "bs.fields.has_expiry_date",
      type: "boolean",
      defaultValue: false,
      hidden: (v) => !v.has_batch_no,
      helpKey: "bs.help.has_expiry_date",
    },
    {
      key: "shelf_life_in_days",
      labelKey: "bs.fields.shelf_life_in_days",
      type: "number",
      hidden: (v) => !v.has_expiry_date,
      helpKey: "bs.help.shelf_life_in_days",
    },
    ...itemGstFields(),
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
    { key: "description", labelKey: "bs.fields.description", type: "textarea" },
  ],
  actions: [
    {
      key: "create-variant",
      labelKey: "vr.actions.create_variant",
      icon: Shapes,
      showWhen: (doc) => !!doc.has_variants,
      kind: "navigate",
      navigate: (doc) => ({ to: `/stock/items/${doc.id}/create-variant` }),
    },
  ],
};

const itemAttributes: ResourceConfig = {
  key: "item-attributes",
  module: "stock",
  endpoint: "/stock/item-attributes/",
  titleKey: "vr.res.item_attributes",
  singularKey: "vr.res.item_attribute",
  icon: ListChecks,
  hideInSidebar: true,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "vr.fields.attribute_name" },
    { key: "disabled", labelKey: "bs.fields.is_disabled", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "vr.fields.attribute_name", type: "text", required: true },
    { key: "disabled", labelKey: "bs.fields.is_disabled", type: "boolean", defaultValue: false },
  ],
  childTables: [
    {
      key: "values",
      titleKey: "vr.values_table",
      columns: [
        { key: "value", labelKey: "vr.fields.value", type: "text", required: true },
        { key: "abbr", labelKey: "vr.fields.abbr", type: "text", required: true, widthClass: "w-40" },
      ],
    },
  ],
};

const warehouses: ResourceConfig = {
  key: "warehouses",
  module: "stock",
  endpoint: "/stock/warehouses/",
  titleKey: "bs.res.warehouses",
  singularKey: "bs.res.warehouse",
  icon: Warehouse,
  titleField: "name",
  ordering: "name",
  tree: { parentKey: "parent" },
  columns: [
    { key: "name", labelKey: "fields.warehouse" },
    { key: "company_name", labelKey: "fields.company" },
    { key: "parent_name", labelKey: "bs.fields.parent_warehouse" },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "fields.warehouse", type: "text", required: true },
    { ...companyField, clears: ["parent"] },
    {
      key: "parent",
      labelKey: "bs.fields.parent_warehouse",
      type: "link",
      link: {
        endpoint: "/stock/warehouses/",
        labelField: "name",
        display: (v) => v.parent_name,
        filters: (_row, form) => ({ company: form.company, is_group: true }),
      },
    },
    { key: "is_group", labelKey: "fields.is_group", type: "boolean", defaultValue: false },
  ],
};

/* ---- documents ---- */

const stockEntries: ResourceConfig = {
  key: "stock-entries",
  module: "stock",
  endpoint: "/stock/stock-entries/",
  titleKey: "bs.res.stock_entries",
  singularKey: "bs.res.stock_entry",
  icon: ArrowLeftRight,
  submittable: true,
  hideInSidebar: true,
  ordering: "-posting_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    {
      key: "stock_entry_type",
      labelKey: "bs.fields.stock_entry_type",
    },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    companyField,
    {
      key: "stock_entry_type",
      labelKey: "bs.fields.stock_entry_type",
      type: "select",
      required: true,
      options: ENTRY_TYPE_OPTIONS,
      defaultValue: "Material Receipt",
      helpKey: "bs.fields.stock_entry_type_help",
    },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    {
      key: "work_order",
      labelKey: "mf.fields.work_order",
      type: "link",
      link: workOrderLink,
      hidden: (values) => !isWorkOrderEntry(values),
      helpKey: "mf.help.stock_entry_work_order",
    },
    {
      key: "bom",
      labelKey: "mf.fields.bom",
      type: "link",
      link: bomLink,
      hidden: (values) => !isWorkOrderEntry(values),
    },
    {
      key: "fg_completed_qty",
      labelKey: "mf.fields.fg_completed_qty",
      type: "number",
      readOnly: true,
      hidden: (values) => !isOutputEntry(values),
      helpKey: "mf.help.fg_completed_qty",
    },
    {
      key: "process_loss_qty",
      labelKey: "mf.fields.process_loss_qty",
      type: "number",
      readOnly: true,
      hidden: (values) => !isOutputEntry(values),
    },
  ],
  childTables: [
    {
      key: "items",
      titleKey: "res.items_table",
      newRow: () => ({ qty: "1" }),
      inheritFromPrevious: ["source_warehouse", "target_warehouse"],
      columns: [
        { key: "item", labelKey: "fields.item", type: "link", required: true, link: stockEntryItemLink, widthClass: "min-w-[16rem]" },
        { key: "source_warehouse", labelKey: "bs.fields.source_warehouse", type: "link", link: warehouseLink, widthClass: "min-w-[11rem]",
          // Material Receipt only moves stock IN (target warehouse only).
          hidden: (_row, form) => form.stock_entry_type === "Material Receipt" },
        { key: "target_warehouse", labelKey: "bs.fields.target_warehouse", type: "link", link: warehouseLink, widthClass: "min-w-[11rem]",
          // Material Issue only moves stock OUT (source warehouse only).
          hidden: (_row, form) => form.stock_entry_type === "Material Issue" },
        { key: "qty", labelKey: "fields.qty", type: "number", required: true, widthClass: "w-28" },
        { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "decimal", decimals: 6, widthClass: "w-32" },
        {
          key: "batch",
          labelKey: "bs.fields.batch",
          type: "link",
          link: batchLink,
          widthClass: "min-w-[12rem]",
          // Batch-tracked lines only. A saved line keeps its batch even before
          // the item is re-picked (the server sends `batch` but not the hint).
          hidden: (row) => !row._has_batch_no && !row.batch,
        },
        {
          key: "supplier_batch_no",
          labelKey: "bs.fields.supplier_batch_no",
          type: "text",
          widthClass: "w-36",
          hidden: (row, form) =>
            form.stock_entry_type !== "Material Receipt" || !row._has_batch_no || !!row.batch,
        },
        {
          key: "manufacturing_date",
          labelKey: "bs.fields.manufacturing_date",
          type: "date",
          widthClass: "w-36",
          hidden: (row, form) =>
            form.stock_entry_type !== "Material Receipt" || !row._has_batch_no || !!row.batch,
        },
        {
          key: "expiry_date",
          labelKey: "bs.fields.expiry_date",
          type: "date",
          widthClass: "w-36",
          hidden: (row, form) =>
            form.stock_entry_type !== "Material Receipt" || !row._has_batch_no || !!row.batch,
        },
        {
          key: "is_finished_item",
          labelKey: "mf.fields.is_finished_item",
          type: "boolean",
          widthClass: "w-24",
          hidden: (_row, form) => !isOutputEntry(form),
        },
        {
          key: "is_scrap_item",
          labelKey: "mf.fields.is_scrap_item",
          type: "boolean",
          widthClass: "w-24",
          hidden: (_row, form) => !isOutputEntry(form),
        },
        {
          key: "cost_allocation_per",
          labelKey: "mf.fields.cost_allocation_per",
          type: "decimal",
          widthClass: "w-32",
          hidden: (_row, form) => form.stock_entry_type !== "Repack",
        },
      ],
    },
  ],
};

/* ---- stock reconciliation (opening stock / physical count) ---- */

const RECO_PURPOSE_OPTIONS = [
  { value: "Stock Reconciliation", labelKey: "reco.purpose.reconciliation" },
  { value: "Opening Stock", labelKey: "reco.purpose.opening" },
];

const recoItemLink: LinkDef = {
  ...itemLink,
  fill: (r) => ({ item_code: r.code, item_name: r.name, _has_batch_no: !!r.has_batch_no }),
};
const recoWarehouseLink: LinkDef = { ...warehouseLink, display: (v) => v.warehouse_name };
const recoBatchLink: LinkDef = {
  endpoint: "/stock/batches/",
  labelField: "batch_no",
  display: (v) => v.batch_no,
  filters: (row) => ({ item: row.item }),
};
const recoAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  display: (v) => v.expense_account_name,
  filters: (_row, form) => ({ company: form.company, is_group: false }),
};

const stockReconciliations: ResourceConfig = {
  key: "stock-reconciliations",
  module: "stock",
  endpoint: "/stock/stock-reconciliations/",
  titleKey: "reco.res.stock_reconciliations",
  singularKey: "reco.res.stock_reconciliation",
  icon: ClipboardCheck,
  submittable: true,
  ordering: "-posting_date,-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "purpose", labelKey: "reco.fields.purpose" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "difference_amount", labelKey: "reco.fields.difference_amount", type: "money" },
    { key: "docstatus", labelKey: "status", type: "status" },
  ],
  fields: [
    docNoField,
    companyField,
    {
      key: "purpose",
      labelKey: "reco.fields.purpose",
      type: "select",
      required: true,
      options: RECO_PURPOSE_OPTIONS,
      defaultValue: "Stock Reconciliation",
    },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date", required: true, defaultValue: todayISO },
    {
      key: "expense_account",
      labelKey: "reco.fields.expense_account",
      type: "link",
      link: recoAccountLink,
      helpKey: "reco.help.expense_account",
    },
    {
      key: "difference_amount",
      labelKey: "reco.fields.difference_amount",
      type: "decimal",
      money: true,
      readOnly: true,
    },
  ],
  childTables: [
    {
      key: "items",
      titleKey: "res.items_table",
      inheritFromPrevious: ["warehouse"],
      fetchRows: {
        labelKey: "reco.fetch.button",
        sourceLabelKey: "reco.fetch.warehouse",
        sourceLink: { ...warehouseLink, display: undefined },
        endpoint: "/stock/stock-reconciliations/fetch-items/",
        params: (warehouse, form) => (form.company ? { company: form.company, warehouse } : null),
        mapRow: (r) => ({ ...r, _has_batch_no: !!r.batch }),
        dedupeKeys: ["item", "warehouse", "batch"],
        pickKey: "reco.fetch.pick",
        notReadyKey: "reco.fetch.company_first",
        noneKey: "reco.fetch.none",
        doneKey: "reco.fetch.done",
        failedKey: "reco.fetch.failed",
      },
      columns: [
        { key: "item", labelKey: "fields.item", type: "link", required: true, link: recoItemLink, widthClass: "min-w-[16rem]" },
        { key: "warehouse", labelKey: "fields.warehouse", type: "link", required: true, link: recoWarehouseLink, widthClass: "min-w-[11rem]" },
        {
          key: "batch",
          labelKey: "bs.fields.batch",
          type: "link",
          link: recoBatchLink,
          widthClass: "min-w-[12rem]",
          hidden: (row) => !row._has_batch_no && !row.batch,
        },
        { key: "current_qty", labelKey: "reco.fields.current_qty", type: "number", readOnly: true, widthClass: "w-28" },
        { key: "current_valuation_rate", labelKey: "reco.fields.current_rate", type: "decimal", decimals: 6, readOnly: true, widthClass: "w-32" },
        { key: "qty", labelKey: "reco.fields.qty", type: "number", widthClass: "w-28" },
        { key: "valuation_rate", labelKey: "reco.fields.rate", type: "decimal", decimals: 6, widthClass: "w-32" },
        { key: "quantity_difference", labelKey: "reco.fields.qty_difference", type: "number", readOnly: true, widthClass: "w-28" },
        { key: "amount_difference", labelKey: "reco.fields.amount_difference", type: "decimal", money: true, readOnly: true, widthClass: "w-32" },
      ],
    },
  ],
};

const priceLists: ResourceConfig = {
  key: "price-lists",
  module: "stock",
  endpoint: "/stock/price-lists/",
  titleKey: "pl.res.price_lists",
  singularKey: "pl.res.price_list",
  icon: Tags,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "pl.fields.price_list" },
    { key: "currency", labelKey: "pl.fields.currency" },
    { key: "selling", labelKey: "pl.fields.selling", type: "boolean" },
    { key: "buying", labelKey: "pl.fields.buying", type: "boolean" },
    { key: "enabled", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "pl.fields.price_list", type: "text", required: true },
    { key: "currency", labelKey: "pl.fields.currency", type: "text", defaultValue: "INR", helpKey: "pl.help.currency" },
    { key: "selling", labelKey: "pl.fields.selling", type: "boolean", defaultValue: true },
    { key: "buying", labelKey: "pl.fields.buying", type: "boolean", defaultValue: false },
    { key: "enabled", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

const itemPrices: ResourceConfig = {
  key: "item-prices",
  module: "stock",
  endpoint: "/stock/item-prices/",
  titleKey: "pl.res.item_prices",
  singularKey: "pl.res.item_price",
  icon: Tags,
  ordering: "-created_at",
  columns: [
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "price_list_name", labelKey: "pl.fields.price_list" },
    { key: "uom_name", labelKey: "bs.fields.uom" },
    { key: "rate", labelKey: "fields.rate", type: "money" },
    { key: "valid_from", labelKey: "pl.fields.valid_from", type: "date" },
    { key: "valid_upto", labelKey: "pl.fields.valid_upto", type: "date" },
  ],
  fields: [
    { key: "item", labelKey: "fields.item", type: "link", required: true, link: { ...itemLink, fill: undefined } },
    { key: "price_list", labelKey: "pl.fields.price_list", type: "link", required: true, link: priceListLink },
    {
      key: "uom",
      labelKey: "bs.fields.uom",
      type: "link",
      link: {
        endpoint: "/stock/uoms/",
        labelField: "name",
        display: (v) => v.uom_name,
        fill: (r) => ({ uom_name: r.name }),
      },
      helpKey: "pl.help.uom",
    },
    { key: "rate", labelKey: "fields.rate", type: "decimal", required: true, decimals: 6 },
    { key: "valid_from", labelKey: "pl.fields.valid_from", type: "date" },
    { key: "valid_upto", labelKey: "pl.fields.valid_upto", type: "date" },
  ],
};

const batches: ResourceConfig = {
  key: "batches",
  module: "stock",
  endpoint: "/stock/batches/",
  titleKey: "bs.res.batches",
  singularKey: "bs.res.batch",
  icon: Tags,
  hideInSidebar: true,
  titleField: "batch_no",
  ordering: "-created_at",
  columns: [
    { key: "batch_no", labelKey: "bs.fields.batch_no" },
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "item_variant_label", labelKey: "vr.fields.variant_label" },
    { key: "manufacturing_date", labelKey: "bs.fields.manufacturing_date", type: "date" },
    { key: "expiry_date", labelKey: "bs.fields.expiry_date", type: "date" },
    { key: "available_qty", labelKey: "bs.fields.available_qty", type: "number", render: qtyCell },
    { key: "days_to_expiry", labelKey: "bs.fields.days_to_expiry", type: "number" },
    { key: "is_disabled", labelKey: "bs.fields.is_disabled", type: "boolean" },
  ],
  fields: [
    {
      key: "item",
      labelKey: "fields.item",
      type: "link",
      required: true,
      // Batch-tracked variants / plain items only: a template is never batched.
      link: { ...itemLink, fill: undefined, filters: () => ({ is_active: true, has_batch_no: true, has_variants: false }) },
    },
    { key: "batch_no", labelKey: "bs.fields.batch_no", type: "text", readOnlyAfterCreate: true, helpKey: "bs.help.batch_no" },
    // Dated today unless the lot was made on another day (the server applies the same default).
    { key: "manufacturing_date", labelKey: "bs.fields.manufacturing_date", type: "date", defaultValue: todayISO, helpKey: "vr.help.batch_date" },
    { key: "expiry_date", labelKey: "bs.fields.expiry_date", type: "date", helpKey: "bs.help.expiry_date" },
    { key: "supplier_batch_no", labelKey: "bs.fields.supplier_batch_no", type: "text" },
    { key: "description", labelKey: "bs.fields.description", type: "textarea" },
    { key: "is_disabled", labelKey: "bs.fields.is_disabled", type: "boolean", defaultValue: false },
  ],
};

/* ---- read-only projections / ledgers ---- */

const bins: ResourceConfig = {
  key: "bins",
  module: "stock",
  endpoint: "/stock/bins/",
  titleKey: "bs.res.bins",
  singularKey: "bs.res.bin",
  icon: Layers,
  listOnly: true,
  ordering: "-modified_at",
  columns: [
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "warehouse_name", labelKey: "fields.warehouse" },
    { key: "actual_qty", labelKey: "bs.fields.actual_qty", type: "number", render: qtyCell },
    { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "number", render: rateCell },
    { key: "stock_value", labelKey: "bs.fields.stock_value", type: "money" },
  ],
  fields: [],
};

const stockLedger: ResourceConfig = {
  key: "stock-ledger",
  module: "stock",
  endpoint: "/stock/sle/",
  titleKey: "bs.res.stock_ledger",
  singularKey: "bs.res.stock_ledger_entry",
  icon: ScrollText,
  listOnly: true,
  ordering: "-posting_date,-posting_time,-created_at",
  columns: [
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "warehouse_name", labelKey: "fields.warehouse" },
    { key: "qty", labelKey: "fields.qty", type: "number", render: qtyCell },
    { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "number", render: rateCell },
    { key: "stock_value_difference", labelKey: "bs.fields.stock_value_difference", type: "money" },
    { key: "voucher_type", labelKey: "bs.fields.voucher_type" },
    { key: "voucher_no", labelKey: "bs.fields.voucher_no" },
    { key: "is_cancelled", labelKey: "bs.fields.is_cancelled", type: "boolean" },
  ],
  fields: [],
};

export const stockResources: ResourceConfig[] = [
  items,
  itemAttributes,
  itemGroups,
  uoms,
  warehouses,
  batches,
  priceLists,
  itemPrices,
  stockEntries,
  stockReconciliations,
  bins,
  stockLedger,
];

/* ---- reports ---- */

const stockBalance: ReportConfig = {
  key: "stock-balance",
  module: "stock",
  endpoint: "/stock/reports/stock-balance/",
  titleKey: "bs.res.stock_balance",
  icon: BarChart3,
  filters: [
    { key: "item", labelKey: "fields.item", type: "link", link: { ...itemLink, fill: undefined } },
    { key: "warehouse", labelKey: "fields.warehouse", type: "link", link: warehouseLink },
  ],
  columns: [
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "warehouse_name", labelKey: "fields.warehouse" },
    { key: "actual_qty", labelKey: "bs.fields.actual_qty", type: "number" },
    { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "money" },
    { key: "stock_value", labelKey: "bs.fields.stock_value", type: "money" },
  ],
  totals: { actual_qty: "total_actual_qty", stock_value: "total_stock_value" },
};

const batchWiseBalance: ReportConfig = {
  key: "batch-wise-balance",
  module: "stock",
  endpoint: "/stock/reports/batch-wise-balance/",
  titleKey: "bs.res.batch_wise_balance",
  icon: Tags,
  hideInSidebar: true,
  filters: [
    { key: "item", labelKey: "fields.item", type: "link", link: { ...itemLink, fill: undefined } },
    { key: "warehouse", labelKey: "fields.warehouse", type: "link", link: warehouseLink },
    { key: "batch", labelKey: "bs.fields.batch", type: "link", link: { endpoint: "/stock/batches/", labelField: "batch_no" } },
    { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
    { key: "as_of", labelKey: "bs.fields.as_of", type: "date" },
    { key: "include_zero", labelKey: "bs.fields.include_zero", type: "boolean", defaultValue: false },
  ],
  columns: [
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "warehouse_name", labelKey: "fields.warehouse" },
    { key: "batch_no", labelKey: "bs.fields.batch_no" },
    { key: "manufacturing_date", labelKey: "bs.fields.manufacturing_date", type: "date" },
    { key: "expiry_date", labelKey: "bs.fields.expiry_date", type: "date" },
    { key: "days_to_expiry", labelKey: "bs.fields.days_to_expiry", type: "number" },
    { key: "balance_qty", labelKey: "bs.fields.balance_qty", type: "number", render: qtyCell },
    { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "number", render: rateCell },
    { key: "stock_value", labelKey: "bs.fields.stock_value", type: "money" },
  ],
  totals: { balance_qty: "total_balance_qty", stock_value: "total_stock_value" },
};

/** Expiry status badge (Expired / Expiring), server-classified. */
const expiryBadge = (value: any) => {
  const status = value ? String(value) : "";
  const cls =
    status === "Expired"
      ? "bg-rose-950/60 text-rose-300 border-rose-800/60"
      : "bg-amber-950/40 text-amber-300 border-amber-800/50";
  return createElement(
    "span",
    { className: `inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap ${cls}` },
    status
  );
};

const expiryReport: ReportConfig = {
  key: "batch-expiry",
  module: "stock",
  endpoint: "/stock/reports/expiry/",
  titleKey: "bs.res.expiry_report",
  icon: CalendarClock,
  hideInSidebar: true,
  filters: [
    { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
    { key: "warehouse", labelKey: "fields.warehouse", type: "link", link: warehouseLink },
    { key: "within_days", labelKey: "bs.fields.within_days", type: "number", defaultValue: 60 },
    { key: "include_expired", labelKey: "bs.fields.include_expired", type: "boolean", defaultValue: true },
  ],
  columns: [
    { key: "batch_no", labelKey: "bs.fields.batch_no" },
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "warehouse_name", labelKey: "fields.warehouse" },
    { key: "expiry_date", labelKey: "bs.fields.expiry_date", type: "date" },
    { key: "days_to_expiry", labelKey: "bs.fields.days_to_expiry", type: "number" },
    { key: "status", labelKey: "bs.fields.expiry_status", render: expiryBadge },
    { key: "balance_qty", labelKey: "bs.fields.balance_qty", type: "number", render: qtyCell },
    { key: "stock_value", labelKey: "bs.fields.stock_value", type: "money" },
  ],
  totals: { balance_qty: "total_balance_qty", stock_value: "total_stock_value" },
};


/** Stage status badge (Critical / Normal). */
const stageCriticalBadge = (value: any, row: any) => {
  const isCrit = row?.is_critical || value === "Critical" || value === true;
  const cls = isCrit
    ? "bg-rose-950/60 text-rose-300 border-rose-800/60 font-semibold"
    : "bg-emerald-950/40 text-emerald-400 border-emerald-800/50";
  const label = isCrit ? "Critical" : "Normal";
  return createElement(
    "span",
    { className: `inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap ${cls}` },
    label
  );
};

const stockStages: ReportConfig = {
  key: "stages",
  module: "stock",
  endpoint: "/stock/reports/stages/",
  titleKey: "bs.res.stock_stages",
  icon: Layers,
  filters: [
    { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
  ],
  columns: [
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "uom", labelKey: "fields.uom" },
    { key: "unprocessed_qty", labelKey: "bs.fields.unprocessed_qty", type: "number", render: qtyCell },
    { key: "in_process_qty", labelKey: "bs.fields.in_process_qty", type: "number", render: qtyCell },
    { key: "ready_qty", labelKey: "bs.fields.ready_qty", type: "number", render: qtyCell },
    { key: "total_qty", labelKey: "bs.fields.total_qty", type: "number", render: qtyCell },
    { key: "critical_stock", labelKey: "bs.fields.critical_stock", type: "number", render: qtyCell },
    { key: "status", labelKey: "fields.status", render: stageCriticalBadge },
  ],
  totals: {
    unprocessed_qty: "total_unprocessed_qty",
    in_process_qty: "total_in_process_qty",
    ready_qty: "total_ready_qty",
    total_qty: "total_qty",
  },
};

export const stockReports: ReportConfig[] = [stockBalance, batchWiseBalance, expiryReport, stockStages];
