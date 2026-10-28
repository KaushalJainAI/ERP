import React from "react";
import { Link, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useModuleNav } from "@/resources/nav";
import type { NavLink } from "@/types";

const Tile: React.FC<{ link: NavLink }> = ({ link }) => {
  const { t } = useTranslation();
  const Icon = link.icon;
  return (
    <Link
      to={link.path}
      className="group flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-sm transition-all hover:border-indigo-700 hover:shadow-lg"
    >
      <span className="w-10 h-10 shrink-0 rounded-lg bg-indigo-950 text-indigo-400 flex items-center justify-center">
        {Icon ? <Icon className="w-5 h-5" /> : null}
      </span>
      <span className="flex-1 min-w-0 text-sm font-medium text-slate-200 group-hover:text-slate-100 truncate">
        {t(link.labelKey)}
      </span>
      <ArrowRight className="w-4 h-4 shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 rtl:rotate-180" />
    </Link>
  );
};

const Section: React.FC<{ titleKey: string; links: NavLink[] }> = ({ titleKey, links }) => {
  const { t } = useTranslation();
  if (links.length === 0) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{t(titleKey)}</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {links.map((l) => (
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
  if (!entry) return <Navigate to="/dashboard" replace />;
  const Icon = entry.module.icon;

  return (
    <Layout>
      <div className="space-y-8">
        <div className="flex items-center gap-3">
          <span className="w-11 h-11 rounded-xl bg-indigo-600 text-white flex items-center justify-center shadow-lg shadow-indigo-600/20">
            <Icon className="w-5 h-5" />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{t(entry.module.titleKey)}</h1>
        </div>
        <Section titleKey="shell.section_records" links={entry.records} />
        <Section titleKey="shell.section_reports" links={entry.reports} />
      </div>
    </Layout>
  );
};
