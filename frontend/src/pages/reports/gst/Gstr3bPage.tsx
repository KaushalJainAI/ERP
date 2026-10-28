import React from "react";
import { useTranslation } from "react-i18next";
import { downloadCsv, fmtMoney, periodTag, slug } from "./lib";
import { Grid, GridCol, GstFilterBar, GstReportFrame, SectionHead, gridCsv, useGstFilters, useGstReport, usePlaceOfSupply } from "./shared";

type Heads = Partial<Record<"taxable_value" | "igst" | "cgst" | "sgst" | "cess", string>>;
type ItcHeads = { igst: string; cgst: string; sgst: string; cess: string };

interface Gstr3b {
  company: { id: number; name: string; gstin: string; state: string };
  period: { from_date: string | null; to_date: string | null };
  "3.1": {
    a_outward_taxable: Heads;
    b_outward_zero_rated: Heads;
    c_other_nil_exempt: Heads;
    d_inward_rcm: Heads;
    e_non_gst_outward: Heads;
  };
  "3.2": { place_of_supply: string; category: string; taxable_value: string; igst: string }[];
  "4": {
    A: { import_goods: ItcHeads; import_services: ItcHeads; inward_rcm: ItcHeads; isd: ItcHeads; all_other_itc: ItcHeads };
    B: { reversed: ItcHeads };
    C_net_itc: ItcHeads;
    D_ineligible: ItcHeads;
  };
  "5": { inter_state: string; intra_state: string };
  "6.1": Record<"igst" | "cgst" | "sgst", { output: string; itc: string; net_payable: string }>;
  warnings: { legacy_sales_invoices_without_gst_data: number; legacy_purchase_invoices_without_gst_data: number };
}

/** A labelled row of a 3B table; `values === null` is a sub-heading with no figures. */
interface Line<K extends string> {
  key: string;
  label: string;
  values: Partial<Record<K, string>> | null;
  bold?: boolean;
}

const DASH = "–";

export const Gstr3bPage: React.FC = () => {
  const { t } = useTranslation();
  const f = useGstFilters();
  const pos = usePlaceOfSupply();
  const { data, isFetching, error } = useGstReport<Gstr3b>("/gst/reports/gstr3b/", f.params, f.ready);
  const file = (section: string) => `gstr3b-${section}-${data?.company.gstin ?? ""}-${periodTag(data?.period)}`;

  /** Figure columns for a table keyed by tax head; a head the return does not have shows a dash. */
  const figureCols = <K extends string>(heads: { key: K; head: string }[]): GridCol<Line<K>>[] =>
    heads.map(({ key, head }) => ({
      head,
      num: true,
      cell: (r) => {
        if (!r.values) return "";
        const v = r.values[key];
        return v === undefined ? (
          <span className="text-slate-600" title={t("gstreports.dash")} aria-label={t("gstreports.dash")}>
            {DASH}
          </span>
        ) : (
          fmtMoney(v)
        );
      },
      csv: (r) => r.values?.[key] ?? "",
    }));

  const natureCol = <K extends string>(): GridCol<Line<K>> => ({
    head: t("gstreports.col_nature"),
    cell: (r) => (
      <span className={r.values === null || r.bold ? "font-semibold text-slate-100" : ""}>{r.label}</span>
    ),
    csv: (r) => r.label,
  });

  const taxHeads = [
    { key: "igst" as const, head: t("gstreports.col_igst") },
    { key: "cgst" as const, head: t("gstreports.col_cgst") },
    { key: "sgst" as const, head: t("gstreports.col_sgst") },
    { key: "cess" as const, head: t("gstreports.col_cess") },
  ];

  const table = <K extends string>(cols: GridCol<Line<K>>[], rows: Line<K>[], title: string, csvName: string) => (
    <section className="space-y-3" aria-label={title}>
      <SectionHead title={title} onDownload={() => downloadCsv(file(csvName), gridCsv(cols, rows))} />
      <Grid cols={cols} rows={rows} rowKey={(r) => r.key} />
    </section>
  );

  const warnings: string[] = [];
  if (data?.warnings.legacy_sales_invoices_without_gst_data)
    warnings.push(t("gstreports.warn_legacy_sales", { count: data.warnings.legacy_sales_invoices_without_gst_data }));
  if (data?.warnings.legacy_purchase_invoices_without_gst_data)
    warnings.push(t("gstreports.warn_legacy_purchases", { count: data.warnings.legacy_purchase_invoices_without_gst_data }));

  const render = (d: Gstr3b) => {
    const t31 = d["3.1"];
    const rows31: Line<"taxable_value" | "igst" | "cgst" | "sgst" | "cess">[] = [
      { key: "a", label: t("gstreports.t31_a"), values: t31.a_outward_taxable },
      { key: "b", label: t("gstreports.t31_b"), values: t31.b_outward_zero_rated },
      { key: "c", label: t("gstreports.t31_c"), values: t31.c_other_nil_exempt },
      { key: "d", label: t("gstreports.t31_d"), values: t31.d_inward_rcm },
      { key: "e", label: t("gstreports.t31_e"), values: t31.e_non_gst_outward },
    ];
    const cols31 = [
      natureCol<"taxable_value" | "igst" | "cgst" | "sgst" | "cess">(),
      ...figureCols([{ key: "taxable_value" as const, head: t("gstreports.col_taxable") }, ...taxHeads]),
    ];

    const t4 = d["4"];
    const itc = (key: string, label: string, values: ItcHeads | null, bold = false): Line<"igst" | "cgst" | "sgst" | "cess"> => ({
      key,
      label,
      values,
      bold,
    });
    const rows4 = [
      itc("A", t("gstreports.t4_a"), null),
      itc("A1", t("gstreports.t4_a1"), t4.A.import_goods),
      itc("A2", t("gstreports.t4_a2"), t4.A.import_services),
      itc("A3", t("gstreports.t4_a3"), t4.A.inward_rcm),
      itc("A4", t("gstreports.t4_a4"), t4.A.isd),
      itc("A5", t("gstreports.t4_a5"), t4.A.all_other_itc),
      itc("B", t("gstreports.t4_b"), t4.B.reversed),
      itc("C", t("gstreports.t4_c"), t4.C_net_itc, true),
      itc("D", t("gstreports.t4_d"), t4.D_ineligible),
    ];

    const cols4 = [natureCol<"igst" | "cgst" | "sgst" | "cess">(), ...figureCols(taxHeads)];

    const cols32: GridCol<Gstr3b["3.2"][number]>[] = [
      { head: t("gstreports.col_pos"), cell: (r) => pos(r.place_of_supply) },
      {
        head: t("gstreports.col_category"),
        cell: (r) => t(`gstreports.cat_${slug(r.category)}`, { defaultValue: r.category }),
        csv: (r) => r.category,
      },
      { head: t("gstreports.col_taxable"), num: true, cell: (r) => fmtMoney(r.taxable_value), csv: (r) => r.taxable_value },
      { head: t("gstreports.col_igst"), num: true, cell: (r) => fmtMoney(r.igst), csv: (r) => r.igst },
    ];

    const rows5: Line<"value">[] = [
      { key: "inter", label: t("gstreports.t5_inter"), values: { value: d["5"].inter_state } },
      { key: "intra", label: t("gstreports.t5_intra"), values: { value: d["5"].intra_state } },
    ];
    const cols5 = [natureCol<"value">(), ...figureCols([{ key: "value" as const, head: t("gstreports.col_value") }])];

    const rows61: Line<"output" | "itc" | "net_payable">[] = (["igst", "cgst", "sgst"] as const).map((h) => ({
      key: h,
      label: t(`gstreports.col_${h}`),
      values: d["6.1"][h],
    }));
    const cols61 = [
      { ...natureCol<"output" | "itc" | "net_payable">(), head: t("gstreports.col_nature") },
      ...figureCols([
        { key: "output" as const, head: t("gstreports.col_output") },
        { key: "itc" as const, head: t("gstreports.col_itc") },
        { key: "net_payable" as const, head: t("gstreports.col_net_payable") },
      ]),
    ];

    return (
      <>
        {table(cols31, rows31, t("gstreports.t31"), "3-1")}
        <section className="space-y-3" aria-label={t("gstreports.t32")}>
          <SectionHead title={t("gstreports.t32")} onDownload={() => downloadCsv(file("3-2"), gridCsv(cols32, d["3.2"]))} />
          <Grid cols={cols32} rows={d["3.2"]} rowKey={(r) => `${r.place_of_supply}-${r.category}`} />
        </section>
        {table(cols4, rows4, t("gstreports.t4"), "4")}
        {table(cols5, rows5, t("gstreports.t5"), "5")}
        {table(cols61, rows61, t("gstreports.t61"), "6-1")}
      </>
    );
  };

  return (
    <GstReportFrame
      titleKey="gstreports.title_gstr3b"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      waiting={!f.ready || !data}
      warnings={warnings}
      meta={data && `${t("gstreports.gstin")}: ${data.company.gstin}`}
      filters={<GstFilterBar f={f} />}
    >
      {data && render(data)}
    </GstReportFrame>
  );
};
