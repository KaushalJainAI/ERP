import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { apiClient } from "@/api/client";
import { formatIndianCurrency } from "@/lib/format";

export interface NotificationItem {
  id: number;
  rule_key: string;
  message_key: string;
  params: Record<string, any>;
  link: string;
  read_at: string | null;
  created_at: string;
  dedupe_key: string;
}

/** Params that hold rupee amounts, formatted before they reach the text. */
const MONEY_PARAMS = [
  "grand_total",
  "amount",
  "outstanding",
  "gst_amount",
  "total_amount",
  "money_in",
  "bills_total",
  "collections_needed",
  "bills_due",
];

/** "5 minutes ago" in the interface language. */
export function formatRelativeTime(dateString: string, language: string): string {
  const diffSec = Math.round((new Date(dateString).getTime() - Date.now()) / 1000);
  const format = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  if (Math.abs(diffSec) < 60) return format.format(0, "second");
  const diffMin = Math.round(diffSec / 60);
  if (Math.abs(diffMin) < 60) return format.format(diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  if (Math.abs(diffHour) < 24) return format.format(diffHour, "hour");
  const diffDays = Math.round(diffHour / 24);
  if (Math.abs(diffDays) < 7) return format.format(diffDays, "day");
  return new Date(dateString).toLocaleDateString(language);
}

/** Title of a notification; a rule with no text yet shows its own name. */
export function notificationTitle(t: TFunction, notif: NotificationItem): string {
  return t(`notifications.rule.${notif.rule_key}.title`, {
    defaultValue: notif.rule_key.replace(/_/g, " "),
  });
}

/** One-line detail of a notification, empty for a rule with no text yet. */
export function notificationBody(t: TFunction, notif: NotificationItem): string {
  const params: Record<string, unknown> = { ...notif.params };
  for (const key of MONEY_PARAMS) {
    if (key in params) params[key] = formatIndianCurrency(params[key] as string);
  }
  return t(`notifications.rule.${notif.rule_key}.body`, { ...params, defaultValue: "" });
}

/** The caller's recent notifications, shared by the bell and the home page. */
export function useNotificationList(enabled: boolean) {
  return useQuery({
    queryKey: ["notifications", "list"],
    queryFn: async () => {
      const res = await apiClient.get<any>("/notifications/");
      const items = Array.isArray(res.data) ? res.data : (res.data.results || []);
      return items as NotificationItem[];
    },
    enabled,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await apiClient.post(`/notifications/${id}/read/`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}
