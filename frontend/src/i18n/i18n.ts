import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { enExtra } from "./en";
import { enDashboard } from "./en.dashboard";
import { enAccounts } from "./en.accounts";
import { enBuyingStock } from "./en.buying_stock";

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

/** Switch the UI language and remember it on this device. */
export function setAppLanguage(code: string) {
  if (!SUPPORTED_LANGUAGES.some((l) => l.code === code)) return;
  try {
    localStorage.setItem("app_lang", code);
  } catch {
    /* storage unavailable: the language still switches for this session */
  }
  void i18n.changeLanguage(code);
}

const resources = {
  en: {
    translation: {
      appName: "Enterprise ERP",
      dashboard: "Dashboard",
      selling: "Selling",
      buying: "Buying",
      stock: "Stock",
      accounts: "Accounts",
      users: "Users & Security",
      login: "Sign In",
      logout: "Sign Out",
      email: "Email Address",
      password: "Password",
      submit: "Submit Document",
      cancel: "Cancel Document",
      amend: "Amend",
      save: "Save Draft",
      delete: "Delete Draft",
      draft: "Draft",
      submitted: "Submitted",
      cancelled: "Cancelled",
      search: "Search...",
      actions: "Actions",
      status: "Status",
      name: "Document No.",
      created_at: "Created Date",
      no_records: "No records found.",
      ...enExtra,
      ...enDashboard,
      ...enAccounts,
      ...enBuyingStock,
    },
  },
  hi: {
    translation: {
      appName: "एंटरप्राइज़ ईआरपी",
      dashboard: "डैशबोर्ड",
      selling: "बिक्री",
      buying: "खरीद",
      stock: "स्टॉक",
      accounts: "खाते",
      users: "उपयोगकर्ता और सुरक्षा",
      login: "साइन इन करें",
      logout: "साइन आउट",
      email: "ईमेल पता",
      password: "पासवर्ड",
      submit: "दस्तावेज़ सबमिट करें",
      cancel: "दस्तावेज़ रद्द करें",
      amend: "संशोधित करें",
      save: "ड्राफ्ट सहेजें",
      delete: "ड्राफ्ट हटाएं",
      draft: "ड्राफ्ट",
      submitted: "जमा किया गया",
      cancelled: "रद्द किया गया",
      search: "खोजें...",
      actions: "कार्रवाइयां",
      status: "स्थिति",
      name: "दस्तावेज़ संख्या",
      created_at: "सृजन तिथि",
      no_records: "कोई रिकॉर्ड नहीं मिला।",
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

// Keep <html dir/lang> in step with the active language (ur is RTL), including
// on first load with a stored language.
applyDocumentDirection(storedLang);
i18n.on("languageChanged", applyDocumentDirection);

export default i18n;
