import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, ChevronRight, Loader2, Minus } from "lucide-react";
import { Layout } from "@/components/Layout";
import { listRecords, normaliseError } from "@/api/endpoints";
import type { ApiRecord } from "@/types";

const FLAGS = ["can_read", "can_create", "can_write", "can_submit", "can_cancel", "can_delete"] as const;
const MAX_PAGES = 40; // safety stop when walking paginated lists

/** Read every page of a DRF list (roles/permissions have no page-size override). */
async function listAll(endpoint: string): Promise<ApiRecord[]> {
  const out: ApiRecord[] = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const res = await listRecords(endpoint, { page });
    out.push(...res.results);
    if (!res.next) break;
  }
  return out;
}

/** Read-only roles list with each role's permission grants (GET /auth/roles/, /auth/permissions/). */
export const Roles: React.FC = () => {
  const { t } = useTranslation();
  const [open, setOpen] = useState<number | null>(null);

  const roles = useQuery({ queryKey: ["auth-roles"], queryFn: () => listAll("/auth/roles/") });
  const perms = useQuery({ queryKey: ["auth-permissions"], queryFn: () => listAll("/auth/permissions/") });

  const err = roles.error ? normaliseError(roles.error) : null;
  const permsByRole = new Map<number, ApiRecord[]>();
  (perms.data ?? []).forEach((p) => {
    const list = permsByRole.get(p.role) ?? [];
    list.push(p);
    permsByRole.set(p.role, list);
  });

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight">{t("roles.title")}</h1>
          <p className="text-slate-400 text-xs mt-1">{t("roles.subtitle")}</p>
        </div>

        {roles.isLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-400" role="status">
            <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {err && (
          <p className="p-4 rounded-xl bg-rose-950/60 border border-rose-800 text-rose-200 text-sm" role="alert">
            {err.forbidden ? t("common.forbidden") : t("common.load_failed")}
          </p>
        )}
        {perms.isError && !err && <p className="text-xs text-amber-300">{t("roles.permissions_unavailable")}</p>}

        {roles.data && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
            {roles.data.length === 0 ? (
              <p className="p-5 text-sm text-slate-400">{t("roles.no_roles")}</p>
            ) : (
              <ul className="divide-y divide-slate-800">
                {roles.data.map((role) => {
                  const id = role.id as number;
                  const isOpen = open === id;
                  const rolePerms = (permsByRole.get(id) ?? [])
                    .slice()
                    .sort((a, b) => String(a.model_label).localeCompare(String(b.model_label)));
                  const Chevron = isOpen ? ChevronDown : ChevronRight;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => setOpen(isOpen ? null : id)}
                        aria-expanded={isOpen}
                        title={isOpen ? t("roles.collapse") : t("roles.expand")}
                        className="w-full flex items-center gap-3 px-5 py-3.5 text-start hover:bg-slate-800/40"
                      >
                        <Chevron className="w-4 h-4 text-slate-500 shrink-0" />
                        <span className="flex-1 min-w-0">
                          <span className="block text-sm font-semibold text-slate-100">{role.name}</span>
                          {role.description && (
                            <span className="block text-xs text-slate-400 break-words">{role.description}</span>
                          )}
                        </span>
                        {perms.data && (
                          <span className="text-xs text-slate-400 shrink-0">
                            {t("roles.permission_count", { count: rolePerms.length })}
                          </span>
                        )}
                      </button>
                      {isOpen && (
                        <div className="px-5 pb-4 overflow-x-auto">
                          {rolePerms.length === 0 ? (
                            <p className="text-xs text-slate-400">
                              {perms.data ? t("roles.no_permissions") : t("roles.permissions_unavailable")}
                            </p>
                          ) : (
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-slate-400 uppercase tracking-wider">
                                  <th className="text-start font-semibold py-2 pe-4">{t("roles.model")}</th>
                                  {FLAGS.map((f) => (
                                    <th key={f} className="font-semibold py-2 px-2 text-center">
                                      {t(`roles.${f}`)}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-800/70">
                                {rolePerms.map((p) => (
                                  <tr key={p.id}>
                                    <td className="py-1.5 pe-4 text-slate-200">{p.model_label}</td>
                                    {FLAGS.map((f) => (
                                      <td key={f} className="py-1.5 px-2 text-center">
                                        {p[f] ? (
                                          <Check className="w-4 h-4 inline text-emerald-400" aria-label={t("roles.granted")} />
                                        ) : (
                                          <Minus className="w-4 h-4 inline text-slate-700" aria-label={t("roles.not_granted")} />
                                        )}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    </Layout>
  );
};
