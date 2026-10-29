import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { CheckCheck, FilePlus2, Landmark, Sparkles } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { LinkField } from "@/components/doc/LinkField";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { sumDec } from "@/lib/decimal";
import { formatDate } from "@/lib/format";
import type { LinkDef } from "@/types";
import { ReportShell, DateInput, subDec, isZero, useReport } from "../reports/parts";
import { fmtMoney } from "../reports/gst/lib";
import { td, tdR, th, thR, theadCls } from "../reports/registers/shared";
import { BankAccountSelect, btn, btnPrimary, monthToDate, useBankAccounts } from "./shared";

interface Tx {
  id: number;
  transaction_date: string;
  description: string;
  reference_no: string;
  amount: string;
}
interface Voucher {
  voucher_type: string;
  voucher_no: string;
  posting_date: string;
  party: string;
  reference_no: string;
  reference_date: string | null;
  amount: string;
}
interface Suggestion {
  bank_transaction: number;
  voucher_type: string;
  voucher_no: string;
  basis: "reference" | "date";
}
interface ToolData {
  transactions: Tx[];
  vouchers: Voucher[];
  suggestions: Suggestion[];
}

const vkey = (v: { voucher_type: string; voucher_no: string }) => `${v.voucher_type}|${v.voucher_no}`;

const counterAccountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.root_type,
  filters: () => ({ is_group: false, is_active: true }),
};

export const BankReconciliationPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { accounts, bankAccount, setBankAccount, loading } = useBankAccounts();
  const initial = useMemo(monthToDate, []);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [txId, setTxId] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [jeFor, setJeFor] = useState<Tx | null>(null);
  const [counter, setCounter] = useState<string | number | null>(null);
  const [error, setError] = useState("");

  const ready = !!bankAccount && !!from && !!to;
  const { data, isFetching, error: loadError } = useReport<ToolData>(
    "/accounts/bank-reconciliation/",
    { bank_account: bankAccount, from_date: from, to_date: to },
    ready,
  );

  useEffect(() => {
    setTxId(null);
    setPicked(new Set());
  }, [bankAccount, from, to]);

  const suggestionByTx = useMemo(() => new Map((data?.suggestions ?? []).map((s) => [s.bank_transaction, s])), [data]);
  const tx = data?.transactions.find((x) => x.id === txId) ?? null;
  const chosen = (data?.vouchers ?? []).filter((v) => picked.has(vkey(v)));
  const total = sumDec(chosen.map((v) => v.amount), 2);
  const diff = tx ? subDec(tx.amount, total) : "0.00";
  const canMatch = !!tx && chosen.length > 0 && isZero(diff);

  const refresh = () => qc.invalidateQueries({ queryKey: ["acc-report", "/accounts/bank-reconciliation/"] });

  const post = useMutation({
    mutationFn: async (args: { url: string; body: Record<string, unknown> }) =>
      (await apiClient.post(args.url, args.body)).data,
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const doMatch = (txn: Tx, vouchers: { voucher_type: string; voucher_no: string }[]) => {
    setError("");
    post.mutate(
      {
        url: "/accounts/bank-reconciliation/match/",
        body: {
          bank_transaction: txn.id,
          vouchers: vouchers.map((v) => ({ voucher_type: v.voucher_type, voucher_no: v.voucher_no })),
        },
      },
      {
        onSuccess: () => {
          toast.success(t("bank.rec.matched"));
          setTxId(null);
          setPicked(new Set());
          refresh();
        },
      },
    );
  };

  const makeJe = useMutation({
    mutationFn: async () =>
      (await apiClient.post(`/accounts/bank-transactions/${jeFor!.id}/make-journal-entry/`, { account: counter })).data,
    onSuccess: (je) => {
      setJeFor(null);
      navigate(`/accounts/journal-entries/${je.id}`);
    },
    onError: (e) => setError(summariseError(normaliseError(e))),
  });

  const pick = (s: Suggestion) => {
    setTxId(s.bank_transaction);
    setPicked(new Set([vkey(s)]));
  };
  const toggle = (v: Voucher) =>
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(vkey(v))) next.delete(vkey(v));
      else next.add(vkey(v));
      return next;
    });

  return (
    <ReportShell
      titleKey="bank.nav.reconcile"
      loading={isFetching || loading}
      error={loadError}
      noCompany={!loading && accounts.length === 0}
      status={
        tx ? (
          <span className="text-xs text-slate-400 tabular-nums">
            {t("bank.rec.selected_total")}: {fmtMoney(total)} / {fmtMoney(tx.amount)}
            {!isZero(diff) && <span className="ms-2 text-amber-300">{t("bank.rec.difference")}: {fmtMoney(diff)}</span>}
          </span>
        ) : null
      }
      filters={
        <>
          <BankAccountSelect accounts={accounts} value={bankAccount} onChange={setBankAccount} />
          <DateInput id="rec-from" labelKey="bank.from_date" value={from} onChange={setFrom} />
          <DateInput id="rec-to" labelKey="bank.to_date" value={to} onChange={setTo} />
          <button className={btnPrimary} disabled={!canMatch || post.isPending} onClick={() => tx && doMatch(tx, chosen)}>
            <CheckCheck className="w-4 h-4" /> {t("bank.rec.match_selected")}
          </button>
        </>
      }
    >
      {error && (
        <p role="alert" className="px-5 py-2 text-sm text-rose-300 border-b border-slate-800 break-words">
          {error}
        </p>
      )}
      <p className="px-5 py-2 text-xs text-slate-500 border-b border-slate-800">{t("bank.rec.help")}</p>
      <div className="grid lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-slate-800">
        <section className="min-w-0">
          <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80 border-b border-slate-800 flex items-center gap-2">
            <Landmark className="w-3.5 h-3.5" /> {t("bank.rec.bank_lines")} ({data?.transactions.length ?? 0})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className={theadCls}>
                <tr>
                  <th className={th}>{t("fields.posting_date")}</th>
                  <th className={th}>{t("bank.rec.description")}</th>
                  <th className={thR}>{t("bank.rec.amount")}</th>
                  <th className={th} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {(data?.transactions ?? []).map((x) => {
                  const s = suggestionByTx.get(x.id);
                  return (
                    <tr
                      key={x.id}
                      onClick={() => {
                        setTxId(x.id);
                        setPicked(new Set());
                      }}
                      className={`cursor-pointer ${x.id === txId ? "bg-indigo-950/50" : "hover:bg-slate-800/40"}`}
                      aria-selected={x.id === txId}
                    >
                      <td className={`${td} whitespace-nowrap`}>{formatDate(x.transaction_date)}</td>
                      <td className={td}>
                        <div className="max-w-xs truncate" title={x.description}>
                          {x.description}
                        </div>
                        {x.reference_no && <div className="text-xs text-slate-500">{x.reference_no}</div>}
                        {s && (
                          <button
                            className="mt-1 inline-flex items-center gap-1 text-xs text-emerald-400 hover:underline"
                            onClick={(e) => {
                              e.stopPropagation();
                              pick(s);
                            }}
                          >
                            <Sparkles className="w-3 h-3" /> {s.voucher_no} ({t(`bank.rec.basis_${s.basis}`)})
                          </button>
                        )}
                      </td>
                      <td className={`${tdR} ${x.amount.startsWith("-") ? "text-rose-300" : "text-emerald-300"}`}>{fmtMoney(x.amount)}</td>
                      <td className={td}>
                        <button
                          className="text-xs text-slate-400 hover:text-slate-100 inline-flex items-center gap-1"
                          title={t("bank.rec.create_je")}
                          onClick={(e) => {
                            e.stopPropagation();
                            setCounter(null);
                            setJeFor(x);
                          }}
                        >
                          <FilePlus2 className="w-3.5 h-3.5" /> {t("bank.rec.create_je")}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {data && data.transactions.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                      {t("bank.rec.no_lines")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="min-w-0">
          <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80 border-b border-slate-800">
            {t("bank.rec.erp_vouchers")} ({data?.vouchers.length ?? 0})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className={theadCls}>
                <tr>
                  <th className={th} />
                  <th className={th}>{t("fields.posting_date")}</th>
                  <th className={th}>{t("bank.rec.voucher")}</th>
                  <th className={thR}>{t("bank.rec.amount")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {(data?.vouchers ?? []).map((v) => (
                  <tr key={vkey(v)} className="hover:bg-slate-800/40">
                    <td className={td}>
                      <input
                        type="checkbox"
                        aria-label={v.voucher_no}
                        checked={picked.has(vkey(v))}
                        disabled={!tx}
                        onChange={() => toggle(v)}
                      />
                    </td>
                    <td className={`${td} whitespace-nowrap`}>{formatDate(v.posting_date)}</td>
                    <td className={td}>
                      <div>{v.voucher_no}</div>
                      <div className="text-xs text-slate-500">
                        {[v.reference_no, v.party].filter(Boolean).join(" - ")}
                      </div>
                    </td>
                    <td className={`${tdR} ${v.amount.startsWith("-") ? "text-rose-300" : "text-emerald-300"}`}>{fmtMoney(v.amount)}</td>
                  </tr>
                ))}
                {data && data.vouchers.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                      {t("bank.rec.no_vouchers")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <Modal
        open={!!jeFor}
        title={t("bank.rec.create_je")}
        description={t("bank.rec.je_help")}
        onClose={() => setJeFor(null)}
        footer={
          <>
            <button className={btn} onClick={() => setJeFor(null)}>
              {t("common.close")}
            </button>
            <button className={btnPrimary} disabled={!counter || makeJe.isPending} onClick={() => makeJe.mutate()}>
              {t("bank.rec.je_create")}
            </button>
          </>
        }
      >
        {jeFor && (
          <div className="space-y-3 text-sm text-slate-300">
            <p className="tabular-nums">
              {formatDate(jeFor.transaction_date)} - {fmtMoney(jeFor.amount)} - {jeFor.description}
            </p>
            <div className="space-y-1">
              <span className="block text-xs font-medium text-slate-400">{t("bank.rec.je_account")}</span>
              <LinkField
                link={counterAccountLink}
                cacheKey="bank.rec.counter_account"
                value={counter}
                values={{}}
                parentValues={{}}
                onSelect={(value) => setCounter(value)}
              />
            </div>
          </div>
        )}
      </Modal>
    </ReportShell>
  );
};
