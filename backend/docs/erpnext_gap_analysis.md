# Gaps against standard ERPNext

What standard ERPNext (v15 + India Compliance) has that this ERP doesn't.
Updated 2026-10-29. Ranked for **Nidhi Grah Udyog** under the current direction:
operators enter everything in the ERP, the ERP exports to Tally, and the
accountant finishes the official books and GST filing in Tally. The scope rule
applies: what this business needs, not full parity. `checklist.md` is the task
tracker; this file explains the ranking.

## Done since the first gap review (2026-10-28)

Sales / purchase returns · Stock Reconciliation · Sales / Purchase Register, Day
Book, Cash / Bank Book, party statement · attachments on documents and masters
· Tally export (vouchers + masters XML) · Tally history (`legacy`) and party
addresses. In progress: print formats (`printing`), bank reconciliation,
period closing, GST go-live accounts (`gst/services/go_live.py`).

## Skipped by decision (2026-10-28)

- **e-Way Bill / e-Invoice (IRN):** Tally handles statutory output.
- **UOM conversion per item:** operators do the carton maths.
- **Analytics:** live in the separate Tally dashboard (`Desktop\Tally\dashboard`).

## P1 — still open, needed before operators stop entering in Tally

| # | ERPNext feature | Why it matters here | Size |
|---|---|---|---|
| 1 | **Print formats** (GST Tax Invoice, Delivery Challan, PO, receipt) | Nothing can go to a customer without them. In progress | M |
| 2 | **Bank Reconciliation** (clearance dates, statement import) | Monthly bank check before the Tally export. In progress | M |
| 3 | **Period Closing Voucher** | Year-end P&L to retained earnings. In progress | S |
| 4 | **Input / Output GST accounts + company GSTIN** | Tally's combined GST ledgers can't drive the GST reports (`tally_data_gaps.md` G3). In progress | S |
| 5 | **Freight / loading charges into stock cost** (ERPNext Landed Cost Voucher) | True cost of raw spice lots. Planned on the purchase bill itself (`checklist.md` 6.9) | S |

## P2 — soon after go-live

| # | ERPNext feature | Why | Size |
|---|---|---|---|
| 6 | **Payment Reconciliation tool** (match unallocated receipts to invoices) | Distributors pay on account. Old Tally balances are unallocated by nature | S |
| 7 | **Delivery Note / Purchase Receipt** (dispatch and GRN separate from the bill) | Goods often arrive before the bill. ERPNext's standard flow | M |
| 8 | **Contact** (people, phones, WhatsApp) | Only addresses are stored; field staff need phone numbers | S |
| 9 | **Gross Profit** and item-wise sales on ERP invoices | Margin per product / distributor (`TallyItemLine` covers the Tally years) | S |
| 10 | **TDS** (Tax Withholding: 194Q, 194C, 194J) | Only if the accountant wants it in the ERP rather than in Tally | M |
| 11 | **Quality Inspection** per lot (moisture, colour, foreign matter) | FSSAI traceability on top of the existing batches | M |
| 12 | **Data import tool** (CSV / Excel for masters and prices) | Bulk price-list updates and new parties | M |

## P3 — later / optional

Item Variants (pack sizes), Pricing Rules (no schemes are recorded today), Sales
Person / Territory (field staff are planned), fixed assets and depreciation,
cost centers / accounting dimensions (Tally never used them), Quotation /
Material Request / RFQ / Production Plan / Job Card, budgets, dunning /
interest, recurring journals, workflows, and email / WhatsApp sending of
invoices (the `Outbox` exists, but no sender is wired).

## Out of scope

Multi-currency, serial numbers, CRM / leads, projects, support, website / POS
(B2C runs through the NGU app), B2B portal (on hold).
