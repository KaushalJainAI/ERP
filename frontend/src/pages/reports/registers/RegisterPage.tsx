import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LinkField } from "@/components/doc/LinkField";
import { formatDate } from "@/lib/format";
import type { LinkDef } from "@/types";
import { ReportShell, useReport } from "../parts";
import { fmtMoney } from "../gst/lib";
import {
  CsvButton,
  Hint,
  MessageRow,
  PAGE_SIZE,
  Pager,
  PeriodFilters,
  fetchAllPages,
  downloadCsv,
  td,
  tdR,
  tfootCls,
  th,
  theadCls,
  thR,
  useRegisterFilters,
  type PageInfo,
} from "./shared";

type Kind = "sales" | "purchase";

interface Row {
  id: number;
  name: string;
  posting_date: string;
  party: string;
  party_gstin: string;
  /** purchase register only: the supplier's own invoice no */
  bill_no?: string | null;
  bill_date?: string | null;
  is_reverse_charge: boolean;
  is_return: boolean;
  return_against: string | null;
  net_total: string;
  taxes: Record<string, string>;
  reverse_charge_tax: string;
  grand_total: string;
  rounding_adjustment: string;
  rounded_total: string;
  outstanding: string;
}
interface Totals {
  net_total: string;
  taxes: Record<string, string>;
  reverse_charge_tax: string;
  grand_total: string;
  rounding_adjustment: string;
  rounded_total: string;
  outstanding: string;
}
interface Register {
  heads: string[];
  rows: Row[];
  totals: Totals;
  pagination: PageInfo;
}

const CONFIG: Record<Kind, { path: string; titleKey: string; partyLink: LinkDef; partyKey: string; partyLabel: string }> = {
  sales: {
    path: "/accounts/reports/sales-register/",
    titleKey: "registers.nav.sales_register",
    partyLink: { endpoint: "/selling/customers/", labelField: "name" },
    partyKey: "customer",
    partyLabel: "registers.customer",
  },
  purchase: {
    path: "/accounts/reports/purchase-register/",
    titleKey: "registers.nav.purchase_register",
    partyLink: { endpoint: "/buying/suppliers/", labelField: "name" },
    partyKey: "supplier",
    partyLabel: "registers.supplier",
  },
};

const HEAD_KEY: Record<string, string> = {
  CGST: "registers.cgst",
  SGST: "registers.sgst",
  IGST: "registers.igst",
  CESS: "registers.cess",
  OTHER: "registers.other_tax",
};

const RegisterPage: React.FC<{ kind: Kind }> = ({ kind }) => {
  const { t } = useTranslation();
  const cfg = CONFIG[kind];
  const f = useRegisterFilters();
  const [party, setParty] = useState("");
  const [itemGroup, setItemGroup] = useState("");

  const base = { company: f.company, from_date: f.from, to_date: f.to, party, item_group: itemGroup };
  const { data, isFetching, error } = useReport<Register>(
    cfg.path,
    { ...base, page: String(f.page), page_size: String(PAGE_SIZE) },
    f.ready,
  );
  const partyLink = useMemo<LinkDef>(() => ({ ...cfg.partyLink, filters: () => ({ company: f.company }) }), [cfg, f.company]);
  const groupLink = useMemo<LinkDef>(() => ({ endpoint: "/stock/item-groups/", labelField: "name" }), []);
  const showRcm = !!data && (kind === "purchase" || data.rows.some((r) => r.is_reverse_charge));
  const heads = data?.heads ?? ["CGST", "SGST", "IGST", "CESS", "OTHER"];
  const isPurchase = kind === "purchase";
  const cols = 7 + heads.length + (showRcm ? 1 : 0) + 3 + (isPurchase ? 2 : 0);

  const exportCsv = async () => {
    const { first, items } = await fetchAllPages<Register>(cfg.path, Object.fromEntries(Object.entries(base).filter(([, v]) => v !== "")), "rows");
    const head = [
      t("fields.posting_date"),
      t("registers.number"),
      t(cfg.partyLabel),
      t("registers.gstin"),
      ...(isPurchase ? [t("chg.bill_no"), t("chg.bill_date")] : []),
      t("registers.net_total"),
      ...first.heads.map((h) => t(HEAD_KEY[h])),
      t("registers.rcm_tax"),
      t("registers.grand_total"),
      t("registers.rounding"),
      t("registers.invoice_total"),
      t("ageing.outstanding"),
    ];
    const rows = (items as Row[]).map((r) => [
      r.posting_date,
      r.name,
      r.party,
      r.party_gstin,
      ...(isPurchase ? [r.bill_no ?? "", r.bill_date ?? ""] : []),
      r.net_total,
      ...first.heads.map((h) => r.taxes[h]),
      r.reverse_charge_tax,
      r.grand_total,
      r.rounding_adjustment,
      r.rounded_total,
      r.outstanding,
    ]);
    const tt = first.totals;
    downloadCsv(`${kind}-register_${f.from}_${f.to}`, [
      head,
      ...rows,
      [t("acc.report.total"), "", "", "", ...(isPurchase ? ["", ""] : []), tt.net_total, ...first.heads.map((h) => tt.taxes[h]), tt.reverse_charge_tax, tt.grand_total, tt.rounding_adjustment, tt.rounded_total, tt.outstanding],
    ]);
  };

  return (
    <ReportShell
      titleKey={cfg.titleKey}
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      status={<CsvButton onExport={exportCsv} disabled={!data || data.rows.length === 0} />}
      filters={
        <>
          <PeriodFilters f={f} prefix={kind} />
          <div className="space-y-1 w-56 max-w-full">
            <label htmlFor={`${kind}-party`} className="block text-xs font-medium text-slate-400">
              {t(cfg.partyLabel)}
            </label>
            <LinkField
              id={`${kind}-party`}
              link={partyLink}
              cacheKey={`registers.${kind}.party`}
              value={party}
              values={{}}
              parentValues={{}}
              onSelect={(v) => {
                setParty(v === null ? "" : String(v));
                f.setPage(1);
              }}
            />
          </div>
          <div className="space-y-1 w-56 max-w-full">
            <label htmlFor={`${kind}-group`} className="block text-xs font-medium text-slate-400">
              {t("registers.item_group")}
            </label>
            <LinkField
              id={`${kind}-group`}
              link={groupLink}
              cacheKey="registers.item_group"
              value={itemGroup}
              values={{}}
              parentValues={{}}
              onSelect={(v) => {
                setItemGroup(v === null ? "" : String(v));
                f.setPage(1);
              }}
            />
          </div>
        </>
      }
    >
      {!f.ready ? (
        <Hint textKey="registers.pick_period" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className={theadCls}>
                <tr>
                  <th className={th}>{t("fields.posting_date")}</th>
                  <th className={th}>{t("registers.number")}</th>
                  <th className={th}>{t(cfg.partyLabel)}</th>
                  <th className={th}>{t("registers.gstin")}</th>
                  {isPurchase && <th className={th}>{t("chg.bill_no")}</th>}
                  {isPurchase && <th className={th}>{t("chg.bill_date")}</th>}
                  <th className={thR}>{t("registers.net_total")}</th>
                  {heads.map((h) => (
                    <th key={h} className={thR}>
                      {t(HEAD_KEY[h])}
                    </th>
                  ))}
                  {showRcm && <th className={thR}>{t("registers.rcm_tax")}</th>}
                  <th className={thR}>{t("registers.grand_total")}</th>
                  <th className={thR}>{t("registers.rounding")}</th>
                  <th className={thR}>{t("registers.invoice_total")}</th>
                  <th className={thR}>{t("ageing.outstanding")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {!data || data.rows.length === 0 ? (
                  <MessageRow cols={cols} loading={!data} />
                ) : (
                  data.rows.map((r) => (
                    <tr key={r.id} className="hover:bg-slate-800/40">
                      <td className={`${td} whitespace-nowrap`}>{formatDate(r.posting_date)}</td>
                      <td className={`${td} whitespace-nowrap`}>
                        {r.name}
                        {r.is_return && (
                          <span
                            className="ms-2 text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800"
                            title={r.return_against ?? undefined}
                          >
                            {t("registers.return_note")}
                          </span>
                        )}
                      </td>
                      <td className={td}>{r.party}</td>
                      <td className={`${td} whitespace-nowrap`}>{r.party_gstin}</td>
                      {isPurchase && <td className={`${td} whitespace-nowrap`}>{r.bill_no ?? ""}</td>}
                      {isPurchase && <td className={`${td} whitespace-nowrap`}>{r.bill_date ? formatDate(r.bill_date) : ""}</td>}
                      <td className={tdR}>{fmtMoney(r.net_total)}</td>
                      {heads.map((h) => (
                        <td key={h} className={tdR}>
                          {fmtMoney(r.taxes[h])}
                        </td>
                      ))}
                      {showRcm && <td className={tdR}>{fmtMoney(r.reverse_charge_tax)}</td>}
                      <td className={tdR}>{fmtMoney(r.grand_total)}</td>
                      <td className={tdR}>{fmtMoney(r.rounding_adjustment)}</td>
                      <td className={tdR}>{fmtMoney(r.rounded_total)}</td>
                      <td className={tdR}>{fmtMoney(r.outstanding)}</td>
                    </tr>
                  ))
                )}
              </tbody>
              {data && data.rows.length > 0 && (
                <tfoot className={tfootCls}>
                  <tr>
                    <td className={td} colSpan={isPurchase ? 6 : 4}>
                      {t("acc.report.total")} ({t("acc.report.entries_count", { count: data.pagination.count })})
                    </td>
                    <td className={tdR}>{fmtMoney(data.totals.net_total)}</td>
                    {heads.map((h) => (
                      <td key={h} className={tdR}>
                        {fmtMoney(data.totals.taxes[h])}
                      </td>
                    ))}
                    {showRcm && <td className={tdR}>{fmtMoney(data.totals.reverse_charge_tax)}</td>}
                    <td className={tdR}>{fmtMoney(data.totals.grand_total)}</td>
                    <td className={tdR}>{fmtMoney(data.totals.rounding_adjustment)}</td>
                    <td className={tdR}>{fmtMoney(data.totals.rounded_total)}</td>
                    <td className={tdR}>{fmtMoney(data.totals.outstanding)}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
          <Pager info={data?.pagination} onPage={f.setPage} />
        </>
      )}
    </ReportShell>
  );
};

export const SalesRegisterPage: React.FC = () => <RegisterPage kind="sales" />;
export const PurchaseRegisterPage: React.FC = () => <RegisterPage kind="purchase" />;
