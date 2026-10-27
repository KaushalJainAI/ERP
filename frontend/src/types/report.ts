import type { ComponentType } from "react";
import type { FieldType, LinkDef, SelectOption } from "./index";

/** A filter input shown above a report; its value is sent as a query param named `key`. */
export interface ReportFilterDef {
  key: string;
  /** i18n key */
  labelKey: string;
  type: Extract<FieldType, "text" | "date" | "select" | "link" | "boolean" | "number" | "decimal">;
  options?: SelectOption[]; // select
  link?: LinkDef; // link (value sent is link.valueField, default id)
  defaultValue?: string | number | boolean | (() => string | number | boolean);
}

export interface ReportColumnDef {
  /** Property on each row. */
  key: string;
  /** i18n key */
  labelKey: string;
  /** Default "text". money -> Indian currency, number -> Indian grouping, date -> dd/mm/yyyy. */
  type?: "text" | "money" | "number" | "date" | "boolean";
}

/**
 * A read-only report backed by a GET endpoint. The response may be a bare array,
 * a DRF page ({results: []}) or an object holding the rows under `rowsKey`.
 */
export interface ReportConfig {
  /** URL slug: /<module>/reports/<key> */
  key: string;
  module: string;
  /** GET endpoint relative to the API base. Filter values are sent as query params. */
  endpoint: string;
  /** i18n key (page title / sidebar). */
  titleKey: string;
  icon?: ComponentType<{ className?: string }>;
  filters?: ReportFilterDef[];
  columns: ReportColumnDef[];
  /** Property of the response holding the row array. Default: "rows", then "results", else the body itself. */
  rowsKey?: string;
  /**
   * Footer totals: column key -> property of the response that carries the total
   * (the server computes it; the client never sums money).
   */
  totals?: Record<string, string>;
  /** i18n key for the footer label. Default "report.total". */
  totalsLabelKey?: string;
  hideInSidebar?: boolean;
}
