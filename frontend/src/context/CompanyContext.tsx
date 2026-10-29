import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiClient } from "@/api/client";
import { listAllRecords } from "@/api/endpoints";
import { queryClient } from "@/lib/queryClient";
import { getActiveCompanyId, setActiveCompanyId } from "@/lib/company";
import { useAuth } from "@/hooks/useAuth";

export interface CompanySummary {
  id: number;
  name: string;
  abbr: string;
  gstin?: string | null;
  is_default?: boolean;
  can_write?: boolean;
}

interface CompanyContextValue {
  companies: CompanySummary[];
  activeCompany: CompanySummary | null;
  loading: boolean;
  error: string | null;
  selectCompany: (company: CompanySummary) => void;
  clearActiveCompany: () => void;
  refresh: () => Promise<void>;
}

const CompanyContext = createContext<CompanyContextValue | null>(null);

function normalise(rows: Record<string, any>[]): CompanySummary[] {
  return rows
    .filter((row) => row && row.id != null)
    .map((row) => ({
      id: Number(row.id),
      name: String(row.name ?? ""),
      abbr: String(row.abbr ?? ""),
      gstin: row.gstin ?? null,
      is_default: !!row.is_default,
      can_write: !!row.can_write,
    }));
}

export const CompanyProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, loading: authLoading } = useAuth();
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [activeCompany, setActiveCompany] = useState<CompanySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) {
      setCompanies([]);
      setActiveCompany(null);
      setActiveCompanyId(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const rows = normalise(await listAllRecords("/core/companies/", { is_active: true }));
      setCompanies(rows);
      const stored = getActiveCompanyId();
      const selected = rows.find((row) => row.id === stored);
      if (selected) {
        setActiveCompany(selected);
      } else if (rows.length === 1) {
        setActiveCompany(rows[0]);
        setActiveCompanyId(rows[0].id);
      } else {
        setActiveCompany(null);
        setActiveCompanyId(null);
      }
    } catch {
      setCompanies([]);
      setActiveCompany(null);
      setError("companies_load_failed");
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (authLoading) return;
    load();
  }, [authLoading, load]);

  const selectCompany = useCallback((company: CompanySummary) => {
    setActiveCompany(company);
    setActiveCompanyId(company.id);
    // Every cached list/report belongs to the previous company. Clear it before
    // the next page paints, so no row can flash from the wrong company.
    queryClient.clear();
  }, []);

  const clearActiveCompany = useCallback(() => {
    setActiveCompany(null);
    setActiveCompanyId(null);
    queryClient.clear();
  }, []);

  const value = useMemo<CompanyContextValue>(
    () => ({
      companies,
      activeCompany,
      loading,
      error,
      selectCompany,
      clearActiveCompany,
      refresh: load,
    }),
    [companies, activeCompany, loading, error, selectCompany, clearActiveCompany, load]
  );

  return <CompanyContext.Provider value={value}>{children}</CompanyContext.Provider>;
};

export function useCompany(): CompanyContextValue {
  const ctx = useContext(CompanyContext);
  if (!ctx) throw new Error("useCompany must be used inside <CompanyProvider>");
  return ctx;
}
