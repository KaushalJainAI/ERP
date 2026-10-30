# 4. Collections and supplier payments

## Goal

At any moment the owner can see who owes us, what we owe, and what is late.

## Payment cycle

- **Customers: 90 days** is the standard. A bill dated 1 January is due on 1 April, and
  overdue from the next day.
- **Suppliers:** to be confirmed (Q4).

Add `Company.default_customer_credit_days` (90) and
`Company.default_supplier_credit_days`. Add a nullable `credit_days` on Customer and
Supplier for exceptions; blank means the company default. Both must be zero or more.

Set the due date automatically:

- Sales bill: bill date + customer credit days, set by the dispatch action.
- Purchase bill: supplier's bill date + supplier credit days.

Due date is required at submit and cannot be before the bill date.

## Status

Staff see one simple status, derived from the existing `InvoiceStatus`:

| Stored status | Shown as |
|---|---|
| Paid | **Paid** |
| Unpaid or Partly Paid, not past due | **Due** |
| Unpaid or Partly Paid, past due | **Due**, with days late in red |

Overdue is calculated from today's date, not stored, as the code already does.

## What exists

Outstanding per invoice, ageing (`/accounts/reports/ageing/`), Payment Entry, Payment
Reconciliation and `InvoiceStatus` are built. The missing part is simple screens and
the money already owed from before go-live.

## Money to Collect

One row per customer: total due, overdue amount, oldest bill, days late. Sorted by
most overdue. Tapping a row shows its bills and **Record Receipt**.

## Bills to Pay

One row per bill: supplier or expense, amount, due date, days left or late. Filters:
overdue, this week, this month. Tapping shows **Record Payment**. This list includes
expenses (doc 5), so the Admin has one list of everything pending.

## Recording money

Keep Payment Entry underneath and show a short form: who, how much, cash or bank, date,
proof.

- Allocate to the oldest bill first; the accountant can change this before saving.
- Allocation can never exceed what is outstanding.
- An amount with no open bill stays as an advance, shown on the party's row.
- Proof is required at submit.
- The bank or cash account has to be chosen, because the current assistant default
  picks the first one found. The form remembers the last used.

## Old balances

Customers owed about ₹5.64 crore and suppliers about ₹1.60 crore on the books at
2026-10-19, as party balances with no bills behind them. Doc 9 turns these into opening
bills, so they appear here and can trigger reminders.

## Supplier payments and GST credit

GST credit on a purchase needs the supplier paid within 180 days. Bills to Pay shows
a warning as an unpaid bill with GST approaches day 180 (doc 6).

The accountant should also confirm whether small-supplier (MSME) payment limits apply.
Add a supplier flag only if they do.

## Tests

- Due date defaults for company, party override and zero days.
- A 90-day boundary: not overdue on day 90, overdue on day 91.
- Oldest-first allocation, and refusal of over-allocation.
- Part payment shows Due with the right balance.
- Cancelling a receipt restores the amount due.
- List totals equal the ageing report.

## Done when

- The accountant records a real receipt and payment in under a minute.
- Money to Collect total matches the receivables in the trial balance.
- Bills to Pay total matches payables.

## Task cards

### M-1 Credit days
- **Files:** `core/models.py`, `selling/models.py`, `buying/models.py`, their serializers, new
  `accounts/services/terms.py`, frontend resources.
- **Do:** add `Company.default_customer_credit_days` (`PositiveIntegerField`, default 90) and
  `Company.default_supplier_credit_days` (null until Q4). Add nullable `credit_days`
  (`PositiveIntegerField`) on Customer and Supplier. Write `due_date_for(party, base_date)`:
  party value, else company default, else `base_date`.
- **Test:** party override, company default, zero days, and 90 days from 1 January 2027.

### M-2 Due dates on bills
- **Depends:** M-1. **Files:** `selling/models.py`, `buying/models.py`.
- **Do:** in draft save, when `due_date` is empty, fill it: sales from `posting_date`,
  purchase from `bill_date` or `posting_date`. Add a CHECK `due_date >= posting_date` for sales.
  For purchases, check against `bill_date` in `validate_before_submit`. Require `due_date` at
  submit. Fix tests only by supplying the default behaviour, never by removing assertions.
- **Test:** default filling; a due date before the bill date is refused.

### M-3 Paid or Due label
- **Files:** `accounts/models.py` near `display_status`, invoice serializers.
- **Do:** add a read-only `payment_label` (`Paid`, `Due`, `Draft`, `Cancelled`) and `days_late`
  (0 if not late). Do not store them. Do not change `InvoiceStatus`.
- **Test:** boundaries on day 90 and 91, part-paid, and a return.

### M-4 Money to Collect
- **Depends:** M-3. **Files:** new `accounts/services/collections.py`, `accounts/views.py`,
  `accounts/urls.py`.
- **Do:** `collections(company, as_on)` built on `outstanding.ageing_report("Customer", ...)`,
  grouped by party: total, overdue, oldest bill date, days late, and bills. Include opening
  bills once G-1 exists. Expose `GET /api/accounts/collections/`. The server sends totals.
- **Test:** total equals the ageing total, and the overdue split on the boundary.

### M-5 Bills to Pay
- **Depends:** M-3. **Do:** `bills_to_pay(company, as_on, filter)` from
  `ageing_report("Supplier", ...)`, with `is_expense` and category when E-2 exists, and
  `days_left`. Expose `GET /api/accounts/bills-to-pay/`, with `filter=overdue|week|month`.
- **Test:** each filter, and that total equals the ageing total.

### M-6 Pages and short form
- **Depends:** M-4, M-5, M-7. **Files:** `frontend/src/pages/accounts/CollectionsPage.tsx`,
  `BillsToPayPage.tsx`, `components/accounts/RecordMoneySheet.tsx`, routes, i18n.
- **Do:** rows as in the design. Tapping opens a sheet: amount, cash or bank account (remember
  last used in `localStorage`, inside try/catch), date, reference, proof upload (reuse
  `AttachmentsPanel` logic), and the suggested allocation, editable. Saving creates and submits
  a Payment Entry through the existing API.
- **Test:** `npm run build`; check at phone width.

### M-7 Oldest-first allocation
- **Files:** new `accounts/services/allocation.py`; read `accounts/mapping.py::build_payment_entry`
  and `invoice_outstanding`.
- **Do:** `suggest_allocation(party_type, party, company, amount)` returns references, oldest
  due first, never above outstanding; any remainder is `unallocated`. Expose `GET
  /api/accounts/payment-entries/suggest-allocation/`. Keep the server check that allocation
  cannot exceed outstanding, adding it if missing.
- **Test:** exact amount, part of a bill, across bills, more than owed, and nothing owed.

### M-8 Proof required
- **Files:** `core/models.py`, `core/attachments.py`, `accounts/models.py`, `buying/models.py`.
- **Do:** add `Company.require_proof`, BooleanField, default False. It is off by default so
  existing tests stay valid, and is turned on at go-live. Add `attachments.has_proof(doc)`. In
  `validate_before_submit` of `PaymentEntry`, `JournalEntry` and `PurchaseInvoice`, raise a
  translated error when required and absent. `TLY-` documents are exempt. `TaxPayment` follows
  the same rule in T-2.
- **Test:** off passes; on without proof refuses; on with proof passes.
