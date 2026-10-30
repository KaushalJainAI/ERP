import React, { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronDown, Search, X } from "lucide-react";
import { activeGroupOf, isActivePath, type ModuleNav, type NavGroup } from "@/resources/nav";
import { badgeClass, useNavBadges } from "@/resources/navBadges";
import type { NavGroupKey, NavLink } from "@/types";

const SideLink: React.FC<{ link: NavLink; active: boolean; count: number }> = ({ link, active, count }) => {
  const { t } = useTranslation();
  const Icon = link.icon;
  return (
    <Link
      to={link.path}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
        active ? "bg-indigo-950 text-indigo-200" : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60"
      }`}
    >
      {Icon && <Icon className={`w-4 h-4 shrink-0 ${active ? "text-indigo-400" : "text-slate-500"}`} />}
      <span className="truncate">{t(link.labelKey)}</span>
      {count > 0 && <span className={badgeClass}>{count}</span>}
    </Link>
  );
};

const GroupSection: React.FC<{
  group: NavGroup;
  open: boolean;
  searching: boolean;
  pathname: string;
  badges: Record<string, number>;
  onToggle: () => void;
}> = ({ group, open, searching, pathname, badges, onToggle }) => {
  const { t } = useTranslation();
  const expanded = searching || open;
  return (
    <div>
      {searching ? (
        <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
          {t(group.titleKey)}
        </p>
      ) : (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex w-full items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500 transition-colors hover:bg-slate-800/40 hover:text-slate-300"
        >
          <span>{t(group.titleKey)}</span>
          <span className="flex items-center gap-1.5">
            <span className="tabular-nums text-slate-600">{group.links.length}</span>
            <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} />
          </span>
        </button>
      )}
      {expanded && (
        <div className="mt-0.5 space-y-0.5">
          {group.links.map((link) => (
            <SideLink
              key={link.path}
              link={link}
              active={isActivePath(pathname, link.path)}
              count={link.badge ? badges[link.badge] ?? 0 : 0}
            />
          ))}
        </div>
      )}
    </div>
  );
};

/** Desktop sidebar: the current module's pages, grouped and searchable. */
export const Sidebar: React.FC<{ active: ModuleNav }> = ({ active }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const badges = useNavBadges();
  const [query, setQuery] = useState("");
  const activeGroup = activeGroupOf(active, pathname);
  const [openGroups, setOpenGroups] = useState<Set<NavGroupKey>>(
    () => new Set(activeGroup ? [activeGroup] : active.groups[0] ? [active.groups[0].key] : [])
  );

  // Opening a page in a collapsed group reveals that group.
  useEffect(() => {
    if (!activeGroup) return;
    setOpenGroups((prev) => (prev.has(activeGroup) ? prev : new Set(prev).add(activeGroup)));
  }, [activeGroup]);

  const searching = query.trim().length > 0;
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return active.groups;
    return active.groups
      .map((group) => {
        const groupMatches = t(group.titleKey).toLowerCase().includes(q);
        return {
          ...group,
          links: group.links.filter((l) => groupMatches || t(l.labelKey).toLowerCase().includes(q)),
        };
      })
      .filter((group) => group.links.length > 0);
  }, [active.groups, query, t]);

  const Icon = active.module.icon;

  return (
    <aside className="hidden md:flex w-60 shrink-0 flex-col border-e border-slate-800 bg-slate-900/40">
      <div className="sticky top-[calc(2.75rem_+_env(safe-area-inset-top))] flex max-h-[calc(100vh_-_2.75rem_-_env(safe-area-inset-top))] flex-col">
        <div className="p-3 pb-2">
          <Link
            to={active.path}
            className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm font-semibold text-slate-100 transition-colors hover:bg-slate-800/50 hover:text-indigo-300"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-950 text-indigo-400">
              <Icon className="h-4 w-4" />
            </span>
            <span className="truncate">{t(active.module.titleKey)}</span>
          </Link>
        </div>

        <div className="px-3 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute start-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("search")}
              aria-label={t("search")}
              className="w-full rounded-lg border border-slate-800 bg-slate-950 py-2 pe-8 ps-8 text-xs text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
            />
            {searching && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("common.clear")}
                className="absolute end-1.5 top-1.5 rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 pb-5" aria-label={t(active.module.titleKey)}>
          {groups.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-slate-500">{t("common.no_results")}</p>
          ) : (
            <div className="space-y-1">
              {groups.map((group) => (
                <GroupSection
                  key={group.key}
                  group={group}
                  open={openGroups.has(group.key)}
                  searching={searching}
                  pathname={pathname}
                  badges={badges}
                  onToggle={() =>
                    setOpenGroups((prev) => {
                      const next = new Set(prev);
                      if (next.has(group.key)) next.delete(group.key);
                      else next.add(group.key);
                      return next;
                    })
                  }
                />
              ))}
            </div>
          )}
        </nav>
      </div>
    </aside>
  );
};
