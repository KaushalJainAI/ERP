import React, { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2, Printer } from "lucide-react";
import { apiClient } from "@/api/client";
import { PrintDocument } from "./PrintDocument";
import { PRINT_ENDPOINT, isChallan, isPrintKind } from "./printTargets";
import type { PrintData } from "./types";
import "./print.css";

const COPIES = ["original", "duplicate", "triplicate"];

/**
 * The print view: a plain A4 "paper" with no app chrome. Print / Save as PDF is the browser
 * print dialog (choose "Save as PDF" as the destination). The paper is always light; the
 * toolbar is hidden when printing (print.css).
 */
export const PrintPage: React.FC = () => {
  const { kind, id } = useParams();
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [copy, setCopy] = useState("original");
  const [withPrices, setWithPrices] = useState(false);
  const valid = isPrintKind(kind) && !!id;
  const wordsLang = i18n.language === "hi" ? "hi" : "en";

  const { data, isLoading, error } = useQuery<PrintData>({
    queryKey: ["print", kind, id, wordsLang, withPrices],
    enabled: valid,
    queryFn: async () => {
      const res = await apiClient.get(PRINT_ENDPOINT[kind as keyof typeof PRINT_ENDPOINT](id as string), {
        params: { words_lang: wordsLang, ...(withPrices ? { with_prices: 1 } : {}) },
      });
      return res.data;
    },
  });

  // Switches the global print rules to this page (print.css) and names the saved PDF after the document.
  useEffect(() => {
    document.body.classList.add("print-page");
    return () => document.body.classList.remove("print-page");
  }, []);
  useEffect(() => {
    if (!data?.name) return;
    const previous = document.title;
    document.title = data.name;
    return () => {
      document.title = previous;
    };
  }, [data?.name]);

  return (
    <div className="pdoc-wrap">
      <div className="pdoc-toolbar no-print">
        <button type="button" onClick={() => navigate(-1)} className="pdoc-btn">
          <ArrowLeft className="w-4 h-4" /> {t("prt.back")}
        </button>
        <label className="pdoc-field">
          <span>{t("prt.copy_label")}</span>
          <select value={copy} onChange={(e) => setCopy(e.target.value)}>
            {COPIES.map((c) => (
              <option key={c} value={c}>
                {t(`prt.copy.${c}`)}
              </option>
            ))}
          </select>
        </label>
        {valid && isChallan(kind) && (
          <label className="pdoc-field">
            <input type="checkbox" checked={withPrices} onChange={(e) => setWithPrices(e.target.checked)} />
            <span>{t("prt.show_prices")}</span>
          </label>
        )}
        <button type="button" onClick={() => window.print()} disabled={!data} className="pdoc-btn pdoc-btn-primary">
          <Printer className="w-4 h-4" /> {t("prt.print_save")}
        </button>
        <span className="pdoc-hint">{t("prt.save_hint")}</span>
      </div>

      {!valid || error ? (
        <p role="alert" className="pdoc-msg no-print">
          {t("prt.load_failed")}
        </p>
      ) : isLoading || !data ? (
        <p className="pdoc-msg no-print">
          <Loader2 className="w-4 h-4 animate-spin" /> {t("prt.loading")}
        </p>
      ) : (
        <PrintDocument data={data} copy={copy} />
      )}
    </div>
  );
};
