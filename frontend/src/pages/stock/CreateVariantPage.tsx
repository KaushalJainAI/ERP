import React, { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2, Plus } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { getRecord, listAllRecords, normaliseError, summariseError } from "@/api/endpoints";
import type { ApiRecord } from "@/types";

const inputClass =
  "w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-60";

/**
 * ERPNext "Create Variant": pick one value for each attribute the template varies
 * by (Pack Size: 500g, Blend: Mild ...) and the server makes the item, copying the
 * template and building the code from the value abbreviations.
 */
export const CreateVariantPage: React.FC = () => {
  const { id } = useParams();
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [code, setCode] = useState("");

  const item = useQuery({ queryKey: ["item", id], queryFn: () => getRecord("/stock/items/", id!) });
  const attributes = useQuery({
    queryKey: ["item-attributes-all"],
    queryFn: () => listAllRecords("/stock/item-attributes/", {}),
  });

  const declared: ApiRecord[] = useMemo(() => {
    const ids: number[] = (item.data?.attributes ?? []).map((a: ApiRecord) => a.attribute);
    return (attributes.data ?? []).filter((a: ApiRecord) => ids.includes(a.id as number) && !a.disabled);
  }, [item.data, attributes.data]);

  const chosen = Object.entries(picked).filter(([, v]) => v);

  const create = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        attributes: Object.fromEntries(chosen.map(([a, v]) => [a, Number(v)])),
      };
      if (code.trim()) body.item_code = code.trim();
      const res = await apiClient.post(`/stock/items/${id}/create-variant/`, body);
      return res.data as ApiRecord;
    },
    onSuccess: (variant) => {
      toast.success(t("vr.page.created", { code: variant.code }));
      qc.invalidateQueries({ queryKey: ["list"] });
      navigate(`/stock/items/${variant.id}`);
    },
    onError: (e) => {
      const n = normaliseError(e);
      toast.error(summariseError(n) || t("common.action_failed"));
    },
  });

  return (
    <Layout>
      <div className="max-w-2xl mx-auto space-y-5 animate-fade-up">
        <div className="flex items-center gap-3">
          <Link
            to={`/stock/items/${id}`}
            className="p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800"
            aria-label={t("common.back_to_list")}
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <h1 className="text-xl font-semibold text-slate-100 truncate">
            {t("vr.page.title", { item: item.data?.name ?? "" })}
          </h1>
        </div>

        {(item.isLoading || attributes.isLoading) && <Loader2 className="w-5 h-5 animate-spin text-slate-400" />}

        {item.data && !item.data.has_variants && (
          <p className="text-sm text-amber-400">{t("vr.page.not_template")}</p>
        )}
        {item.data?.has_variants && attributes.data && declared.length === 0 && (
          <p className="text-sm text-amber-400">{t("vr.page.no_attributes")}</p>
        )}

        {declared.length > 0 && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-4">
            {declared.map((attr) => (
              <div key={attr.id as number}>
                <label className="block text-xs font-medium text-slate-400 mb-1" htmlFor={`attr-${attr.id}`}>
                  {attr.name}
                </label>
                <select
                  id={`attr-${attr.id}`}
                  className={inputClass}
                  value={picked[attr.id as number] ?? ""}
                  onChange={(e) => setPicked((p) => ({ ...p, [attr.id as number]: e.target.value }))}
                >
                  <option value="">{t("vr.page.choose")}</option>
                  {(attr.values ?? []).map((v: ApiRecord) => (
                    <option key={v.id as number} value={String(v.id)}>
                      {v.value} ({v.abbr})
                    </option>
                  ))}
                </select>
              </div>
            ))}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1" htmlFor="variant-code">
                {t("vr.fields.item_code_optional")}
              </label>
              <input
                id="variant-code"
                className={inputClass}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder={item.data?.code}
              />
              <p className="text-[11px] text-slate-500 mt-1">{t("vr.help.variant_code")}</p>
            </div>
            <button
              type="button"
              disabled={chosen.length === 0 || create.isPending}
              onClick={() => create.mutate()}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50 transition-colors"
            >
              {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {t("vr.page.create")}
            </button>
            {chosen.length === 0 && <p className="text-[11px] text-slate-500">{t("vr.page.pick_one")}</p>}
          </div>
        )}
      </div>
    </Layout>
  );
};
