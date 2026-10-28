/**
 * Helpers shared by the GST report pages: exact (string-based) number formatting and
 * client-side CSV export. Money arrives as 2dp decimal strings and is never converted to
 * a float here: the CSV carries the server's strings verbatim.
 */

const DEC_RE = /^-?\d+(\.\d+)?$/;

export const isDec = (v: unknown): v is string => typeof v === "string" && DEC_RE.test(v);

/** True for "0", "-0.00", "0.000" ... */
export const isZeroDec = (v: string): boolean => /^-?0+(\.0+)?$/.test(v);

/** Drop a leading minus sign (the sign is shown as Dr / Cr by the caller). */
export const absDec = (v: string): string => (v.startsWith("-") ? v.slice(1) : v);

/** "1234567.5" -> "12,34,567.50" (lakh / crore grouping). Non-decimals pass through. */
export function fmtMoney(v: string | null | undefined, symbol = false): string {
  if (v === null || v === undefined) return "";
  if (!isDec(v)) return String(v);
  const neg = v.startsWith("-");
  const [int, frac = ""] = (neg ? v.slice(1) : v).split(".");
  const last3 = int.slice(-3);
  const rest = int.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${neg ? "-" : ""}${symbol ? "₹" : ""}${grouped}.${frac.padEnd(2, "0")}`;
}

/** "18.000" -> "18", "2.500" -> "2.5". */
export function fmtRate(v: string | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (!isDec(v) || !v.includes(".")) return String(v);
  return v.replace(/0+$/, "").replace(/\.$/, "");
}

/** "1234.500" -> "1,234.5" (quantities; grouped, trailing zeros trimmed). */
export function fmtQty(v: string | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (!isDec(v)) return String(v);
  const [int, frac = ""] = fmtRate(v).split(".");
  const neg = int.startsWith("-");
  const digits = neg ? int.slice(1) : int;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${neg ? "-" : ""}${grouped}${frac ? "." + frac : ""}`;
}

/** Lower-case a server value into an i18n key slug: "Reverse Charge" -> "reverse_charge". */
export const slug = (v: string): string => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

/* ---------------------------------------------------------------- CSV */

export type CsvCell = string | number | null | undefined;

const csvCell = (cell: CsvCell): string => {
  if (cell === null || cell === undefined) return "";
  let s = String(cell);
  // Spreadsheet formula injection: neutralise text that starts like a formula
  // (numbers such as "-5.00" are left alone so the figures stay exact).
  if (/^[=+\-@\t\r]/.test(s) && !DEC_RE.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const toCsv = (rows: CsvCell[][]): string => rows.map((r) => r.map(csvCell).join(",")).join("\r\n");

/** Save `rows` as a UTF-8 (BOM, so Excel reads Indian scripts) CSV file. */
export function downloadCsv(filename: string, rows: CsvCell[][]): void {
  const blob = new Blob(["﻿", toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** File-name-safe tag for the reporting period. */
export function periodTag(period: { from_date: string | null; to_date: string | null } | undefined): string {
  if (!period) return "";
  const { from_date: f, to_date: t } = period;
  if (!f && !t) return "all";
  if (f && t && f.slice(0, 7) === t.slice(0, 7) && f.endsWith("-01")) return f.slice(0, 7);
  return `${f ?? "start"}_${t ?? "end"}`;
}
