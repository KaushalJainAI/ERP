import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertCircle, Info, Loader2 } from "lucide-react";
import { apiClient } from "@/api/client";
import { fetchStates } from "@/api/lookups";
import { useDebounced } from "@/hooks/useDebounced";
import type { PreviewState } from "@/types";

/** The server's supply decision (dry-run `supply` object / GET /gst/supply-context/). */
export interface SupplyInfo {
  kind: "INTRA" | "INTER" | null;
  place_of_supply: string | null;
  gst_category: string;
  party_gstin: string | null;
  allowed_components: string[];
  reason: string;
}

interface Props {
  partyKey: "customer" | "supplier";
  values: Record<string, any>;
  editable: boolean;
  preview?: PreviewState;
}

/**
 * Supply kind ("Intra-state: CGST + SGST" / "Inter-state: IGST"), the server's
 * reason when GST does not apply, the effective place of supply and any dry-run
 * errors. Before the dry run has lines to work with (customer picked, no items
 * yet) it falls back to GET /gst/supply-context/ so the user already sees the
 * supply kind.
 */
export const GstSummary: React.FC<Props> = ({ partyKey, values, editable, preview }) => {
  const { t } = useTranslation();

  const direction = partyKey === "customer" ? "Sales" : "Purchase";
  const party = values[partyKey];

  const ctxParams = useDebounced(
    editable && !preview?.data && values.company && party
      ? {
          company: values.company,
          party,
          direction,
          ...(values.place_of_supply ? { place_of_supply: values.place_of_supply } : {}),
          is_export_with_gst: !!values.is_export_with_gst,
          is_reverse_charge: !!values.is_reverse_charge,
        }
      : null,
    400
  );
  const ctxQ = useQuery({
    queryKey: ["supply-context", JSON.stringify(ctxParams)],
    queryFn: async () => (await apiClient.get("/gst/supply-context/", { params: ctxParams })).data as SupplyInfo,
    enabled: ctxParams !== null,
    retry: false,
    staleTime: 0,
  });

  const supply: SupplyInfo | null = (preview?.data?.supply as SupplyInfo | undefined) ?? (ctxParams ? (ctxQ.data ?? null) : null);

  const { data: states } = useQuery({ queryKey: ["states"], queryFn: () => fetchStates(""), staleTime: Infinity });
  const posCode = supply?.place_of_supply ?? values.place_of_supply ?? null;
  const posName = posCode ? (states?.find((s) => s.code === posCode)?.name ?? values.place_of_supply_name) : null;

  const errors = preview?.errors ?? [];
  if (!supply && errors.length === 0) return null;

  // CESS may always be added on top; the headline names the main components only.
  const components = (supply?.allowed_components ?? []).filter((c) => c !== "CESS").join(" + ");
  const headline =
    supply?.kind === "INTRA"
      ? t("gst.supply.intra", { components })
      : supply?.kind === "INTER"
        ? t("gst.supply.inter", { components })
        : supply
          ? t("gst.supply.none")
          : "";

  return (
    <div className="space-y-3">
      {errors.length > 0 && (
        <div role="alert" className="flex gap-2 p-3.5 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-200 text-sm">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">{t("gst.supply.calc_failed")}</p>
            <ul className="space-y-0.5">
              {errors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {supply && (
        <div className="flex gap-2 p-3.5 rounded-lg bg-slate-900 border border-slate-800 text-sm text-slate-300">
          <Info className="w-4 h-4 mt-0.5 shrink-0 text-indigo-400" />
          <div className="space-y-0.5 min-w-0">
            <p className="font-semibold text-slate-100 flex items-center gap-2">
              {headline}
              {preview?.pending && <Loader2 className="w-3 h-3 animate-spin text-slate-500" aria-label={t("gst.supply.calculating")} />}
            </p>
            {supply.reason && <p className="text-slate-400">{supply.reason}</p>}
            {posCode && (
              <p className="text-xs text-slate-500">
                {t("gst.fields.place_of_supply")}: {posName ? `${posCode} - ${posName}` : posCode}
                {supply.party_gstin ? ` | ${t("gst.fields.party_gstin")}: ${supply.party_gstin}` : ""}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
