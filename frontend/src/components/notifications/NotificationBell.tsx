import React, { useState, useEffect, useRef } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Bell, Check, Settings, ExternalLink } from "lucide-react";
import { apiClient } from "@/api/client";

interface NotificationItem {
  id: number;
  rule_key: string;
  message_key: string;
  params: Record<string, any>;
  link: string;
  read_at: string | null;
  created_at: string;
  dedupe_key: string;
}

function formatRelativeTime(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffSec < 60) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDays = Math.floor(diffHour / 24);
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

function formatNotificationTitle(notif: NotificationItem): string {
  const key = notif.rule_key;
  if (key === "raw_material_shortage") return "Raw Material Shortage";
  if (key === "ready_low") return "Low Ready Stock";
  if (key === "large_order") return "Large Sales Order";
  if (key === "large_payment") return "Large Payment Received";
  if (key === "payment_delay") return "Payment Delayed";
  if (key === "expense_unpaid") return "Expense Unpaid";
  if (key === "gst_credit_at_risk") return "GST Credit At Risk";
  if (key === "integrity_failed") return "System Integrity Alert";
  if (key === "overdue_bills_digest") return "Overdue Bills Digest";
  if (key === "daily_summary") return "Daily Operations Summary";
  if (key === "weekly_digest") return "Weekly Digest";
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const NotificationBell: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside
  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  // Poll unread count every 60 seconds
  const { data: countData } = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: async () => {
      const res = await apiClient.get<{ count: number }>("/notifications/unread-count/");
      return res.data;
    },
    refetchInterval: 60000,
  });

  // Fetch recent notifications when popover is open
  const { data: notifData } = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: async () => {
      const res = await apiClient.get<any>("/notifications/");
      const items = Array.isArray(res.data) ? res.data : (res.data.results || []);
      return items as NotificationItem[];
    },
    enabled: open,
  });

  const markReadMutation = useMutation({
    mutationFn: async (id: number) => {
      await apiClient.post(`/notifications/${id}/read/`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post("/notifications/read-all/");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const unreadCount = countData?.count || 0;
  const notifications = notifData || [];

  const handleNotificationClick = (notif: NotificationItem) => {
    if (!notif.read_at) {
      markReadMutation.mutate(notif.id);
    }
    setOpen(false);
    if (notif.link) {
      navigate(notif.link);
    }
  };

  return (
    <div ref={dropdownRef} className="relative inline-block text-start">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="relative p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors focus:outline-none"
        title={t("notifications.title", "Notifications")}
        aria-label={t("notifications.title", "Notifications")}
      >
        <Bell className="w-5 h-5" />
        {unreadCount > 0 && (
          <span className="absolute top-1 end-1 flex items-center justify-center min-w-[1.125rem] h-4.5 px-1 text-[10px] font-bold text-white bg-rose-600 rounded-full border-2 border-slate-900 animate-pulse">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute end-0 mt-2 w-80 sm:w-96 bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl z-50 overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/90 backdrop-blur">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-slate-100">
                {t("notifications.title", "Notifications")}
              </span>
              {unreadCount > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-medium">
                  {unreadCount} {t("notifications.unread", "new")}
                </span>
              )}
            </div>
            {unreadCount > 0 && (
              <button
                type="button"
                onClick={() => markAllReadMutation.mutate()}
                className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1 transition-colors"
              >
                <Check className="w-3.5 h-3.5" />
                {t("notifications.mark_all_read", "Mark all read")}
              </button>
            )}
          </div>

          {/* List */}
          <div className="max-h-80 overflow-y-auto divide-y divide-slate-800/60">
            {notifications.length === 0 ? (
              <div className="p-8 text-center text-slate-500 text-sm">
                <Bell className="w-8 h-8 mx-auto mb-2 opacity-30" />
                {t("notifications.no_notifications", "No notifications yet.")}
              </div>
            ) : (
              notifications.map((notif) => {
                const isUnread = !notif.read_at;
                return (
                  <div
                    key={notif.id}
                    onClick={() => handleNotificationClick(notif)}
                    className={`p-3.5 hover:bg-slate-800/60 transition-colors cursor-pointer flex gap-3 ${
                      isUnread ? "bg-slate-800/30" : ""
                    }`}
                  >
                    <span
                      className={`w-2 h-2 mt-1.5 rounded-full shrink-0 ${
                        isUnread ? "bg-brand-400 ring-2 ring-brand-400/30" : "bg-transparent"
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold text-slate-200 truncate">
                          {formatNotificationTitle(notif)}
                        </p>
                        <span className="text-[10px] text-slate-500 shrink-0">
                          {formatRelativeTime(notif.created_at)}
                        </span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5 line-clamp-2">
                        {Object.entries(notif.params || {}).map(([k, v]) => (
                          <span key={k} className="me-2">
                            <span className="text-slate-500">{k}:</span> {String(v)}
                          </span>
                        ))}
                      </div>
                      {notif.link && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-brand-400 hover:underline">
                          <span>{t("common.view", "View")}</span>
                          <ExternalLink className="w-3 h-3" />
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer */}
          <div className="px-4 py-2.5 bg-slate-950/80 border-t border-slate-800 flex justify-between items-center text-xs">
            <Link
              to="/settings/alerts"
              onClick={() => setOpen(false)}
              className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 transition-colors"
            >
              <Settings className="w-3.5 h-3.5" />
              <span>{t("notifications.alert_settings", "Alert Settings")}</span>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
};
