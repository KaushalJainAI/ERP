import { useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import { SUPPORTED_LANGUAGES, setAppLanguage } from "@/i18n/i18n";

/**
 * On a device with no stored language choice, adopt the language saved on the
 * user's profile. A choice already made on this device wins (it is also what
 * the API client sends as Accept-Language).
 */
export function useSyncUserLanguage() {
  const { user } = useAuth();
  const preferred = user?.language;

  useEffect(() => {
    if (!preferred || !SUPPORTED_LANGUAGES.some((l) => l.code === preferred)) return;
    let stored: string | null = null;
    try {
      stored = localStorage.getItem("app_lang");
    } catch {
      /* ignore */
    }
    if (!stored) setAppLanguage(preferred);
  }, [preferred]);
}
