# 6. Taxes

## Scope

Owner decision: the ERP handles only three things.

1. GST input tax credit.
2. GST payable to the government.
3. TDS on employee salary.

Out of scope in the ERP: TDS on suppliers and contractors, professional tax, advance
income tax, PF and ESI. They remain the accountant's job, and the ERP does not track
them. Returns are still filed by the accountant on the government portals.

**Every legal point here must be confirmed by the accountant.** Rules changed in 2026
and these notes come from secondary sources.

## GST: how it works

- **Output tax:** GST collected on sales.
- **Input tax credit (ITC):** GST paid on purchases and expenses, used to reduce output
  tax.
- **Payable:** output tax minus usable credit. This is paid to the government.

Credit is only usable when, among other conditions:

- The bill appears in GSTR-2B.
- The goods or services were received.
- The supplier is paid within 180 days; otherwise the credit is reversed with interest,
  and can be claimed again after payment.
- It is claimed within the time limit (reported as 30 November after the financial
  year, or the annual return if earlier).

Some credit is blocked by law under section 17(5). The accountant confirms what applies.

## GST: what exists

`gst/reports/gstr3b.py` already calculates output tax, ITC and net payable per head, and
`gst/reports/tax_ledger.py` shows the movement per account. Gaps:

- Every purchase counts as ITC. Blocked credit and reversals return zero.
- There is no opening credit balance.
- There is no record of GST paid, or of what is still due.
- The reports need the company GSTIN, which is blank.
- Check that new postings use the separate Input and Output accounts, not Tally's combined
  `C Gst`, `S Gst` and `I Gst`.

## GST: what to build

**Credit flag.** `itc_eligible` on purchase and expense bills, default yes. Reports
exclude tax on bills marked no.

**GST Position screen, one card per month:**

| Line | Source |
|---|---|
| Output tax (IGST, CGST, SGST) | Sales bills |
| Input credit this month | Eligible purchase and expense bills |
| Credit carried from last month | Stored at month close, or opening balance |
| Net to pay | Calculated |
| Paid | GST Payments |
| Status | Paid or Due, with the due date |

Credit is used across heads in a legal order, so the accountant confirms the final
head-wise figures from the portal. The ERP figure is for planning cash.

**GST Payment.** Period, challan number (CPIN or CIN), head-wise cash paid and head-wise
credit used, bank account, challan file. Submit posts a Journal Entry through the normal
service, and it exports to Tally. Period plus challan is unique, and amounts are not
negative.

**180-day watch.** A list of unpaid bills with GST nearing 180 days (doc 7).

**Later.** Import GSTR-2B and match it to bills. Until then the accountant reconciles it
on the portal and marks exceptions.

## TDS on employee salary

### Rules

- Salary TDS now falls under section 392 of the Income-tax Act 2025, replacing
  section 192, from 1 April 2026.
- The salary certificate is Form 130, replacing Form 16. The quarterly statement is
  reported as Form 138, replacing Form 24Q.
- Tax is deducted from salary and deposited with the government, based on the employee's
  estimated annual income.

Most daily wagers should be below the taxable limit, so TDS may apply to very few
people, or nobody. Q5 answers that before any build starts.

### Design

Original position: do not build a tax calculator, because slabs change every year and a
wrong automatic figure is worse than none. **Owner decision, 2026-10-31, supersedes this:**
the monthly TDS is worked out from a dated rate table that the accountant edits (no rate
in code), with the new regime as default and the old regime as a per-employee opt-in. A
fixed monthly figure (`tds_per_month`) still overrides it. See
[backend/docs/erpnext_divergences.md](../backend/docs/erpnext_divergences.md), "Salary TDS".

1. Add a `tds` system component to `SystemComponent` and an account `TDS Payable` to
   `HRSettings`.
2. Add `Employee.tds_per_month`, set by the accountant. Default zero.
3. Payroll deducts it and posts Cr TDS Payable. Pay-any-day workers need one rule: deduct
   once per month, on the first pay of that month.
4. **TDS Payment:** month, challan number, amount, bank, challan file. Posts Dr TDS Payable,
   Cr Bank, and exports to Tally.
5. **TDS Register:** per employee and quarter: PAN, pay, TDS deducted and deposited. The
   accountant uses it for Form 138 and Form 130.

Rules: TDS is zero or more and no more than pay; PAN must be present when TDS is above
zero; the monthly amount cannot be deposited twice for the same challan.

## Due dates

Keep due dates in settings, not in code, since they can change:

| Item | Date to confirm |
|---|---|
| GST payment | 20th of next month if filing monthly; 25th under the quarterly scheme |
| TDS deposit | Accountant to confirm; could not be verified for 2026-27 |

## Tests

- Eligible and blocked credit, and carry-forward between months.
- Net payable with and without credit.
- GST Payment posts, exports and shows Paid.
- Cancelling a payment returns the status to Due.
- TDS deducts once per month with several pay dates.
- TDS Payable balance equals deducted minus deposited.

## Done when

- The accountant confirms one month's GST Position against the portal.
- GST and TDS payments carry challan proof and appear correctly in Tally.

## Task cards

### T-1 Input credit flag
- **Files:** `buying/models.py`, `buying/serializers.py`, `gst/reports/gstr3b.py`,
  `gst/reports/tax_ledger.py`.
- **Do:** add `PurchaseInvoice.itc_eligible`, default True. In `gstr3b.build`, leave tax on
  ineligible bills out of `other_itc`, and report it under the ineligible section. When
  posting, ineligible GST must become cost, not input credit. Read `gst/services/posting.py`
  first; if that needs a new ledger rule, stop and report for review.
- **Test:** 3B ITC excludes an ineligible bill, and the voucher still balances.

### T-2 Tax Payment document
- **Depends:** M-8. **Files:** `accounts/models.py`, new `accounts/services/tax_payment.py`,
  serializer, viewset, urls, `tally_export/constants.py`.
- **Do:** `TaxPayment(SubmittableDocument)`: `company`, `tax_type` (`GST`, `TDS`), `period` (first
  day of the month), `posting_date`, `challan_no`, `paid_from` (Bank or Cash account),
  `igst_cash`, `cgst_cash`, `sgst_cash`, `igst_credit_used`, `cgst_credit_used`, `sgst_credit_used`,
  `tds_amount`, `journal_entry` (OneToOne, null, PROTECT). CHECKs: all amounts zero or more; a
  non-zero amount exists; GST rows have no `tds_amount`, and TDS rows have no GST amounts. Unique
  `(company, tax_type, period, challan_no)` for non-cancelled rows.
  - `on_submit` creates and submits a Journal Entry: for GST, Dr Output tax, Cr Bank for cash,
    and Dr Output tax, Cr Input tax for credit used; for TDS, Dr TDS Payable, Cr Bank.
  - `on_cancel` cancels that entry.
  - Proof is required as in M-8.
  Because it posts through a Journal Entry, confirm Tally exports that entry once, and does not
  try to export `TaxPayment`.
- **Test:** each post, cancel reversal, balance, uniqueness, and proof.

### T-3 GST Position service
- **Depends:** T-1, T-2. **Files:** new `gst/services/position.py`, `gst/views.py`, `gst/urls.py`.
- **Do:** add `Company.gst_due_day` (default 20) and `Company.tds_due_day` (default 7, to confirm).
  Add `GstOpeningCredit` (`company`, `as_on`, `igst`, `cgst`, `sgst`), unique on `(company, as_on)`.
  `position(company, month)` reuses `gstr3b.build`, adds opening or carried credit and paid
  amounts, and returns net to pay, status, and due date. Carried credit is computed, not stored:
  opening credit plus eligible input, less credit used, through to the prior month. Expose `GET
  /api/gst/position/?month=YYYY-MM`.
- **Test:** a month with payable, a month with spare credit carried on, part-paid, and fully paid.

### T-4 GST page
- **Depends:** T-3. **Files:** `frontend/src/pages/gst/GstPositionPage.tsx`,
  `TaxPaymentSheet.tsx`, routes, i18n.
- **Do:** month cards as in the design, a Paid or Due badge, a **Record payment** sheet with
  challan upload, and a note that the portal figure is final.
- **Test:** `npm run build`.

### T-5 Credit at risk
- **Depends:** T-1. **Do:** `itc_at_risk(company, as_on)`: unpaid eligible bills with GST, 150 days
  or more from bill date, with days left to 180. Add it to Bills to Pay as a warning.
- **Test:** days 149, 150 and 181; a paid bill drops out.

### T-6 TDS on salary (BLOCKED Q5)
- **Files:** `hr/models.py`, `hr/services/setup.py`, `hr/services/payroll.py`, `hr/serializers.py`,
  `frontend/src/resources/hr.ts`.
- **Do:** add `SystemComponent.TDS`, `Employee.tds_per_month` (`(18,2)`, default 0, CHECK `>= 0`)
  and nullable `HRSettings.tds_payable_account`. Update `hr/services/setup.py` to create the
  component with type deduction. In the slip flow, add a `propose_tds(slip)` step, next to
  `propose_advance_recovery`, which adds one row when `tds_per_month > 0` and no other submitted
  slip of that employee in that calendar month already has one. Cap it by available pay. Require
  PAN in `validate_slip` when TDS is above zero. Read `post_slip` and confirm the deduction
  credits `tds_payable_account`.
- **Test:** several slips in a month deduct once; net pay stays zero or more; missing PAN refuses;
  GL balances.

### T-7 TDS register
- **Depends:** T-6, T-2. **Do:** `GET /api/hr/reports/tds-register/?quarter=` per employee: PAN, pay,
  TDS deducted, and month-wise deposited from `TaxPayment`. Add `TDS Due` on the Accounts home:
  the `TDS Payable` balance, with its due date.
- **Test:** register total equals the ledger movement.

### T-8 Rule check (OWNER)
The accountant confirms this document, the due days, what is blocked credit, and the opening
credit. Their answers are recorded in this file before go-live.
