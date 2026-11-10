import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { WifiOff } from "lucide-react";

/**
 * Shown while the browser reports no network. Typing is kept by the page itself (the
 * drafts of U-3); nothing is queued or sent later, so a document is never written offline.
 */
export const OfflineBanner: React.FC = () => {
  const { t } = useTranslation();
  const [online, setOnline] = useState<boolean>(() => (typeof navigator === "undefined" ? true : navigator.onLine));

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  if (online) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-2 bg-amber-500/90 px-3 py-2 text-sm font-medium text-slate-950"
    >
      <WifiOff className="w-4 h-4 shrink-0" />
      <span>{t("ease.offline")}</span>
    </div>
  );
};
