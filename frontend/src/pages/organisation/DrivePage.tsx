import React, { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  Folder,
  FolderPlus,
  Home,
  Loader2,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { PAGE_SIZE, normaliseError, summariseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { DriveFileList } from "@/components/drive/DriveFileList";
import { ShareFileDialog } from "@/components/drive/ShareFileDialog";
import { DRIVE_FILES, openDriveFile } from "@/components/drive/fileUtils";
import type { Paginated } from "@/types";
import type { DriveFile, DriveFolder, DriveMembersResponse } from "@/types/organisation";

type DeleteTarget =
  | { kind: "file"; file: DriveFile }
  | { kind: "folder"; folder: DriveFolder }
  | null;

const inputClass =
  "w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20";

/** The internal organisation drive: folders, uploads, downloads and sharing. */
export const DrivePage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const { activeCompany } = useCompany();
  const [searchParams, setSearchParams] = useSearchParams();
  const fileRef = useRef<HTMLInputElement>(null);

  const folderParam = searchParams.get("folder");
  const folderId = folderParam && /^\d+$/.test(folderParam) ? Number(folderParam) : null;

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [uploading, setUploading] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renameFile, setRenameFile] = useState<DriveFile | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [shareFile, setShareFile] = useState<DriveFile | null>(null);
  const [toDelete, setToDelete] = useState<DeleteTarget>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setPage(1);
  }, [folderId, search]);

  useEffect(() => {
    if (renameFile) setRenameValue(renameFile.name);
  }, [renameFile]);

  const companyId = activeCompany?.id ?? null;

  const { data: currentFolder, isLoading: folderLoading } = useQuery<DriveFolder>({
    queryKey: ["drive-folder", companyId, folderId],
    queryFn: async () => (await apiClient.get(`/drive/folders/${folderId}/`)).data,
    enabled: !!activeCompany && folderId !== null,
  });

  const { data: folders = [], isLoading: foldersLoading, isError: foldersError } = useQuery<DriveFolder[]>({
    queryKey: ["drive-folders", companyId, folderId, search],
    queryFn: async () => {
      const res = await apiClient.get("/drive/folders/", {
        params: { parent: folderId ?? "root", search: search || undefined },
      });
      return Array.isArray(res.data) ? res.data : (res.data?.results ?? []);
    },
    enabled: !!activeCompany,
  });

  const { data: filesPage, isLoading: filesLoading, isError: filesError } = useQuery<Paginated<DriveFile>>({
    queryKey: ["drive-files", companyId, folderId, search, page],
    queryFn: async () => {
      const res = await apiClient.get("/drive/files/", {
        params: { folder: folderId ?? "root", search: search || undefined, page },
      });
      return res.data;
    },
    enabled: !!activeCompany,
  });

  const { data: members } = useQuery<DriveMembersResponse>({
    queryKey: ["drive-members", companyId],
    queryFn: async () => (await apiClient.get("/drive/members/")).data,
    enabled: !!activeCompany,
    staleTime: 60_000,
  });

  const files = filesPage?.results ?? [];
  const totalPages = Math.max(1, Math.ceil((filesPage?.count ?? 0) / PAGE_SIZE));
  const breadcrumb = useMemo(() => {
    if (folderId === null) return [];
    return currentFolder?.path ?? [];
  }, [currentFolder, folderId]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["drive-files"] });
    qc.invalidateQueries({ queryKey: ["drive-folders"] });
    qc.invalidateQueries({ queryKey: ["drive-folder"] });
    qc.invalidateQueries({ queryKey: ["company-documents"] });
  };

  const openFolder = (id: number | null) => {
    setSearch("");
    setPage(1);
    if (id === null) setSearchParams({});
    else setSearchParams({ folder: String(id) });
  };

  const explain = (err: unknown, fallback: string) => summariseError(normaliseError(err)) || fallback;

  const uploadFiles = async (incoming: FileList) => {
    for (const file of Array.from(incoming)) {
      setUploading(file.name);
      try {
        const body = new FormData();
        body.append("file", file);
        if (folderId !== null) body.append("folder", String(folderId));
        await apiClient.post(DRIVE_FILES, body, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        toast.success(t("org.drive.uploaded", { name: file.name }));
      } catch (err) {
        toast.error(explain(err, t("org.drive.upload_failed")));
      }
    }
    setUploading(null);
    setPage(1);
    invalidate();
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) void uploadFiles(e.target.files);
    e.target.value = "";
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files?.length) void uploadFiles(e.dataTransfer.files);
  };

  const createFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    setCreatingFolder(true);
    try {
      await apiClient.post("/drive/folders/", {
        name,
        parent: folderId,
      });
      toast.success(t("org.drive.folder_created"));
      setNewFolderOpen(false);
      setNewFolderName("");
      invalidate();
    } catch (err) {
      toast.error(explain(err, t("org.drive.create_failed")));
    } finally {
      setCreatingFolder(false);
    }
  };

  const removeTarget = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      if (toDelete.kind === "file") {
        await apiClient.delete(`${DRIVE_FILES}${toDelete.file.id}/`);
        toast.success(t("org.drive.file_deleted", { name: toDelete.file.name }));
      } else {
        await apiClient.delete(`/drive/folders/${toDelete.folder.id}/`);
        toast.success(t("org.drive.folder_deleted"));
      }
      setToDelete(null);
      invalidate();
    } catch (err) {
      toast.error(
        explain(
          err,
          toDelete.kind === "file" ? t("org.drive.delete_file_failed") : t("org.drive.folder_delete_failed")
        )
      );
    } finally {
      setDeleting(false);
    }
  };

  const saveName = async () => {
    if (!renameFile) return;
    const name = renameValue.trim();
    if (!name) return;
    setSavingName(true);
    try {
      await apiClient.patch(`${DRIVE_FILES}${renameFile.id}/`, { name });
      toast.success(t("org.drive.renamed"));
      setRenameFile(null);
      invalidate();
    } catch (err) {
      toast.error(explain(err, t("org.drive.rename_failed")));
    } finally {
      setSavingName(false);
    }
  };

  const open = async (file: DriveFile, inline: boolean) => {
    try {
      await openDriveFile(file, inline);
    } catch (err) {
      toast.error(explain(err, t("org.drive.download_failed")));
    }
  };

  const accept = members?.allowed_extensions.map((ext) => `.${ext}`).join(",");
  const maxMb = members ? Math.round(members.max_bytes / (1024 * 1024)) : 25;
  const types = members?.allowed_extensions.map((ext) => ext.toUpperCase()).join(", ") ?? "";
  const hasAnything = folders.length > 0 || files.length > 0;
  const anythingLoading = foldersLoading || filesLoading;

  if (folderLoading && folderId !== null) {
    return (
      <Layout>
        <div className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-5">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-400">
              {t("org.drive.eyebrow")}
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">{t("org.drive.title")}</h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">
              {t("org.drive.subtitle", { name: activeCompany?.name ?? "" })}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setNewFolderOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-300 hover:border-indigo-700 hover:text-indigo-300"
            >
              <FolderPlus className="h-4 w-4" /> {t("org.drive.new_folder")}
            </button>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={!!uploading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-medium text-white shadow-lg shadow-indigo-600/20 hover:bg-indigo-500 disabled:opacity-50"
            >
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {t("org.drive.upload")}
            </button>
            <input ref={fileRef} type="file" multiple accept={accept} className="hidden" onChange={onPick} />
          </div>
        </header>

        <div
          className="relative space-y-5"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(e) => {
            if (e.currentTarget === e.target) setDragging(false);
          }}
          onDrop={onDrop}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl border-2 border-dashed border-indigo-500/70 bg-slate-950/80 backdrop-blur-sm">
              <div className="flex flex-col items-center gap-2 text-sm font-medium text-indigo-200">
                <Upload className="h-7 w-7" />
                {t("org.drive.drop_here")}
              </div>
            </div>
          )}

          <div className="rounded-2xl border border-slate-800 bg-slate-900 shadow-sm">
            <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-4 py-3">
              <nav className="flex min-w-0 flex-1 flex-wrap items-center gap-1 text-sm" aria-label={t("org.drive.root")}>
                <button
                  type="button"
                  onClick={() => openFolder(null)}
                  className={`inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 ${
                    folderId === null ? "text-slate-100" : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                  }`}
                >
                  <Home className="h-3.5 w-3.5" /> {t("org.drive.root")}
                </button>
                {breadcrumb.map((item, index) => {
                  const last = index === breadcrumb.length - 1;
                  return (
                    <React.Fragment key={item.id}>
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-600 rtl:rotate-180" />
                      <button
                        type="button"
                        onClick={() => !last && openFolder(item.id)}
                        className={`max-w-[14rem] truncate rounded-md px-1.5 py-1 ${
                          last ? "font-medium text-slate-100" : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                        }`}
                      >
                        {item.name}
                      </button>
                    </React.Fragment>
                  );
                })}
              </nav>

              <label className="relative w-full sm:w-64">
                <Search className="pointer-events-none absolute start-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("org.drive.search_placeholder")}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 py-1.5 pe-3 ps-8 text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
                />
              </label>
            </div>

            {uploading && (
              <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2 text-xs text-slate-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("org.drive.uploading", { name: uploading })}
              </div>
            )}

            <div className="space-y-5 p-4">
              {foldersError || filesError ? (
                <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
                  {t("org.drive.load_failed")}
                </div>
              ) : anythingLoading ? (
                <div className="flex items-center gap-2 py-10 text-sm text-slate-400">
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
                </div>
              ) : (
                <>
                  {folders.length > 0 && (
                    <section className="space-y-2">
                      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        {t("org.drive.folders")}
                      </h2>
                      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                        {folders.map((folder) => (
                          <div
                            key={folder.id}
                            className="group flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/50 p-2.5 hover:border-slate-700"
                          >
                            <button
                              type="button"
                              onClick={() => openFolder(folder.id)}
                              className="flex min-w-0 flex-1 items-center gap-3 text-start"
                            >
                              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-900/40 bg-amber-950/30 text-amber-300">
                                <Folder className="h-4 w-4" />
                              </span>
                              <span className="min-w-0">
                                <span className="flex items-center gap-1.5 truncate text-sm font-medium text-slate-200">
                                  {folder.name}
                                  {folder.is_system && (
                                    <span className="rounded border border-slate-700 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-slate-500">
                                      {t("org.drive.system_folder")}
                                    </span>
                                  )}
                                </span>
                                <span className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-slate-500">
                                  <span>
                                    {t("org.drive.folder_meta", { count: folder.file_count })}
                                  </span>
                                  {folder.folder_count > 0 && (
                                    <span>
                                      {t("org.drive.folder_meta_folders", { count: folder.folder_count })}
                                    </span>
                                  )}
                                </span>
                              </span>
                            </button>
                            {folder.can_edit && !folder.is_system && (
                              <button
                                type="button"
                                onClick={() => setToDelete({ kind: "folder", folder })}
                                className="rounded-md p-1.5 text-slate-600 hover:bg-slate-800 hover:text-rose-300"
                                aria-label={t("org.drive.delete_folder_title")}
                                title={t("org.drive.delete_folder_title")}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  <section className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        {t("org.drive.files")}
                        <span className="ms-1.5 font-normal normal-case text-slate-600">({filesPage?.count ?? 0})</span>
                      </h2>
                      {filesPage && filesPage.count > 0 && (
                        <span className="text-[11px] text-slate-500">
                          {t("org.drive.page_of", { page, total: totalPages })}
                        </span>
                      )}
                    </div>

                    <DriveFileList
                      files={files}
                      empty={search ? t("org.drive.empty_search") : t("org.drive.empty")}
                      onPreview={(file) => void open(file, true)}
                      onDownload={(file) => void open(file, false)}
                      onDelete={(file) => setToDelete({ kind: "file", file })}
                      onShare={(file) => setShareFile(file)}
                      onRename={(file) => setRenameFile(file)}
                      showFolder
                    />

                    {totalPages > 1 && (
                      <div className="flex items-center justify-end gap-2 pt-1">
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          disabled={page <= 1}
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                        >
                          <ChevronLeft className="h-3.5 w-3.5 rtl:rotate-180" /> {t("org.drive.previous")}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                          disabled={page >= totalPages}
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                        >
                          {t("org.drive.next")} <ChevronRight className="h-3.5 w-3.5 rtl:rotate-180" />
                        </button>
                      </div>
                    )}
                  </section>

                  {!hasAnything && !search && (
                    <p className="text-center text-[11px] text-slate-600">
                      {t("org.drive.hint", { mb: maxMb, types })}
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        <p className="text-[11px] text-slate-600">{t("org.drive.hint", { mb: maxMb, types })}</p>
      </div>

      <Modal
        open={newFolderOpen}
        title={t("org.drive.new_folder")}
        onClose={() => {
          if (!creatingFolder) {
            setNewFolderOpen(false);
            setNewFolderName("");
          }
        }}
        footer={
          <>
            <button
              type="button"
              onClick={() => {
                setNewFolderOpen(false);
                setNewFolderName("");
              }}
              disabled={creatingFolder}
              className="rounded-lg border border-slate-800 bg-slate-900 px-3.5 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              {t("common.close")}
            </button>
            <button
              type="button"
              onClick={() => void createFolder()}
              disabled={!newFolderName.trim() || creatingFolder}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {creatingFolder && <Loader2 className="h-4 w-4 animate-spin" />}
              {creatingFolder ? t("org.drive.creating") : t("org.drive.create_folder")}
            </button>
          </>
        }
      >
        <label className="block space-y-1.5">
          <span className="block text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {t("org.drive.folder_name")}
          </span>
          <input
            autoFocus
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void createFolder();
            }}
            placeholder={t("org.drive.folder_name_placeholder")}
            className={inputClass}
          />
        </label>
      </Modal>

      <Modal
        open={!!renameFile}
        title={t("org.drive.rename_title")}
        onClose={() => !savingName && setRenameFile(null)}
        footer={
          <>
            <button
              type="button"
              onClick={() => setRenameFile(null)}
              disabled={savingName}
              className="rounded-lg border border-slate-800 bg-slate-900 px-3.5 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              {t("common.close")}
            </button>
            <button
              type="button"
              onClick={() => void saveName()}
              disabled={!renameValue.trim() || savingName}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {savingName && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("org.drive.save_name")}
            </button>
          </>
        }
      >
        <label className="block space-y-1.5">
          <span className="block text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {t("org.drive.name_label")}
          </span>
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void saveName();
            }}
            className={inputClass}
          />
        </label>
      </Modal>

      <ShareFileDialog
        file={shareFile}
        onClose={() => setShareFile(null)}
        onSaved={() => {
          setShareFile(null);
          invalidate();
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        title={
          toDelete?.kind === "folder" ? t("org.drive.delete_folder_title") : t("org.drive.delete_file_title")
        }
        message={
          toDelete?.kind === "folder"
            ? t("org.drive.delete_folder_message", { name: toDelete.folder.name })
            : t("org.drive.delete_file_message", { name: toDelete?.file.name ?? "" })
        }
        confirmLabel={t("common.delete")}
        tone="danger"
        busy={deleting}
        onConfirm={() => void removeTarget()}
        onClose={() => setToDelete(null)}
      />

    </Layout>
  );
};
