import { ArrowLeftRight, BarChart3, FolderTree, Layers, Package, Ruler, ScrollText, Warehouse } from "lucide-react";
import { roundDec } from "@/lib/decimal";
import { todayISO } from "@/lib/format";
import type { LinkDef, ResourceConfig } from "@/types";
import type { ReportConfig } from "@/types/report";
import { companyField, docNoField, itemLink, warehouseLink } from "./selling";

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

/** Item picker for stock-entry lines (no `rate`; seeds the valuation rate from the standard rate). */
const stockEntryItemLink: LinkDef = {
  ...itemLink,
  fill: (r, row) => ({
    item_code: r.code,
    item_name: r.name,
    valuation_rate: roundDec(r.standard_rate, 6) ?? row.valuation_rate ?? "",
    qty: row.qty ? row.qty : "1",
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
];

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
  columns: [{ key: "name", labelKey: "bs.fields.uom" }],
  fields: [{ key: "name", labelKey: "bs.fields.uom", type: "text", required: true }],
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
    { key: "valuation_method", labelKey: "bs.fields.valuation_method" },
    { key: "standard_rate", labelKey: "bs.fields.standard_rate", type: "number", render: rateCell },
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
    { key: "is_stock_item", labelKey: "bs.fields.is_stock_item", type: "boolean", defaultValue: true },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
    { key: "description", labelKey: "bs.fields.description", type: "textarea" },
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
  ],
  childTables: [
    {
      key: "items",
      titleKey: "res.items_table",
      newRow: () => ({ qty: "1" }),
      inheritFromPrevious: ["source_warehouse", "target_warehouse"],
      columns: [
        { key: "item", labelKey: "fields.item", type: "link", required: true, link: stockEntryItemLink, widthClass: "min-w-[16rem]" },
        { key: "source_warehouse", labelKey: "bs.fields.source_warehouse", type: "link", link: warehouseLink, widthClass: "min-w-[11rem]" },
        { key: "target_warehouse", labelKey: "bs.fields.target_warehouse", type: "link", link: warehouseLink, widthClass: "min-w-[11rem]" },
        { key: "qty", labelKey: "fields.qty", type: "number", required: true, widthClass: "w-28" },
        { key: "valuation_rate", labelKey: "bs.fields.valuation_rate", type: "decimal", decimals: 6, widthClass: "w-32" },
      ],
    },
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
  itemGroups,
  uoms,
  warehouses,
  stockEntries,
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

export const stockReports: ReportConfig[] = [stockBalance];
