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
