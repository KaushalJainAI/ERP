import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, Loader2, Mic, Plus, Square, X } from "lucide-react";
import { apiClient } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { btnPrimary, btnSecondary, inputCls } from "@/components/hr/hrUi";
import { QUICK_NOTES_ENDPOINT } from "./quickNotes";

/** A voice clip stops on its own after this long, so a forgotten mic cannot run on. */
const MAX_VOICE_MS = 120_000;

interface Attachment {
  file: File;
  kind: "photo" | "voice";
}

/** The file extension the server accepts for a recorded clip (webm, ogg or mp4). */
function voiceExtension(mime: string): string {
  if (mime.includes("mp4")) return "mp4";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

const QuickNoteSheet: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);

  const releaseMic = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  // Closing the sheet mid-recording discards the take and frees the mic.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const recorder = recorderRef.current;
      if (recorder) {
        recorder.onstop = null;
        if (recorder.state !== "inactive") recorder.stop();
        recorderRef.current = null;
      }
      releaseMic();
    };
  }, []);

  const stopVoice = () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  };

  const startVoice = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error(t("quick_note.mic_denied"));
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      toast.error(t("quick_note.mic_denied"));
      return;
    }
    streamRef.current = stream;
    const recorder = new MediaRecorder(stream);
    chunksRef.current = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const mime = recorder.mimeType || "audio/webm";
      const blob = new Blob(chunksRef.current, { type: mime });
      chunksRef.current = [];
      recorderRef.current = null;
      releaseMic();
      setRecording(false);
      if (blob.size) {
        const file = new File([blob], `voice.${voiceExtension(mime)}`, { type: mime });
        setAttachment({ file, kind: "voice" });
      }
    };
    recorder.start(250);
    recorderRef.current = recorder;
    timerRef.current = window.setTimeout(stopVoice, MAX_VOICE_MS);
    setRecording(true);
  };

  const onPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) setAttachment({ file, kind: "photo" });
  };

  const hasContent = text.trim().length > 0 || attachment !== null;

  const save = async () => {
    if (!hasContent || saving) return;
    const form = new FormData();
    const body = text.trim();
    if (body) form.append("text", body);
    if (attachment) form.append("attachment", attachment.file);
    setSaving(true);
    try {
      await apiClient.post(QUICK_NOTES_ENDPOINT, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      await qc.invalidateQueries({ queryKey: ["quick-notes"] });
      toast.success(t("quick_note.saved"));
      onClose();
    } catch {
      toast.error(t("quick_note.save_failed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("quick_note.title")}
        className="max-h-[85vh] overflow-y-auto rounded-t-2xl border-t border-slate-800 bg-slate-900 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-slate-700" />
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-slate-100">{t("quick_note.title")}</h2>
            <p className="mt-0.5 text-xs text-slate-400">{t("quick_note.hint")}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("quick_note.cancel")}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <label htmlFor="quick-note-text" className="sr-only">
          {t("quick_note.text_label")}
        </label>
        <textarea
          id="quick-note-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder={t("quick_note.text_placeholder")}
          className={`${inputCls} mt-3 min-h-[5.5rem] text-base`}
        />

        <div className="mt-3 grid grid-cols-2 gap-2.5">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={recording}
            className={`${btnSecondary} min-h-[44px]`}
          >
            <Camera className="h-4 w-4" />
            {t("quick_note.photo")}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*,application/pdf"
            capture="environment"
            onChange={onPhoto}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          />
          {recording ? (
            <button
              type="button"
              onClick={stopVoice}
              className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white"
            >
              <Square className="h-4 w-4" />
              {t("quick_note.voice_stop")}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void startVoice()}
              className={`${btnSecondary} min-h-[44px]`}
            >
              <Mic className="h-4 w-4" />
              {t("quick_note.voice")}
            </button>
          )}
        </div>

        {recording && (
          <p className="mt-2 flex items-center gap-2 text-sm text-rose-300" role="status">
            <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" />
            {t("quick_note.voice_recording")}
          </p>
        )}

        {attachment && !recording && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
            <span className="min-w-0 truncate text-sm text-slate-300">
              {attachment.kind === "voice" ? t("quick_note.voice_added") : t("quick_note.photo_added")}
              <span className="ms-2 text-xs text-slate-500">{attachment.file.name}</span>
            </span>
            <button
              type="button"
              onClick={() => setAttachment(null)}
              className="min-h-[44px] shrink-0 px-2 text-sm font-medium text-slate-400 hover:text-slate-200"
            >
              {t("quick_note.remove")}
            </button>
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-2.5">
          <button type="button" onClick={onClose} className={`${btnSecondary} min-h-[44px]`}>
            {t("quick_note.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!hasContent || saving || recording}
            className={`${btnPrimary} min-h-[44px] disabled:opacity-50`}
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {saving ? t("quick_note.saving") : t("quick_note.save")}
          </button>
        </div>
        {!hasContent && <p className="mt-2 text-xs text-slate-500">{t("quick_note.empty")}</p>}
      </div>
    </div>
  );
};

/** A round "+" beside the assistant launcher: one tap opens the note sheet. */
export const QuickNoteButton: React.FC = () => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("quick_note.title")}
        title={t("quick_note.title")}
        className="flex h-11 w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-800 text-slate-100 shadow-xl transition-colors hover:bg-slate-700"
      >
        <Plus className="h-5 w-5" />
      </button>
      {open && <QuickNoteSheet onClose={() => setOpen(false)} />}
    </>
  );
};
