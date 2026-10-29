import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, MessageSquare, Sparkles } from "lucide-react";
import { AssistantPanel } from "./AssistantPanel";

export const AssistantLauncher: React.FC = () => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <div className="fixed bottom-6 right-6 z-30 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          className="group flex items-center gap-2.5 px-4 py-3 rounded-full bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white font-medium text-sm shadow-xl hover:shadow-indigo-500/25 transition-all duration-200 border border-indigo-400/30"
          title={t("assistant.floating_label")}
        >
          <div className="w-5 h-5 rounded-full bg-white/20 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="hidden sm:inline font-semibold">{t("assistant.title")}</span>
          <div className="w-px h-4 bg-white/20 hidden sm:block" />
          <Mic className="w-4 h-4 text-indigo-100 group-hover:scale-110 transition-transform" />
        </button>
      </div>

      <AssistantPanel isOpen={isOpen} onClose={() => setIsOpen(false)} />
    </>
  );
};
