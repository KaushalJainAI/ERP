import { apiClient } from "./client";
import type { ApiRecord, Paginated } from "@/types";

/**
 * Endpoint helpers. `endpoint` is a DRF list route relative to the API base with a
 * trailing slash, e.g. "/selling/sales-orders/".
 */
export const detailUrl = (endpoint: string, id: number | string) => `${endpoint}${id}/`;
export const actionUrl = (endpoint: string, id: number | string, action: "submit" | "cancel" | "amend") =>
  `${endpoint}${id}/${action}/`;

export const PAGE_SIZE = 25; // backend/config/settings.py REST_FRAMEWORK["PAGE_SIZE"]

export interface ListParams {
  page?: number;
  search?: string;
  ordering?: string;
  [filter: string]: string | number | boolean | undefined | null;
}

function cleanParams(params: ListParams): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") out[k] = v;
  });
  return out;
}

export async function listRecords(endpoint: string, params: ListParams = {}): Promise<Paginated<ApiRecord>> {
  const res = await apiClient.get(endpoint, { params: cleanParams(params) });
  // Tolerate un-paginated endpoints.
  if (Array.isArray(res.data)) {
    return { count: res.data.length, next: null, previous: null, results: res.data };
  }
  return res.data as Paginated<ApiRecord>;
}

/**
 * Fetch EVERY row of a list endpoint (used by tree views, which need the whole
 * hierarchy). The backend page size is fixed (no `page_size` query param), so
 * this reads page 1, then the remaining pages in small parallel batches.
 */
export async function listAllRecords(endpoint: string, params: ListParams = {}): Promise<ApiRecord[]> {
  const first = await listRecords(endpoint, { ...params, page: 1 });
  const rows = [...first.results];
  if (!first.next || first.results.length === 0) return rows;
  const pages = Math.min(Math.ceil(first.count / first.results.length), 400);
  const BATCH = 4;
  for (let start = 2; start <= pages; start += BATCH) {
    const nums = Array.from({ length: Math.min(BATCH, pages - start + 1) }, (_, i) => start + i);
    const chunk = await Promise.all(nums.map((n) => listRecords(endpoint, { ...params, page: n })));
    chunk.forEach((c) => rows.push(...c.results));
  }
  return rows;
}

export async function getRecord(endpoint: string, id: number | string): Promise<ApiRecord> {
  const res = await apiClient.get(detailUrl(endpoint, id));
  return res.data;
}

export async function createRecord(endpoint: string, body: ApiRecord, headers?: Record<string, string>): Promise<ApiRecord> {
  const res = await apiClient.post(endpoint, body, { headers });
  return res.data;
}

export async function updateRecord(endpoint: string, id: number | string, body: ApiRecord): Promise<ApiRecord> {
  const res = await apiClient.put(detailUrl(endpoint, id), body);
  return res.data;
}

export async function deleteRecord(endpoint: string, id: number | string): Promise<void> {
  await apiClient.delete(detailUrl(endpoint, id));
}

export async function docAction(
  endpoint: string,
  id: number | string,
  action: "submit" | "cancel" | "amend"
): Promise<ApiRecord> {
  const res = await apiClient.post(actionUrl(endpoint, id, action));
  return res.data;
}

/* ------------------------------------------------------------------ */
/* Error normalisation                                                 */
/* ------------------------------------------------------------------ */

export interface NormalisedError {
  /** Errors on header fields: field -> messages. */
  fields: Record<string, string[]>;
  /** Errors on child rows: table -> row index -> column -> messages. */
  rows: Record<string, Record<number, Record<string, string[]>>>;
  /** Errors on a child table as a whole (e.g. "this field is required"). */
  tables: Record<string, string[]>;
  /** Anything not tied to a field (detail / error / non_field_errors / network). */
  general: string[];
  status?: number;
  forbidden?: boolean;
}

const asMessages = (v: unknown): string[] => {
  if (v === null || v === undefined) return [];
  if (typeof v === "string") return [v];
  if (Array.isArray(v)) return v.flatMap(asMessages);
  if (typeof v === "object") return Object.values(v as object).flatMap(asMessages);
  return [String(v)];
};

/**
 * Turn a thrown axios error (or any error) into something the form can render.
 * Handles DRF validation dicts (`{field: [msgs]}`, nested `{items: [{qty: [..]}]}`)
 * and `{detail}` / `{error}` envelopes (the latter may itself be a dict or list -
 * see backend/core/mixins.py).
 */
export function normaliseError(err: any, childKeys: string[] = []): NormalisedError {
  const out: NormalisedError = { fields: {}, rows: {}, tables: {}, general: [] };
  const status: number | undefined = err?.response?.status;
  out.status = status;
  out.forbidden = status === 403;

  const data = err?.response?.data;
  if (!err?.response) {
    out.general.push(err?.message || "Network error");
    return out;
  }
  if (typeof data === "string" || data === undefined || data === null) {
    // HTML error pages (500) etc.
    out.general.push(typeof data === "string" && data.length < 200 && !data.includes("<") ? data : `HTTP ${status}`);
    return out;
  }

  const absorb = (obj: Record<string, unknown>) => {
    for (const [key, val] of Object.entries(obj)) {
      if (key === "detail" || key === "non_field_errors") {
        out.general.push(...asMessages(val));
      } else if (key === "error") {
        if (val && typeof val === "object" && !Array.isArray(val)) absorb(val as Record<string, unknown>);
        else out.general.push(...asMessages(val));
      } else if (childKeys.includes(key) && Array.isArray(val)) {
        val.forEach((rowErr, idx) => {
          if (rowErr && typeof rowErr === "object" && !Array.isArray(rowErr)) {
            const cols: Record<string, string[]> = {};
            Object.entries(rowErr as Record<string, unknown>).forEach(([c, m]) => {
              const msgs = asMessages(m);
              if (msgs.length) cols[c] = msgs;
            });
            if (Object.keys(cols).length) {
              out.rows[key] = out.rows[key] || {};
              out.rows[key][idx] = cols;
            }
          } else {
            const msgs = asMessages(rowErr);
            if (msgs.length) out.tables[key] = [...(out.tables[key] || []), ...msgs];
          }
        });
      } else if (childKeys.includes(key)) {
        out.tables[key] = asMessages(val);
      } else {
        const msgs = asMessages(val);
        if (msgs.length) out.fields[key] = msgs;
      }
    }
  };

  if (Array.isArray(data)) out.general.push(...asMessages(data));
  else absorb(data as Record<string, unknown>);

  if (!out.general.length && !Object.keys(out.fields).length && !Object.keys(out.rows).length && !Object.keys(out.tables).length) {
    out.general.push(`HTTP ${status}`);
  }
  return out;
}

/** Single human-readable line for toasts. */
export function summariseError(e: NormalisedError): string {
  if (e.general.length) return e.general.join(" ");
  const first = Object.entries(e.fields)[0];
  if (first) return `${first[0]}: ${first[1][0]}`;
  const tbl = Object.entries(e.tables)[0];
  if (tbl) return `${tbl[0]}: ${tbl[1][0]}`;
  return "";
}
