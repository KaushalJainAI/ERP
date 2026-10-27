import { apiClient } from "./client";
import { listAllRecords } from "./endpoints";
import type { LinkRecord } from "@/types";

/**
 * Company lookup.
 *
 * The backend exposes no Company list endpoint (backend/core/urls.py is empty),
 * so a company picker cannot use `endpoint`. This fetcher tries /core/companies/
 * (in case it is added) and otherwise derives the distinct companies from
 * records that carry `company` + `company_name` (warehouses, accounts).
 */
let cache: LinkRecord[] | null = null;
let inflight: Promise<LinkRecord[]> | null = null;

async function load(): Promise<LinkRecord[]> {
  try {
    const res = await apiClient.get("/core/companies/", { params: { page_size: 100 } });
    const rows: LinkRecord[] = Array.isArray(res.data) ? res.data : res.data.results || [];
    return rows;
  } catch {
    /* fall through to derivation */
  }
  const found = new Map<number, LinkRecord>();
  for (const url of ["/stock/warehouses/", "/accounts/accounts/"]) {
    try {
      const res = await apiClient.get(url);
      const rows: LinkRecord[] = Array.isArray(res.data) ? res.data : res.data.results || [];
      rows.forEach((r) => {
        if (r.company != null && !found.has(r.company)) {
          found.set(r.company, { id: r.company, name: r.company_name ?? String(r.company) });
        }
      });
      if (found.size) break;
    } catch {
      /* try next source */
    }
  }
  return [...found.values()];
}

export async function fetchCompanies(search = ""): Promise<LinkRecord[]> {
  if (!cache) {
    inflight = inflight || load();
    try {
      cache = await inflight;
    } finally {
      inflight = null;
    }
  }
  const q = search.trim().toLowerCase();
  return q ? cache.filter((c) => String(c.name).toLowerCase().includes(q)) : cache;
}

export function resetCompanyCache() {
  cache = null;
}

/**
 * GST states / union territories (GET /core/states/, read-only, 2-digit code as
 * primary key). ~38 rows, so the whole list is loaded once and filtered locally;
 * this also lets a stored code be resolved to its name without a detail endpoint.
 */
let stateCache: LinkRecord[] | null = null;
let stateInflight: Promise<LinkRecord[]> | null = null;

export async function fetchStates(search = ""): Promise<LinkRecord[]> {
  if (!stateCache) {
    stateInflight = stateInflight || listAllRecords("/core/states/", { is_active: true });
    try {
      stateCache = await stateInflight;
    } finally {
      stateInflight = null;
    }
  }
  const q = search.trim().toLowerCase();
  return q
    ? stateCache.filter((s) => `${s.code} ${s.name} ${s.abbr ?? ""}`.toLowerCase().includes(q))
    : stateCache;
}
