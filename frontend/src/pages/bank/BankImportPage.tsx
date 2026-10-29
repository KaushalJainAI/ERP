import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FileUp, Upload } from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { listRecords, normaliseError, summariseError } from "@/api/endpoints";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { formatDate } from "@/lib/format";
import { inputCls } from "../reports/parts";
import { td, th, theadCls } from "../reports/registers/shared";
import { BankAccountSelect, btn, btnPrimary, useBankAccounts } from "./shared";

interface Preview {
  header_row: number | null;
  columns: string[];
  mapping: Record<string, string>;
  sample: string[][];
  row_count: number;
}
interface ImportResult {
  rows: number;
  imported: number;
  duplicates: number;
  skipped: number;
}

const FIELDS = ["date", "description", "reference", "withdrawal", "deposit", "balance", "amount", "dr_cr"] as const;
const REQUIRED = new Set(["date"]);
const NONE = "__none__";

export const BankImportPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const { accounts, bankAccount, setBankAccount, loading } = useBankAccounts();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [headerRow, setHeaderRow] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");

  const history = useQuery({
    queryKey: ["bank-statements", bankAccount],
    queryFn: async () => (await listRecords("/accounts/bank-statements/", { bank_account: bankAccount, ordering: "-imported_at" })).results,
    enabled: !!bankAccount,
  });

  const read = useMutation({
    mutationFn: async (f: File) => {
      const body = new FormData();
      body.append("file", f);
      return (await apiClient.post<Preview>("/accounts/bank-statements/preview/", body, { headers: { "Content-Type": "multipart/form-data" } })).data;
    },
    onSuccess: (p) => {
      setPreview(p);
      setMapping(p.mapping);
      setHeaderRow(p.header_row === null ? "" : String(p.header_row));
      setResult(null);
      setError("");
    },
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const run = useMutation({
    mutationFn: async () => {
      const body = new FormData();
      body.append("file", file!);
      body.append("bank_account", bankAccount);
      body.append("mapping", JSON.stringify(mapping));
      if (headerRow !== "") body.append("header_row", headerRow);
      return (await apiClient.post<ImportResult>("/accounts/bank-statements/import-statement/", body, { headers: { "Content-Type": "multipart/form-data" } })).data;
    },
    onSuccess: (r) => {
      setResult(r);
      setError("");
      toast.success(t("bank.import.done", { ...r }));
      qc.invalidateQueries({ queryKey: ["bank-statements"] });
    },
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const options = [
    { value: NONE, label: t("bank.import.not_in_file") },
    ...(preview?.columns ?? []).map((c) => ({ value: c, label: c })),
  ];
  const mapped = (field: string) => mapping[field] || NONE;
  const setField = (field: string, value: string) =>
    setMapping((m) => {
      const next = { ...m };
      if (value === NONE) delete next[field];
      else next[field] = value;
      return next;
    });
  const canImport = !!file && !!preview && !!bankAccount && !!mapping.date && (!!mapping.amount || !!mapping.withdrawal || !!mapping.deposit);

  return (
    <Layout>
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl">
          <div className="p-5 border-b border-slate-800">
            <h1 className="text-xl font-semibold text-slate-100 flex items-center gap-2">
              <FileUp className="w-5 h-5 text-slate-400" /> {t("bank.nav.import")}
            </h1>
            <p className="mt-1 text-xs text-slate-500">{t("bank.import.csv_only")}</p>
          </div>
          <div className="p-5 space-y-5">
            {!loading && accounts.length === 0 ? (
              <p className="text-sm text-slate-400">{t("bank.no_accounts")}</p>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-4">
                  <BankAccountSelect accounts={accounts} value={bankAccount} onChange={setBankAccount} />
                  <div className="space-y-1">
                    <label htmlFor="bank-file" className="block text-xs font-medium text-slate-400">
                      {t("bank.import.file")}
                    </label>
                    <input
                      id="bank-file"
                      type="file"
                      accept=".csv,text/csv"
                      className={inputCls}
                      onChange={(e) => {
                        setFile(e.target.files?.[0] ?? null);
                        setPreview(null);
                        setResult(null);
                        setError("");
                      }}
                    />
                  </div>
                  <button className={btn} disabled={!file || read.isPending} onClick={() => file && read.mutate(file)}>
                    <Upload className="w-4 h-4" /> {t("bank.import.read")}
                  </button>
                </div>

                {preview && (
                  <>
                    <div>
                      <h2 className="text-sm font-semibold text-slate-200 mb-2">{t("bank.import.map_columns")}</h2>
                      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                        {FIELDS.map((f) => (
                          <div key={f} className="space-y-1">
                            <label htmlFor={`map-${f}`} className="block text-xs font-medium text-slate-400">
                              {t(`bank.import.col.${f}`)}
                              {REQUIRED.has(f) && " *"}
                            </label>
                            <Select id={`map-${f}`} value={mapped(f)} options={options} onChange={(v) => setField(f, v)} className="w-full" />
                          </div>
                        ))}
                        <div className="space-y-1">
                          <label htmlFor="map-header" className="block text-xs font-medium text-slate-400">
                            {t("bank.import.header_row")}
                          </label>
                          <input id="map-header" type="number" min={0} value={headerRow} onChange={(e) => setHeaderRow(e.target.value)} className={`${inputCls} w-full`} />
                        </div>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">{t("bank.import.map_help")}</p>
                    </div>

                    {preview.sample.length > 0 && (
                      <div className="overflow-x-auto border border-slate-800 rounded-lg">
                        <p className="px-4 py-2 text-xs text-slate-500">{t("bank.import.sample")}</p>
                        <table className="w-full text-sm text-left">
                          <thead className={theadCls}>
                            <tr>
                              {preview.columns.map((c, i) => (
                                <th key={i} className={th}>
                                  {c}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60 text-slate-300">
                            {preview.sample.map((r, i) => (
                              <tr key={i}>
                                {preview.columns.map((_c, j) => (
                                  <td key={j} className={td}>
                                    {r[j] ?? ""}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    <button className={btnPrimary} disabled={!canImport || run.isPending} onClick={() => run.mutate()}>
                      <Upload className="w-4 h-4" /> {t("bank.import.run")}
                    </button>
                  </>
                )}

                {error && (
                  <p role="alert" className="text-sm text-rose-300 break-words">
                    {error}
                  </p>
                )}
                {result && (
                  <p role="status" className="text-sm text-emerald-400">
                    {t("bank.import.done", { ...result })}
                  </p>
                )}
              </>
            )}
          </div>
        </div>

        {(history.data?.length ?? 0) > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <h2 className="px-5 py-3 text-sm font-semibold text-slate-200 border-b border-slate-800">{t("bank.import.history")}</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className={theadCls}>
                  <tr>
                    <th className={th}>{t("bank.import.file")}</th>
                    <th className={th}>{t("bank.import.period")}</th>
                    <th className={th}>{t("bank.import.imported")}</th>
                    <th className={th}>{t("bank.import.duplicates")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {history.data!.map((r) => (
                    <tr key={r.id}>
                      <td className={td}>{r.file_name}</td>
                      <td className={td}>
                        {formatDate(r.from_date)} - {formatDate(r.to_date)}
                      </td>
                      <td className={td}>{r.imported_count}</td>
                      <td className={td}>{r.duplicate_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
