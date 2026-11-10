import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Loader2 } from "lucide-react";
import { Layout } from "@/components/Layout";
import { clearSharedFiles, postMultipart, readSharedFiles } from "@/lib/sharedFiles";

/** The bill reader takes at most this many files for one bill (backend BILL_MAX_FILES). */
const BILL_MAX_FILES = 4;

interface Outcome {
  reply?: string;
  proposal?: any;
  /** The bill could not be read; the files were kept as quick notes. */
  readFailed: boolean;
  /** Files saved as quick notes (every file when the read failed; the extras otherwise). */
  noted: number;
  /** Shared files beyond BILL_MAX_FILES, saved as their own notes. */
  extra: number;
  /** Saving a note failed: the files stay in the cache for a later try. */
  noteError?: "auth" | "other";
}

/**
 * A bill shared to the app from WhatsApp or another Android app (the share target in
 * manifest.webmanifest, stored by public/sw.js). The files go to the bill reader. If the
 * read fails, each file is kept as a quick note, so nothing shared is lost.
 */
export const ShareTargetPage: React.FC = () => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const started = useRef(false);
  const [reading, setReading] = useState(true);
  const [hasFiles, setHasFiles] = useState(true);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    // Read the cache once, even if the effect runs twice in development.
    if (started.current) return;
    started.current = true;

    const run = async () => {
      const files = await readSharedFiles().catch(() => [] as File[]);
      if (files.length === 0) {
        setHasFiles(false);
        setReading(false);
        return;
      }
      const billFiles = files.slice(0, BILL_MAX_FILES);
      const extraFiles = files.slice(BILL_MAX_FILES);

      let reply: string | undefined;
      let proposal: any;
      let readFailed = false;
      try {
        const response = await postMultipart("/assistant/bill-photo/", () => {
          const form = new FormData();
          billFiles.forEach((file) => form.append("files", file, file.name));
          form.append("language", i18n.language || "en");
          return form;
        });
        const data = await response.json().catch(() => null);
        if (response.ok && data && !data.error) {
          reply = data.reply ?? data.message;
          proposal = data.proposal ?? undefined;
        } else {
          readFailed = true;
          reply = data?.reply ?? data?.message;
        }
      } catch {
        readFailed = true;
      }

      // A failed read keeps every file as a note; otherwise only the extra files need one.
      const toNote = readFailed ? files : extraFiles;
      let noted = 0;
      let noteError: Outcome["noteError"];
      for (const file of toNote) {
        try {
          const response = await postMultipart("/assistant/quick-notes/", () => {
            const form = new FormData();
            form.append("attachment", file, file.name);
            form.append("text", t("ease.share.note_text"));
            return form;
          });
          if (response.status === 401) {
            noteError = "auth";
            break;
          }
          if (!response.ok) {
            noteError = "other";
            break;
          }
          noted += 1;
        } catch {
          noteError = "other";
          break;
        }
      }

      setReading(false);
      if (noteError) {
        // Keep the files in the cache: nothing is lost until a note is saved.
        setOutcome({ reply, proposal, readFailed, noted, extra: extraFiles.length, noteError });
        return;
      }
      await clearSharedFiles().catch(() => undefined);
      setOutcome({ reply, proposal, readFailed, noted, extra: extraFiles.length });
    };

    void run();
  }, [i18n.language, t]);

  const openProposal = () => {
    const proposal = outcome?.proposal;
    if (!proposal?.route) return;
    navigate(proposal.route, {
      state: {
        initialValues: proposal.values,
        assistant: {
          proposalId: proposal.proposal_id,
          summary: proposal.summary,
          aiFilledKeys: proposal.ai_filled_keys,
          warnings: proposal.warnings,
          target: proposal.target,
        },
      },
    });
  };

  return (
    <Layout>
      <div className="mx-auto max-w-xl space-y-4">
        <h1 className="text-2xl font-bold text-white tracking-tight">{t("ease.share.title")}</h1>

        {reading && (
          <p className="flex items-center gap-2 text-sm text-slate-300">
            <Loader2 className="w-4 h-4 animate-spin" /> {t("ease.share.reading")}
          </p>
        )}

        {!reading && !hasFiles && <p className="text-sm text-slate-300">{t("ease.share.none")}</p>}

        {!reading && outcome && (
          <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-sm text-slate-200">
            {outcome.reply && <p className="whitespace-pre-line">{outcome.reply}</p>}

            {outcome.readFailed && outcome.noteError === undefined && outcome.noted > 0 && (
              <p className="text-amber-300">{t("ease.share.saved_note")}</p>
            )}

            {outcome.noteError === "auth" && (
              <p className="text-amber-300">
                {t("ease.share.signed_out")}{" "}
                <Link to="/login" className="underline">
                  {t("ease.share.sign_in")}
                </Link>
              </p>
            )}
            {outcome.noteError === "other" && <p className="text-rose-300">{t("ease.share.note_failed")}</p>}

            {!outcome.readFailed && outcome.extra > 0 && outcome.noteError === undefined && (
              <p>{t("ease.share.more_notes", { count: outcome.extra })}</p>
            )}

            {outcome.proposal?.route && (
              <button
                type="button"
                onClick={openProposal}
                className="w-full flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white"
              >
                <span>{t("ease.share.review")}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            )}
          </div>
        )}

        <Link
          to="/"
          className="inline-flex items-center justify-center min-h-[44px] rounded-lg border border-slate-700 px-4 text-sm font-medium text-slate-200 hover:bg-slate-800"
        >
          {t("ease.share.home")}
        </Link>
      </div>
    </Layout>
  );
};
