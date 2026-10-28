import { useCallback, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "app_theme";

/** The theme applied to <html> (index.html sets it before first paint). */
const current = (): Theme => (document.documentElement.classList.contains("dark") ? "dark" : "light");

/** Light/dark toggle, remembered on this device. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(current);

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* storage unavailable: the theme still applies for this session */
    }
    setThemeState(next);
  }, []);

  const toggle = useCallback(() => setTheme(current() === "dark" ? "light" : "dark"), [setTheme]);

  return { theme, setTheme, toggle };
}
