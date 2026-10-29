import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LinkField } from "@/components/doc/LinkField";
import { listRecords } from "@/api/endpoints";
import { formatDate } from "@/lib/format";
import type { LinkDef } from "@/types";
import { ReportShell, useReport } from "../parts";
import { absDec, fmtMoney, isZeroDec } from "../gst/lib";
import {
  CsvButton,
  Hint,
  MessageRow,
  PAGE_SIZE,
  Pager,
  PeriodFilters,
  VoucherNo,
  downloadCsv,
  fetchAllPages,
  td,
  tdR,
  tfootCls,
  th,
  theadCls,
  thR,
  useRegisterFilters,
  voucherNoText,
  voucherTypeText,
  type PageInfo,
  type TallyRef,
} from "./shared";

interface Row {
  id: number;
  posting_date: string;
  voucher_type: string;
  voucher_no: string;
  tally: TallyRef | null;
  against: string;
  party: string | null;
  debit: string;
  credit: string;
  balance: string;
}
interface CashBook {
  account: { id: number; name: string; account_type: string };
  opening_balance: string;
  rows: Row[];
  total_debit: string;
  total_credit: string;
  closing_balance: string;
  pagination: PageInfo;
}

/** Debit-minus-credit balance: positive is Dr, negative is Cr (an overdraft). */
const Bal: React.FC<{ v: string }> = ({ v }) => {
  const { t } = useTranslation();
  return (
    <span className="tabular-nums whitespace-nowrap">
      {fmtMoney(absDec(v))}
      {!isZeroDec(v) && <span className="ms-1 text-xs text-slate-400">{v.startsWith("-") ? t("registers.cr") : t("registers.dr")}</span>}
    </span>
  );
};

export const CashBookPage: React.FC = () => {
  const { t } = useTranslation();
  const f = useRegisterFilters();
  const [account, setAccount] = useState("");

  // Cash and Bank accounts of the chosen company (the account API filters on one type at a time).
  const accountLink = useMemo<LinkDef>(
    () => ({
      labelField: "name",
      fetcher: async (search: string) => {
        const lists = await Promise.all(
          ["Bank", "Cash"].map((account_type) =>
            listRecords("/accounts/accounts/", { company: f.company, account_type, is_group: false, search }),
          ),
        );
        return lists.flatMap((l) => l.results);
      },
    }),
    [f.company],
  );

  const base = { company: f.company, account, from_date: f.from, to_date: f.to };
  const ready = f.ready && !!account;
  const { data, isFetching, error } = useReport<CashBook>(
    "/accounts/reports/cash-book/",
    { ...base, page: String(f.page), page_size: String(PAGE_SIZE) },
    ready,
  );

  const exportCsv = async () => {
    const { first, items } = await fetchAllPages<CashBook>("/accounts/reports/cash-book/", base, "rows");
    const head = [
      t("fields.posting_date"),
      t("acc.fields.voucher_type"),
      t("acc.fields.voucher_no"),
      t("registers.against"),
      t("acc.fields.party"),
      t("acc.fields.debit"),
      t("acc.fields.credit"),
      t("acc.fields.balance"),
    ];
    downloadCsv(`cash-book_${first.account.name}_${f.from}_${f.to}`, [
      head,
      ["", t("registers.opening_balance"), "", "", "", "", "", first.opening_balance],
      ...(items as Row[]).map((r) => [r.posting_date, voucherTypeText(r), voucherNoText(r), r.against, r.party ?? "", r.debit, r.credit, r.balance]),
      ["", t("registers.closing_balance"), "", "", "", first.total_debit, first.total_credit, first.closing_balance],
    ]);
  };

  return (
    <ReportShell
      titleKey="registers.nav.cash_book"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      status={<CsvButton onExport={exportCsv} disabled={!data || data.rows.length === 0} />}
      filters={
        <>
          <PeriodFilters
            f={{
              ...f,
              setCompany: (v) => {
                f.setCompany(v);
                setAccount("");
              },
            }}
            prefix="cb"
          />
          <div className="space-y-1 w-64 max-w-full">
            <label htmlFor="cb-account" className="block text-xs font-medium text-slate-400">
              {t("acc.fields.account")}
            </label>
            <LinkField
              id="cb-account"
              link={accountLink}
              cacheKey={`registers.cash_book.account.${f.company}`}
              value={account}
              values={{}}
              parentValues={{}}
              onSelect={(v) => {
                setAccount(v === null ? "" : String(v));
                f.setPage(1);
              }}
            />
          </div>
        </>
      }
    >
      {!ready ? (
        <Hint textKey={f.ready ? "registers.pick_account" : "registers.pick_period"} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className={theadCls}>
                <tr>
                  <th className={th}>{t("fields.posting_date")}</th>
                  <th className={th}>{t("acc.fields.voucher_type")}</th>
                  <th className={th}>{t("acc.fields.voucher_no")}</th>
                  <th className={th}>{t("registers.against")}</th>
                  <th className={th}>{t("acc.fields.party")}</th>
                  <th className={thR}>{t("acc.fields.debit")}</th>
                  <th className={thR}>{t("acc.fields.credit")}</th>
                  <th className={thR}>{t("acc.fields.balance")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {!data ? (
                  <MessageRow cols={8} loading />
                ) : (
                  <>
                    <tr className="bg-slate-950/50 font-semibold text-slate-100">
                      <td className={td} colSpan={7}>
                        {t("registers.opening_balance")}
                      </td>
                      <td className={tdR}>
                        <Bal v={data.opening_balance} />
                      </td>
                    </tr>
                    {data.rows.length === 0 ? (
                      <MessageRow cols={8} loading={false} />
                    ) : (
                      data.rows.map((r) => (
                        <tr key={r.id} className="hover:bg-slate-800/40">
                          <td className={`${td} whitespace-nowrap`}>{formatDate(r.posting_date)}</td>
                          <td className={td}>{voucherTypeText(r)}</td>
                          <td className={td}>
                            <VoucherNo row={r} />
                          </td>
                          <td className={td}>{r.against}</td>
                          <td className={td}>{r.party ?? ""}</td>
                          <td className={tdR}>{isZeroDec(r.debit) ? "" : fmtMoney(r.debit)}</td>
                          <td className={tdR}>{isZeroDec(r.credit) ? "" : fmtMoney(r.credit)}</td>
                          <td className={tdR}>
                            <Bal v={r.balance} />
                          </td>
                        </tr>
                      ))
                    )}
                  </>
                )}
              </tbody>
              {data && (
                <tfoot className={tfootCls}>
                  <tr>
                    <td className={td} colSpan={5}>
                      {t("registers.closing_balance")}
                    </td>
                    <td className={tdR}>{fmtMoney(data.total_debit)}</td>
                    <td className={tdR}>{fmtMoney(data.total_credit)}</td>
                    <td className={tdR}>
                      <Bal v={data.closing_balance} />
                    </td>
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
