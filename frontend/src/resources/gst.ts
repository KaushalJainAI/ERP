import { createElement } from "react";
import i18n from "i18next";
import { GstSummary } from "@/components/gst/GstSummary";
import { fetchStates } from "@/api/lookups";
import { isDecimal, mulDec, sumDec } from "@/lib/decimal";
import type {
  ChildColumnDef,
  ChildTableDef,
  FieldDef,
  LinkDef,
  PreviewDef,
  ResourceConfig,
  SelectOption,
} from "@/types";

/**
 * GST building blocks shared by the master configs (customers, suppliers, items,
 * UOMs, accounts) and the four taxable documents (SO / SI / PO / PI).
 *
 * The server is the single source of truth for every tax figure: the document
 * forms call the dry-run endpoint (`PreviewDef`, see types/index.ts) instead of
 * re-implementing the tax engine here. Nothing in this file does money math except
 * the instant net-total estimate (lib/decimal.ts).
 *
 * This module must not import selling.ts / buying.ts (they import it).
 */

/* ---- option lists (values are the backend's TextChoices) ---- */

const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "_");
const opt = (value: string): SelectOption => ({ value, labelKey: `gst.opt.${slug(value)}` });

export const GST_CATEGORIES = [
  "Registered Regular",
  "Registered Composition",
  "Unregistered",
  "Consumer",
  "SEZ",
  "Overseas",
].map(opt);
export const GST_TREATMENTS = ["Taxable", "Nil-Rated", "Exempted", "Non-GST"].map(opt);
export const GST_COMPONENTS = ["CGST", "SGST", "IGST", "CESS"].map(opt);
export const GST_DIRECTIONS = ["Input", "Output", "Reverse Charge"].map(opt);
export const TEMPLATE_TYPES = ["Sales", "Purchase"].map(opt);
export const SUPPLY_TYPES = ["Intra-State", "Inter-State"].map(opt);
export const ADD_DEDUCT = ["Add", "Deduct"].map(opt);

/** Translate a raw TextChoices value coming from the API (list cells). */
export const gstOptionCell = (value: any): string =>
  value ? i18n.t(`gst.opt.${slug(String(value))}`, { defaultValue: String(value) }) : "";

/** supply.kind from the server -> the template `gst_supply_type` it needs. */
const KIND_TO_SUPPLY_TYPE: Record<string, string> = { INTRA: "Intra-State", INTER: "Inter-State" };

/* ---- states ---- */

/** Link to /core/states/ (stored value = the 2-digit state code). `nameKey` is the serializer's name property. */
export const stateLink = (nameKey: string): LinkDef => ({
  fetcher: fetchStates,
  valueField: "code",
  label: (r) => `${r.code} - ${r.name}`,
  display: (v) => v[nameKey] || undefined,
  fill: (r) => ({ [nameKey]: r.name }),
});

/* ---- party (Customer / Supplier) GST fields ---- */

const CHECK_FILLS_CATEGORY = ["", "Unregistered", "Consumer"];

/** GSTIN input: validated against the server on blur; a valid GSTIN fills the state (and a registered category). */
export const gstinField: FieldDef = {
  key: "gstin",
  labelKey: "fields.gstin",
  type: "text",
  helpKey: "gst.help.gstin",
  check: {
    endpoint: "/gst/validate-gstin/",
    param: "gstin",
    okKey: "gst.gstin_valid",
    interpret: (r, values) => {
      if (!r.valid) return { ok: false, message: r.reason };
      const fill: Record<string, any> = {
        gstin: String(values.gstin ?? "").trim().toUpperCase(),
        state: r.state_code,
        state_name: r.state_name,
      };
      if (CHECK_FILLS_CATEGORY.includes(values.gst_category ?? "")) fill.gst_category = "Registered Regular";
      return { ok: true, fill };
    },
  },
};

/** gstin + gst_category + state, in the order they appear on the Customer / Supplier form. */
export const partyGstFields = (): FieldDef[] => [
  gstinField,
  {
    key: "gst_category",
    labelKey: "gst.fields.gst_category",
    type: "select",
    required: true,
    options: GST_CATEGORIES,
    defaultValue: "Unregistered",
  },
  {
    key: "state",
    labelKey: "gst.fields.state",
    type: "link",
    link: stateLink("state_name"),
    helpKey: "gst.help.party_state",
  },
];

/* ---- item / UOM / account fields ---- */

export const itemTaxTemplateLink: LinkDef = {
  endpoint: "/accounts/item-tax-templates/",
  labelField: "name",
  display: (v) => v.item_tax_template_name || undefined,
  filters: () => ({ is_active: true }),
  fill: (r) => ({ item_tax_template_name: r.name }),
};

export const itemGstFields = (): FieldDef[] => [
  { key: "gst_hsn_code", labelKey: "gst.fields.hsn_code", type: "text", helpKey: "gst.help.hsn" },
  { key: "item_tax_template", labelKey: "gst.fields.item_tax_template", type: "link", link: itemTaxTemplateLink },
  { key: "is_nil_exempt", labelKey: "gst.fields.is_nil_exempt", type: "boolean", defaultValue: false },
  { key: "is_non_gst", labelKey: "gst.fields.is_non_gst", type: "boolean", defaultValue: false },
];

// Only Tax-type accounts can carry a GST tag.
const notTaxAccount = (v: Record<string, any>) => v.account_type !== "Tax";

export const accountGstFields = (): FieldDef[] => [
  { key: "gst_direction", labelKey: "gst.fields.gst_direction", type: "select", options: GST_DIRECTIONS, helpKey: "gst.help.account_tag", hidden: notTaxAccount },
  { key: "gst_component", labelKey: "gst.fields.gst_component", type: "select", options: GST_COMPONENTS, hidden: notTaxAccount },
];

/* ---- taxable documents (SO / SI / PO / PI) ---- */

export type Direction = "Sales" | "Purchase";

const SPECIAL_CATEGORIES = ["SEZ", "Overseas"];
const isZero = (v: unknown) => v === undefined || v === null || v === "" || /^-?0+(\.0+)?$/.test(String(v));
const rowsOf = (v: Record<string, any>, key: string): Record<string, any>[] => (v[key] as Record<string, any>[]) ?? [];

/** Tax template picker: this company, this direction, enabled, and (once known) matching the supply kind. */
export const taxTemplateLink = (direction: Direction): LinkDef => ({
  endpoint: "/accounts/tax-templates/",
  label: (r) => r.title,
  subLabel: (r) =>
    [r.gst_supply_type, r.is_reverse_charge ? i18n.t("gst.fields.is_reverse_charge") : ""].filter(Boolean).join(" - ") ||
    undefined,
  display: (v) => v.taxes_and_charges_name || undefined,
  filters: (values) => ({
    company: values.company,
    template_type: direction,
    disabled: false,
    ...(direction === "Purchase" ? { is_reverse_charge: !!values.is_reverse_charge } : {}),
    ...(KIND_TO_SUPPLY_TYPE[values._supply_kind] ? { gst_supply_type: KIND_TO_SUPPLY_TYPE[values._supply_kind] } : {}),
  }),
  fill: (r) => ({ taxes_and_charges_name: r.title }),
});

/**
 * Header fields every taxable document gets: place of supply, tax template, the
 * export / reverse-charge flag, and the read-only snapshot (category, GSTINs).
 */
export const taxHeaderFields = (direction: Direction): FieldDef[] => [
  {
    key: "place_of_supply",
    labelKey: "gst.fields.place_of_supply",
    type: "link",
    link: stateLink("place_of_supply_name"),
    helpKey: "gst.help.place_of_supply",
    // A different place of supply usually flips intra/inter-state: fall back to the default template.
    clears: ["taxes_and_charges"],
    advanced: true,
  },
  {
    key: "taxes_and_charges",
    labelKey: "gst.fields.taxes_and_charges",
    type: "link",
    link: taxTemplateLink(direction),
    helpKey: "gst.help.template",
    advanced: true,
  },
  direction === "Sales"
    ? {
        key: "is_export_with_gst",
        labelKey: "gst.fields.is_export_with_gst",
        type: "boolean",
        defaultValue: false,
        clears: ["taxes_and_charges"],
        // Only meaningful for SEZ / overseas customers (the server rejects it otherwise).
        hidden: (v) => !SPECIAL_CATEGORIES.includes(v.gst_category),
      }
    : {
        key: "is_reverse_charge",
        labelKey: "gst.fields.is_reverse_charge",
        type: "boolean",
        defaultValue: false,
        // Reverse-charge purchases use a different template set.
        clears: ["taxes_and_charges"],
        advanced: true,
      },
  {
    key: "gst_category",
    labelKey: "gst.fields.gst_category",
    type: "text",
    readOnly: true,
    hidden: (v) => !v.gst_category,
  },
  {
    key: "party_gstin",
    labelKey: "gst.fields.party_gstin",
    type: "text",
    readOnly: true,
    hidden: (v) => !v.party_gstin,
  },
];

/**
 * Totals block (replaces `grandTotal()`): net total, taxes, grand total (tax
 * inclusive), rounding. Values come from the server (saved, or the dry-run
 * preview while editing); only the net total is also estimated instantly from the lines.
 */
export const taxTotalFields = (itemsKey = "items"): FieldDef[] => {
  const fromForm = (key: string) => (v: Record<string, any>) => (v[key] === "" || v[key] === undefined ? null : String(v[key]));
  const base = { type: "decimal", money: true, readOnly: true } as const;
  return [
    {
      ...base,
      key: "net_total",
      labelKey: "gst.fields.net_total",
      preview: (v) =>
        sumDec(
          rowsOf(v, itemsKey).map((r) => mulDec(r.qty, r.rate, 2)),
          2
        ),
    },
    { ...base, key: "total_taxes_and_charges", labelKey: "gst.fields.total_taxes", preview: fromForm("total_taxes_and_charges") },
    { ...base, key: "grand_total", labelKey: "fields.grand_total", preview: fromForm("grand_total") },
    {
      ...base,
      key: "rounding_adjustment",
      labelKey: "gst.fields.rounding_adjustment",
      preview: fromForm("rounding_adjustment"),
      hidden: (v) => isZero(v.rounding_adjustment),
    },
    {
      ...base,
      key: "rounded_total",
      labelKey: "gst.fields.rounded_total",
      preview: fromForm("rounded_total"),
      hidden: (v) => isZero(v.rounding_adjustment),
    },
  ];
};

/* ---- line columns ---- */

const gstCol = (key: string, labelKey: string, widthClass: string, extra: Partial<ChildColumnDef> = {}): ChildColumnDef => ({
  key,
  labelKey,
  type: "decimal",
  readOnly: true,
  widthClass,
  ...extra,
});

/** Server-owned GST columns of a document line (read-only; filled from the saved doc or the preview). */
export const lineGstColumns: ChildColumnDef[] = [
  { key: "gst_hsn_code", labelKey: "gst.fields.hsn_sac", type: "text", readOnly: true, widthClass: "w-24" },
  gstCol("gst_rate", "gst.fields.gst_rate", "w-20"),
  gstCol("cgst_amount", "gst.fields.cgst", "w-28", { money: true }),
  gstCol("sgst_amount", "gst.fields.sgst", "w-28", { money: true }),
  gstCol("igst_amount", "gst.fields.igst", "w-28", { money: true }),
];

/* ---- tax table ---- */

const taxAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  label: (r) => r.name,
  subLabel: (r) => [r.gst_direction, r.gst_component].filter(Boolean).join(" ") || undefined,
  display: (row) => row.account_name || undefined,
  filters: (_row, form) => ({ company: form.company, account_type: "Tax", is_group: false, is_active: true }),
  fill: (r, row) => ({
    account_name: r.name,
    gst_component: r.gst_component ?? "",
    description: row.description || r.name,
  }),
};

export const taxesTable: ChildTableDef = {
  key: "taxes",
  titleKey: "gst.table.taxes",
  derived: true,
  newRow: () => ({ add_deduct_tax: "Add", rate: "0" }),
  columns: [
    { key: "account", labelKey: "acc.fields.account", type: "link", required: true, link: taxAccountLink, widthClass: "min-w-[14rem]" },
    { key: "gst_component", labelKey: "gst.fields.gst_component", type: "text", readOnly: true, widthClass: "w-24" },
    { key: "description", labelKey: "bs.fields.description", type: "text", widthClass: "min-w-[10rem]" },
    { key: "rate", labelKey: "gst.fields.rate_pct", type: "decimal", decimals: 3, required: true, widthClass: "w-28" },
    { key: "add_deduct_tax", labelKey: "gst.fields.add_deduct", type: "select", required: true, options: ADD_DEDUCT, widthClass: "w-28" },
    gstCol("tax_amount", "gst.fields.tax_amount", "w-32", { money: true }),
    gstCol("total", "gst.fields.total", "w-32", { money: true }),
  ],
};

/* ---- dry-run preview ---- */

/** Rows complete enough for the dry run (a half-typed line must not make it 400). */
const itemUsable = (r: Record<string, any>) =>
  !!r.item && isDecimal(r.qty) && /[1-9]/.test(String(r.qty)) && isDecimal(r.rate);
const taxRowUsable = (r: Record<string, any>) => !!r.account;

/**
 * Dry-run preview for SO / SI / PO / PI against POST /accounts/taxes/calculate/.
 *
 * Request: only INPUT fields (company, party, flags, template, usable lines, and
 * the tax rows when the user took the table over). A template that the preview
 * itself auto-selected (`_auto_template`) is not sent, so a changed party / place
 * of supply re-selects the right default instead of a stale one.
 */
export const taxPreview = (doctype: string, partyKey: string): PreviewDef => ({
  endpoint: "/accounts/taxes/calculate/",
  // These header fields change which rows apply, so editing them re-derives the tax table.
  rederiveOn: ["taxes_and_charges", partyKey, "place_of_supply", "is_export_with_gst", "is_reverse_charge"],
  buildRequest: (v, manual) => {
    const items = rowsOf(v, "items").filter(itemUsable);
    if (!v.company || !v[partyKey] || items.length === 0) return null;
    const explicitTemplate = v.taxes_and_charges && v.taxes_and_charges !== v._auto_template ? v.taxes_and_charges : null;
    const body: Record<string, any> = {
      doctype,
      company: v.company,
      party: v[partyKey],
      posting_date: v.posting_date || v.transaction_date || null,
      place_of_supply: v.place_of_supply || null,
      is_export_with_gst: !!v.is_export_with_gst,
      is_reverse_charge: !!v.is_reverse_charge,
      taxes_and_charges: explicitTemplate,
      items: items.map((r) => ({ item: r.item, qty: r.qty, rate: r.rate })),
    };
    if (manual.has("taxes")) {
      body.taxes = rowsOf(v, "taxes")
        .filter(taxRowUsable)
        .map((r) => ({
          account: r.account,
          rate: isDecimal(r.rate) ? r.rate : "0",
          charge_type: "On Net Total",
          add_deduct_tax: r.add_deduct_tax || "Add",
          category: "Total",
          description: r.description || "",
        }));
    }
    return body;
  },
  apply: (res, { values, tables, manual }) => {
    const supply = res.supply ?? {};
    const out: Record<string, any> = {
      net_total: res.net_total,
      total_taxes_and_charges: res.total_taxes_and_charges,
      grand_total: res.grand_total,
      rounding_adjustment: res.rounding_adjustment,
      rounded_total: res.rounded_total,
      gst_category: supply.gst_category ?? "",
      party_gstin: supply.party_gstin ?? "",
      _supply_kind: supply.kind ?? "",
    };
    // Show the template the server picked; a user-chosen one is never overwritten.
    const auto = values._auto_template ?? null;
    if (!values.taxes_and_charges || values.taxes_and_charges === auto) {
      out.taxes_and_charges = res.taxes_and_charges ?? "";
      out.taxes_and_charges_name = "";
      out._auto_template = res.taxes_and_charges ?? null;
    }
    if (values.is_export_with_gst && !SPECIAL_CATEGORIES.includes(supply.gst_category)) out.is_export_with_gst = false;

    // Per-line GST (response rows are the usable lines, in order).
    const itemRows = rowsOf(tables as any, "items");
    const usable = itemRows.map((r, i) => (itemUsable(r) ? i : -1)).filter((i) => i >= 0);
    const lineRes: Record<string, any>[] = res.items ?? [];
    const items = itemRows.map((r) => ({ ...r }));
    usable.forEach((rowIdx, k) => {
      const l = lineRes[k];
      if (!l) return;
      Object.assign(items[rowIdx], {
        gst_hsn_code: l.gst_hsn_code,
        gst_treatment: l.gst_treatment,
        gst_rate: l.gst_rate,
        cgst_amount: l.cgst_amount,
        sgst_amount: l.sgst_amount,
        igst_amount: l.igst_amount,
      });
    });

    // Tax rows: derived -> replaced wholesale by the server's rows; manual -> only the amounts are refreshed.
    const taxRows: Record<string, any>[] = res.taxes ?? [];
    const current = rowsOf(tables as any, "taxes");
    let taxes: Record<string, any>[];
    if (manual.has("taxes")) {
      const idxs = current.map((r, i) => (taxRowUsable(r) ? i : -1)).filter((i) => i >= 0);
      taxes = current.map((r) => ({ ...r }));
      idxs.forEach((rowIdx, k) => {
        const tr = taxRows[k];
        if (tr) Object.assign(taxes[rowIdx], { tax_amount: tr.tax_amount, total: tr.total, gst_component: tr.gst_component ?? "" });
      });
    } else {
      taxes = taxRows.map((tr, i) => ({
        _key: current[i]?._key,
        account: tr.account,
        account_name: tr.account_name,
        gst_component: tr.gst_component ?? "",
        description: tr.description,
        rate: tr.rate,
        add_deduct_tax: tr.add_deduct_tax,
        tax_amount: tr.tax_amount,
        total: tr.total,
      }));
    }
    return { values: out, tables: { items, taxes } };
  },
});

/** `renderSummary` for the taxable documents: supply kind + reason, place of supply, preview errors. */
export const renderGstSummary =
  (partyKey: "customer" | "supplier"): NonNullable<ResourceConfig["renderSummary"]> =>
  ({ values, editable, preview }) =>
    createElement(GstSummary, { partyKey, values, editable, preview });
