import React, { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { LinkField } from "@/components/doc/LinkField";
import { Select } from "@/components/ui/Select";
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
  PrintButton,
  downloadCsv,
  useRegisterFilters,
  type PageInfo,
} from "./shared";

interface Row {
  id: number;
  posting_date: string;
  voucher_type: string;
  voucher_no: string;
  against_voucher_no: string | null;
  remarks: string | null;
  debit: string;
  credit: string;
  balance: string;
  dr_cr: string;
}
interface AgeingRow {
  invoice: string;
  posting_date: string;
  due_date: string | null;
  outstanding: string;
  days_overdue: number;
}
interface Statement {
  company: { id: number; name: string; gstin: string | null };
  party_type: "Customer" | "Supplier";
  party: { id: number; name: string; gstin: string | null; phone: string | null; email: string | null };
  period: { from_date: string; to_date: string };
  opening_balance: string;
  opening_dr_cr: string;
  rows: Row[];
  total_debit: string;
  total_credit: string;
  closing_balance: string;
  closing_dr_cr: string;
  ledger_balance: string;
  ageing: {
    as_on: string;
    rows: AgeingRow[];
    total: { outstanding: string; buckets: Record<string, string> };
  };
  pagination: PageInfo;
}

const BUCKETS = ["0-30", "31-60", "61-90", "90+"];

const sideLabel = (side: string, t: (k: string) => string) => (side === "Dr" ? t("registers.dr") : side === "Cr" ? t("registers.cr") : "");

/** The statement as a printable paper document (light, fixed colours, see .stmt in index.css). */
const StatementDocument: React.FC<{ s: Statement }> = ({ s }) => {
  const { t } = useTranslation();
  const bal = (v: string, side: string) => (
    <>
      {fmtMoney(absDec(v))} {sideLabel(side, t)}
    </>
  );
  return (
    <div className="stmt">
      <div className="stmt-head">
        <div>
          <h2>{s.company.name}</h2>
          {s.company.gstin && (
            <p>
              {t("registers.gstin")}: {s.company.gstin}
            </p>
          )}
        </div>
        <div className="stmt-title">
          <h1>{t("registers.statement_title")}</h1>
          <p>
            {formatDate(s.period.from_date)} - {formatDate(s.period.to_date)}
          </p>
        </div>
      </div>
      <div className="stmt-party">
        <strong>{s.party.name}</strong>
        <span>{t(s.party_type === "Customer" ? "registers.customer" : "registers.supplier")}</span>
        {s.party.gstin && (
          <span>
            {t("registers.gstin")}: {s.party.gstin}
          </span>
        )}
        {s.party.phone && <span>{s.party.phone}</span>}
        {s.party.email && <span>{s.party.email}</span>}
      </div>

      <table>
        <thead>
          <tr>
            <th>{t("fields.posting_date")}</th>
            <th>{t("acc.fields.voucher_type")}</th>
            <th>{t("acc.fields.voucher_no")}</th>
            <th className="num">{t("acc.fields.debit")}</th>
            <th className="num">{t("acc.fields.credit")}</th>
            <th className="num">{t("acc.fields.balance")}</th>
          </tr>
        </thead>
        <tbody>
          <tr className="strong">
            <td colSpan={5}>{t("registers.opening_balance")}</td>
            <td className="num">{bal(s.opening_balance, s.opening_dr_cr)}</td>
          </tr>
          {s.rows.map((r) => (
            <tr key={r.id}>
              <td>{formatDate(r.posting_date)}</td>
              <td>{r.voucher_type}</td>
              <td>
                {r.voucher_no}
                {r.remarks ? <small> {r.remarks}</small> : null}
              </td>
              <td className="num">{isZeroDec(r.debit) ? "" : fmtMoney(r.debit)}</td>
              <td className="num">{isZeroDec(r.credit) ? "" : fmtMoney(r.credit)}</td>
              <td className="num">{bal(r.balance, r.dr_cr)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="strong">
            <td colSpan={3}>{t("registers.closing_balance")}</td>
            <td className="num">{fmtMoney(s.total_debit)}</td>
            <td className="num">{fmtMoney(s.total_credit)}</td>
            <td className="num">{bal(s.closing_balance, s.closing_dr_cr)}</td>
          </tr>
        </tfoot>
      </table>

      <h3>
        {t("registers.ageing_summary")} ({t("ageing.as_on")} {formatDate(s.ageing.as_on)})
      </h3>
      <table>
        <thead>
          <tr>
            {BUCKETS.map((b) => (
              <th key={b} className="num">
                {t(`ageing.bucket.${b}`)}
              </th>
            ))}
            <th className="num">{t("ageing.outstanding")}</th>
          </tr>
        </thead>
        <tbody>
          <tr className="strong">
            {BUCKETS.map((b) => (
              <td key={b} className="num">
                {fmtMoney(s.ageing.total.buckets[b])}
              </td>
            ))}
            <td className="num">{fmtMoney(s.ageing.total.outstanding)}</td>
          </tr>
        </tbody>
      </table>
      {s.ageing.rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t("ageing.invoice")}</th>
              <th>{t("fields.posting_date")}</th>
              <th>{t("registers.due_date")}</th>
              <th className="num">{t("ageing.days_overdue")}</th>
              <th className="num">{t("ageing.outstanding")}</th>
            </tr>
          </thead>
          <tbody>
            {s.ageing.rows.map((r) => (
              <tr key={r.invoice}>
                <td>{r.invoice}</td>
                <td>{formatDate(r.posting_date)}</td>
                <td>{formatDate(r.due_date)}</td>
                <td className="num">{r.days_overdue}</td>
                <td className="num">{fmtMoney(r.outstanding)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="stmt-foot">{t("registers.computer_generated")}</p>
    </div>
  );
};

export const PartyStatementPage: React.FC = () => {
  const { t } = useTranslation();
  const f = useRegisterFilters();
  const [partyType, setPartyType] = useState<"Customer" | "Supplier">("Customer");
  const [party, setParty] = useState("");

  const partyLink = useMemo<LinkDef>(
    () => ({
      endpoint: partyType === "Customer" ? "/selling/customers/" : "/buying/suppliers/",
      labelField: "name",
      filters: () => ({ company: f.company }),
    }),
    [partyType, f.company],
  );

  const base = { company: f.company, party_type: partyType, party, from_date: f.from, to_date: f.to };
  const ready = f.ready && !!party;
  const { data, isFetching, error } = useReport<Statement>(
    "/accounts/reports/party-statement/",
    { ...base, page: String(f.page), page_size: String(PAGE_SIZE) },
    ready,
  );

  const exportCsv = () => {
    if (!data) return;
    downloadCsv(`statement_${data.party.name}_${f.from}_${f.to}`, [
      [t("fields.posting_date"), t("acc.fields.voucher_type"), t("acc.fields.voucher_no"), t("acc.fields.debit"), t("acc.fields.credit"), t("acc.fields.balance")],
      ["", t("registers.opening_balance"), "", "", "", `${data.opening_balance} ${data.opening_dr_cr}`.trim()],
      ...data.rows.map((r) => [r.posting_date, r.voucher_type, r.voucher_no, r.debit, r.credit, `${r.balance} ${r.dr_cr}`.trim()]),
      ["", t("registers.closing_balance"), "", data.total_debit, data.total_credit, `${data.closing_balance} ${data.closing_dr_cr}`.trim()],
    ]);
  };

  return (
    <ReportShell
      titleKey="registers.nav.party_statement"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      status={
        <div className="flex gap-2">
          <CsvButton onExport={exportCsv} disabled={!data} />
          <PrintButton disabled={!data} />
        </div>
      }
      filters={
        <>
          <PeriodFilters
            f={{
              ...f,
              setCompany: (v) => {
                f.setCompany(v);
                setParty("");
              },
            }}
            prefix="ps"
          />
          <div className="space-y-1">
            <label htmlFor="ps-type" className="block text-xs font-medium text-slate-400">
              {t("acc.fields.party_type")}
            </label>
            <Select
              id="ps-type"
              value={partyType}
              onChange={(v) => {
                setPartyType(v as "Customer" | "Supplier");
                setParty("");
                f.setPage(1);
              }}
              className="w-40"
              options={[
                { value: "Customer", label: t("registers.customer") },
                { value: "Supplier", label: t("registers.supplier") },
              ]}
            />
          </div>
          <div className="space-y-1 w-60 max-w-full">
            <label htmlFor="ps-party" className="block text-xs font-medium text-slate-400">
              {t("acc.fields.party")}
            </label>
            <LinkField
              id="ps-party"
              link={partyLink}
              cacheKey={`registers.statement.${partyType}.${f.company}`}
              value={party}
              values={{}}
              parentValues={{}}
              onSelect={(v) => {
                setParty(v === null ? "" : String(v));
                f.setPage(1);
              }}
            />
          </div>
        </>
      }
    >
      {!ready ? (
        <Hint textKey={f.ready ? "registers.pick_party" : "registers.pick_period"} />
      ) : !data ? (
        <table className="w-full">
          <tbody>
            <MessageRow cols={1} loading />
          </tbody>
        </table>
      ) : (
        <>
          <div className="p-4 sm:p-6 bg-slate-950/40">
            <StatementDocument s={data} />
          </div>
          {data.pagination.num_pages > 1 && <p className="no-print px-5 text-xs text-amber-400">{t("registers.paged_note")}</p>}
          <Pager info={data.pagination} onPage={f.setPage} />
          {/* The print copy: #root is hidden in print, this portal is shown instead (index.css). */}
          {createPortal(
            <div className="print-only">
              <StatementDocument s={data} />
            </div>,
            document.body,
          )}
        </>
      )}
    </ReportShell>
  );
};
