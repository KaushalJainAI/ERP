import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import type { ModuleDef, NavLink } from "@/types";
import { MODULES, reportsOf, resourcesOf } from "./registry";

export interface ModuleNav {
  module: ModuleDef;
  /** Landing page of the module (/<module key>). */
  path: string;
  /** Documents, masters and hand-written pages. */
  records: NavLink[];
  /** Read-only reports (paths under /<module>/reports/). */
  reports: NavLink[];
}

const isReport = (link: NavLink) => link.path.includes("/reports/");

/**
 * Navigation generated from the resource registry, filtered to what the current
 * user may see. Modules with nothing visible are dropped.
 */
export function useModuleNav(): ModuleNav[] {
  const { user } = useAuth();
  return useMemo(
    () =>
      MODULES.map((m) => {
        const all: NavLink[] = [
          ...(m.extraLinks ?? []),
          ...resourcesOf(m.key).map((r) => ({ path: `/${r.module}/${r.key}`, labelKey: r.titleKey, icon: r.icon })),
          ...reportsOf(m.key).map((r) => ({
            path: `/${r.module}/reports/${r.key}`,
            labelKey: r.titleKey,
            icon: r.icon,
          })),
        ];
        const links = all.filter((l) => !l.visible || (user !== null && l.visible(user)));
        return {
          module: m,
          path: `/${m.key}`,
          records: links.filter((l) => !isReport(l)),
          reports: links.filter(isReport),
        };
      }).filter((n) => n.records.length + n.reports.length > 0),
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
