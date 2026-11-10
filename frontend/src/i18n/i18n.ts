import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { enCore } from "./en.core";
import { enExtra } from "./en";
import { enDashboard } from "./en.dashboard";
import { enHome } from "./en.home";
import { enAccounts } from "./en.accounts";
import { enBuyingStock } from "./en.buying_stock";
import { enCharges } from "./en.charges";
import { enUsers } from "./en.users";
import { enGst } from "./en.gst";
import { enGstReports } from "./en.gstreports";
import { enManufacturing } from "./en.manufacturing";
import { enLegacy } from "./en.legacy";
import { enRegisters } from "./en.registers";
import { enReturns } from "./en.returns";
import { enHr } from "./en.hr";
import { enPortal } from "./en.portal";
import { enReco } from "./en.reco";
import { enAttachments } from "./en.attachments";
import { enTallyExport } from "./en.tallyexport";
import { enOrganisation } from "./en.organisation";
import { enBank } from "./en.bank";
import { enPrint } from "./en.print";
import { enPayRecon } from "./en.payrecon";
import { enSalesKpi } from "./en.sales_kpi";
import { enAssistant } from "./en.assistant";
import { enSelling } from "./en.selling";
import { enEase } from "./en.ease";

export const SUPPORTED_LANGUAGES = [
  { code: "en", name: "English", rtl: false },
  { code: "hi", name: "हिंदी (Hindi)", rtl: false },
  { code: "ta", name: "தமிழ் (Tamil)", rtl: false },
  { code: "pa", name: "ਪੰਜਾਬੀ (Punjabi)", rtl: false },
  { code: "gu", name: "ગુજરાતી (Gujarati)", rtl: false },
  { code: "or", name: "ଓଡ଼ିଆ (Odia)", rtl: false },
  { code: "bn", name: "বাংলা (Bengali)", rtl: false },
  { code: "kn", name: "ಕನ್ನಡ (Kannada)", rtl: false },
  { code: "as", name: "অসমীয়া (Assamese)", rtl: false },
  { code: "te", name: "తెలుగు (Telugu)", rtl: false },
  { code: "ml", name: "മലയാളം (Malayalam)", rtl: false },
  { code: "mr", name: "मराठी (Marathi)", rtl: false },
  { code: "ur", name: "اردو (Urdu)", rtl: true },
];

/** Set <html dir/lang> for a language code (ur is right-to-left). */
export function applyDocumentDirection(code: string) {
  const lang = SUPPORTED_LANGUAGES.find((l) => l.code === code);
  document.documentElement.dir = lang?.rtl ? "rtl" : "ltr";
  document.documentElement.lang = code;
}

const localeLoaders = import.meta.glob<{ default: Record<string, unknown> }>("./locales/*.ts");

/** Load a language's translation bundle on first use (English is always bundled). */
export async function ensureLanguage(code: string): Promise<void> {
  if (code === "en" || i18n.hasResourceBundle(code, "translation")) return;
  const load = localeLoaders[`./locales/${code}.ts`];
  if (!load) return;
  try {
    const mod = await load();
    i18n.addResourceBundle(code, "translation", mod.default, true, true);
  } catch {
    /* offline / chunk failed: keep showing English (fallbackLng) */
  }
}

/** Switch the UI language and remember it on this device. */
export function setAppLanguage(code: string) {
  if (!SUPPORTED_LANGUAGES.some((l) => l.code === code)) return;
  try {
    localStorage.setItem("app_lang", code);
  } catch {
    /* storage unavailable: the language still switches for this session */
  }
  void ensureLanguage(code).then(() => i18n.changeLanguage(code));
}

// English is bundled (it is the fallback); the 12 Indian languages are loaded on
// demand from ./locales/<code>.ts (see ensureLanguage). Missing keys fall back to English.
const resources = {
  en: {
    translation: {
      ...enCore,
      ...enExtra,
      ...enDashboard,
      ...enHome,
      ...enAccounts,
      ...enBuyingStock,
      ...enUsers,
      ...enGst,
      ...enGstReports,
      ...enManufacturing,
      ...enLegacy,
      ...enRegisters,
      ...enReturns,
      ...enHr,
      ...enPortal,
      ...enReco,
      ...enAttachments,
      ...enTallyExport,
      ...enOrganisation,
      ...enBank,
      ...enPrint,
      ...enCharges,
      ...enPayRecon,
      ...enSalesKpi,
      ...enAssistant,
      ...enSelling,
      ...enEase,
    },
  },
};

const storedLang = (() => {
  try {
    const code = localStorage.getItem("app_lang");
    return code && SUPPORTED_LANGUAGES.some((l) => l.code === code) ? code : "en";
  } catch {
    return "en";
  }
})();

i18n.use(initReactI18next).init({
  resources,
  lng: storedLang,
  fallbackLng: "en",
  supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
  interpolation: {
    escapeValue: false,
  },
});

// A stored non-English language: its bundle loads asynchronously (English meanwhile).
if (storedLang !== "en") void ensureLanguage(storedLang).then(() => i18n.changeLanguage(storedLang));

// Keep <html dir/lang> in step with the active language (ur is RTL), including
// on first load with a stored language.
applyDocumentDirection(storedLang);
i18n.on("languageChanged", applyDocumentDirection);

export default i18n;
