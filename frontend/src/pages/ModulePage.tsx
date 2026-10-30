import React, { useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Search, X } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useModuleNav, type NavGroup } from "@/resources/nav";
import { badgeClass, useNavBadges } from "@/resources/navBadges";
import type { NavLink } from "@/types";

const Tile: React.FC<{ link: NavLink }> = ({ link }) => {
  const { t } = useTranslation();
  const Icon = link.icon;
  const count = useNavBadges()[link.badge ?? ""] ?? 0;
  return (
    <Link
      to={link.path}
      className="group flex min-w-0 items-center gap-2.5 rounded-xl border border-slate-800 bg-slate-900 p-2.5 shadow-sm transition-all hover:border-indigo-700 hover:shadow-lg sm:gap-3 sm:p-4"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-950 text-indigo-400 sm:h-10 sm:w-10">
        {Icon ? <Icon className="h-4 w-4 sm:h-5 sm:w-5" /> : null}
      </span>
      <span className="line-clamp-2 min-w-0 flex-1 text-xs font-medium leading-snug text-slate-200 group-hover:text-slate-100 sm:text-sm">
        {t(link.labelKey)}
      </span>
      {count > 0 && <span className={badgeClass}>{count}</span>}
      <ArrowRight className="hidden h-3.5 w-3.5 shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 sm:block sm:h-4 sm:w-4 rtl:rotate-180" />
    </Link>
  );
};

const Section: React.FC<{ group: NavGroup }> = ({ group }) => {
  const { t } = useTranslation();
  if (group.links.length === 0) return null;
  return (
    <section className="space-y-2.5 sm:space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{t(group.titleKey)}</h2>
        <span className="text-[10px] tabular-nums text-slate-600">{group.links.length}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-2 sm:gap-3 lg:grid-cols-3 xl:grid-cols-4">
        {group.links.map((l) => (
          <Tile key={l.path} link={l} />
        ))}
      </div>
    </section>
  );
};

/** Landing page of a module (/<module>): every document, master and report the user can open. */
export const ModulePage: React.FC<{ moduleKey: string }> = ({ moduleKey }) => {
  const { t } = useTranslation();
  const entry = useModuleNav().find((n) => n.module.key === moduleKey);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    if (!entry) return [];
    const q = query.trim().toLowerCase();
    if (!q) return entry.groups;
    return entry.groups
      .map((group) => {
        const groupMatches = t(group.titleKey).toLowerCase().includes(q);
        return {
          ...group,
          links: group.links.filter((l) => groupMatches || t(l.labelKey).toLowerCase().includes(q)),
        };
      })
      .filter((group) => group.links.length > 0);
  }, [entry, query, t]);

  if (!entry) return <Navigate to="/" replace />;
  const Icon = entry.module.icon;
  const searching = query.trim().length > 0;

  return (
    <Layout>
      <div className="space-y-6 sm:space-y-8">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-lg shadow-indigo-600/20 sm:h-11 sm:w-11">
              <Icon className="h-5 w-5" />
            </span>
            <h1 className="truncate text-xl font-semibold tracking-tight text-slate-100 sm:text-2xl">
              {t(entry.module.titleKey)}
            </h1>
          </div>
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute start-3 top-3 h-4 w-4 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("search")}
              aria-label={t("search")}
              className="w-full rounded-lg border border-slate-800 bg-slate-950 py-2.5 ps-9 pe-9 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
            />
            {searching && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("common.clear")}
                className="absolute end-2 top-2 rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>

        {groups.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-800 bg-slate-900/40 px-4 py-10 text-center text-sm text-slate-500">
            {t("common.no_results")}
          </p>
        ) : (
          groups.map((group) => <Section key={group.key} group={group} />)
        )}
      </div>
    </Layout>
  );
};
