# Financial & Stock Reporting Documentation

## Overview
Comprehensive financial and inventory reporting endpoints providing real-time data aggregation directly from the immutable ledgers (`GLEntry` and `StockLedgerEntry` / `Bin`).

## Key Endpoints

### 1. General Ledger (`/api/accounts/reports/general-ledger/`)
- Query params: `company`, `account`, `from_date`, `to_date`.
- Returns line-by-line posting history with running balance for any account or across all accounts.

### 2. Profit & Loss Statement (`/api/accounts/reports/profit-and-loss/`)
- Query params: `company`.
- Calculates total income (`root_type='Income'`), total expenses (`root_type='Expense'`), and net profit/loss (`total_income - total_expense`).

### 3. Balance Sheet (`/api/accounts/reports/balance-sheet/`)
- Query params: `company`.
- Returns aggregate valuation for `total_assets`, `total_liabilities`, and `total_equity`.

### 4. Stock Balance Report (`/api/stock/reports/stock-balance/`)
- Query params: `item`, `warehouse`.
- Returns current inventory quantities (`actual_qty`), valuation rates, and total stock values (`stock_value`) per item and warehouse.

## Verification & Tests
Unit tests in `accounts/tests.py` and `stock/tests.py` (62/62 overall test suite) verify financial statement metrics, running balance calculations, and inventory stock balance aggregations.

## GST reports (`/api/gst/reports/`, app `gst`)

Design: [gst_design.md](gst_design.md) section 6. Code: `gst/reports/{gstr1,gstr3b,tax_ledger}.py`,
views in `gst/views.py`. All three need `company` (with a GSTIN, else 400) and a period: `month=YYYY-MM`
or `from_date` + `to_date` (the tax ledger's period is optional). Malformed input is 400. Money is a
2dp string, GST rate a 3dp string.

- **GSTR-1** (`gstr1/`, `can_read` on `selling.SalesInvoice`): b2b, b2cl, b2cs, exports, nil/exempt/non-GST,
  HSN (B2B / B2C), documents issued, warnings. Aggregates the per-line GST columns of submitted invoices.
- **GSTR-3B** (`gstr3b/`, read on Sales **and** Purchase Invoice): tables 3.1, 3.2, 4, 5 and 6.1.
- **Tax ledger** (`tax-ledger/`, `can_read` on `accounts.GLEntry`): GL on `gst_direction`-tagged accounts with
  opening / running / closing balance (debit minus credit) and `net_by_component` (output + rcm - input).
- Legacy documents (blank `gst_category`) are excluded and counted in `warnings`; cancelled documents
  are excluded everywhere except GSTR-1 `docs_issued`; the tax ledger drops `is_cancelled` GL rows
  (original and reversal), like the general ledger.
- Tests: `gst/tests/test_reports.py`. GSTR-3B 6.1 `net_payable` equals the tax ledger's `net_payable` per head.

## Registers and party statement (`/api/accounts/reports/`, app `accounts`)

Code: `accounts/reports/{registers,daybook,cashbook,statement}.py`, views in `accounts/report_views.py`,
tests in `accounts/test_registers.py`, frontend in `frontend/src/pages/reports/registers/`. The daily Tally
views plus a printable statement for distributors. Read-only: ledgers and submitted documents only.
`company`, `from_date` and `to_date` are required on all five (400 otherwise, also for `from_date > to_date`
or an id of another company). Rows are paged (`page`, `page_size`, default 500, max 5000); totals always
cover the whole period. Money is a 2dp string. CSV export is client-side (the GST report pattern) and
walks all pages. Cancelled vouchers follow the General Ledger rule: original and reversal rows are both
`is_cancelled`, so both are hidden.

- **Sales / Purchase Register** (`sales-register/`, `purchase-register/`; ERPNext `sales_register`,
  `purchase_register`; `can_read` on `selling.SalesInvoice` / `buying.PurchaseInvoice`): one row per
  submitted invoice with party GSTIN, net total, one column per tax head (CGST, SGST, IGST, CESS, other),
  grand total, rounding, rounded total and outstanding. Credit / debit notes (`is_return`) are listed with
  negative figures. Filters: `party` (id), `item_group` (id, child groups included; it selects invoices
  that carry such an item, the amounts stay invoice-level). Reverse-charge purchase tax is shown in
  `reverse_charge_tax` (it is not part of the grand total). Purchase rows add the supplier's `bill_no` /
  `bill_date`; freight / hamali charged on the bill are in the `OTHER` head (they are inside the grand total).
- **Day Book** (`day-book/`, `can_read` on `accounts.GLEntry`): live GL vouchers in creation order with
  their Dr/Cr account lines, a total per voucher, `daily_totals` and grand totals (debit = credit).
  Optional `voucher_type`.
- **Cash / Bank Book** (`cash-book/`, `account` = a Cash or Bank account of the company): opening balance,
  entries with the against account(s), party and running balance, closing balance (debit minus credit).
- **Party Statement** (`party-statement/`, `party_type` + `party`; needs `can_read` on `accounts.GLEntry` and on
  the party master): opening balance, every GL row on the party control accounts with a running balance
  in the party sense (`dr_cr` names the real side), closing balance and the ageing of open invoices as on
  `to_date`. The closing balance equals `outstanding.party_balance` when nothing is posted after `to_date`;
  `ledger_balance` is the all-dates figure. The Accounts page offers Print / Save as PDF (CSS `@media print`,
  company header) for sending to a distributor.
- Tests: `accounts/test_registers.py` (hand-computed GST fixtures, a real credit note, company isolation, RBAC).
