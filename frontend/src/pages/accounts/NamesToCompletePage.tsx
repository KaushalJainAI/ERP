import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertCircle, ListChecks, Loader2 } from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";

export type NameKind = "customer" | "supplier" | "item";
export type MissingPart = "state" | "gstin" | "group" | "hsn" | "tax_template";

export interface NameToComplete {
  kind: NameKind;
  id: number;
  name: string;
  missing: MissingPart[];
}

interface NamesToCompleteData {
  items: NameToComplete[];
  count: number;
}

const KIND_ORDER: NameKind[] = ["customer", "supplier", "item"];

const KIND_PATH: Record<NameKind, string> = {
  customer: "/selling/customers",
  supplier: "/buying/suppliers",
  item: "/stock/items",
};

const KIND_LABEL_KEY: Record<NameKind, string> = {
  customer: "acc.names.kind_customer",
  supplier: "acc.names.kind_supplier",
  item: "acc.names.kind_item",
};

const MISSING_LABEL_KEY: Record<MissingPart, string> = {
  state: "acc.names.missing_state",
  gstin: "acc.names.missing_gstin",
  group: "acc.names.missing_group",
  hsn: "acc.names.missing_hsn",
  tax_template: "acc.names.missing_tax_template",
};

const namesQueryKey = (companyName: string | undefined) => ["accounts", "names-to-complete", companyName ?? null];

/** Shared query: the page and the count hook use the same cache entry. */
export function useNamesToComplete() {
  const { activeCompany } = useCompany();
  return useQuery({
    queryKey: namesQueryKey(activeCompany?.name),
    queryFn: async (): Promise<NamesToCompleteData> => {
      const res = await apiClient.get("/contacts/names-to-complete/");
      return res.data;
    },
    enabled: !!activeCompany,
  });
}

/** How many customers, suppliers and items still need details. For an attention row on a home screen. */
export function useNamesToCompleteCount(): number {
  const { data } = useNamesToComplete();
  return data?.count ?? 0;
}

export const NamesToCompletePage: React.FC = () => {
  const { t } = useTranslation();
  const { data, isLoading, isFetching, error } = useNamesToComplete();
  const items = data?.items ?? [];

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            {t("acc.names.title")}
            {isFetching && <Loader2 className="w-5 h-5 animate-spin text-indigo-400" />}
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t("acc.names.subtitle")}</p>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-rose-400 text-sm">
            <AlertCircle className="w-4 h-4" />
            {summariseError(normaliseError(error))}
          </div>
        )}

        {isLoading ? (
          <div className="p-12 flex justify-center text-slate-400">
            <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
          </div>
        ) : items.length === 0 && !error ? (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-12 text-center text-slate-400 text-sm">
            <ListChecks className="w-8 h-8 mx-auto mb-3 text-slate-600" />
            {t("acc.names.empty")}
          </div>
        ) : (
          KIND_ORDER.map((kind) => {
            const rows = items.filter((row) => row.kind === kind);
            if (rows.length === 0) return null;
            return (
              <section key={kind}>
                <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                  {t(KIND_LABEL_KEY[kind])} ({rows.length})
                </h2>
                <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden divide-y divide-slate-800/80">
                  {rows.map((row) => (
                    <Link
                      key={`${row.kind}-${row.id}`}
                      to={`${KIND_PATH[row.kind]}/${row.id}`}
                      className="flex min-h-[44px] items-center gap-3 p-4 hover:bg-slate-800/30 transition-colors"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-white truncate">{row.name}</div>
                        {row.missing.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {row.missing.map((m) => (
                              <span
                                key={m}
                                className="px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30"
                              >
                                {t(MISSING_LABEL_KEY[m])}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </Link>
                  ))}
                </div>
              </section>
            );
          })
        )}
      </div>
    </Layout>
  );
};
