import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, Loader2 } from "lucide-react";
import { apiClient } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { LinkField } from "./LinkField";
import { newRowKey, type Row } from "./ChildTable";
import type { ChildTableDef } from "@/types";

interface Props {
  def: ChildTableDef;
  cacheKeyBase: string;
  rows: Row[];
  form: Record<string, any>;
  onRows: (rows: Row[]) => void;
}

/**
 * "Fetch items" bar of a child table (ERPNext "Fetch Items from Warehouse"): pick a
 * source record, GET `endpoint`, and append the returned rows that are not already
 * in the table. The server does the lookup; nothing is computed here.
 */
export const FetchRowsBar: React.FC<Props> = ({ def, cacheKeyBase, rows, form, onRows }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const cfg = def.fetchRows!;
  const [source, setSource] = useState<string | number | null>(null);
  const [busy, setBusy] = useState(false);

  const fetchRows = async () => {
    if (source === null || source === "") {
      toast.error(t(cfg.pickKey));
      return;
    }
    const params = cfg.params(source, form);
    if (!params) {
      toast.error(t(cfg.notReadyKey));
      return;
    }
    setBusy(true);
    try {
      const res = await apiClient.get(cfg.endpoint, { params });
      const keyOf = (r: Row) => (cfg.dedupeKeys ?? []).map((k) => String(r[k] ?? "")).join("|");
      const have = new Set(rows.map(keyOf));
      const added: Row[] = ((res.data?.rows as Row[]) ?? [])
        .map((r) => ({ ...(cfg.mapRow ? cfg.mapRow(r) : r), _key: newRowKey() }))
        .filter((r) => !have.has(keyOf(r)));
      if (added.length === 0) {
        toast.error(t(cfg.noneKey));
      } else {
        onRows([...rows, ...added]);
        toast.success(t(cfg.doneKey, { count: added.length }));
      }
    } catch {
      toast.error(t(cfg.failedKey));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-3 flex flex-wrap items-end gap-3">
      <div className="w-64 max-w-full">
        <label className="block text-xs font-medium text-slate-400 mb-1">{t(cfg.sourceLabelKey)}</label>
        <LinkField
          link={cfg.sourceLink}
          cacheKey={`${cacheKeyBase}.fetch_source`}
          value={source}
          values={{}}
          parentValues={form}
          onSelect={(v) => setSource(v)}
        />
      </div>
      <button
        type="button"
        onClick={fetchRows}
        disabled={busy}
        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-indigo-300 bg-indigo-600/10 hover:bg-indigo-600/20 border border-indigo-500/30 disabled:opacity-50"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
        {t(cfg.labelKey)}
      </button>
    </div>
  );
};
