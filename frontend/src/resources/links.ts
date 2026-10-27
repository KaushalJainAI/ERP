import { MODULES, RESOURCES } from "./registry";

/**
 * Route helpers for places that link into other modules (dashboard cards,
 * quick-create buttons). They return null when the target page does not exist
 * (yet), so callers can hide the link instead of sending the user to a redirect.
 */

const hasResource = (module: string, key: string) => RESOURCES.some((r) => r.module === module && r.key === key);

/** Resources that have a create/detail page (ledgers and projections are list-only). */
const isCreatable = (module: string, key: string) =>
  RESOURCES.some((r) => r.module === module && r.key === key && !r.listOnly);

const hasLegacyLink = (module: string, key: string) =>
  !!MODULES.find((m) => m.key === module)?.extraLinks?.some((l) => l.path === `/${module}/${key}`);

/** List page of a resource, optionally with a query string such as "docstatus=1". */
export function listRoute(module: string, key: string, query?: string): string | null {
  if (hasResource(module, key)) return `/${module}/${key}${query ? `?${query}` : ""}`;
  // Hand-written legacy pages ignore the query string.
  if (hasLegacyLink(module, key)) return `/${module}/${key}`;
  return null;
}

/** "New document" page of a config-driven resource. */
export function newRoute(module: string, key: string): string | null {
  return isCreatable(module, key) ? `/${module}/${key}/new` : null;
}

/** First list route that exists out of several candidates. */
export function firstListRoute(candidates: Array<[module: string, key: string]>): string | null {
  for (const [m, k] of candidates) {
    const route = listRoute(m, k);
    if (route) return route;
  }
  return null;
}
