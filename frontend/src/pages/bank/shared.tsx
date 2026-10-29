import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { listRecords } from "@/api/endpoints";
import { Select } from "@/components/ui/Select";

export interface BankAccountRow {
  id: number;
  account_name: string;
  bank_name: string;
  account_number_last4: string;
  is_default: boolean;
  is_active: boolean;
}

export const btn =
  "inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed";
export const btnPrimary =
  "inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40 disabled:cursor-not-allowed";

/** The active company's bank accounts; selects the default (or first) one. */
export function useBankAccounts() {
  const { data, isLoading } = useQuery({
    queryKey: ["bank-accounts", "active"],
    queryFn: async () => (await listRecords("/accounts/bank-accounts/", { page_size: 100 })).results as BankAccountRow[],
  });
  const accounts = (data ?? []).filter((a) => a.is_active);
  const [bankAccount, setBankAccount] = useState("");
  useEffect(() => {
    if (!bankAccount && accounts.length) {
      setBankAccount(String((accounts.find((a) => a.is_default) ?? accounts[0]).id));
    }
  }, [accounts, bankAccount]);
  return { accounts, bankAccount, setBankAccount, loading: isLoading };
}

export const BankAccountSelect: React.FC<{
  accounts: BankAccountRow[];
  value: string;
  onChange: (v: string) => void;
}> = ({ accounts, value, onChange }) => {
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <label htmlFor="bank-account" className="block text-xs font-medium text-slate-400">
        {t("bank.bank_account")}
      </label>
      <Select
        id="bank-account"
        value={value}
        onChange={onChange}
        className="w-72"
        placeholder={t("bank.choose_bank")}
        options={accounts.map((a) => ({
          value: String(a.id),
          label: `${a.account_name} - ${a.bank_name}${a.account_number_last4 ? ` (…${a.account_number_last4})` : ""}`,
        }))}
      />
    </div>
  );
};

/** First of the current month .. today, as ISO dates. */
export function monthToDate(): { from: string; to: string } {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return {
    from: `${d.getFullYear()}-${p(d.getMonth() + 1)}-01`,
    to: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`,
  };
}
