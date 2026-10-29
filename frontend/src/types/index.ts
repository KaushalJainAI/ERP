import type { ComponentType, ReactNode } from "react";

/* ------------------------------------------------------------------ */
/* API shapes                                                          */
/* ------------------------------------------------------------------ */

/** DRF PageNumberPagination envelope (backend/core/pagination.py). */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** A record as returned by the API. Money/qty values are decimal STRINGS. */
export type ApiRecord = Record<string, any> & { id?: number | string };

/** core.DocStatus */
export const DocStatus = {
  DRAFT: 0,
  SUBMITTED: 1,
  CANCELLED: 2,
} as const;

export interface UserProfile {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  language: string;
  roles: string[];
  /** Model labels (`app.Model`) the user may read; `["*"]` for a superuser. */
  readable_models: string[];
  is_superuser?: boolean;
}

/* ------------------------------------------------------------------ */
/* Resource configuration                                              */
/* ------------------------------------------------------------------ */

export type FieldType =
  | "text"
  | "email"
  | "textarea"
  | "number" // quantity-like (default 3 decimals)
  | "decimal" // money / rate-like (default 2 decimals)
  | "date"
  | "select"
  | "boolean"
  | "link"
  | "password" // write-only secret input (never echoed back by the API)
  | "multiselect"; // list of foreign keys, options from `link.endpoint` (e.g. a user's role ids)

/** A record in a link-field dropdown: whatever the target endpoint returns. */
export type LinkRecord = Record<string, any>;

export interface LinkDef {
  /** List endpoint of the target resource, e.g. "/stock/items/". Searched with ?search=. */
  endpoint?: string;
  /**
   * Alternative to `endpoint` for targets without a list API. Must return the
   * candidate records (each carrying `valueField`) for the search text.
   */
  fetcher?: (search: string) => Promise<LinkRecord[]>;
  /** Property used as the stored value. Default "id". */
  valueField?: string;
  /** Property shown as the option label. Default "name". */
  labelField?: string;
  /** Build the label for a record (overrides labelField). */
  label?: (record: LinkRecord) => string;
  /** Secondary text shown under the label in the dropdown. */
  subLabel?: (record: LinkRecord) => string | undefined;
  /** Extra query params (e.g. { company: 3 }) derived from the row / form values. */
  filters?: (values: Record<string, any>, parentValues: Record<string, any>) => Record<string, any>;
  /**
   * Label for an already stored value, derived from the record's own serializer
   * fields (e.g. `customer_name`). When it returns undefined the label is fetched.
   */
  display?: (values: Record<string, any>) => string | undefined;
  /**
   * Called when the user picks a record. Returns extra values to merge into the
   * form (header) or row (child table) - e.g. auto-fill rate from standard_rate.
   */
  fill?: (record: LinkRecord, values: Record<string, any>) => Record<string, any>;
}

export interface SelectOption {
  value: string | number;
  /** i18n key */
  labelKey: string;
}

export interface FieldDef {
  key: string;
  /** i18n key */
  labelKey: string;
  type: FieldType;
  required?: boolean;
  /** Never editable (server computed). */
  readOnly?: boolean;
  /** Editable on create only. */
  readOnlyAfterCreate?: boolean;
  options?: SelectOption[]; // select
  link?: LinkDef; // link
  /** Digits after the decimal point allowed for number/decimal. */
  decimals?: number;
  /** Initial value for a new document. */
  defaultValue?: string | number | boolean | null | (() => string | number | boolean | null);
  /** Grid span (1-3) in the form. textarea defaults to full width. */
  span?: 1 | 2 | 3;
  /** Hide the field when this returns true. */
  hidden?: (values: Record<string, any>, isNew: boolean) => boolean;
  /**
   * Rarely needed (the server fills a default): shown under the collapsed
   * "More details" toggle instead of in the main form.
   */
  advanced?: boolean;
  /** Render as money (Indian grouping) when read-only. */
  money?: boolean;
  /**
   * Read-only computed field: client-side estimate while the form has unsaved
   * changes. After a save the server value is shown (server is the source of truth).
   */
  preview?: (values: Record<string, any>) => string | null;
  /** Custom display of a `preview` (computed) field in the totals block, e.g. a status badge. */
  renderValue?: (value: any, values: Record<string, any>) => ReactNode;
  /** i18n key for a hint under the input */
  helpKey?: string;
  /** Keys of other fields to reset when this one changes (e.g. company -> customer). */
  clears?: string[];
  /**
   * Server-side check run when the (text) input loses focus - e.g. GSTIN
   * validation. Never blocks saving; the server re-validates on save.
   */
  check?: FieldCheckDef;
}

/** Result of interpreting a field-check response. */
export interface FieldCheckResult {
  ok: boolean;
  /** Shown under the input when !ok (typically the server's own reason). */
  message?: string;
  /** Values merged into the form when the check ran (e.g. state from the GSTIN prefix). */
  fill?: Record<string, any>;
}

export interface FieldCheckDef {
  /** GET endpoint relative to the API base; the value is sent as `?<param>=`. */
  endpoint: string;
  param: string;
  /** i18n key shown (green) when the check passes. */
  okKey: string;
  interpret: (response: any, values: Record<string, any>) => FieldCheckResult;
}

export interface ChildColumnDef {
  key: string;
  labelKey: string;
  type: FieldType;
  required?: boolean;
  readOnly?: boolean;
  link?: LinkDef;
  decimals?: number;
  money?: boolean;
  /** number / decimal: accept a leading minus (quantity of a return line). */
  allowNegative?: boolean;
  options?: SelectOption[];
  /** Width hint (tailwind class), e.g. "w-28". */
  widthClass?: string;
  /**
   * Computed read-only column: preview from the row's values; once saved the
   * server value (row[key]) is shown until the `dependsOn` inputs change.
   */
  preview?: (row: Record<string, any>) => string | null;
  dependsOn?: string[];
  /**
   * Conditional column: when this returns true for a row the cell is not
   * rendered, and its value is cleared (and never sent to the server). The
   * header is hidden when the column is hidden for every row; with no rows it
   * is evaluated against an empty row (so parent-driven conditions still work).
   */
  hidden?: (row: Record<string, any>, parentValues: Record<string, any>) => boolean;
}

export interface ChildTableDef {
  /** Key in the document payload, e.g. "items" or "lines". */
  key: string;
  /** i18n key of the table title. */
  titleKey: string;
  columns: ChildColumnDef[];
  /** Values for a freshly added row. */
  newRow?: () => Record<string, any>;
  /** Server-owned rows: no add/remove controls (e.g. Work Order required items). */
  readOnly?: boolean;
  /** Column keys copied from the previous row when adding a row (e.g. warehouse). */
  inheritFromPrevious?: string[];
  /**
   * Rows the server derives (e.g. the tax table from a tax template). Requires
   * `ResourceConfig.preview`. While "derived" the rows come from the preview
   * response and are OMITTED from the save payload (the server re-derives them);
   * once the user edits a row by hand the table turns "manual" and is sent as
   * edited. A new document starts derived; a saved one starts manual (as saved)
   * until a field in `PreviewDef.rederiveOn` changes.
   */
  derived?: boolean;
  /**
   * "Fetch items" bar above the table (ERPNext "Fetch Items from Warehouse"):
   * pick a source record, GET `endpoint` with `params`, append the returned
   * `rows` (skipping ones already present by `dedupeKeys`). Draft documents only.
   */
  fetchRows?: {
    /** i18n key of the button */
    labelKey: string;
    sourceLabelKey: string;
    sourceLink: LinkDef;
    endpoint: string;
    /** Query params from the picked source and the form; null = form not ready. */
    params: (source: string | number, form: Record<string, any>) => Record<string, any> | null;
    mapRow?: (row: Record<string, any>) => Record<string, any>;
    dedupeKeys?: string[];
    pickKey: string;
    notReadyKey: string;
    noneKey: string;
    /** receives {{count}} */
    doneKey: string;
    failedKey: string;
  };
}

/** Context the preview hook gets when merging a response into the form. */
export interface PreviewApplyContext {
  values: Record<string, any>;
  tables: Record<string, Record<string, any>[]>;
  /** Derived tables the user has taken over by hand. */
  manual: ReadonlySet<string>;
}

/**
 * Server-side preview of a whole document (dry run). While a draft has unsaved
 * changes the form debounces a POST to `endpoint` and merges the response back,
 * so computed totals/columns always come from the server's own engine instead of
 * a client re-implementation. Stale responses are dropped.
 */
export interface PreviewDef {
  /** POST endpoint relative to the API base. */
  endpoint: string;
  /** Debounce before a request is sent. Default 500 ms. */
  debounceMs?: number;
  /**
   * Request body from the form (header values with child tables merged in by key;
   * `manual` = derived tables the user edited by hand). Return null while the
   * form is not ready for a preview (e.g. no customer yet). Must only read INPUT
   * fields - never values written by `apply` - or it would loop.
   */
  buildRequest: (values: Record<string, any>, manual: ReadonlySet<string>) => Record<string, any> | null;
  /** Merge a response into the form: header `values` and/or whole replacement `tables`. */
  apply: (
    response: any,
    ctx: PreviewApplyContext
  ) => { values?: Record<string, any>; tables?: Record<string, Record<string, any>[]> };
  /** Header fields that, when the user changes them, put every `derived` table back to "derived". */
  rederiveOn?: string[];
}

/** Live state of the preview, handed to `renderSummary`. */
export interface PreviewState {
  /** Last response that matches the current form, if any. */
  data: any | null;
  /** Human-readable errors of the last dry run (empty when it succeeded). */
  errors: string[];
  /** A request is queued or in flight. */
  pending: boolean;
}

export interface DocAction {
  key: string;
  /** i18n key */
  labelKey: string;
  icon?: ComponentType<{ className?: string }>;
  /** Show only when this returns true. */
  showWhen?: (doc: ApiRecord) => boolean;
  /**
   * "navigate": go to another route, optionally pre-filling a new document
   * (router state `initialValues`).
   * "request": POST `url(doc)` (relative to the API base). This is how mapping
   * actions work ("Make Sales Invoice" -> POST .../make-sales-invoice/ returns
   * the new DRAFT document). `resultTo` turns the response into a route to open;
   * without it the current document is simply refreshed.
   */
  kind: "navigate" | "request";
  navigate?: (doc: ApiRecord) => { to: string; initialValues?: Record<string, any> };
  url?: (doc: ApiRecord) => string;
  /** POST body for a "request" action (e.g. make-stock-entry type). */
  body?: (doc: ApiRecord) => Record<string, any>;
  resultTo?: (result: ApiRecord) => string;
}

export interface ListColumnDef {
  key: string;
  labelKey: string;
  type?: "text" | "money" | "number" | "date" | "boolean" | "status";
  /** Custom rendering. */
  render?: (value: any, row: ApiRecord) => ReactNode;
}

/** Hierarchical master (ERPNext tree view): rendered as a collapsible, indented tree. */
export interface TreeConfig {
  /** Property holding the parent's id (e.g. "parent"). Rows with a null/unknown parent are roots. */
  parentKey: string;
  /** Column key that gets the indent + expand/collapse toggle. Default: the first column. */
  labelColumn?: string;
  /** Property marking a row as a group (shown with a folder icon). Default "is_group". */
  groupKey?: string;
  /** Start in flat-list mode instead of tree mode. */
  defaultFlat?: boolean;
}

export interface ResourceConfig {
  /** URL slug: /<module>/<key> */
  key: string;
  /** Module key (matches a ModuleDef.key in the registry). */
  module: string;
  /** DRF list endpoint, relative to the API base, with trailing slash. */
  endpoint: string;
  /** i18n key, plural (list title / sidebar). */
  titleKey: string;
  /** i18n key, singular (form title, "New ..."). */
  singularKey: string;
  icon?: ComponentType<{ className?: string }>;
  /** Uses the Draft/Submitted/Cancelled lifecycle (submit/cancel/amend). */
  submittable?: boolean;
  columns: ListColumnDef[];
  fields: FieldDef[];
  childTables?: ChildTableDef[];
  actions?: DocAction[];
  /** Property used as the document title. Default "name". */
  titleField?: string;
  /** DRF ordering for the list. Default "-created_at". */
  ordering?: string;
  /** Enables the tree view (with a toggle back to the flat, paginated list). */
  tree?: TreeConfig;
  /** Hide the Delete button on masters. */
  noDelete?: boolean;
  /** Never show the Attachments panel (default: shown wherever the API allows attachments). */
  noAttachments?: boolean;
  /** Hide from the sidebar (still routable). */
  hideInSidebar?: boolean;
  /** List-only (ledger / projection): no "New" button, rows are not clickable, no detail/new routes. */
  listOnly?: boolean;
  /** Past-bills tab (pages/history) of the older Tally vouchers of this kind, linked above the list. */
  history?: string;
  /**
   * Optional extra block rendered under the child tables / totals (e.g. the
   * Journal Entry "balanced / not balanced" warning). `values` is the form with
   * child tables merged in by key (values.lines = rows).
   */
  renderSummary?: (ctx: {
    values: Record<string, any>;
    dirty: boolean;
    editable: boolean;
    /** Present when the resource defines `preview`. */
    preview?: PreviewState;
  }) => ReactNode;
  /** Dry-run preview of the whole document (see PreviewDef). */
  preview?: PreviewDef;
}

export interface NavLink {
  path: string;
  /** i18n key */
  labelKey: string;
  icon?: ComponentType<{ className?: string }>;
  /** Hide the link for users this returns false for (default: always shown). */
  visible?: (user: UserProfile) => boolean;
  /** Id of a live count badge shown next to the link (see resources/navBadges.ts). */
  badge?: string;
}

export interface ModuleDef {
  key: string;
  /** i18n key */
  titleKey: string;
  icon: ComponentType<{ className?: string }>;
  /** Links to pages that are not (yet) config-driven resources. */
  extraLinks?: NavLink[];
}
