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
