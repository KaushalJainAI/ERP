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
  | "link";

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
  /** Render as money (Indian grouping) when read-only. */
  money?: boolean;
  /**
   * Read-only computed field: client-side estimate while the form has unsaved
   * changes. After a save the server value is shown (server is the source of truth).
   */
  preview?: (values: Record<string, any>) => string | null;
  /** i18n key for a hint under the input */
  helpKey?: string;
  /** Keys of other fields to reset when this one changes (e.g. company -> customer). */
  clears?: string[];
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
  options?: SelectOption[];
  /** Width hint (tailwind class), e.g. "w-28". */
  widthClass?: string;
  /**
   * Computed read-only column: preview from the row's values; once saved the
   * server value (row[key]) is shown until the `dependsOn` inputs change.
   */
  preview?: (row: Record<string, any>) => string | null;
  dependsOn?: string[];
}

export interface ChildTableDef {
  /** Key in the document payload, e.g. "items" or "lines". */
  key: string;
  /** i18n key of the table title. */
  titleKey: string;
  columns: ChildColumnDef[];
  /** Values for a freshly added row. */
  newRow?: () => Record<string, any>;
  /** Column keys copied from the previous row when adding a row (e.g. warehouse). */
  inheritFromPrevious?: string[];
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
  resultTo?: (result: ApiRecord) => string;
}

export interface ListColumnDef {
  key: string;
  labelKey: string;
  type?: "text" | "money" | "number" | "date" | "boolean" | "status";
  /** Custom rendering. */
  render?: (value: any, row: ApiRecord) => ReactNode;
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
  /** Hide the Delete button on masters. */
  noDelete?: boolean;
  /** Hide from the sidebar (still routable). */
  hideInSidebar?: boolean;
  /** List-only (ledger / projection): no "New" button, rows are not clickable, no detail/new routes. */
  listOnly?: boolean;
  /**
   * Optional extra block rendered under the child tables / totals (e.g. the
   * Journal Entry "balanced / not balanced" warning). `values` is the form with
   * child tables merged in by key (values.lines = rows).
   */
  renderSummary?: (ctx: { values: Record<string, any>; dirty: boolean; editable: boolean }) => ReactNode;
}

export interface NavLink {
  path: string;
  /** i18n key */
  labelKey: string;
  icon?: ComponentType<{ className?: string }>;
  /** Hide the link for users this returns false for (default: always shown). */
  visible?: (user: UserProfile) => boolean;
}

export interface ModuleDef {
  key: string;
  /** i18n key */
  titleKey: string;
  icon: ComponentType<{ className?: string }>;
  /** Links to pages that are not (yet) config-driven resources. */
  extraLinks?: NavLink[];
}
