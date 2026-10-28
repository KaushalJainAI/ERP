import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { formatDate } from "@/lib/format";
import { downloadCsv, fmtMoney, fmtQty, fmtRate, isZeroDec, periodTag, slug } from "./lib";
import {
  Grid,
  GridCol,
  GstFilterBar,
  GstReportFrame,
  SectionHead,
  StatTile,
  gridCsv,
  useGstFilters,
  useGstReport,
  usePlaceOfSupply,
} from "./shared";

interface RateRow {
  rate: string;
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
}
interface Invoice {
  name: string;
  posting_date: string;
  invoice_value: string;
  place_of_supply: string;
  reverse_charge: string;
  invoice_type: string;
  rates: RateRow[];
}
interface Tax {
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
}
interface Gstr1 {
  company: { id: number; name: string; gstin: string; state: string };
  period: { from_date: string | null; to_date: string | null };
  summary: { invoice_count: number; taxable_value: string; igst: string; cgst: string; sgst: string; invoice_value: string };
  b2b: { party_gstin: string; party_name: string; invoices: Invoice[] }[];
  b2cl: { place_of_supply: string; invoices: Invoice[] }[];
  b2cs: (Tax & { place_of_supply: string; supply_type: string; rate: string })[];
  exports: { export_type: string; invoices: Invoice[] }[];
  nil_exempt_non_gst: { description: string; nil_rated: string; exempted: string; non_gst: string }[];
  hsn: { b2b: HsnRow[]; b2c: HsnRow[] };
  docs_issued: { series: string; from: string; to: string; total: number; cancelled: number }[];
  warnings: { legacy_invoices_without_gst_data: number; lines_without_hsn: number };
}
type HsnRow = Tax & { hsn: string; uqc: string; total_qty: string; rate: string; total_value: string };

/** One invoice x rate line, with the group it belongs to (GSTIN / state / export type). */
interface FlatRow {
  group: string[];
  inv: Invoice;
  rate: RateRow;
  firstOfInvoice: boolean;
  firstOfGroup: boolean;
}

const flatten = <G,>(groups: G[], label: (g: G) => string[], invoices: (g: G) => Invoice[]): FlatRow[] => {
  const out: FlatRow[] = [];
  for (const g of groups) {
    let firstOfGroup = true;
    for (const inv of invoices(g)) {
      inv.rates.forEach((rate, i) => {
        out.push({ group: label(g), inv, rate, firstOfInvoice: i === 0, firstOfGroup });
        firstOfGroup = false;
      });
    }
  }
  return out;
};

type TabKey = "b2b" | "b2cl" | "b2cs" | "exports" | "nil" | "hsn" | "docs";
const TABS: TabKey[] = ["b2b", "b2cl", "b2cs", "exports", "nil", "hsn", "docs"];

export const Gstr1Page: React.FC = () => {
  const { t } = useTranslation();
  const f = useGstFilters();
  const pos = usePlaceOfSupply();
  const [tab, setTab] = useState<TabKey>("b2b");
  const { data, isFetching, error } = useGstReport<Gstr1>("/gst/reports/gstr1/", f.params, f.ready);

  const money = (v: string) => fmtMoney(v);
  const tr = (prefix: string, raw: string) => t(`gstreports.${prefix}_${slug(raw)}`, { defaultValue: raw });
  const invoiceType = (raw: string) =>
    t(`gstreports.it_${raw === "Regular" ? "regular" : raw.includes("without") ? "sez_without" : "sez_with"}`, { defaultValue: raw });
  const supplyType = (raw: string) => (raw === "INTER" ? t("gstreports.st_inter") : raw === "INTRA" ? t("gstreports.st_intra") : raw);
  const exportType = (raw: string) => tr("exp", raw);

  const invoiceCell = (name: string) => (
    <Link
      className="text-indigo-300 hover:underline whitespace-nowrap"
      to={`/selling/sales-invoices?search=${encodeURIComponent(name)}`}
      title={t("gstreports.open_invoice")}
    >
      {name}
    </Link>
  );

  /** Columns of the invoice-bearing sections (B2B, B2CL, exports). */
  const invoiceCols = (groups: { head: string; text: (g: string) => string }[]): GridCol<FlatRow>[] => [
    ...groups.map(
      (g, gi): GridCol<FlatRow> => ({
        head: g.head,
        cell: (r) => (r.firstOfGroup ? g.text(r.group[gi]) : ""),
        csv: (r) => g.text(r.group[gi]),
      })
    ),
    { head: t("gstreports.col_invoice"), cell: (r) => (r.firstOfInvoice ? invoiceCell(r.inv.name) : ""), csv: (r) => r.inv.name },
    { head: t("gstreports.col_date"), cell: (r) => (r.firstOfInvoice ? formatDate(r.inv.posting_date) : ""), csv: (r) => r.inv.posting_date },
    { head: t("gstreports.col_invoice_value"), num: true, cell: (r) => (r.firstOfInvoice ? money(r.inv.invoice_value) : ""), csv: (r) => r.inv.invoice_value },
    { head: t("gstreports.col_pos"), cell: (r) => (r.firstOfInvoice ? pos(r.inv.place_of_supply) : ""), csv: (r) => pos(r.inv.place_of_supply) },
    { head: t("gstreports.col_reverse_charge"), cell: (r) => (r.firstOfInvoice ? r.inv.reverse_charge : ""), csv: (r) => r.inv.reverse_charge },
    { head: t("gstreports.col_invoice_type"), cell: (r) => (r.firstOfInvoice ? invoiceType(r.inv.invoice_type) : ""), csv: (r) => invoiceType(r.inv.invoice_type) },
    { head: t("gstreports.col_rate"), num: true, cell: (r) => fmtRate(r.rate.rate), csv: (r) => r.rate.rate },
    { head: t("gstreports.col_taxable"), num: true, cell: (r) => money(r.rate.taxable_value), csv: (r) => r.rate.taxable_value },
    { head: t("gstreports.col_igst"), num: true, cell: (r) => money(r.rate.igst), csv: (r) => r.rate.igst },
    { head: t("gstreports.col_cgst"), num: true, cell: (r) => money(r.rate.cgst), csv: (r) => r.rate.cgst },
    { head: t("gstreports.col_sgst"), num: true, cell: (r) => money(r.rate.sgst), csv: (r) => r.rate.sgst },
  ];

  const taxCols = <R extends Tax>(): GridCol<R>[] => [
    { head: t("gstreports.col_taxable"), num: true, cell: (r) => money(r.taxable_value), csv: (r) => r.taxable_value },
    { head: t("gstreports.col_igst"), num: true, cell: (r) => money(r.igst), csv: (r) => r.igst },
    { head: t("gstreports.col_cgst"), num: true, cell: (r) => money(r.cgst), csv: (r) => r.cgst },
    { head: t("gstreports.col_sgst"), num: true, cell: (r) => money(r.sgst), csv: (r) => r.sgst },
  ];

  const sections = useMemo(() => {
    if (!data) return null;
    const b2bRows = flatten(data.b2b, (g) => [g.party_gstin, g.party_name], (g) => g.invoices);
    const b2clRows = flatten(data.b2cl, (g) => [g.place_of_supply], (g) => g.invoices);
    const expRows = flatten(data.exports, (g) => [g.export_type], (g) => g.invoices);
    return { b2bRows, b2clRows, expRows };
  }, [data]);

  const invCount = (rows: FlatRow[]) => rows.filter((r) => r.firstOfInvoice).length;
  const nilRows = data ? data.nil_exempt_non_gst.filter((r) => !isZeroDec(r.nil_rated) || !isZeroDec(r.exempted) || !isZeroDec(r.non_gst)) : [];
  const counts: Record<TabKey, number> =
    data && sections
      ? {
          b2b: invCount(sections.b2bRows),
          b2cl: invCount(sections.b2clRows),
          b2cs: data.b2cs.length,
          exports: invCount(sections.expRows),
          nil: nilRows.length,
          hsn: data.hsn.b2b.length + data.hsn.b2c.length,
          docs: data.docs_issued.length,
        }
      : { b2b: 0, b2cl: 0, b2cs: 0, exports: 0, nil: 0, hsn: 0, docs: 0 };

  const file = (section: string) =>
    `gstr1-${section}-${data?.company.gstin ?? ""}-${periodTag(data?.period)}`;

  const warnings: string[] = [];
  if (data?.warnings.legacy_invoices_without_gst_data)
    warnings.push(t("gstreports.warn_legacy_invoices", { count: data.warnings.legacy_invoices_without_gst_data }));
  if (data?.warnings.lines_without_hsn)
    warnings.push(t("gstreports.warn_no_hsn", { count: data.warnings.lines_without_hsn }));

  const panel = () => {
    if (!data || !sections) return null;
    const title = t(`gstreports.tab_${tab}`);
    switch (tab) {
      case "b2b": {
        const cols = invoiceCols([
          { head: t("gstreports.col_party_gstin"), text: (g) => g },
          { head: t("gstreports.col_party_name"), text: (g) => g },
        ]);
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("b2b"), gridCsv(cols, sections.b2bRows))} />
            <Grid cols={cols} rows={sections.b2bRows} rowKey={(r, i) => `${r.inv.name}-${i}`} />
          </>
        );
      }
      case "b2cl": {
        const cols = invoiceCols([{ head: t("gstreports.col_pos"), text: pos }]);
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("b2cl"), gridCsv(cols, sections.b2clRows))} />
            <Grid cols={cols} rows={sections.b2clRows} rowKey={(r, i) => `${r.inv.name}-${i}`} />
          </>
        );
      }
      case "exports": {
        const cols = invoiceCols([{ head: t("gstreports.col_export_type"), text: exportType }]);
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("exports"), gridCsv(cols, sections.expRows))} />
            <Grid cols={cols} rows={sections.expRows} rowKey={(r, i) => `${r.inv.name}-${i}`} />
          </>
        );
      }
      case "b2cs": {
        const cols: GridCol<Gstr1["b2cs"][number]>[] = [
          { head: t("gstreports.col_pos"), cell: (r) => pos(r.place_of_supply) },
          { head: t("gstreports.col_supply_type"), cell: (r) => supplyType(r.supply_type), csv: (r) => r.supply_type },
          { head: t("gstreports.col_rate"), num: true, cell: (r) => fmtRate(r.rate), csv: (r) => r.rate },
          ...taxCols<Gstr1["b2cs"][number]>(),
        ];
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("b2cs"), gridCsv(cols, data.b2cs))} />
            <Grid cols={cols} rows={data.b2cs} rowKey={(r) => `${r.place_of_supply}-${r.supply_type}-${r.rate}`} />
          </>
        );
      }
      case "nil": {
        const cols: GridCol<Gstr1["nil_exempt_non_gst"][number]>[] = [
          {
            head: t("gstreports.col_description"),
            cell: (r) =>
              t(`gstreports.nil_${/^Inter/.test(r.description) ? "inter" : "intra"}_${/unregistered/.test(r.description) ? "unregistered" : "registered"}`, {
                defaultValue: r.description,
              }),
            csv: (r) => r.description,
          },
          { head: t("gstreports.col_nil_rated"), num: true, cell: (r) => money(r.nil_rated), csv: (r) => r.nil_rated },
          { head: t("gstreports.col_exempted"), num: true, cell: (r) => money(r.exempted), csv: (r) => r.exempted },
          { head: t("gstreports.col_non_gst"), num: true, cell: (r) => money(r.non_gst), csv: (r) => r.non_gst },
        ];
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("nil-exempt-non-gst"), gridCsv(cols, data.nil_exempt_non_gst))} />
            <Grid cols={cols} rows={data.nil_exempt_non_gst} rowKey={(r) => r.description} />
          </>
        );
      }
      case "hsn": {
        const cols: GridCol<HsnRow>[] = [
          { head: t("gstreports.col_hsn"), cell: (r) => r.hsn },
          { head: t("gstreports.col_uqc"), cell: (r) => r.uqc },
          { head: t("gstreports.col_qty"), num: true, cell: (r) => fmtQty(r.total_qty), csv: (r) => r.total_qty },
          { head: t("gstreports.col_rate"), num: true, cell: (r) => fmtRate(r.rate), csv: (r) => r.rate },
          { head: t("gstreports.col_total_value"), num: true, cell: (r) => money(r.total_value), csv: (r) => r.total_value },
          ...taxCols<HsnRow>(),
        ];
        const csvAll = () => {
          const kind: GridCol<HsnRow & { kind: string }> = { head: t("gstreports.col_kind"), cell: (r) => r.kind };
          const all = [
            ...data.hsn.b2b.map((r) => ({ ...r, kind: "B2B" })),
            ...data.hsn.b2c.map((r) => ({ ...r, kind: "B2C" })),
          ];
          downloadCsv(file("hsn"), gridCsv([kind, ...cols], all));
        };
        return (
          <>
            <SectionHead title={title} onDownload={csvAll} />
            <p className="text-xs font-medium text-slate-400">{t("gstreports.hsn_b2b")}</p>
            <Grid cols={cols} rows={data.hsn.b2b} rowKey={(r) => `${r.hsn}-${r.uqc}-${r.rate}`} />
            <p className="text-xs font-medium text-slate-400">{t("gstreports.hsn_b2c")}</p>
            <Grid cols={cols} rows={data.hsn.b2c} rowKey={(r) => `${r.hsn}-${r.uqc}-${r.rate}`} />
          </>
        );
      }
      case "docs": {
        const cols: GridCol<Gstr1["docs_issued"][number]>[] = [
          { head: t("gstreports.col_series"), cell: (r) => r.series },
          { head: t("gstreports.col_from"), cell: (r) => r.from },
          { head: t("gstreports.col_to"), cell: (r) => r.to },
          { head: t("gstreports.col_total"), num: true, cell: (r) => String(r.total) },
          { head: t("gstreports.col_cancelled"), num: true, cell: (r) => String(r.cancelled) },
        ];
        return (
          <>
            <SectionHead title={title} onDownload={() => downloadCsv(file("docs-issued"), gridCsv(cols, data.docs_issued))} />
            <Grid cols={cols} rows={data.docs_issued} rowKey={(r) => r.series} />
          </>
        );
      }
    }
  };

  return (
    <GstReportFrame
      titleKey="gstreports.title_gstr1"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      waiting={!f.ready || !data}
      warnings={warnings}
      meta={data && `${t("gstreports.gstin")}: ${data.company.gstin}`}
      filters={<GstFilterBar f={f} />}
    >
      {data && (
        <>
          <section aria-label={t("gstreports.summary")} className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatTile label={t("gstreports.sum_invoices")} value={String(data.summary.invoice_count)} money={false} />
            <StatTile label={t("gstreports.sum_taxable")} value={data.summary.taxable_value} />
            <StatTile label={t("gstreports.col_igst")} value={data.summary.igst} />
            <StatTile label={t("gstreports.col_cgst")} value={data.summary.cgst} />
            <StatTile label={t("gstreports.col_sgst")} value={data.summary.sgst} />
            <StatTile label={t("gstreports.sum_invoice_value")} value={data.summary.invoice_value} />
          </section>

          <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-slate-800">
            {TABS.map((k) => (
              <button
                key={k}
                role="tab"
                id={`gstr1-tab-${k}`}
                aria-selected={tab === k}
                aria-controls="gstr1-panel"
                type="button"
                onClick={() => setTab(k)}
                className={`shrink-0 inline-flex items-center gap-2 px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
                  tab === k ? "border-indigo-500 text-indigo-300 font-medium" : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                {t(`gstreports.tab_${k}`)}
                <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs tabular-nums text-slate-300">{counts[k]}</span>
              </button>
            ))}
          </div>
          <div role="tabpanel" id="gstr1-panel" aria-labelledby={`gstr1-tab-${tab}`} className="space-y-3">
            {panel()}
          </div>
        </>
      )}
    </GstReportFrame>
  );
};
