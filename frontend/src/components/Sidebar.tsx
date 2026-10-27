import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LayoutDashboard } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { MODULES, reportsOf, resourcesOf } from "@/resources/registry";
import type { NavLink } from "@/types";

export const Sidebar: React.FC = () => {
  const { t } = useTranslation();
  const location = useLocation();
  const { user } = useAuth();

  const isActive = (path: string) => location.pathname === path || location.pathname.startsWith(path + "/");

  const renderLink = (link: NavLink) => {
    const Icon = link.icon;
    const active = isActive(link.path);
    return (
      <Link
        key={link.path}
        to={link.path}
        className={`flex items-center gap-3 px-3.5 py-2 rounded-lg text-sm font-medium transition-all ${
          active
            ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 shadow-md shadow-indigo-500/10"
            : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
        }`}
      >
        {Icon ? <Icon className={`w-4 h-4 ${active ? "text-indigo-400" : "text-slate-500"}`} /> : <span className="w-4" />}
        <span>{t(link.labelKey)}</span>
      </Link>
    );
  };

  // Sections are generated from the resource registry; modules with nothing in them are hidden.
  const sections = MODULES.map((m) => {
    const links: NavLink[] = [
      ...(m.extraLinks ?? []),
      ...resourcesOf(m.key).map((r) => ({
        path: `/${r.module}/${r.key}`,
        labelKey: r.titleKey,
        icon: r.icon,
      })),
      ...reportsOf(m.key).map((r) => ({
        path: `/${r.module}/reports/${r.key}`,
        labelKey: r.titleKey,
        icon: r.icon,
      })),
    ];
    return { module: m, links: links.filter((l) => !l.visible || (user !== null && l.visible(user))) };
  }).filter((s) => s.links.length > 0);

  return (
    <aside className="w-64 shrink-0 bg-slate-950 border-e border-slate-800 flex flex-col justify-between min-h-screen p-4 overflow-y-auto">
      <div className="space-y-5">
        <div className="flex items-center gap-3 px-2 py-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-600/30">
            ERP
          </div>
          <div>
            <h1 className="font-bold text-slate-100 text-lg tracking-tight leading-tight">{t("appName")}</h1>
            <span className="text-[10px] uppercase font-semibold tracking-widest text-indigo-400">{t("shell.tagline")}</span>
          </div>
        </div>

        <nav className="space-y-4" aria-label={t("shell.main_navigation")}>
          {renderLink({ path: "/dashboard", labelKey: "dashboard", icon: LayoutDashboard })}
          {sections.map(({ module: m, links }) => {
            const Icon = m.icon;
            return (
              <div key={m.key} className="space-y-1">
                <div className="flex items-center gap-2 px-3.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <Icon className="w-3.5 h-3.5" />
                  {t(m.titleKey)}
                </div>
                {links.map(renderLink)}
              </div>
            );
          })}
        </nav>
      </div>
    </aside>
  );
};
