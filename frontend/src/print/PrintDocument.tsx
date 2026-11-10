import React from "react";
import { useTranslation } from "react-i18next";
import { formatDate } from "@/lib/format";
import { absDec, fmtMoney, fmtQty, fmtRate, isZeroDec } from "@/pages/reports/gst/lib";
import type { CompanyBlock, DocPrint, PartyBlock, PaymentPrint, PrintData, StateRef } from "./types";
import "./print.css";

type TFn = (key: string, opts?: Record<string, unknown>) => string;

const stateText = (s: StateRef | null | undefined, t: TFn) => (s ? `${s.name} (${t("prt.state_code")}: ${s.code})` : "");

/* ------------------------------------------------------------------ */
/* shared blocks                                                       */
/* ------------------------------------------------------------------ */

const Letterhead: React.FC<{ c: CompanyBlock }> = ({ c }) => {
  const { t } = useTranslation();
  const cityLine = [c.city, c.pincode].filter(Boolean).join(" - ");
  return (
    <div className="pdoc-company">
      {c.logo_url && <img src={c.logo_url} alt="" className="pdoc-logo" />}
      <div>
        <h2>{c.legal_name || c.name}</h2>
        {c.address_line1 && <p>{c.address_line1}</p>}
        {c.address_line2 && <p>{c.address_line2}</p>}
        {cityLine && <p>{cityLine}</p>}
        {c.state && <p>{stateText(c.state, t)}</p>}
        {c.gstin && (
          <p>
            <b>{t("prt.gstin")}:</b> {c.gstin}
          </p>
        )}
        {c.pan && (
          <p>
            {t("prt.pan")}: {c.pan}
          </p>
        )}
        {c.cin && (
          <p>
            {t("prt.cin")}: {c.cin}
          </p>
        )}
        {c.fssai_license_no && (
          <p>
            {t("prt.fssai")}: {c.fssai_license_no}
          </p>
        )}
        {(c.phone || c.email) && (
          <p>
            {c.phone && `${t("prt.phone")}: ${c.phone}`}
            {c.phone && c.email && " | "}
            {c.email && `${t("prt.email")}: ${c.email}`}
          </p>
        )}
      </div>
    </div>
  );
};

const Watermark: React.FC<{ d: PrintData }> = ({ d }) => {
  const { t } = useTranslation();
  if (!d.is_draft && !d.is_cancelled) return null;
  return <div className="pdoc-watermark">{t(d.is_cancelled ? "prt.cancelled" : "prt.draft")}</div>;
};

const PartyCard: React.FC<{ heading: string; p: PartyBlock | null | undefined }> = ({ heading, p }) => {
  const { t } = useTranslation();
  if (!p) return null;
  const a = p.address;
  const cityLine = a ? [a.city, a.pincode].filter(Boolean).join(" - ") : "";
  return (
    <div className="pdoc-box">
      <h3>{heading}</h3>
      <p>
        <b>{p.name}</b>
      </p>
      {a?.line1 && <p>{a.line1}</p>}
      {a?.line2 && <p>{a.line2}</p>}
      {cityLine && <p>{cityLine}</p>}
      {p.state && <p>{stateText(p.state, t)}</p>}
      {p.gstin && (
        <p>
          <b>{t("prt.gstin")}:</b> {p.gstin}
        </p>
      )}
      {p.phone && (
        <p>
          {t("prt.phone")}: {p.phone}
        </p>
      )}
    </div>
  );
};

const Field: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) =>
  value ? (
    <p>
      <span>{label}:</span> <b>{value}</b>
    </p>
  ) : null;

const CopyLabel: React.FC<{ copy: string }> = ({ copy }) => {
  const { t } = useTranslation();
  return <div className="pdoc-copy">{t(`prt.copy.${copy}`)}</div>;
};

const Signatory: React.FC<{ c: CompanyBlock; receiver?: boolean }> = ({ c, receiver }) => {
  const { t } = useTranslation();
  return (
    <div className="pdoc-sign">
      {receiver ? (
        <div>
          <p className="pdoc-sign-space" />
          <p>{t("prt.receiver")}</p>
        </div>
      ) : (
        <div />
      )}
      <div>
        <p>{t("prt.for_company", { company: c.legal_name || c.name })}</p>
        <p className="pdoc-sign-space" />
        <p>{c.authorised_signatory || t("prt.signatory")}</p>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* invoice-like documents (Tax Invoice / Credit Note / PO / PI)        */
/* ------------------------------------------------------------------ */

const InvoiceDocument: React.FC<{ d: DocPrint; copy: string }> = ({ d, copy }) => {
  const { t } = useTranslation();
  const isReturn = !!d.is_return;
  // A credit / debit note carries negative figures; the title already says "Credit Note".
  const m = (v: string | null | undefined) => (v == null ? "" : fmtMoney(isReturn ? absDec(v) : v));
  const q = (v: string) => fmtQty(isReturn ? absDec(v) : v);
  const isSales = d.kind === "sales_invoice";
  const isPO = d.kind === "purchase_order";
  const totals = d.totals!;
  const hs = d.hsn_summary;
  const hasBatch = d.lines.some((l) => l.batch_no || l.expiry_date);
  const hasDisc = d.lines.some((l) => l.discount_amount && !isZeroDec(l.discount_amount));
  const taxLabel = (c: string) => (["CGST", "SGST", "IGST", "CESS"].includes(c) ? t(`prt.tax.${c.toLowerCase()}`) : "");
  const bank = d.company.bank;
  const bal = d.balances;
  const showBal = !!bal && bal.previous_balance !== null;

  return (
    <>
      <Watermark d={d} />
      <div className="pdoc-head">
        <Letterhead c={d.company} />
        <div className="pdoc-title">
          <h1>{t(`prt.title.${d.title_key}`)}</h1>
          <CopyLabel copy={copy} />
          <Field label={t(isSales || d.kind === "purchase_invoice" ? "prt.invoice_no" : "prt.doc_no")} value={d.name} />
          <Field label={t("prt.date")} value={formatDate(d.date)} />
          <Field label={t("prt.due_date")} value={formatDate(d.due_date)} />
          <Field label={t("prt.required_by")} value={formatDate(d.required_by)} />
          {d.return_against && (
            <p>{t("prt.against_invoice", { name: d.return_against.name, date: formatDate(d.return_against.date) })}</p>
          )}
          {d.supplier_invoice?.no && (
            <Field
              label={t("prt.supplier_invoice")}
              value={`${d.supplier_invoice.no}${d.supplier_invoice.date ? ` / ${formatDate(d.supplier_invoice.date)}` : ""}`}
            />
          )}
          <Field label={t("prt.place_of_supply")} value={stateText(d.place_of_supply, t)} />
          {d.gst?.is_reverse_charge && <Field label={t("prt.reverse_charge")} value={t("prt.yes")} />}
        </div>
      </div>

      <div className="pdoc-parties">
        <PartyCard heading={t(isSales ? "prt.bill_to" : "prt.supplier")} p={d.bill_to} />
        {isSales && <PartyCard heading={t("prt.ship_to")} p={d.ship_to} />}
      </div>

      <table className="pdoc-table pdoc-lines">
        <thead>
          <tr>
            <th className="c">{t("prt.sno")}</th>
            <th>{t("prt.item")}</th>
            <th>{t("prt.hsn")}</th>
            {hasBatch && <th>{t("prt.batch")}</th>}
            <th className="num">{t("prt.qty")}</th>
            <th>{t("prt.unit")}</th>
            <th className="num">{t("prt.rate")}</th>
            {hasDisc && <th className="num">{t("prt.discount")}</th>}
            <th className="num">{t("prt.gst_pct")}</th>
            <th className="num">{t("prt.taxable")}</th>
          </tr>
        </thead>
        <tbody>
          {d.lines.map((l) => (
            <tr key={l.idx}>
              <td className="c">{l.idx}</td>
              <td>
                {l.item_name}
                {l.description && <small> {l.description}</small>}
              </td>
              <td>{l.hsn}</td>
              {hasBatch && (
                <td>
                  {l.batch_no}
                  {l.expiry_date && (
                    <small>
                      {" "}
                      {t("prt.exp")} {formatDate(l.expiry_date)}
                    </small>
                  )}
                </td>
              )}
              <td className="num">{q(l.qty)}</td>
              <td>{l.uom}</td>
              <td className="num">{m(l.rate)}</td>
              {hasDisc && <td className="num">{l.discount_amount ? m(l.discount_amount) : ""}</td>}
              <td className="num">{fmtRate(l.gst_rate)}</td>
              <td className="num">{m(l.amount)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="strong">
            <td colSpan={3 + (hasBatch ? 1 : 0)}>{t("prt.total_qty")}</td>
            <td className="num">{q(totals.total_qty)}</td>
            <td colSpan={2 + (hasDisc ? 1 : 0)} />
            <td />
            <td className="num">{m(totals.net_total)}</td>
          </tr>
        </tfoot>
      </table>

      <div className="pdoc-summary">
        <div className="pdoc-words">
          <h3>{t("prt.in_words")}</h3>
          <p>
            <b>{totals.in_words}</b>
          </p>
          {isReturn && <p>({t("prt.credit_amount_note")})</p>}
        </div>
        <table className="pdoc-table pdoc-totals">
          <tbody>
            <tr>
              <td>{t("prt.total_taxable")}</td>
              <td className="num">{m(totals.net_total)}</td>
            </tr>
            {(d.taxes ?? []).map((tx, i) => (
              <tr key={i}>
                <td>
                  {taxLabel(tx.component) || tx.description}
                  {tx.rate && !isZeroDec(tx.rate) ? ` @ ${fmtRate(tx.rate)}%` : ""}
                </td>
                <td className="num">{m(tx.tax_amount)}</td>
              </tr>
            ))}
            {!isZeroDec(totals.rounding_adjustment) && (
              <tr>
                <td>{t("prt.round_off")}</td>
                <td className="num">{m(totals.rounding_adjustment)}</td>
              </tr>
            )}
            <tr className="strong grand">
              <td>{t("prt.grand_total")}</td>
              <td className="num">₹ {m(totals.rounded_total)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {hs && hs.rows.length > 0 && hs.mode !== "none" && (
        <>
          <h3 className="pdoc-h3">{t("prt.hsn_summary")}</h3>
          <table className="pdoc-table pdoc-hsn">
            <thead>
              <tr>
                <th>{t("prt.hsn")}</th>
                <th className="num">{t("prt.taxable")}</th>
                {hs.mode === "cgst_sgst" ? (
                  <>
                    <th className="num">{t("prt.central_tax")}</th>
                    <th className="num">{t("prt.state_tax")}</th>
                  </>
                ) : (
                  <th className="num">{t("prt.integrated_tax")}</th>
                )}
                <th className="num">{t("prt.total_tax")}</th>
              </tr>
            </thead>
            <tbody>
              {hs.rows.map((r) => {
                const half = fmtRate(String(Number(r.gst_rate) / 2));
                return (
                  <tr key={`${r.hsn}-${r.gst_rate}`}>
                    <td>{r.hsn}</td>
                    <td className="num">{m(r.taxable_value)}</td>
                    {hs.mode === "cgst_sgst" ? (
                      <>
                        <td className="num">
                          {half}% | {m(r.cgst)}
                        </td>
                        <td className="num">
                          {half}% | {m(r.sgst)}
                        </td>
                      </>
                    ) : (
                      <td className="num">
                        {fmtRate(r.gst_rate)}% | {m(r.igst)}
                      </td>
                    )}
                    <td className="num">{m(r.total_tax)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="strong">
                <td>{t("prt.total")}</td>
                <td className="num">{m(hs.totals.taxable_value)}</td>
                {hs.mode === "cgst_sgst" ? (
                  <>
                    <td className="num">{m(hs.totals.cgst)}</td>
                    <td className="num">{m(hs.totals.sgst)}</td>
                  </>
                ) : (
                  <td className="num">{m(hs.totals.igst)}</td>
                )}
                <td className="num">{m(hs.totals.total_tax)}</td>
              </tr>
            </tfoot>
          </table>
        </>
      )}

      {showBal && bal && (
        <table className="pdoc-table pdoc-balances">
          <tbody>
            <tr>
              <td>{t("prt.previous_balance")}</td>
              <td className="num">{fmtMoney(bal.previous_balance)}</td>
            </tr>
            <tr>
              <td>{t("prt.invoice_outstanding")}</td>
              <td className="num">{fmtMoney(bal.outstanding_amount)}</td>
            </tr>
            <tr className="strong">
              <td>{t("prt.current_balance")}</td>
              <td className="num">{fmtMoney(bal.current_balance)}</td>
            </tr>
          </tbody>
        </table>
      )}

      <div className="pdoc-footer">
        {isSales && bank && (
          <div className="pdoc-box">
            <h3>{t("prt.bank_details")}</h3>
            <Field label={t("prt.bank")} value={bank.bank_name} />
            <Field label={t("ease.bank.holder")} value={bank.account_holder} />
            <Field label={t("prt.account_no")} value={bank.account_no} />
            <Field label={t("prt.ifsc")} value={bank.ifsc} />
            <Field label={t("prt.branch")} value={bank.branch} />
            <Field label={t("prt.upi")} value={bank.upi_id} />
          </div>
        )}
        {d.company.terms && (
          <div className="pdoc-box">
            <h3>{t("prt.terms")}</h3>
            <p className="pre">{d.company.terms}</p>
          </div>
        )}
      </div>
      {!isPO && <p className="pdoc-declaration">{t("prt.declaration")}</p>}
      <Signatory c={d.company} />
    </>
  );
};

/* ------------------------------------------------------------------ */
/* delivery challan                                                    */
/* ------------------------------------------------------------------ */

const ChallanDocument: React.FC<{ d: DocPrint; copy: string }> = ({ d, copy }) => {
  const { t } = useTranslation();
  const priced = !!d.with_prices;
  const m = (v: string | null | undefined) => (v == null ? "" : fmtMoney(v));
  const hasBatch = d.lines.some((l) => l.batch_no || l.expiry_date);
  const totals = d.totals;
  return (
    <>
      <Watermark d={d} />
      <div className="pdoc-head">
        <Letterhead c={d.company} />
        <div className="pdoc-title">
          <h1>{t("prt.title.delivery_challan")}</h1>
          <CopyLabel copy={copy} />
          <Field label={t("prt.challan_no")} value={d.name} />
          <Field label={t("prt.date")} value={formatDate(d.date)} />
          {d.source && (
            <p>
              {t("prt.against_doc", {
                type: t(d.source.type === "Sales Order" ? "prt.sales_order" : "prt.sales_invoice"),
                name: d.source.name,
              })}
            </p>
          )}
          <Field label={t("prt.delivery_date")} value={formatDate(d.delivery_date)} />
          <Field label={t("prt.place_of_supply")} value={stateText(d.place_of_supply, t)} />
        </div>
      </div>
      <div className="pdoc-parties">
        <PartyCard heading={t("prt.bill_to")} p={d.bill_to} />
        <PartyCard heading={t("prt.deliver_to")} p={d.ship_to} />
      </div>
      <table className="pdoc-table pdoc-lines">
        <thead>
          <tr>
            <th className="c">{t("prt.sno")}</th>
            <th>{t("prt.item")}</th>
            <th>{t("prt.hsn")}</th>
            {hasBatch && <th>{t("prt.batch")}</th>}
            <th className="num">{t("prt.qty")}</th>
            <th>{t("prt.unit")}</th>
            {priced && <th className="num">{t("prt.rate")}</th>}
            {priced && <th className="num">{t("prt.amount")}</th>}
          </tr>
        </thead>
        <tbody>
          {d.lines.map((l) => (
            <tr key={l.idx}>
              <td className="c">{l.idx}</td>
              <td>
                {l.item_name}
                {l.description && <small> {l.description}</small>}
              </td>
              <td>{l.hsn}</td>
              {hasBatch && (
                <td>
                  {l.batch_no}
                  {l.expiry_date && (
                    <small>
                      {" "}
                      {t("prt.exp")} {formatDate(l.expiry_date)}
                    </small>
                  )}
                </td>
              )}
              <td className="num">{fmtQty(l.qty)}</td>
              <td>{l.uom}</td>
              {priced && <td className="num">{m(l.rate)}</td>}
              {priced && <td className="num">{m(l.amount)}</td>}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="strong">
            <td colSpan={3 + (hasBatch ? 1 : 0)}>{t("prt.total_qty")}</td>
            <td className="num">{fmtQty(d.total_qty)}</td>
            <td colSpan={1 + (priced ? 1 : 0)} />
            {priced && <td className="num">{m(totals?.net_total)}</td>}
          </tr>
        </tfoot>
      </table>
      {priced && totals && (
        <div className="pdoc-summary">
          <div className="pdoc-words">
            <h3>{t("prt.in_words")}</h3>
            <p>
              <b>{totals.in_words}</b>
            </p>
          </div>
          <table className="pdoc-table pdoc-totals">
            <tbody>
              <tr>
                <td>{t("prt.total_taxable")}</td>
                <td className="num">{m(totals.net_total)}</td>
              </tr>
              <tr>
                <td>{t("prt.total_tax")}</td>
                <td className="num">{m(totals.total_taxes_and_charges)}</td>
              </tr>
              <tr className="strong grand">
                <td>{t("prt.grand_total")}</td>
                <td className="num">₹ {m(totals.rounded_total)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      <div className="pdoc-transport">
        <p>{t("prt.vehicle_no")}: ______________________</p>
        <p>{t("prt.driver")}: ______________________</p>
      </div>
      <p className="pdoc-declaration">{t("prt.received_note")}</p>
      <Signatory c={d.company} receiver />
    </>
  );
};

/* ------------------------------------------------------------------ */
/* payment receipt / voucher                                           */
/* ------------------------------------------------------------------ */

const PaymentDocument: React.FC<{ d: PaymentPrint; copy: string }> = ({ d, copy }) => {
  const { t } = useTranslation();
  const receive = d.payment_type === "Receive";
  const p = d.payment;
  const docTypeKey = (dt: string) => (dt === "buying.PurchaseInvoice" ? "prt.purchase_invoice" : "prt.sales_invoice");
  return (
    <>
      <Watermark d={d} />
      <div className="pdoc-head">
        <Letterhead c={d.company} />
        <div className="pdoc-title">
          <h1>{t(`prt.title.${d.title_key}`)}</h1>
          <CopyLabel copy={copy} />
          <Field label={t(receive ? "prt.receipt_no" : "prt.voucher_no")} value={d.name} />
          <Field label={t("prt.date")} value={formatDate(d.date)} />
        </div>
      </div>
      {d.party && <PartyCard heading={t(receive ? "prt.received_from" : "prt.paid_to")} p={d.party} />}
      <div className="pdoc-box pdoc-pay">
        <Field label={t("prt.mode")} value={t(`prt.mode_${p.mode}`)} />
        <Field label={t("prt.account")} value={p.account} />
        <Field label={t("prt.reference_no")} value={p.reference_no} />
        <Field label={t("prt.reference_date")} value={formatDate(p.reference_date)} />
        <p className="pdoc-amount">
          {t(receive ? "prt.receipt_sum" : "prt.voucher_sum")} <b>₹ {fmtMoney(d.totals.amount)}</b>
        </p>
        <p>
          {t("prt.in_words")}: <b>{d.totals.in_words}</b>
        </p>
      </div>
      {d.allocations.length > 0 && (
        <>
          <h3 className="pdoc-h3">{t("prt.allocations")}</h3>
          <table className="pdoc-table">
            <thead>
              <tr>
                <th>{t("prt.invoice")}</th>
                <th>{t("prt.invoice_date")}</th>
                <th className="num">{t("prt.invoice_total")}</th>
                <th className="num">{t("prt.allocated")}</th>
                <th className="num">{t("prt.balance_due")}</th>
              </tr>
            </thead>
            <tbody>
              {d.allocations.map((a, i) => (
                <tr key={i}>
                  <td>
                    {t(docTypeKey(a.reference_doctype))} {a.reference_name}
                  </td>
                  <td>{formatDate(a.invoice_date)}</td>
                  <td className="num">{fmtMoney(a.invoice_total)}</td>
                  <td className="num">{fmtMoney(a.allocated_amount)}</td>
                  <td className="num">{fmtMoney(a.outstanding_after)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="strong">
                <td colSpan={3}>{t("prt.total")}</td>
                <td className="num">{fmtMoney(d.totals.allocated)}</td>
                <td />
              </tr>
              {!isZeroDec(d.totals.unallocated) && (
                <tr>
                  <td colSpan={3}>{t("prt.advance")}</td>
                  <td className="num">{fmtMoney(d.totals.unallocated)}</td>
                  <td />
                </tr>
              )}
            </tfoot>
          </table>
        </>
      )}
      <Signatory c={d.company} receiver={!receive} />
    </>
  );
};

/* ------------------------------------------------------------------ */

export const PrintDocument: React.FC<{ data: PrintData; copy: string }> = ({ data, copy }) => (
  <div className="pdoc">
    {data.kind === "payment_entry" ? (
      <PaymentDocument d={data as PaymentPrint} copy={copy} />
    ) : data.kind === "delivery_challan" ? (
      <ChallanDocument d={data as DocPrint} copy={copy} />
    ) : (
      <InvoiceDocument d={data as DocPrint} copy={copy} />
    )}
  </div>
);
