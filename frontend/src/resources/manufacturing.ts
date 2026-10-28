import { BarChart3, ClipboardList, Factory, Pause, Play, Wrench } from "lucide-react";
import type { LinkDef, ResourceConfig, SelectOption } from "@/types";
import type { ReportConfig } from "@/types/report";
import { companyField, companyLink, docNoField, itemLink, warehouseLink } from "./selling";
import { bomLink, workOrderLink } from "./manufacturingLinks";

/* ---- reusable links ---- */

/** Item picker for BOM rows: the rate is derived server-side, never typed. */
const bomItemLink: LinkDef = { ...itemLink, fill: undefined };

const rateBasisOptions: SelectOption[] = [
  { value: "Valuation Rate", labelKey: "mf.opt.valuation_rate" },
  { value: "Standard Rate", labelKey: "mf.opt.standard_rate" },
];

const bomComponentColumns = [
  { key: "item", labelKey: "fields.item", type: "link" as const, required: true, link: bomItemLink, widthClass: "min-w-[16rem]" },
  { key: "qty", labelKey: "fields.qty", type: "number" as const, required: true, widthClass: "w-28" },
  { key: "bom_no", labelKey: "mf.fields.sub_bom", type: "link" as const, link: bomLink, widthClass: "min-w-[14rem]" },
  { key: "rate", labelKey: "fields.rate", type: "decimal" as const, money: true, readOnly: true, widthClass: "w-32" },
  { key: "amount", labelKey: "fields.amount", type: "decimal" as const, money: true, readOnly: true, widthClass: "w-36" },
  { key: "source", labelKey: "mf.fields.cost_source", type: "text" as const, readOnly: true, widthClass: "w-28" },
];

/* ---- BOM ---- */

const boms: ResourceConfig = {
  key: "boms",
  module: "manufacturing",
  endpoint: "/manufacturing/boms/",
  titleKey: "mf.res.boms",
  singularKey: "mf.res.bom",
  icon: ClipboardList,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "item_code", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "quantity", labelKey: "mf.fields.quantity", type: "number" },
    { key: "process_loss_percentage", labelKey: "mf.fields.process_loss_percentage", type: "number" },
    { key: "good_qty", labelKey: "mf.fields.good_qty", type: "number" },
    { key: "unit_cost", labelKey: "mf.fields.unit_cost", type: "money" },
    { key: "is_default", labelKey: "mf.fields.is_default", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "mf.help.name" },
    companyField,
    { key: "item", labelKey: "mf.fields.product_item", type: "link", required: true, link: bomItemLink },
    { key: "quantity", labelKey: "mf.fields.quantity", type: "number", required: true, defaultValue: "1", helpKey: "mf.help.quantity" },
    { key: "process_loss_percentage", labelKey: "mf.fields.process_loss_percentage", type: "number", defaultValue: 0, helpKey: "mf.help.process_loss" },
    { key: "rate_basis", labelKey: "mf.fields.rate_basis", type: "select", options: rateBasisOptions, defaultValue: "Valuation Rate" },
    { key: "is_default", labelKey: "mf.fields.is_default", type: "boolean", defaultValue: false },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
    { key: "description", labelKey: "bs.fields.description", type: "textarea" },
    { key: "raw_material_cost", labelKey: "mf.fields.raw_material_cost", type: "decimal", money: true, readOnly: true },
    { key: "scrap_value", labelKey: "mf.fields.scrap_value", type: "decimal", money: true, readOnly: true },
    { key: "good_qty", labelKey: "mf.fields.good_qty", type: "number", readOnly: true },
    { key: "unit_cost", labelKey: "mf.fields.unit_cost", type: "decimal", money: true, readOnly: true },
  ],
  childTables: [
    {
      key: "items",
      titleKey: "mf.res.components",
      newRow: () => ({ qty: "1" }),
      columns: bomComponentColumns,
    },
    {
      key: "scrap_items",
      titleKey: "mf.res.scrap_items",
      newRow: () => ({ qty: "1", rate: "0" }),
      columns: [
        { key: "item", labelKey: "fields.item", type: "link", required: true, link: bomItemLink, widthClass: "min-w-[16rem]" },
        { key: "qty", labelKey: "fields.qty", type: "number", required: true, widthClass: "w-28" },
        { key: "rate", labelKey: "fields.rate", type: "decimal", money: true, widthClass: "w-32" },
      ],
    },
  ],
};

/* ---- Work Order ---- */

const workOrders: ResourceConfig = {
  key: "work-orders",
  module: "manufacturing",
  endpoint: "/manufacturing/work-orders/",
  titleKey: "mf.res.work_orders",
  singularKey: "mf.res.work_order",
  icon: Factory,
  submittable: true,
  titleField: "name",
  ordering: "-created_at",
  columns: [
    { key: "name", labelKey: "fields.docno" },
    { key: "production_item_code", labelKey: "bs.fields.item_code" },
    { key: "bom_name", labelKey: "mf.fields.bom" },
    { key: "qty", labelKey: "mf.fields.planned_qty", type: "number" },
    { key: "produced_qty", labelKey: "mf.fields.produced_qty", type: "number" },
    { key: "process_loss_qty", labelKey: "mf.fields.process_loss_qty", type: "number" },
    { key: "status", labelKey: "mf.fields.status" },
    { key: "planned_start_date", labelKey: "mf.fields.planned_start_date", type: "date" },
  ],
  fields: [
    { key: "name", labelKey: "fields.docno", type: "text", readOnlyAfterCreate: true, helpKey: "mf.help.name" },
    companyField,
    { key: "production_item", labelKey: "mf.fields.product_item", type: "link", required: true, link: bomItemLink, clears: ["bom"] },
    {
      key: "bom",
      labelKey: "mf.fields.bom",
      type: "link",
      required: true,
      link: {
        ...bomLink,
        fill: (r) => ({
          production_item: r.item,
          production_item_code: r.item_code,
          production_item_name: r.item_name,
        }),
      },
      helpKey: "mf.help.bom",
    },
    { key: "qty", labelKey: "mf.fields.planned_qty", type: "number", required: true, defaultValue: "1", helpKey: "mf.help.planned_qty" },
    { key: "source_warehouse", labelKey: "bs.fields.source_warehouse", type: "link", required: true, link: warehouseLink },
    { key: "wip_warehouse", labelKey: "mf.fields.wip_warehouse", type: "link", link: warehouseLink, helpKey: "mf.help.wip" },
    { key: "fg_warehouse", labelKey: "mf.fields.fg_warehouse", type: "link", required: true, link: warehouseLink },
    { key: "scrap_warehouse", labelKey: "mf.fields.scrap_warehouse", type: "link", link: warehouseLink },
    { key: "skip_transfer", labelKey: "mf.fields.skip_transfer", type: "boolean", defaultValue: false, helpKey: "mf.help.skip_transfer" },
    { key: "planned_start_date", labelKey: "mf.fields.planned_start_date", type: "date" },
    { key: "expected_end_date", labelKey: "mf.fields.expected_end_date", type: "date" },
    { key: "status", labelKey: "mf.fields.status", type: "text", readOnly: true },
    { key: "produced_qty", labelKey: "mf.fields.produced_qty", type: "number", readOnly: true },
    { key: "process_loss_qty", labelKey: "mf.fields.process_loss_qty", type: "number", readOnly: true },
  ],
  childTables: [
    {
      key: "required_items",
      titleKey: "mf.res.required_items",
      readOnly: true,
      columns: [
        { key: "item_code", labelKey: "bs.fields.item_code", type: "text" },
        { key: "item_name", labelKey: "bs.fields.item_name", type: "text" },
        { key: "source_warehouse_name", labelKey: "bs.fields.source_warehouse", type: "text" },
        { key: "required_qty", labelKey: "mf.fields.required_qty", type: "number" },
        { key: "transferred_qty", labelKey: "mf.fields.transferred_qty", type: "number" },
        { key: "consumed_qty", labelKey: "mf.fields.consumed_qty", type: "number" },
      ],
    },
  ],
  actions: [
    {
      key: "stop",
      labelKey: "mf.actions.stop",
      icon: Pause,
      showWhen: (doc) => doc.docstatus === 1 && !doc.stopped && doc.status !== "Completed",
      kind: "request",
      url: (doc) => `/manufacturing/work-orders/${doc.id}/stop/`,
    },
    {
      key: "resume",
      labelKey: "mf.actions.resume",
      icon: Play,
      showWhen: (doc) => doc.docstatus === 1 && !!doc.stopped,
      kind: "request",
      url: (doc) => `/manufacturing/work-orders/${doc.id}/resume/`,
    },
    {
      key: "material-transfer",
      labelKey: "mf.actions.make_transfer",
      icon: Wrench,
      showWhen: (doc) => doc.docstatus === 1 && Number(doc.produced_qty) < Number(doc.qty),
      kind: "request",
      url: (doc) => `/manufacturing/work-orders/${doc.id}/make-stock-entry/`,
      body: () => ({ type: "material_transfer" }),
      resultTo: (result) => `/stock/stock-entries/${result.id}`,
    },
    {
      key: "manufacture",
      labelKey: "mf.actions.make_manufacture",
      icon: Factory,
      showWhen: (doc) => doc.docstatus === 1 && Number(doc.produced_qty) < Number(doc.qty),
      kind: "request",
      url: (doc) => `/manufacturing/work-orders/${doc.id}/make-stock-entry/`,
      body: () => ({ type: "manufacture" }),
      resultTo: (result) => `/stock/stock-entries/${result.id}`,
    },
  ],
};

export const manufacturingResources: ResourceConfig[] = [boms, workOrders];

/* ---- reports ---- */

const productionYield: ReportConfig = {
  key: "production-yield",
  module: "manufacturing",
  endpoint: "/manufacturing/reports/production-yield/",
  titleKey: "mf.res.production_yield",
  icon: Factory,
  filters: [
    { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
    { key: "from_date", labelKey: "mf.fields.from_date", type: "date" },
    { key: "to_date", labelKey: "mf.fields.to_date", type: "date" },
    { key: "work_order", labelKey: "mf.fields.work_order", type: "link", link: workOrderLink },
    { key: "bom", labelKey: "mf.fields.bom", type: "link", link: bomLink },
  ],
  columns: [
    { key: "stock_entry", labelKey: "mf.fields.stock_entry" },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "work_order", labelKey: "mf.fields.work_order" },
    { key: "bom", labelKey: "mf.fields.bom" },
    { key: "item", labelKey: "bs.fields.item_code" },
    { key: "entry_type", labelKey: "mf.fields.entry_type" },
    { key: "planned_qty", labelKey: "mf.fields.planned_qty", type: "number" },
    { key: "produced_qty", labelKey: "mf.fields.produced_qty", type: "number" },
    { key: "process_loss_qty", labelKey: "mf.fields.process_loss_qty", type: "number" },
    { key: "loss_percent", labelKey: "mf.fields.loss_percent", type: "number" },
    { key: "output_value", labelKey: "mf.fields.output_value", type: "money" },
    { key: "unit_cost", labelKey: "mf.fields.unit_cost", type: "money" },
    { key: "scrap_qty", labelKey: "mf.fields.scrap_qty", type: "number" },
  ],
  totals: {
    produced_qty: "total_produced_qty",
    process_loss_qty: "total_process_loss_qty",
    loss_percent: "total_loss_percent",
  },
};

const bomCost: ReportConfig = {
  key: "bom-cost",
  module: "manufacturing",
  endpoint: "/manufacturing/reports/bom-cost/",
  titleKey: "mf.res.bom_cost",
  icon: BarChart3,
  filters: [
    { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
    { key: "item", labelKey: "fields.item", type: "link", link: { ...itemLink, fill: undefined } },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
  columns: [
    { key: "bom", labelKey: "mf.fields.bom" },
    { key: "item", labelKey: "bs.fields.item_code" },
    { key: "item_name", labelKey: "bs.fields.item_name" },
    { key: "quantity", labelKey: "mf.fields.quantity", type: "number" },
    { key: "process_loss_percentage", labelKey: "mf.fields.process_loss_percentage", type: "number" },
    { key: "good_qty", labelKey: "mf.fields.good_qty", type: "number" },
    { key: "rate_basis", labelKey: "mf.fields.rate_basis" },
    { key: "raw_material_cost", labelKey: "mf.fields.raw_material_cost", type: "money" },
    { key: "scrap_value", labelKey: "mf.fields.scrap_value", type: "money" },
    { key: "unit_cost", labelKey: "mf.fields.unit_cost", type: "money" },
    { key: "last_actual_unit_cost", labelKey: "mf.fields.last_actual_unit_cost", type: "money" },
    { key: "is_default", labelKey: "mf.fields.is_default", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
};

export const manufacturingReports: ReportConfig[] = [productionYield, bomCost];
