# Gaps against standard ERPNext

What standard ERPNext (v15 + India Compliance) has that this ERP doesn't, as of
2026-10-28. Ranked for **Nidhi Grah Udyog**: a spice manufacturer moving off
Tally, selling to distributors. The scope rule from `plan.md` applies: what this
business needs, not full parity.

**What exists today:** chart of accounts, Journal / Payment Entry with invoice
allocation, Trial Balance, P&L, Balance Sheet, General Ledger, ageing; GST
setup with tax and item tax templates, GSTR-1, GSTR-3B and tax ledger; items,
warehouses, price lists, batches with expiry, Stock Entry (receipt / issue /
transfer / manufacture / repack), stock balance; Sales / Purchase Order and
Invoice, credit limits; BOM and Work Order with yield and BOM cost; addresses;
RBAC, audit log, 13 languages; Tally history (`legacy`).

## P1 — needed to stop using Tally for daily work

| # | ERPNext feature | Why it matters here | Size |
|---|---|---|---|
| 1 | **Print formats** (GST Tax Invoice, Delivery Challan, PO, receipt) as PDF | Nothing in the ERP can be printed or sent to a customer. Tally prints every invoice | M |
| 2 | **Sales / Purchase Returns** (`is_return` Credit / Debit Note against an invoice, stock back in) | Damaged or expired returns from distributors. Tally had 22 notes | M |
| 3 | **e-Way Bill** (and **e-Invoice / IRN** if aggregate turnover > ₹5 crore) | e-way bill is mandatory for goods moved above ₹50,000. Check the turnover for e-invoice | L |
| 4 | **Stock Reconciliation** (physical count, opening stock) | Go-live opening stock, and the 7 negative-stock items | S |
| 5 | **UOM conversion per item** (stock in Kg, sell in Bag / Box / Carton) | Packs go out in cartons, raw spice comes in in bags | S |
| 6 | **Registers**: Sales Register, Purchase Register, Day Book, Cash / Bank Book, party statement | The accountant's daily views in Tally | M |
| 7 | **Bank Account + Bank Reconciliation** (statement import, clearance dates) | Monthly bank reconciliation | M |
| 8 | **Period Closing Voucher** (year-end P&L to retained earnings) | Only the fiscal-year lock exists. The Tally years were closed with importer JEs | S |
| 9 | **Separate Input / Output GST accounts + go-live GST setup** for the company | Tally's combined ledgers can't drive the GST reports (see `tally_data_gaps.md` G3) | S |

## P2 — soon after go-live

| # | ERPNext feature | Why | Size |
|---|---|---|---|
| 10 | **Delivery Note** and **Purchase Receipt** (dispatch / GRN separate from the bill) | Goods often arrive before the bill. ERPNext's standard flow | M |
| 11 | **Landed Cost Voucher** (freight and loading into raw-material cost) | True cost of turmeric / chilli lots, and margins | S |
| 12 | **Gross Profit** and **item-wise sales** reports on ERP invoices | Margin per product / distributor (`TallyItemLine` covers history) | S |
| 13 | **TDS** (Tax Withholding Category: 194Q purchases, 194C freight, 194J) | Applies once turnover / supplier thresholds are crossed. Accountant to confirm | M |
| 14 | **Payment Reconciliation tool** (match unallocated receipts to invoices) | Receipts often come in before they're allocated | S |
| 15 | **Contact** (people, phones, WhatsApp) | Only the address is stored today | S |
| 16 | **Data import / export tool** (CSV / Excel for masters and opening entries) | Bulk price updates and new parties | M |
| 17 | **Quality Inspection** (moisture, colour, foreign matter per lot) | Food business and FSSAI traceability, with the batches that already exist | M |

## P3 — later / optional

- Item Variants (pack sizes as attributes), Pricing Rules / promotional schemes,
  Sales Person and commission, Territory.
- Fixed Assets and depreciation (machinery). Tally booked depreciation by journal.
- Cost Centers / accounting dimensions (ERPNext makes them mandatory; Tally never used them).
- Quotation, Material Request, RFQ / Supplier Quotation, Production Plan, Job Card.
- Budgets, dunning / interest, recurring journals, workflows and approvals.
- Email / WhatsApp sending of invoices (the `Outbox` exists, but no sender is wired).
- HR and payroll: already planned in `checklist.md` (open).

## Deliberately out of scope

Multi-currency, serial numbers, CRM / leads, projects, support, website / portal
(the B2B portal is on hold), POS (B2C goes through the NGU app).
