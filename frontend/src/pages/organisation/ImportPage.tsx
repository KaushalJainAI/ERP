import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download, FileUp, Upload } from "lucide-react";
import { Layout } from "@/components/Layout";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { inputCls } from "../reports/parts";
import { td, th, theadCls } from "../reports/registers/shared";
import { btn, btnPrimary } from "../bank/shared";

type Kind = "items" | "customers" | "suppliers";
type RowStatus = "ready" | "skip" | "error";

interface PreviewRow {
  row: number;
  status: RowStatus;
  message: string;
}
interface Preview {
  kind: Kind;
  rows: PreviewRow[];
  ready: number;
  skipped: number;
  errors: number;
}
interface ImportResult {
  created: number;
  skipped: number;
  kind: Kind;
}

const KINDS: Kind[] = ["items", "customers", "suppliers"];
const STATUS_CLASS: Record<RowStatus, string> = {
  ready: "bg-emerald-950/60 text-emerald-300 border-emerald-800/60",
  skip: "bg-amber-950/60 text-amber-300 border-amber-800/60",
  error: "bg-rose-950/60 text-rose-300 border-rose-800/60",
};

export const ImportPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("items");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");

  const kindOptions = KINDS.map((k) => ({ value: k, label: t(`org.import.kind_${k}`) }));

  const reset = () => {
    setPreview(null);
    setResult(null);
    setError("");
  };

  const body = (withKind: boolean) => {
    const form = new FormData();
    if (withKind) form.append("kind", kind);
    form.append("file", file!);
    return form;
  };
  const multipart = { headers: { "Content-Type": "multipart/form-data" } };

  const download = useMutation({
    mutationFn: async () => {
      const res = await apiClient.get(`/core/import/template/`, { params: { kind }, responseType: "blob" });
      return res.data as Blob;
    },
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${kind}-template.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const check = useMutation({
    mutationFn: async () => (await apiClient.post<Preview>("/core/import/preview/", body(true), multipart)).data,
    onSuccess: (p) => {
      setPreview(p);
      setResult(null);
      setError("");
    },
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const confirm = useMutation({
    mutationFn: async () => (await apiClient.post<ImportResult>("/core/import/confirm/", body(true), multipart)).data,
    onSuccess: (r) => {
      setResult(r);
      setPreview(null);
      setError("");
      toast.success(t("org.import.done", { created: r.created, skipped: r.skipped }));
    },
    onError: (e: any) => {
      // A 400 carries the preview with the rows that need a fix.
      const data = e?.response?.data;
      if (data && Array.isArray(data.rows)) {
        setPreview(data as Preview);
        setError(data.detail ?? t("org.import.nothing"));
      } else {
        setError(summariseError(normaliseError(e)));
      }
    },
  });

  const canConfirm = !!preview && preview.errors === 0 && preview.ready > 0;
  const busy = download.isPending || check.isPending || confirm.isPending;

  return (
    <Layout>
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl">
          <div className="p-5 border-b border-slate-800">
            <h1 className="text-xl font-semibold text-slate-100 flex items-center gap-2">
              <FileUp className="w-5 h-5 text-slate-400" /> {t("org.import.title")}
            </h1>
            <p className="mt-1 text-sm text-slate-400">{t("org.import.intro")}</p>
          </div>

          <div className="p-5 space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1">
                <label htmlFor="import-kind" className="block text-xs font-medium text-slate-400">
                  {t("org.import.kind")}
                </label>
                <Select
                  id="import-kind"
                  value={kind}
                  options={kindOptions}
                  onChange={(v) => {
                    setKind(v as Kind);
                    reset();
                  }}
                  className="w-full"
                />
                <p className="text-xs text-slate-500">{t(`org.import.columns_${kind}`)}</p>
              </div>
              <div className="space-y-1">
                <label htmlFor="import-file" className="block text-xs font-medium text-slate-400">
                  {t("org.import.file")}
                </label>
                <input
                  id="import-file"
                  type="file"
                  accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className={`${inputCls} w-full`}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    reset();
                  }}
                />
                <p className="text-xs text-slate-500">{t("org.import.file_hint")}</p>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <button className={btn} disabled={busy} onClick={() => download.mutate()}>
                <Download className="w-4 h-4" /> {t("org.import.template")}
              </button>
              <button className={btn} disabled={!file || busy} onClick={() => check.mutate()}>
                <Upload className="w-4 h-4" /> {t("org.import.check")}
              </button>
            </div>

            {error && (
              <p role="alert" className="text-sm text-rose-300 break-words">
                {error}
              </p>
            )}
            {result && (
              <p role="status" className="text-sm text-emerald-400">
                {t("org.import.done", { created: result.created, skipped: result.skipped })}
              </p>
            )}

            {preview && (
              <div className="space-y-3">
                <p className="text-sm text-slate-300">
                  {t("org.import.summary", { ready: preview.ready, skipped: preview.skipped, errors: preview.errors })}
                </p>
                <div className="overflow-x-auto border border-slate-800 rounded-lg">
                  <table className="w-full text-sm text-left">
                    <thead className={theadCls}>
                      <tr>
                        <th className={th}>{t("org.import.row")}</th>
                        <th className={th}>{t("org.import.status")}</th>
                        <th className={th}>{t("org.import.message")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60 text-slate-300">
                      {preview.rows.map((r) => (
                        <tr key={r.row}>
                          <td className={`${td} tabular-nums`}>{r.row}</td>
                          <td className={td}>
                            <span className={`inline-block px-2 py-0.5 rounded-full border text-xs ${STATUS_CLASS[r.status]}`}>
                              {t(`org.import.status_${r.status}`)}
                            </span>
                          </td>
                          <td className={`${td} break-words`}>{r.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button className={btnPrimary} disabled={!canConfirm || busy} onClick={() => confirm.mutate()}>
                  <FileUp className="w-4 h-4" />{" "}
                  {canConfirm ? t("org.import.confirm", { count: preview.ready }) : t("org.import.confirm_blocked")}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
};
