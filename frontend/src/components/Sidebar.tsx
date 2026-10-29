import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isActivePath, type ModuleNav } from "@/resources/nav";
import { badgeClass, useNavBadges } from "@/resources/navBadges";
import type { NavLink } from "@/types";

const SideLink: React.FC<{ link: NavLink; active: boolean }> = ({ link, active }) => {
  const { t } = useTranslation();
  const Icon = link.icon;
  const count = useNavBadges()[link.badge ?? ""] ?? 0;
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

const Section: React.FC<{ titleKey: string; links: NavLink[] }> = ({ titleKey, links }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  if (links.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{t(titleKey)}</p>
      {links.map((l) => (
        <SideLink key={l.path} link={l} active={isActivePath(pathname, l.path)} />
      ))}
    </div>
  );
};

/** Desktop sidebar: the sub-pages of the module selected in the top bar. */
export const Sidebar: React.FC<{ active: ModuleNav }> = ({ active }) => {
  const { t } = useTranslation();
  return (
    <aside className="hidden md:block w-60 shrink-0 border-e border-slate-800 bg-slate-900/40">
      <nav
        className="sticky top-14 max-h-[calc(100vh-3.5rem)] overflow-y-auto p-3 space-y-5"
        aria-label={t(active.module.titleKey)}
      >
        <Link
          to={active.path}
          className="block px-3 pt-1 text-sm font-semibold text-slate-100 hover:text-indigo-300"
        >
          {t(active.module.titleKey)}
        </Link>
        <Section titleKey="shell.section_records" links={active.records} />
        <Section titleKey="shell.section_reports" links={active.reports} />
      </nav>
    </aside>
  );
};
