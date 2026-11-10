import React, { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Bell, Check, Loader2, Save, ShieldAlert, Users } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";

interface UserOption {
  id: number;
  username: string;
  email: string;
  first_name?: string;
  last_name?: string;
}

interface AlertRuleItem {
  id: number;
  key: string;
  is_active: boolean;
  days: number | null;
  recipients: number[];
  recipient_details?: UserOption[];
}

const RULE_DESCRIPTIONS: Record<string, { label: string; desc: string }> = {
  raw_material_shortage: {
    label: "Raw Material Shortage",
    desc: "Alerts when raw material stock in unprocessed/ready warehouse falls below critical level.",
  },
  ready_low: {
    label: "Ready Stock Below Critical",
    desc: "Instant alert after dispatch or production when finished goods fall below critical level.",
  },
  large_order: {
    label: "Large Sales Order",
    desc: "Alerts when a Sales Order exceeding ₹50,000 is submitted.",
  },
  large_payment: {
    label: "Large Payment",
    desc: "Alerts when a Payment Entry exceeding ₹50,000 is submitted.",
  },
  payment_delay: {
    label: "Payment Delay (Overdue)",
    desc: "Alerts when an unpaid sales invoice reaches threshold days.",
  },
  expense_unpaid: {
    label: "Expense Unpaid",
    desc: "Alerts when an unpaid expense bill reaches threshold days.",
  },
  gst_credit_at_risk: {
    label: "GST Credit At Risk",
    desc: "Alerts when unpaid GST purchase invoices reach 150 days (ITC reversal risk).",
  },
  integrity_failed: {
    label: "System Integrity Failure",
    desc: "Instant alert when scheduled ledger or inventory integrity checks detect drifts.",
  },
  overdue_bills_digest: {
    label: "Overdue Bills Digest",
    desc: "Morning summary of all bills unpaid past 90 days.",
  },
  daily_summary: {
    label: "Daily Operations Summary",
    desc: "Morning digest of collections, dispatches, and short stock.",
  },
  day_summary: {
    label: "Day Summary",
    desc: "Evening summary for Admins: sold, collected, spent, to collect, to pay and low stock.",
  },
  weekly_digest: {
    label: "Weekly Financial Summary",
    desc: "Monday digest of collections needed, bills due, and tax deadlines.",
  },
};

export const AlertSettingsPage: React.FC = () => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [ruleEdits, setRuleEdits] = useState<Record<number, Partial<AlertRuleItem>>>({});

  // 1. Fetch Alert Rules
  const { data: rules = [], isLoading: isLoadingRules } = useQuery({
    queryKey: ["notifications", "rules"],
    queryFn: async () => {
      const res = await apiClient.get<any>("/notifications/rules/");
      return (Array.isArray(res.data) ? res.data : res.data.results || []) as AlertRuleItem[];
    },
  });

  // 2. Fetch Users for Recipient Selection
  const { data: users = [] } = useQuery({
    queryKey: ["users", "list"],
    queryFn: async () => {
      const res = await apiClient.get<any>("/auth/users/");
      return (Array.isArray(res.data) ? res.data : res.data.results || []) as UserOption[];
    },
  });

  useEffect(() => {
    if (rules.length > 0) {
      const initial: Record<number, Partial<AlertRuleItem>> = {};
      rules.forEach((r) => {
        initial[r.id] = {
          is_active: r.is_active,
          days: r.days,
          recipients: r.recipients || [],
        };
      });
      setRuleEdits(initial);
    }
  }, [rules]);

  // Update mutation
  const updateRuleMutation = useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: Partial<AlertRuleItem> }) => {
      const res = await apiClient.patch(`/notifications/rules/${id}/`, payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications", "rules"] });
      toast.success(t("common.saved", "Saved."));
    },
    onError: () => {
      toast.error(t("common.action_failed", "Failed to save rule."));
    },
  });

  const handleToggleActive = (id: number, current: boolean) => {
    const nextVal = !current;
    setRuleEdits((prev) => ({
      ...prev,
      [id]: { ...prev[id], is_active: nextVal },
    }));
  };

  const handleDaysChange = (id: number, val: string) => {
    const num = val === "" ? null : parseInt(val, 10);
    setRuleEdits((prev) => ({
      ...prev,
      [id]: { ...prev[id], days: isNaN(num as number) ? null : num },
    }));
  };

  const handleToggleRecipient = (ruleId: number, userId: number) => {
    setRuleEdits((prev) => {
      const currList = prev[ruleId]?.recipients || [];
      const nextList = currList.includes(userId)
        ? currList.filter((uid) => uid !== userId)
        : [...currList, userId];
      return {
        ...prev,
        [ruleId]: { ...prev[ruleId], recipients: nextList },
      };
    });
  };

  const handleSaveRule = (id: number) => {
    const edits = ruleEdits[id];
    if (edits) {
      updateRuleMutation.mutate({ id, payload: edits });
    }
  };

  return (
    <Layout>
      <div className="max-w-5xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-2">
              <ShieldAlert className="w-7 h-7 text-brand-400" />
              {t("notifications.rules_title", "Alert & Notification Rules")}
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              {t(
                "notifications.rules_subtitle",
                "Configure automated alert thresholds, email schedules, and staff recipients."
              )}
            </p>
          </div>
        </div>

        {/* Content */}
        {isLoadingRules ? (
          <div className="py-20 flex justify-center items-center text-slate-400 gap-3">
            <Loader2 className="w-6 h-6 animate-spin" />
            <span>{t("common.loading", "Loading...")}</span>
          </div>
        ) : (
          <div className="space-y-4">
            {rules.map((rule) => {
              const currentEdit = ruleEdits[rule.id] || {
                is_active: rule.is_active,
                days: rule.days,
                recipients: rule.recipients || [],
              };
              const meta = RULE_DESCRIPTIONS[rule.key] || {
                label: rule.key.replace(/_/g, " ").toUpperCase(),
                desc: "",
              };
              const isSaving = updateRuleMutation.isPending;

              return (
                <div
                  key={rule.id}
                  className={`bg-slate-900/90 border rounded-xl p-5 transition-colors shadow-sm ${
                    currentEdit.is_active
                      ? "border-slate-800"
                      : "border-slate-800/50 opacity-60 bg-slate-950/60"
                  }`}
                >
                  <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3">
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            checked={!!currentEdit.is_active}
                            onChange={() => handleToggleActive(rule.id, !!currentEdit.is_active)}
                            className="sr-only peer"
                          />
                          <div className="w-10 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-brand-500"></div>
                        </label>
                        <h2 className="text-base font-semibold text-slate-100">{meta.label}</h2>
                        <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                          {rule.key}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 mt-2">{meta.desc}</p>
                    </div>

                    {/* Threshold Days if applicable */}
                    <div className="flex items-center gap-3 shrink-0">
                      <div className="flex flex-col">
                        <label className="text-xs text-slate-400 mb-1">
                          {t("notifications.threshold_days", "Threshold (days)")}
                        </label>
                        <input
                          type="number"
                          min="0"
                          value={currentEdit.days !== null && currentEdit.days !== undefined ? currentEdit.days : ""}
                          onChange={(e) => handleDaysChange(rule.id, e.target.value)}
                          placeholder="None"
                          className="w-24 px-3 py-1.5 text-sm bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:border-brand-400"
                        />
                      </div>

                      <button
                        type="button"
                        onClick={() => handleSaveRule(rule.id)}
                        disabled={isSaving}
                        className="mt-5 px-3.5 py-1.5 text-xs font-medium rounded-lg bg-brand-500 text-white hover:bg-brand-600 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      >
                        <Save className="w-3.5 h-3.5" />
                        {t("common.save_short", "Save")}
                      </button>
                    </div>
                  </div>

                  {/* Recipients */}
                  <div className="mt-4 pt-4 border-t border-slate-800/80">
                    <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 mb-2.5">
                      <Users className="w-3.5 h-3.5 text-slate-400" />
                      {t("notifications.recipients", "Recipients:")}
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {users.length === 0 ? (
                        <span className="text-xs text-slate-500">{t("notifications.no_users")}</span>
                      ) : (
                        users.map((u) => {
                          const isSelected = (currentEdit.recipients || []).includes(u.id);
                          return (
                            <button
                              key={u.id}
                              type="button"
                              onClick={() => handleToggleRecipient(rule.id, u.id)}
                              className={`px-2.5 py-1 text-xs rounded-lg border transition-colors flex items-center gap-1.5 ${
                                isSelected
                                  ? "bg-brand-500/20 border-brand-500/40 text-brand-300 font-medium"
                                  : "bg-slate-800/50 border-slate-700/60 text-slate-400 hover:text-slate-200"
                              }`}
                            >
                              {isSelected && <Check className="w-3 h-3" />}
                              <span>{u.username}</span>
                              {u.email && <span className="text-[10px] opacity-70">({u.email})</span>}
                            </button>
                          );
                        })
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Layout>
  );
};

export default AlertSettingsPage;
