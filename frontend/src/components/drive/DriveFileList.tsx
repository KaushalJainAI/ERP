import React from "react";
import { Download, Eye, Lock, Pencil, Share2, Trash2, Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { DriveFile } from "@/types/organisation";
import { formatDate } from "@/lib/format";
import { DriveFileIcon } from "./DriveFileIcon";
import { formatBytes } from "./fileUtils";

interface Props {
  files: DriveFile[];
  empty: string;
  onPreview: (file: DriveFile) => void;
  onDownload: (file: DriveFile) => void;
  onDelete?: (file: DriveFile) => void;
  onShare?: (file: DriveFile) => void;
  onRename?: (file: DriveFile) => void;
  /** Hide the per-file folder label (company-documents panel). */
  showFolder?: boolean;
}

/** Shared file list used by the drive and the company-documents panel. */
export const DriveFileList: React.FC<Props> = ({
  files,
  empty,
  onPreview,
  onDownload,
  onDelete,
  onShare,
  onRename,
  showFolder = false,
}) => {
  const { t } = useTranslation();

  if (files.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-sm text-slate-500">
        {empty}
      </div>
    );
  }

  return (
    <ul className="divide-y divide-slate-800/80 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/50">
      {files.map((file) => (
        <li key={file.id} className="group flex items-center gap-3 px-3.5 py-3 transition-colors hover:bg-slate-800/30">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-950 text-slate-400">
            <DriveFileIcon contentType={file.content_type} className="h-4 w-4" />
          </span>

          <div className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => (file.previewable ? onPreview(file) : onDownload(file))}
              className="block max-w-full truncate text-start text-sm font-medium text-slate-200 hover:text-indigo-300"
              title={file.previewable ? t("org.drive.preview") : t("org.drive.download")}
            >
              {file.name}
            </button>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
              <span>{formatBytes(file.size)}</span>
              <span aria-hidden>-</span>
              <span className="truncate">{t("org.drive.uploaded_by", { name: file.uploaded_by_name })}</span>
              <span aria-hidden>-</span>
              <span>{formatDate(file.created_at)}</span>
              {showFolder && file.folder_name && (
                <>
                  <span aria-hidden>-</span>
                  <span className="truncate">{file.folder_name}</span>
                </>
              )}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {file.visibility === "private" ? (
              <span className="hidden items-center gap-1 rounded-full border border-amber-800/60 bg-amber-950/40 px-2 py-0.5 text-[10px] font-medium text-amber-300 sm:inline-flex">
                <Lock className="h-3 w-3" /> {t("org.drive.private_badge")}
              </span>
            ) : (
              <span className="hidden items-center gap-1 rounded-full border border-slate-700 bg-slate-800/50 px-2 py-0.5 text-[10px] font-medium text-slate-400 sm:inline-flex">
                <Users className="h-3 w-3" /> {t("org.drive.company_wide")}
              </span>
            )}
            {file.visibility === "private" && file.shared_with.length > 0 && (
              <span className="hidden rounded-full border border-indigo-800/60 bg-indigo-950/40 px-2 py-0.5 text-[10px] font-medium text-indigo-300 md:inline">
                {t("org.drive.shared_badge")}
              </span>
            )}

            {file.previewable && (
              <button
                type="button"
                onClick={() => onPreview(file)}
                title={t("org.drive.preview")}
                aria-label={t("org.drive.preview")}
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <Eye className="h-4 w-4" />
              </button>
            )}
            <button
              type="button"
              onClick={() => onDownload(file)}
              title={t("org.drive.download")}
              aria-label={t("org.drive.download")}
              className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <Download className="h-4 w-4" />
            </button>
            {onShare && file.is_owner && (
              <button
                type="button"
                onClick={() => onShare(file)}
                title={t("org.drive.share")}
                aria-label={t("org.drive.share")}
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <Share2 className="h-4 w-4" />
              </button>
            )}
            {onRename && file.can_edit && (
              <button
                type="button"
                onClick={() => onRename(file)}
                title={t("org.drive.rename")}
                aria-label={t("org.drive.rename")}
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <Pencil className="h-4 w-4" />
              </button>
            )}
            {onDelete && file.can_delete && (
              <button
                type="button"
                onClick={() => onDelete(file)}
                title={t("org.drive.delete_file_title")}
                aria-label={t("org.drive.delete_file_title")}
                className="rounded-md p-1.5 text-rose-400 hover:bg-slate-800 hover:text-rose-300"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
};
