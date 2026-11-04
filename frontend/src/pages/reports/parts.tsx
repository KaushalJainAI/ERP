import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { fetchCompanies } from "@/api/lookups";
import { normaliseError } from "@/api/endpoints";
import { sumDec } from "@/lib/decimal";
import { negDec } from "@/components/accounts/BalanceBanner";
import { Select } from "@/components/ui/Select";

export const inputCls =
  "bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500";

/** a - b on decimal strings (exact). */
export const subDec = (a: string, b: string): string => sumDec([a, negDec(b)], 2);

/** True when a decimal string equals zero. */
export const isZero = (v: string): boolean => /^-?0+(\.0+)?$/.test(v);

/** Company picker state: defaults to the first company once the list loads. */
export function useCompany() {
  const { data: companies = [], isLoading } = useQuery({
    queryKey: ["companies"],
    queryFn: () => fetchCompanies(""),
    staleTime: 5 * 60 * 1000,
  });
  const [company, setCompany] = useState<string>("");
  useEffect(() => {
    if (!company && companies.length) setCompany(String(companies[0].id));
  }, [companies, company]);
  return { companies, company, setCompany, loadingCompanies: isLoading };
}

/** GET a report endpoint; disabled until `enabled`. */
export function useReport<T>(path: string, params: Record<string, string>, enabled: boolean) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== ""));
  return useQuery({
    queryKey: ["acc-report", path, clean],
    queryFn: async () => (await apiClient.get<T>(path, { params: clean })).data,
    enabled,
  });
}

/** Balanced / not-balanced pill driven by a real comparison, never a constant. */
export const StatusPill: React.FC<{ ok: boolean }> = ({ ok }) => {
  const { t } = useTranslation();
  return ok ? (
    <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-emerald-950 text-emerald-400 border border-emerald-800 font-semibold">
      <CheckCircle2 className="w-3.5 h-3.5" /> {t("acc.report.status_balanced")}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-rose-950 text-rose-300 border border-rose-800 font-semibold">
      <AlertTriangle className="w-3.5 h-3.5" /> {t("acc.report.status_unbalanced")}
    </span>
  );
};

export const CompanySelect: React.FC<{
  companies: Record<string, any>[];
  value: string;
  onChange: (v: string) => void;
}> = ({ companies, value, onChange }) => {
  const { t } = useTranslation();
  if (companies.length <= 1) return null;
  return (
    <div className="space-y-1">
      <label htmlFor="rep-company" className="block text-xs font-medium text-slate-400">
        {t("acc.report.company")}
      </label>
      <Select
        id="rep-company"
        value={value}
        onChange={onChange}
        className="w-56"
        options={companies.map((c) => ({ value: String(c.id), label: String(c.name) }))}
      />
    </div>
  );
};

export const DateInput: React.FC<{ id: string; labelKey: string; value: string; onChange: (v: string) => void }> = ({
  id,
  labelKey,
  value,
  onChange,
}) => {
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-slate-400">
        {t(labelKey)}
      </label>
      <input id={id} type="date" value={value} onChange={(e) => onChange(e.target.value)} className={inputCls} />
    </div>
  );
};

/** Page chrome: title, filter bar, status slot, loading / error / empty-company states. */
export const ReportShell: React.FC<{
  titleKey: string;
  filters: React.ReactNode;
  status?: React.ReactNode;
  loading: boolean;
  error: unknown;
  noCompany: boolean;
  children: React.ReactNode;
}> = ({ titleKey, filters, status, loading, error, noCompany, children }) => {
  const { t } = useTranslation();
  const err = error ? normaliseError(error) : null;
  return (
    <Layout>
      <div className="max-w-6xl mx-auto bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="p-5 border-b border-slate-800 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="text-xl font-semibold text-slate-100 tracking-tight flex items-center gap-2">
              {t(titleKey)}
              {loading && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
            </h1>
            {status}
          </div>
          <div className="flex flex-wrap items-end gap-4">{filters}</div>
        </div>
        {noCompany ? (
          <div className="px-6 py-12 text-center text-sm text-slate-500">{t("acc.report.no_company")}</div>
        ) : err ? (
          <div className="px-6 py-12 text-center space-y-2">
            <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" />
            <p className="text-sm text-rose-300">{err.forbidden ? t("common.forbidden") : t("acc.report.load_failed")}</p>
            {!err.forbidden && err.general[0] && <p className="text-xs text-slate-500">{err.general[0]}</p>}
          </div>
        ) : (
          children
        )}
      </div>
    </Layout>
  );
};
