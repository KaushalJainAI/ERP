import { FileText, Percent } from "lucide-react";
import type { LinkDef, ResourceConfig } from "@/types";
import { companyField } from "./selling";
import { ADD_DEDUCT, GST_COMPONENTS, GST_TREATMENTS, SUPPLY_TYPES, TEMPLATE_TYPES, gstOptionCell } from "./gst";

/**
 * GST masters (sidebar module "gst"): Item Tax Templates (global GST rate sets,
 * rows keyed by component) and Sales / Purchase Tax Templates (per company, rows
 * keyed by tax account). Both are plain CRUD with a nested `rows` table.
 */

/** Tax account of the template's company (a sales template needs Output accounts, a purchase one Input). */
const templateAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  label: (r) => r.name,
  subLabel: (r) => [r.gst_direction, r.gst_component].filter(Boolean).join(" ") || undefined,
  display: (row) => row.account_name || undefined,
  filters: (_row, form) => ({ company: form.company, account_type: "Tax", is_group: false, is_active: true }),
  fill: (r, row) => ({ account_name: r.name, description: row.description || r.name }),
};

const itemTaxTemplates: ResourceConfig = {
  key: "item-tax-templates",
  module: "gst",
  endpoint: "/accounts/item-tax-templates/",
  titleKey: "gst.res.item_tax_templates",
  singularKey: "gst.res.item_tax_template",
  icon: Percent,
  titleField: "name",
  ordering: "name",
  columns: [
    { key: "name", labelKey: "gst.fields.template_name" },
    { key: "gst_treatment", labelKey: "gst.fields.gst_treatment", render: gstOptionCell },
    { key: "is_legacy", labelKey: "gst.fields.is_legacy", type: "boolean" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean" },
  ],
  fields: [
    { key: "name", labelKey: "gst.fields.template_name", type: "text", required: true },
    {
      key: "gst_treatment",
      labelKey: "gst.fields.gst_treatment",
      type: "select",
      required: true,
      options: GST_TREATMENTS,
      defaultValue: "Taxable",
    },
    { key: "is_legacy", labelKey: "gst.fields.is_legacy", type: "boolean", defaultValue: false, helpKey: "gst.help.is_legacy" },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
  childTables: [
    {
      key: "rows",
      titleKey: "gst.res.rows",
      newRow: () => ({ gst_component: "CGST" }),
      columns: [
        { key: "gst_component", labelKey: "gst.fields.gst_component", type: "select", required: true, options: GST_COMPONENTS, widthClass: "w-40" },
        { key: "tax_rate", labelKey: "gst.fields.rate_pct", type: "decimal", decimals: 3, required: true, widthClass: "w-40" },
      ],
    },
  ],
};

const taxTemplates: ResourceConfig = {
  key: "tax-templates",
  module: "gst",
  endpoint: "/accounts/tax-templates/",
  titleKey: "gst.res.tax_templates",
  singularKey: "gst.res.tax_template",
  icon: FileText,
  titleField: "title",
  ordering: "title",
  columns: [
    { key: "title", labelKey: "gst.fields.title" },
    { key: "company_name", labelKey: "fields.company" },
    { key: "template_type", labelKey: "gst.fields.template_type", render: gstOptionCell },
    { key: "gst_supply_type", labelKey: "gst.fields.gst_supply_type", render: gstOptionCell },
    { key: "is_reverse_charge", labelKey: "gst.fields.is_reverse_charge", type: "boolean" },
    { key: "is_default", labelKey: "gst.fields.is_default", type: "boolean" },
    { key: "disabled", labelKey: "gst.fields.disabled", type: "boolean" },
  ],
  fields: [
    { key: "title", labelKey: "gst.fields.title", type: "text", required: true },
    companyField,
    { key: "template_type", labelKey: "gst.fields.template_type", type: "select", required: true, options: TEMPLATE_TYPES, defaultValue: "Sales" },
    { key: "gst_supply_type", labelKey: "gst.fields.gst_supply_type", type: "select", options: SUPPLY_TYPES, helpKey: "gst.help.supply_type" },
    {
      key: "is_reverse_charge",
      labelKey: "gst.fields.is_reverse_charge",
      type: "boolean",
      defaultValue: false,
      // Reverse charge applies to purchase templates only.
      hidden: (v) => v.template_type !== "Purchase",
    },
    { key: "is_default", labelKey: "gst.fields.is_default", type: "boolean", defaultValue: false, helpKey: "gst.help.is_default" },
    { key: "disabled", labelKey: "gst.fields.disabled", type: "boolean", defaultValue: false },
  ],
  childTables: [
    {
      key: "rows",
      titleKey: "gst.res.rows",
      newRow: () => ({ add_deduct_tax: "Add", rate: "0" }),
      columns: [
        { key: "account", labelKey: "acc.fields.account", type: "link", required: true, link: templateAccountLink, widthClass: "min-w-[14rem]" },
        { key: "description", labelKey: "bs.fields.description", type: "text", widthClass: "min-w-[10rem]" },
        { key: "rate", labelKey: "gst.fields.rate_pct", type: "decimal", decimals: 3, required: true, widthClass: "w-28" },
        { key: "add_deduct_tax", labelKey: "gst.fields.add_deduct", type: "select", required: true, options: ADD_DEDUCT, widthClass: "w-28" },
      ],
    },
  ],
};

export const gstResources: ResourceConfig[] = [taxTemplates, itemTaxTemplates];
