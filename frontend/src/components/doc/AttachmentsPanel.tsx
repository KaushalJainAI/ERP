import React, { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Camera,
  Download,
  Eye,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Loader2,
  Lock,
  Paperclip,
  Trash2,
  Upload,
} from "lucide-react";
import { apiClient } from "@/api/client";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";

interface AttachmentRow {
  id: number;
  original_filename: string;
  content_type: string;
  size: number;
  uploaded_by_name: string;
  uploaded_at: string;
  description: string;
  can_delete: boolean;
  previewable: boolean;
}

interface AttachmentList {
  results: AttachmentRow[];
  max_bytes: number;
  allowed_extensions: string[];
  can_upload: boolean;
}

interface Props {
  /** DRF list endpoint of the parent resource, e.g. "/selling/sales-invoices/". */
  endpoint: string;
  /** Parent record id; undefined while the record is still unsaved. */
  objectId?: string | number;
  /** Parent is submitted/cancelled: proof can still be added but never removed. */
  locked?: boolean;
}

const API_PATH = "/core/attachments/";

const formatSize = (n: number) =>
  n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

const formatStamp = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const iconFor = (ct: string) =>
  ct.startsWith("image/") ? ImageIcon : ct === "application/pdf" || ct === "text/csv" ? FileText : FileSpreadsheet;

/** Read the stable `code` the API returns on a refusal (blob responses included). */
async function errorCode(err: any): Promise<string | undefined> {
  let data = err?.response?.data;
  if (data instanceof Blob) {
    try {
      data = JSON.parse(await data.text());
    } catch {
      return undefined;
    }
  }
  return typeof data?.code === "string" ? data.code : undefined;
}

export const AttachmentsPanel: React.FC<Props> = ({ endpoint, objectId, locked }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<AttachmentRow | null>(null);

  const saved = objectId !== undefined && objectId !== null && objectId !== "";
  const key = ["attachments", endpoint, String(objectId)];

  const { data, isLoading, error } = useQuery({
    queryKey: key,
    enabled: saved,
    queryFn: async () => (await apiClient.get<AttachmentList>(API_PATH, { params: { endpoint, object_id: objectId } })).data,
  });

  const mb = data ? `${+(data.max_bytes / (1024 * 1024)).toFixed(1)}` : "10";

  const explain = async (err: any) => {
    const code = await errorCode(err);
    if (code) {
      const msg = t(`att.err.${code}`, { mb, defaultValue: "" });
      if (msg) return msg;
    }
    return t("att.err.generic");
  };

  const uploadFiles = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      setUploading(file.name);
      const body = new FormData();
      body.append("endpoint", endpoint);
      body.append("object_id", String(objectId));
      body.append("file", file);
      try {
        await apiClient.post(API_PATH, body, { headers: { "Content-Type": "multipart/form-data" } });
        toast.success(t("att.uploaded", { name: file.name }));
      } catch (e) {
        toast.error(`${file.name}: ${await explain(e)}`);
      }
    }
    setUploading(null);
    qc.invalidateQueries({ queryKey: key });
  };

  const del = useMutation({
    mutationFn: async (row: AttachmentRow) => {
      await apiClient.delete(`${API_PATH}${row.id}/`);
      return row;
    },
    onSuccess: (row) => {
      toast.success(t("att.deleted", { name: row.original_filename }));
      setToDelete(null);
      qc.invalidateQueries({ queryKey: key });
    },
    onError: async (e) => {
      toast.error(await explain(e));
      setToDelete(null);
      qc.invalidateQueries({ queryKey: key });
    },
  });

  /** Fetch with the auth header (the endpoint is never public), then open or save the blob. */
  const fetchFile = async (row: AttachmentRow, inline: boolean) => {
    // Open the tab synchronously so the popup blocker allows it.
    const tab = inline ? window.open("", "_blank") : null;
    try {
      const res = await apiClient.get(`${API_PATH}${row.id}/download/`, {
        params: inline ? { inline: 1 } : undefined,
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(res.data as Blob);
      if (inline && tab) {
        tab.location.href = url;
      } else {
        const a = document.createElement("a");
        a.href = url;
        a.download = row.original_filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      tab?.close();
      toast.error(await explain(e));
    }
  };

  const canUpload = saved && !!data?.can_upload;
  const accept = data ? data.allowed_extensions.map((e) => `.${e}`).join(",") : undefined;
  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) void uploadFiles(e.target.files);
    e.target.value = "";
  };

  // Not an attachable record (400) or no read access (403): no panel at all.
  const status = (error as any)?.response?.status;
  if (status === 400 || status === 403) return null;

  return (
    <section
      className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3"
      aria-label={t("att.title")}
      data-testid="attachments-panel"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Paperclip className="w-4 h-4 text-slate-400" /> {t("att.title")}
          {data && <span className="text-xs font-normal text-slate-500">({data.results.length})</span>}
        </h3>
        {canUpload && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={!!uploading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50"
            >
              <Upload className="w-3.5 h-3.5" /> {t("att.add_file")}
            </button>
            <button
              type="button"
              onClick={() => cameraRef.current?.click()}
              disabled={!!uploading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 disabled:opacity-50 sm:hidden"
            >
              <Camera className="w-3.5 h-3.5" /> {t("att.take_photo")}
            </button>
          </div>
        )}
      </header>

      {canUpload && (
        <>
          <input ref={fileRef} type="file" multiple accept={accept} className="hidden" onChange={onPick} />
          <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={onPick} />
        </>
      )}

      {!saved ? (
        <p className="text-xs text-slate-500">{t("att.save_first")}</p>
      ) : isLoading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
        </div>
      ) : error || !data ? (
        <p className="text-xs text-rose-400">{t("att.load_failed")}</p>
      ) : (
        <>
          {canUpload && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (e.dataTransfer.files?.length) void uploadFiles(e.dataTransfer.files);
              }}
              className={`rounded-lg border border-dashed px-3 py-3 text-center text-xs ${
                dragging ? "border-indigo-400 bg-indigo-500/10 text-indigo-200" : "border-slate-700 text-slate-500"
              }`}
            >
              {uploading ? (
                <span className="inline-flex items-center gap-2 text-slate-300">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t("att.uploading", { name: uploading })}
                </span>
              ) : (
                <>
                  <div>{t("att.drop_here")}</div>
                  <div className="mt-0.5 text-[11px]">{t("att.hint", { mb })}</div>
                </>
              )}
            </div>
          )}

          {data.results.length === 0 ? (
            <p className="text-xs text-slate-500">{t("att.empty")}</p>
          ) : (
            <ul className="divide-y divide-slate-800">
              {data.results.map((row) => {
                const Icon = iconFor(row.content_type);
                return (
                  <li key={row.id} className="flex items-center gap-3 py-2">
                    <Icon className="w-5 h-5 shrink-0 text-slate-400" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-slate-200" title={row.original_filename}>
                        {row.original_filename}
                      </div>
                      <div className="text-[11px] text-slate-500">
                        {formatSize(row.size)} ·{" "}
                        {t("att.uploaded_by", { name: row.uploaded_by_name, date: formatStamp(row.uploaded_at) })}
                        {row.description ? ` · ${row.description}` : ""}
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      {row.previewable && (
                        <button
                          type="button"
                          onClick={() => void fetchFile(row, true)}
                          title={t("att.view")}
                          aria-label={t("att.view")}
                          className="p-1.5 rounded-md text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void fetchFile(row, false)}
                        title={t("att.download")}
                        aria-label={t("att.download")}
                        className="p-1.5 rounded-md text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                      {row.can_delete && (
                        <button
                          type="button"
                          onClick={() => setToDelete(row)}
                          title={t("att.delete")}
                          aria-label={t("att.delete")}
                          className="p-1.5 rounded-md text-rose-400 hover:text-rose-300 hover:bg-slate-800"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {locked && data.results.length > 0 && (
            <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <Lock className="w-3 h-3" /> {t("att.locked_note")}
            </p>
          )}
        </>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title={t("att.delete_title")}
        message={t("att.delete_message", { name: toDelete?.original_filename ?? "" })}
        confirmLabel={t("att.delete")}
        tone="danger"
        busy={del.isPending}
        onConfirm={() => toDelete && del.mutate(toDelete)}
        onClose={() => setToDelete(null)}
      />
    </section>
  );
};
