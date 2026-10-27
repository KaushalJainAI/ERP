import React from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "@/i18n/i18n";
import { useChangeLanguage } from "@/hooks/useChangeLanguage";
import { useToast } from "@/components/ui/Toast";
import { Globe } from "lucide-react";

export const LanguageSwitcher: React.FC = () => {
  const { t, i18n } = useTranslation();
  const changeLanguage = useChangeLanguage();
  const toast = useToast();
  // i18n.language may be a regional variant; match on the base code.
  const current = i18n.language?.split("-")[0] ?? "en";

  const handleLanguageChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const saved = await changeLanguage(e.target.value);
    if (!saved) toast.info(t("lang.saved_locally"));
  };

  return (
    <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-slate-300">
      <Globe className="w-4 h-4 text-indigo-400" />
      <select
        value={current}
        onChange={handleLanguageChange}
        aria-label={t("lang.label")}
        className="bg-transparent focus:outline-none cursor-pointer text-slate-200"
      >
        {SUPPORTED_LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code} className="bg-slate-900 text-slate-200">
            {lang.name}
          </option>
        ))}
      </select>
    </div>
  );
};
