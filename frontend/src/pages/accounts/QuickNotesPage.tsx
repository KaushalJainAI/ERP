import React from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock3, Loader2, Paperclip } from "lucide-react";
import { apiClient } from "@/api/client";
import { Layout } from "@/components/Layout";
import { openAssistant } from "@/components/assistant/AssistantLauncher";
import { QUICK_NOTES_ENDPOINT, useOpenQuickNotes, type QuickNote } from "@/components/assistant/quickNotes";
import { formatRelativeTime } from "@/components/notifications/useNotifications";
import { useToast } from "@/components/ui/Toast";
import { btnPrimary, btnSecondary } from "@/components/hr/hrUi";

/** Open quick notes to enter into the books. Each one opens the assistant, then is marked done. */
export const QuickNotesPage: React.FC = () => {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const notes = useOpenQuickNotes(true);

  const openFile = async (note: QuickNote) => {
    // Open the tab at once, while the click still counts for the pop-up blocker.
    const win = window.open("", "_blank");
    try {
      const res = await apiClient.get<Blob>(`${QUICK_NOTES_ENDPOINT}${note.id}/file/`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data);
      if (win) win.location.href = url;
      else window.open(url, "_blank");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      win?.close();
      toast.error(t("quick_notes.file_failed"));
    }
  };

  const enterIt = async (note: QuickNote) => {
    // The assistant takes no text yet, so the note is copied for pasting into it.
    if (note.text) {
      try {
        await navigator.clipboard.writeText(note.text);
        toast.success(t("quick_notes.copied"));
      } catch {
        toast.error(t("quick_notes.copy_failed"));
      }
    }
    openAssistant();
  };

  const markDone = useMutation({
    mutationFn: (id: number) => apiClient.post(`${QUICK_NOTES_ENDPOINT}${id}/done/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["quick-notes"] }),
    onError: () => toast.error(t("quick_notes.done_failed")),
  });

  const rows = notes.data ?? [];

  return (
    <Layout>
      <div className="max-w-2xl mx-auto space-y-4 pb-28">
        <header>
          <h1 className="text-xl font-semibold text-slate-100">{t("quick_notes.title")}</h1>
          <p className="mt-1 text-sm text-slate-400">{t("quick_notes.subtitle")}</p>
        </header>

        {notes.isLoading && (
          <p className="flex items-center gap-2 text-sm text-slate-500" role="status">
            <Clock3 className="h-4 w-4 animate-pulse" />
            {t("common.loading")}
          </p>
        )}
        {notes.isError && <p className="text-sm text-rose-400">{t("quick_notes.load_failed")}</p>}
        {notes.data && rows.length === 0 && (
          <p className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm text-slate-400">
            {t("quick_notes.empty")}
          </p>
        )}

        <ul className="space-y-3">
          {rows.map((note) => {
            const busy = markDone.isPending && markDone.variables === note.id;
            return (
              <li key={note.id} className="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-3.5">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 flex-1 whitespace-pre-line break-words text-sm text-slate-100">
                    {note.text || t("quick_notes.photo_or_voice")}
                  </p>
                  <time dateTime={note.created_at} className="shrink-0 text-xs text-slate-500">
                    {formatRelativeTime(note.created_at, i18n.language)}
                  </time>
                </div>

                <div className="flex flex-wrap gap-2">
                  {note.has_attachment && (
                    <button type="button" onClick={() => void openFile(note)} className={`${btnSecondary} min-h-[44px]`}>
                      <Paperclip className="h-4 w-4" />
                      {t("quick_notes.open_file")}
                    </button>
                  )}
                  <button type="button" onClick={() => void enterIt(note)} className={`${btnPrimary} min-h-[44px]`}>
                    {t("quick_notes.enter_it")}
                  </button>
                  <button
                    type="button"
                    onClick={() => markDone.mutate(note.id)}
                    disabled={busy}
                    className={`${btnSecondary} min-h-[44px] disabled:opacity-50`}
                  >
                    {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                    {t("quick_notes.done")}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </Layout>
  );
};
