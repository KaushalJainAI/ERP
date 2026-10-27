import { useCallback } from "react";
import { apiClient } from "@/api/client";
import { setAppLanguage } from "@/i18n/i18n";
import { useAuth } from "@/context/AuthContext";

/**
 * Change the UI language. Always applied on this device immediately; when signed
 * in it is also saved on the user's profile (PATCH /auth/me/ {language}).
 * Resolves true when the profile was updated (or nobody is signed in), false if
 * only the local change took effect.
 */
export function useChangeLanguage() {
  const { user, refetchUser } = useAuth();

  return useCallback(
    async (code: string): Promise<boolean> => {
      setAppLanguage(code);
      if (!user) return true;
      try {
        await apiClient.patch("/auth/me/", { language: code });
        await refetchUser();
        return true;
      } catch {
        return false;
      }
    },
    [user, refetchUser]
  );
}
