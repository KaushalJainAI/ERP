/** The one company the session is working in. Persisted only as an id; the
 * server validates it against the user's memberships on every request. */
export const ACTIVE_COMPANY_KEY = "active_company_id";

export function getActiveCompanyId(): number | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(ACTIVE_COMPANY_KEY);
  if (!raw) return null;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function setActiveCompanyId(id: number | null) {
  if (typeof window === "undefined") return;
  if (id == null) window.localStorage.removeItem(ACTIVE_COMPANY_KEY);
  else window.localStorage.setItem(ACTIVE_COMPANY_KEY, String(id));
}
