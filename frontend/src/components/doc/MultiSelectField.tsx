import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { listRecords } from "@/api/endpoints";
import type { LinkDef, LinkRecord } from "@/types";

const MAX_PAGES = 40; // safety stop when walking a paginated list

/** Every page of a DRF list (option lists are small: roles, tags, ...). */
async function listAll(endpoint: string): Promise<LinkRecord[]> {
  const out: LinkRecord[] = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const res = await listRecords(endpoint, { page });
    out.push(...res.results);
    if (!res.next) break;
  }
  return out;
}

interface MultiSelectFieldProps {
  link: LinkDef;
  cacheKey: string;
  /** Array of selected `valueField` values. */
  value: Array<string | number> | null | undefined;
  onChange: (value: Array<string | number>) => void;
  disabled?: boolean;
  invalid?: boolean;
  id?: string;
}

/**
 * Many-of-many picker for a list of foreign keys (e.g. a user's `role_ids`).
 * Options come from `link.endpoint`; `link.valueField` (default "id") is stored,
 * `link.label` / `link.labelField` (default "name") is shown.
 */
export const MultiSelectField: React.FC<MultiSelectFieldProps> = ({
  link,
  cacheKey,
  value,
  onChange,
  disabled,
  invalid,
  id,
}) => {
  const { t } = useTranslation();
  const valueField = link.valueField || "id";
  const selected = new Set((value ?? []).map(String));

  const { data, isLoading, isError } = useQuery({
    queryKey: ["multiselect", cacheKey, link.endpoint],
    queryFn: () => listAll(link.endpoint!),
    enabled: !!link.endpoint,
    staleTime: 60 * 1000,
  });

  const labelOf = (rec: LinkRecord) =>
    link.label ? link.label(rec) : String(rec[link.labelField || "name"] ?? rec[valueField] ?? "");

  const toggle = (rec: LinkRecord) => {
    const v = rec[valueField];
    const next = (value ?? []).filter((x) => String(x) !== String(v));
    if (next.length === (value ?? []).length) next.push(v);
    onChange(next);
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400" role="status">
        <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
      </div>
    );
  }
  if (isError) return <p className="text-sm text-rose-300">{t("usermgmt.roles_load_failed")}</p>;
  if (!data || data.length === 0) return <p className="text-sm text-slate-400">{t("usermgmt.roles_empty")}</p>;

  return (
    <div
      id={id}
      role="group"
      className={`flex flex-wrap gap-2 rounded-lg border bg-slate-950 p-2 ${invalid ? "border-rose-500" : "border-slate-800"}`}
    >
      {data.map((rec) => {
        const on = selected.has(String(rec[valueField]));
        return (
          <label
            key={String(rec[valueField])}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border select-none ${
              on
                ? "bg-indigo-950/60 text-indigo-200 border-indigo-700"
                : "bg-slate-900 text-slate-300 border-slate-700"
            } ${disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer hover:border-indigo-500"}`}
          >
            <input
              type="checkbox"
              checked={on}
              disabled={disabled}
              onChange={() => toggle(rec)}
              className="w-3.5 h-3.5 accent-indigo-500"
            />
            {labelOf(rec)}
          </label>
        );
      })}
    </div>
  );
};
