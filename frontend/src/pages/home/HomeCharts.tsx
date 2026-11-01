import React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight } from "lucide-react";
import { formatIndianCompact, formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import { AGEING_BUCKETS, type AgeingBucket, type HomeData } from "./useHomeData";

/** A chart with its title and link. Shows a note instead of an empty plot. */
const ChartFrame: React.FC<{
  title: string;
  hint?: string;
  to: string;
  note?: string;
  children?: React.ReactNode;
}> = ({ title, hint, to, note, children }) => (
  <section className="min-w-0">
    <Link to={to} className="group inline-flex max-w-full items-center gap-1.5">
      <h2 className="truncate text-sm font-semibold text-slate-100">{title}</h2>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 rtl:rotate-180" />
    </Link>
    {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    {note ? <p className="mt-4 text-sm text-slate-500">{note}</p> : <div className="mt-4">{children}</div>}
  </section>
);

/** The note a chart shows instead of its plot, if it has one. */
function chartNote(
  t: (key: string) => string,
  query: { isError: boolean; data?: unknown },
  isEmpty: boolean,
  emptyKey: string
): string | undefined {
  if (query.isError) return t("home.dash.chart_failed");
  if (query.data !== undefined && isEmpty) return t(emptyKey);
  return undefined;
}

/** Smallest round axis top at or above `value`; each halves to a round tick too. */
function niceCeiling(value: number): number {
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 3, 4, 5, 6, 8, 10].find((n) => n * power >= value) ?? 10;
  return step * power;
}

/** Monthly sales columns. This month is the strong bar, and the only one labelled. */
export const SalesChart: React.FC<{ query: HomeData["sales"] }> = ({ query }) => {
  const { t, i18n } = useTranslation();
  const rows = query.data ?? [];
  const peak = Math.max(...rows.map((row) => row.amount), 0);
  const top = peak > 0 ? niceCeiling(peak) : 1;
  const monthName = new Intl.DateTimeFormat(i18n.language, { month: "short", timeZone: "UTC" });
  const label = (period: string) => monthName.format(new Date(`${period}-01T00:00:00Z`));

  return (
    <ChartFrame
      title={t("home.dash.sales_title")}
      hint={t("home.dash.sales_hint")}
      to="/selling/analytics"
      note={chartNote(t, query, peak <= 0, "home.dash.no_sales")}
    >
      <div className="flex gap-2" aria-hidden={rows.length === 0}>
        <div className="flex h-44 w-11 shrink-0 flex-col justify-between text-end text-[11px] tabular-nums text-slate-500">
          {[top, top / 2, 0].map((tick) => (
            <span key={tick} className="-my-1.5 leading-3">
              {formatIndianCompact(tick)}
            </span>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <div className="relative flex h-44 items-end border-b border-slate-700">
            <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-slate-800" />
            <div className="pointer-events-none absolute inset-x-0 top-1/2 border-t border-slate-800" />
            {rows.map((row, index) => {
              const current = index === rows.length - 1;
              return (
                <div
                  key={row.period}
                  tabIndex={0}
                  aria-label={`${label(row.period)}: ${formatIndianCurrency(row.amount)}`}
                  className="group relative flex h-full flex-1 flex-col items-center justify-end rounded-t outline-none hover:bg-slate-800/40 focus-visible:bg-slate-800/40"
                >
                  {current && row.amount > 0 && (
                    <span className="mb-1 whitespace-nowrap text-[11px] font-semibold text-slate-200 group-hover:invisible group-focus-visible:invisible">
                      {formatIndianCompact(row.amount)}
                    </span>
                  )}
                  <div
                    className={`w-full max-w-6 rounded-t ${current ? "bg-indigo-600" : "bg-indigo-700"}`}
                    style={{
                      height: `${(row.amount / top) * 100}%`,
                      minHeight: row.amount > 0 ? 2 : 0,
                      maxHeight: "calc(100% - 18px)",
                    }}
                  />
                  <span
                    className={`pointer-events-none absolute top-1 z-10 hidden whitespace-nowrap rounded-lg bg-slate-100 px-2 py-1 text-xs font-medium text-slate-900 shadow-lg group-hover:block group-focus-visible:block ${
                      index < rows.length / 2 ? "start-0" : "end-0"
                    }`}
                  >
                    {label(row.period)}: {formatIndianCurrency(row.amount)}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-1.5 flex">
            {rows.map((row) => (
              <span key={row.period} className="flex-1 truncate text-center text-[11px] text-slate-500">
                {label(row.period)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <table className="sr-only">
        <caption>{t("home.dash.sales_title")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("home.dash.month")}</th>
            <th scope="col">{t("home.dash.amount")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.period}>
              <td>{label(row.period)}</td>
              <td>{formatIndianCurrency(row.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ChartFrame>
  );
};

/** Older money is a stronger step of the same hue. Both modes step away from the surface. */
const BUCKET_FILL: Record<AgeingBucket, string> = {
  "0-30": "bg-indigo-700",
  "31-60": "bg-indigo-600",
  "61-90": "bg-indigo-400",
  "90+": "bg-indigo-200",
};

/** Customer money split by days overdue. The legend lists every amount, so nothing needs a hover. */
export const AgeingChart: React.FC<{ query: HomeData["ageing"] }> = ({ query }) => {
  const { t } = useTranslation();
  const total = query.data;
  // A credit note can make a bucket negative. It stays in the list but draws nothing.
  const segments = AGEING_BUCKETS.map((bucket) => ({
    bucket,
    amount: Number(total?.buckets[bucket] ?? 0),
  }));
  const drawn = segments.reduce((sum, segment) => sum + Math.max(segment.amount, 0), 0);

  return (
    <ChartFrame
      title={t("home.dash.ageing_title")}
      to="/accounts/collections"
      note={chartNote(t, query, drawn <= 0, "home.dash.no_dues")}
    >
      <p className="text-2xl font-semibold text-slate-100">
        {total ? formatIndianCompact(total.outstanding) : "…"}
      </p>
      <div className="mt-3 flex h-3 gap-0.5" aria-hidden="true">
        {segments
          .filter((segment) => segment.amount > 0)
          .map((segment) => (
            <div
              key={segment.bucket}
              title={`${t(`ageing.bucket.${segment.bucket}`)}: ${formatIndianCurrency(segment.amount)}`}
              className={`min-w-1 rounded-sm first:rounded-s last:rounded-e ${BUCKET_FILL[segment.bucket]}`}
              style={{ flexGrow: segment.amount }}
            />
          ))}
      </div>
      <dl className="mt-3 space-y-1.5">
        {segments.map((segment) => (
          <div key={segment.bucket} className="flex items-center gap-2 text-xs">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${BUCKET_FILL[segment.bucket]}`} />
            <dt className="min-w-0 flex-1 truncate text-slate-400">{t(`ageing.bucket.${segment.bucket}`)}</dt>
            <dd className="tabular-nums text-slate-200">{formatIndianCompact(segment.amount)}</dd>
          </div>
        ))}
      </dl>
    </ChartFrame>
  );
};

/** The six items with the most ready stock. Items at or below their critical level are flagged. */
export const ReadyStockChart: React.FC<{ query: HomeData["stages"] }> = ({ query }) => {
  const { t } = useTranslation();
  const rows = (query.data ?? [])
    .map((row) => ({ ...row, qty: Number(row.ready_qty) }))
    .filter((row) => row.qty > 0)
    .sort((a, b) => b.qty - a.qty)
    .slice(0, 6);
  const peak = rows[0]?.qty ?? 0;

  return (
    <ChartFrame
      title={t("home.ws.factory.ready")}
      to="/stock/reports/stages"
      note={chartNote(t, query, rows.length === 0, "home.dash.no_ready")}
    >
      <ul className="space-y-2.5">
        {rows.map((row) => (
          <li key={row.item_name} className="text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-slate-300">{row.item_name}</span>
              {row.is_critical && (
                <span className="inline-flex shrink-0 items-center gap-1 font-medium text-rose-400">
                  <AlertTriangle className="h-3 w-3" />
                  {t("home.dash.low_tag")}
                </span>
              )}
              <span className="ms-auto shrink-0 tabular-nums text-slate-200">{formatIndianNumber(row.qty)}</span>
            </div>
            <div className="mt-1 h-1.5">
              <div
                className={`h-full rounded-e ${row.is_critical ? "bg-rose-600" : "bg-indigo-600"}`}
                style={{ width: `${(row.qty / peak) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </ChartFrame>
  );
};
