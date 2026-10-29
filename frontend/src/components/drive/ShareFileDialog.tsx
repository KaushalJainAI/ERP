import React, { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Lock, Users } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { useCompany } from "@/context/CompanyContext";
import type { DriveFile, DriveMembersResponse } from "@/types/organisation";
import { DRIVE_FILES } from "./fileUtils";

interface Props {
  file: DriveFile | null;
  onClose: () => void;
  onSaved: () => void;
}

/** Sharing editor: company-wide, or private to selected company members. */
export const ShareFileDialog: React.FC<Props> = ({ file, onClose, onSaved }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const { activeCompany } = useCompany();
  const [companyWide, setCompanyWide] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [canWrite, setCanWrite] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data: members, isLoading } = useQuery<DriveMembersResponse>({
    queryKey: ["drive-members", activeCompany?.id ?? null],
    queryFn: async () => (await apiClient.get("/drive/members/")).data,
    enabled: !!activeCompany,
  });

  useEffect(() => {
    if (!file) return;
    setCompanyWide(file.visibility === "company");
    setSelected(new Set(file.shared_with.map((share) => share.user)));
    setCanWrite(file.shared_with.some((share) => share.can_write));
  }, [file]);

  const otherMembers = useMemo(
    () => (members?.results ?? []).filter((member) => !member.is_self),
    [members]
  );

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const save = async () => {
    if (!file) return;
    if (!companyWide && selected.size === 0) {
      toast.error(t("org.drive.select_people"));
      return;
    }
    setSaving(true);
    try {
      const visibility = companyWide ? "company" : "private";
      if (file.visibility !== visibility) {
        await apiClient.patch(`${DRIVE_FILES}${file.id}/`, { visibility });
      }
      if (!companyWide) {
        const existing = new Set(file.shared_with.map((share) => share.user));
        if (selected.size > 0) {
          await apiClient.post(`${DRIVE_FILES}${file.id}/share/`, {
            user_ids: Array.from(selected),
            can_write: canWrite,
          });
        }
        const removed = Array.from(existing).filter((id) => !selected.has(id));
        for (const userId of removed) {
          await apiClient.post(`${DRIVE_FILES}${file.id}/unshare/`, { user_id: userId });
        }
      }
      toast.success(t("org.drive.share_saved"));
      onSaved();
      onClose();
    } catch (err) {
      toast.error(summariseError(normaliseError(err)) || t("org.drive.share_failed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={!!file}
      title={file ? t("org.drive.share_title", { name: file.name }) : ""}
      description={t("org.drive.share_description")}
      onClose={saving ? () => undefined : onClose}
      className="max-w-lg"
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg border border-slate-800 bg-slate-900 px-3.5 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
          >
            {t("common.close")}
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {saving ? t("org.drive.sharing") : t("org.drive.save_share")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
          <input
            type="radio"
            checked={companyWide}
            onChange={() => setCompanyWide(true)}
            className="mt-0.5 h-4 w-4 accent-indigo-500"
          />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-sm font-medium text-slate-200">
              <Users className="h-4 w-4 text-slate-500" />
              {t("org.drive.everyone")}
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
              {t("org.drive.everyone_help", { name: activeCompany?.name ?? "" })}
            </span>
          </span>
        </label>

        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
          <input
            type="radio"
            checked={!companyWide}
            onChange={() => setCompanyWide(false)}
            className="mt-0.5 h-4 w-4 accent-indigo-500"
          />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-sm font-medium text-slate-200">
              <Lock className="h-4 w-4 text-slate-500" />
              {t("org.drive.private")}
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
              {t("org.drive.private_help")}
            </span>
          </span>
        </label>

        {!companyWide && (
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              {t("org.drive.members")}
            </p>
            {isLoading ? (
              <div className="flex items-center gap-2 py-3 text-xs text-slate-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("common.loading")}
              </div>
            ) : otherMembers.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                {t("org.drive.no_members")}
              </p>
            ) : (
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/40 p-1.5">
                {otherMembers.map((member) => {
                  const active = selected.has(member.id);
                  return (
                    <button
                      type="button"
                      key={member.id}
                      onClick={() => toggle(member.id)}
                      className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-start transition-colors ${
                        active ? "bg-indigo-950/60 text-indigo-100" : "text-slate-300 hover:bg-slate-800/60"
                      }`}
                    >
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                          active ? "border-indigo-500 bg-indigo-600 text-white" : "border-slate-700"
                        }`}
                      >
                        {active && <Check className="h-3 w-3" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">{member.name}</span>
                        <span className="block truncate text-[11px] text-slate-500">{member.email}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            <label className="flex items-start gap-2.5 rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={canWrite}
                onChange={(e) => setCanWrite(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-indigo-500"
              />
              {t("org.drive.allow_edit")}
            </label>
          </div>
        )}
      </div>
    </Modal>
  );
};
