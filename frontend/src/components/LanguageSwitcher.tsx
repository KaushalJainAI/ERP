import React from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "@/i18n/i18n";
import { useChangeLanguage } from "@/hooks/useChangeLanguage";
import { useToast } from "@/components/ui/Toast";
import { Select } from "@/components/ui/Select";
import { Globe } from "lucide-react";

const OPTIONS = SUPPORTED_LANGUAGES.map((l) => ({ value: l.code, label: l.name }));

export const LanguageSwitcher: React.FC = () => {
  const { t, i18n } = useTranslation();
  const changeLanguage = useChangeLanguage();
  const toast = useToast();
  // i18n.language may be a regional variant; match on the base code.
  const current = i18n.language?.split("-")[0] ?? "en";

  const handleLanguageChange = async (code: string) => {
    const saved = await changeLanguage(code);
    if (!saved) toast.info(t("lang.saved_locally"));
  };

  return (
    <Select
      value={current}
      options={OPTIONS}
      onChange={handleLanguageChange}
      aria-label={t("lang.label")}
      variant="ghost"
      className="w-auto max-w-[11rem]"
      leading={<Globe className="w-4 h-4 shrink-0 text-slate-500" aria-hidden />}
    />
  );
};
