import React from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "@/i18n/i18n";
import { Globe } from "lucide-react";

export const LanguageSwitcher: React.FC = () => {
  const { i18n } = useTranslation();

  const handleLanguageChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const langCode = e.target.value;
    i18n.changeLanguage(langCode);
    localStorage.setItem("app_lang", langCode);

    const langObj = SUPPORTED_LANGUAGES.find((l) => l.code === langCode);
    if (langObj && langObj.rtl) {
      document.documentElement.dir = "rtl";
    } else {
      document.documentElement.dir = "ltr";
    }
  };

  return (
    <div className="flex items-center space-x-2 bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-slate-300">
      <Globe className="w-4 h-4 text-indigo-400" />
      <select
        value={i18n.language}
        onChange={handleLanguageChange}
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
