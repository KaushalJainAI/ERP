import React, { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, FolderOpen, Loader2, Upload } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { useCompany } from "@/context/CompanyContext";
import type { CompanyDocumentsResponse, DriveFile } from "@/types/organisation";
import { DriveFileList } from "./DriveFileList";
import { DRIVE_FILES, openDriveFile } from "./fileUtils";

/** The "Company documents" card on the company profile page. */
export const CompanyDocumentsPanel: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const { activeCompany } = useCompany();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<DriveFile | null>(null);
  const [deleting, setDeleting] = useState(false);

  const key = ["company-documents", activeCompany?.id ?? null];
  const { data, isLoading, isError } = useQuery<CompanyDocumentsResponse>({
    queryKey: key,
    queryFn: async () => (await apiClient.get("/drive/folders/company-documents/")).data,
    enabled: !!activeCompany,
  });

  const explain = (err: unknown, fallback: string) => summariseError(normaliseError(err)) || fallback;

  const uploadFiles = async (files: FileList) => {
    if (!data) return;
    for (const file of Array.from(files)) {
      setUploading(file.name);
      try {
        const body = new FormData();
        body.append("file", file);
        body.append("folder", String(data.folder.id));
        await apiClient.post(DRIVE_FILES, body, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        toast.success(t("org.company.documents.uploaded", { name: file.name }));
      } catch (err) {
        toast.error(explain(err, t("org.company.documents.upload_failed")));
      }
    }
    setUploading(null);
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["drive-folders"] });
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) void uploadFiles(e.target.files);
    e.target.value = "";
  };

  const open = async (file: DriveFile, inline: boolean) => {
    try {
      await openDriveFile(file, inline);
    } catch (err) {
      toast.error(explain(err, t("org.drive.download_failed")));
    }
  };

  const remove = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await apiClient.delete(`${DRIVE_FILES}${toDelete.id}/`);
      toast.success(t("org.company.documents.deleted", { name: toDelete.name }));
      setToDelete(null);
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["drive-folders"] });
    } catch (err) {
      toast.error(explain(err, t("org.company.documents.delete_failed")));
    } finally {
      setDeleting(false);
    }
  };

  const accept = data?.allowed_extensions.map((ext) => `.${ext}`).join(",");
  const maxMb = data ? Math.round(data.max_bytes / (1024 * 1024)) : 25;
  const types = data?.allowed_extensions.map((ext) => ext.toUpperCase()).join(", ") ?? "";

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <FolderOpen className="h-4 w-4 text-slate-400" />
            {t("org.company.documents.title")}
          </h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-slate-400">
            {t("org.company.documents.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/organisation/drive"
            className="inline-flex items-center gap-1 rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:border-indigo-700/60 hover:text-indigo-300"
          >
            {t("org.company.documents.open_drive")}
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={!data || !!uploading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {t("org.company.documents.upload")}
          </button>
        </div>
      </header>

      <input ref={fileRef} type="file" multiple accept={accept} className="hidden" onChange={onPick} />

      <div className="mt-4">
        {uploading ? (
          <p className="mb-3 flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("org.company.documents.uploading", { name: uploading })}
          </p>
        ) : data ? (
          <p className="mb-3 text-[11px] text-slate-500">
            {t("org.company.documents.hint", { mb: maxMb, types })}
          </p>
        ) : null}

        {isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
          </div>
        ) : isError || !data ? (
          <p className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-xs text-rose-300">
            {t("org.company.documents.load_failed")}
          </p>
        ) : (
          <DriveFileList
            files={data.files}
            empty={t("org.company.documents.empty")}
            onPreview={(file) => void open(file, true)}
            onDownload={(file) => void open(file, false)}
            onDelete={(file) => setToDelete(file)}
          />
        )}
      </div>

      <ConfirmDialog
        open={!!toDelete}
        title={t("org.company.documents.delete_title")}
        message={t("org.company.documents.delete_message", { name: toDelete?.name ?? "" })}
        confirmLabel={t("common.delete")}
        tone="danger"
        busy={deleting}
        onConfirm={() => void remove()}
        onClose={() => setToDelete(null)}
      />
    </section>
  );
};
