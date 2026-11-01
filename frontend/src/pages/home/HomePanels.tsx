import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bell, CheckCircle2, ChevronRight, Clock3, LayoutGrid } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  formatRelativeTime,
  notificationBody,
  notificationTitle,
  useMarkNotificationRead,
  useNotificationList,
  type NotificationItem,
} from "@/components/notifications/useNotifications";

/** One app on the home screen: a link, or a button when `onClick` is given. */
export interface HomeApp {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  fill: string;
  to?: string;
  onClick?: () => void;
  badge?: number;
}

const AppIcon: React.FC<{ app: HomeApp }> = ({ app }) => {
  const Icon = app.icon;
  const body = (
    <>
      <span className={`relative flex h-14 w-14 items-center justify-center rounded-2xl text-white shadow-md transition-transform group-hover:-translate-y-0.5 group-active:scale-95 sm:h-16 sm:w-16 ${app.fill}`}>
        <Icon className="h-7 w-7" />
        {!!app.badge && (
          <span className="absolute -end-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-slate-950 bg-rose-600 px-1 text-[10px] font-bold text-white">
            {app.badge > 99 ? "99+" : app.badge}
          </span>
        )}
      </span>
      <span className="w-full break-words text-center text-[11px] font-medium leading-tight text-slate-300 line-clamp-2 sm:text-xs">
        {app.label}
      </span>
    </>
  );
  const className =
    "group flex w-full min-w-0 flex-col items-center gap-2 rounded-xl py-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40";
  return app.to ? (
    <Link to={app.to} className={className}>
      {body}
    </Link>
  ) : (
    <button type="button" onClick={app.onClick} className={className}>
      {body}
    </button>
  );
};

/** The workspaces as a phone-style grid of app icons, ending in "All modules". */
export const AppGrid: React.FC<{ apps: HomeApp[] }> = ({ apps }) => {
  const { t } = useTranslation();
  const all: HomeApp = {
    key: "all-modules",
    label: t("home.ws.all_modules"),
    icon: LayoutGrid,
    fill: "bg-slate-500",
    to: "/all-modules",
  };
  return (
    <nav aria-label={t("home.workspaces")}>
      <ul className="grid grid-cols-4 gap-x-2 gap-y-4 sm:grid-cols-[repeat(auto-fill,minmax(6.5rem,1fr))]">
        {[...apps, all].map((app) => (
          <li key={app.key} className="min-w-0">
            <AppIcon app={app} />
          </li>
        ))}
      </ul>
    </nav>
  );
};

export interface HomeFigure {
  key: string;
  label: string;
  value: string;
  to: string;
}

/** The day's key numbers on one line, split by hairlines rather than boxed. */
export const FigureStrip: React.FC<{ figures: HomeFigure[] }> = ({ figures }) => (
  <dl className="grid grid-cols-2 gap-y-4 border-y border-slate-800 py-4 lg:flex lg:divide-x lg:divide-slate-800 rtl:lg:divide-x-reverse">
    {figures.map((figure) => (
      <div key={figure.key} className="min-w-0 lg:flex-1 lg:px-6 lg:first:ps-0">
        <Link to={figure.to} className="group block">
          <dt className="truncate text-xs text-slate-400 group-hover:text-indigo-400">{figure.label}</dt>
          <dd className="mt-0.5 truncate text-xl font-semibold text-slate-100">{figure.value}</dd>
        </Link>
      </div>
    ))}
  </dl>
);

export interface AttentionRow {
  key: string;
  label: string;
  value: string;
  to: string;
  icon: LucideIcon;
  /** Money or stock already in trouble, not just work waiting its turn. */
  urgent?: boolean;
}

/** What needs doing now. Says "all clear" only when every source has answered. */
export const AttentionList: React.FC<{ rows: AttentionRow[]; loading: boolean; failed: boolean }> = ({
  rows,
  loading,
  failed,
}) => {
  const { t } = useTranslation();
  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-100">{t("home.dash.attention")}</h2>
      {rows.length > 0 && (
        <ul className="mt-2 divide-y divide-slate-800">
          {rows.map((row) => {
            const Icon = row.urgent ? AlertTriangle : row.icon;
            return (
              <li key={row.key}>
                <Link to={row.to} className="group flex items-center gap-3 py-2.5">
                  <Icon className={`h-4 w-4 shrink-0 ${row.urgent ? "text-rose-400" : "text-amber-400"}`} />
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-300 group-hover:text-slate-100">
                    {row.label}
                  </span>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-100">{row.value}</span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-indigo-400 rtl:rotate-180" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {loading && rows.length === 0 && (
        <p className="mt-2 flex items-center gap-2 text-sm text-slate-500" role="status">
          <Clock3 className="h-4 w-4 animate-pulse" />
          {t("home.pending.loading")}
        </p>
      )}
      {!loading && !failed && rows.length === 0 && (
        <p className="mt-2 flex items-center gap-2 text-sm text-slate-400">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
          {t("home.dash.all_clear")}
        </p>
      )}
      {failed && <p className="mt-2 text-xs text-rose-400">{t("home.dash.partial")}</p>}
    </section>
  );
};

/** The latest notifications and digests, the same feed as the bell in the top bar. */
export const NotificationFeed: React.FC = () => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const list = useNotificationList(true);
  const markRead = useMarkNotificationRead();
  const items = (list.data ?? []).slice(0, 5);

  const open = (notif: NotificationItem) => {
    if (!notif.read_at) markRead.mutate(notif.id);
    if (notif.link) navigate(notif.link);
  };

  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-100">{t("notifications.title")}</h2>
      {list.isError && <p className="mt-2 text-sm text-slate-500">{t("home.dash.partial")}</p>}
      {list.data && items.length === 0 && (
        <p className="mt-2 flex items-center gap-2 text-sm text-slate-500">
          <Bell className="h-4 w-4 shrink-0" />
          {t("notifications.no_notifications")}
        </p>
      )}
      {items.length > 0 && (
        <ul className="mt-2 divide-y divide-slate-800">
          {items.map((notif) => (
            <li key={notif.id}>
              <button
                type="button"
                onClick={() => open(notif)}
                className="flex w-full gap-2.5 py-2.5 text-start hover:bg-slate-800/40"
              >
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${notif.read_at ? "bg-transparent" : "bg-indigo-500"}`}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-200">
                      {notificationTitle(t, notif)}
                    </span>
                    <span className="shrink-0 text-[11px] text-slate-500">
                      {formatRelativeTime(notif.created_at, i18n.language)}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-400 line-clamp-2">
                    {notificationBody(t, notif)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
