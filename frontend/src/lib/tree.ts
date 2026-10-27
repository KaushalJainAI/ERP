import type { ApiRecord } from "@/types";

export interface TreeRow {
  row: ApiRecord;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
}

const idOf = (r: ApiRecord) => String(r.id);

/** Parent id -> child rows (fetch order kept). Rows whose parent is not in the set become roots (key ""). */
export function groupByParent(rows: ApiRecord[], parentKey: string): Map<string, ApiRecord[]> {
  const known = new Set(rows.map(idOf));
  const byParent = new Map<string, ApiRecord[]>();
  rows.forEach((r) => {
    const p = r[parentKey];
    const key = p !== null && p !== undefined && p !== "" && known.has(String(p)) && String(p) !== idOf(r) ? String(p) : "";
    const list = byParent.get(key);
    if (list) list.push(r);
    else byParent.set(key, [r]);
  });
  return byParent;
}

/**
 * Flatten the hierarchy into display order.
 * - `expanded`: ids of open nodes. `null` = default (roots open, deeper levels closed).
 * - `visibleIds`: when set (search active) only these nodes are shown and all of them are opened.
 */
export function flattenTree(
  rows: ApiRecord[],
  parentKey: string,
  expanded: Set<string> | null,
  visibleIds: Set<string> | null
): TreeRow[] {
  const byParent = groupByParent(rows, parentKey);
  const out: TreeRow[] = [];
  const seen = new Set<string>(); // cycle guard
  const walk = (parent: string, depth: number) => {
    (byParent.get(parent) ?? []).forEach((row) => {
      const id = idOf(row);
      if (seen.has(id) || (visibleIds && !visibleIds.has(id))) return;
      seen.add(id);
      const kids = (byParent.get(id) ?? []).filter((k) => !visibleIds || visibleIds.has(idOf(k)));
      const open = visibleIds ? true : expanded ? expanded.has(id) : depth === 0;
      out.push({ row, depth, hasChildren: kids.length > 0, expanded: open && kids.length > 0 });
      if (open) walk(id, depth + 1);
    });
  };
  walk("", 0);
  return out;
}

/** Ids of rows matching `matches`, plus all their ancestors (so a hit stays reachable in the tree). */
export function matchWithAncestors(
  rows: ApiRecord[],
  parentKey: string,
  matches: (row: ApiRecord) => boolean
): Set<string> {
  const byId = new Map(rows.map((r) => [idOf(r), r]));
  const out = new Set<string>();
  rows.forEach((r) => {
    if (!matches(r)) return;
    let cur: ApiRecord | undefined = r;
    while (cur && !out.has(idOf(cur))) {
      out.add(idOf(cur));
      const p: unknown = cur[parentKey];
      cur = p !== null && p !== undefined && p !== "" ? byId.get(String(p)) : undefined;
    }
  });
  return out;
}

/** Ids of every node that has children (for "expand all"). */
export function parentIds(rows: ApiRecord[], parentKey: string): Set<string> {
  const byParent = groupByParent(rows, parentKey);
  const ids = new Set<string>();
  byParent.forEach((_kids, key) => {
    if (key) ids.add(key);
  });
  return ids;
}
