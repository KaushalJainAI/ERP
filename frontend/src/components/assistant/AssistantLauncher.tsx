import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Sparkles } from "lucide-react";
import { AssistantPanel } from "./AssistantPanel";
import { QuickNoteButton } from "./QuickNoteButton";

/** Window event that opens the assistant sheet from anywhere (see openAssistant). */
export const ASSISTANT_OPEN_EVENT = "assistant:open";

/** Open the assistant sheet, e.g. from the Orders home mic button. */
export function openAssistant(): void {
  window.dispatchEvent(new Event(ASSISTANT_OPEN_EVENT));
}

export const AssistantLauncher: React.FC = () => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const open = () => setIsOpen(true);
    window.addEventListener(ASSISTANT_OPEN_EVENT, open);
    return () => window.removeEventListener(ASSISTANT_OPEN_EVENT, open);
  }, []);

  return (
    <>
      <div className="assistant-fab fixed bottom-[calc(5rem_+_env(safe-area-inset-bottom))] end-4 z-30 flex items-center gap-2 sm:end-6 md:bottom-6">
        <QuickNoteButton />
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          aria-label={t("assistant.floating_label")}
          title={t("assistant.floating_label")}
          className="group flex h-11 w-11 items-center justify-center rounded-full border border-indigo-400/30 bg-gradient-to-r from-indigo-600 to-indigo-500 text-white shadow-xl transition-all duration-200 hover:from-indigo-500 hover:to-indigo-400 hover:shadow-indigo-500/25 sm:h-auto sm:w-auto sm:gap-2 sm:px-4 sm:py-3"
        >
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/20">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="hidden font-semibold sm:inline">{t("assistant.title")}</span>
          <div className="hidden h-4 w-px bg-white/20 sm:block" />
          <Mic className="hidden w-4 h-4 shrink-0 text-indigo-100 transition-transform group-hover:scale-110 sm:block" />
        </button>
      </div>

      <AssistantPanel isOpen={isOpen} onClose={() => setIsOpen(false)} />
    </>
  );
};
