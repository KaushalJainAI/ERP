import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import type { ModuleDef, NavGroupKey, NavLink, ResourceConfig } from "@/types";
import { MODULES, reportsOf, resourcesOf } from "./registry";

export interface NavGroup {
  key: NavGroupKey;
  /** i18n key of the group heading. */
  titleKey: string;
  links: NavLink[];
}

export interface ModuleNav {
  module: ModuleDef;
  /** Landing page of the module (/<module key>). */
  path: string;
  /** Flat view kept for badge counts and older callers. */
  records: NavLink[];
  /** Read-only reports (paths under /<module>/reports/). */
  reports: NavLink[];
  /** Sidebar order: transactions, masters, reports, tools. Empty groups are dropped. */
  groups: NavGroup[];
}

const GROUP_ORDER: NavGroupKey[] = ["transactions", "masters", "reports", "tools"];

const GROUP_TITLE: Record<NavGroupKey, string> = {
  transactions: "shell.section_transactions",
  masters: "shell.section_masters",
  reports: "shell.section_reports",
  tools: "shell.section_tools",
};

const isReport = (link: NavLink) => link.path.includes("/reports/");

/** Where a resource belongs unless it overrides `navGroup`. */
function groupOfResource(resource: ResourceConfig): NavGroupKey {
  if (resource.navGroup) return resource.navGroup;
  if (resource.listOnly) return "reports";
  if (resource.submittable) return "transactions";
  return "masters";
}

/**
 * Navigation generated from the resource registry, filtered to what the current
 * user may see. Modules with nothing visible are dropped.
 */
export function useModuleNav(): ModuleNav[] {
  const { user } = useAuth();
  return useMemo(
    () =>
      MODULES.map((m) => {
        const buckets: Record<NavGroupKey, NavLink[]> = {
          transactions: [],
          masters: [],
          reports: [],
          tools: [],
        };
        const add = (link: NavLink, group: NavGroupKey) => buckets[group].push(link);

        (m.extraLinks ?? []).forEach((l) => {
          if (l.visible && (user === null || !l.visible(user))) return;
          add(l, l.group ?? (isReport(l) ? "reports" : "tools"));
        });
        resourcesOf(m.key).forEach((r) => {
          add(
            { path: `/${r.module}/${r.key}`, labelKey: r.titleKey, icon: r.icon },
            groupOfResource(r)
          );
        });
        reportsOf(m.key).forEach((r) => {
          add({ path: `/${r.module}/reports/${r.key}`, labelKey: r.titleKey, icon: r.icon }, "reports");
        });

        const groups: NavGroup[] = GROUP_ORDER.filter((key) => buckets[key].length > 0).map((key) => ({
          key,
          titleKey: GROUP_TITLE[key],
          links: buckets[key],
        }));
        const links = groups.flatMap((g) => g.links);
        return {
          module: m,
          path: `/${m.key}`,
          records: links.filter((l) => !isReport(l)),
          reports: links.filter(isReport),
          groups,
        };
      }).filter((n) => n.groups.length > 0),
    [user]
  );
}

/** The module the current route belongs to (by its first path segment), if any. */
export function useActiveModule(nav: ModuleNav[]): ModuleNav | undefined {
  const { pathname } = useLocation();
  const first = pathname.split("/")[1];
  return nav.find((n) => n.module.key === first);
}

export const isActivePath = (pathname: string, path: string) =>
  pathname === path || pathname.startsWith(path + "/");

/** The group that contains the current page, if any. */
export function activeGroupOf(module: ModuleNav, pathname: string): NavGroupKey | undefined {
  return module.groups.find((g) => g.links.some((l) => isActivePath(pathname, l.path)))?.key;
}
