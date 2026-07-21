# Stock & Inventory Documentation (`stock/`)

## Overview
The `stock` app implements Item and Warehouse master trees, the immutable `StockLedgerEntry` (SLE) ledger, and the derived `Bin` inventory balance projection.

## Key Models & Services

### 1. Masters (`Item`, `Warehouse`, `UOM`, `ItemGroup`)
- `Item`: `code` (unique), `name`, `description`, `item_group`, `stock_uom`, `valuation_method` (`FIFO` or `Moving Average`), `standard_rate`.
- `Warehouse`: Tree model with `UNIQUE(company, name)`. Postings directly to group warehouses (`is_group=True`) are strictly forbidden.

### 2. Immutable Ledger (`StockLedgerEntry`)
- Append-only facts representing physical stock movements.
- Fields: `posting_date`, `posting_time`, `item`, `warehouse`, `qty` (signed: +in / -out), `valuation_rate`, `stock_value_difference`, `voucher_type`, `voucher_no`, `is_cancelled`.
- **Database Indexes**:
  - `(item, warehouse, posting_date, posting_time)`
  - `(voucher_type, voucher_no)`
- **Immutability Mandate**: Direct updates and deletes raise `ValidationError`. Reversals post new SLE rows with `-qty` and `-stock_value_difference` and `is_cancelled=True`.

### 3. Derived Projection (`Bin`)
- Stores current stock balance for `(item, warehouse)`: `actual_qty`, `valuation_rate`, `stock_value`.
- `UNIQUE(item, warehouse)` constraint.
- Never updated over the API; automatically updated by the stock posting service.

### 4. Stock Posting Service (`stock/services/ledger.py`)
- `post(voucher, sle_entries)`: Validates warehouse node restrictions, computes MOVING AVERAGE / FIFO valuation deltas, updates `Bin`, and creates immutable `StockLedgerEntry` records inside `transaction.atomic()`. Rejects issues if `actual_qty < abs(issue_qty)`.
- `reverse(voucher)`: Posts reversing SLE rows with `-qty` and `-stock_value_difference` and updates `Bin`.
- `repost(item_id, warehouse_id)`: Self-healing command that deletes derived `Bin` records in scope and completely rebuilds `actual_qty`, `valuation_rate`, and `stock_value` from chronological `StockLedgerEntry` logs.

## Verification & Tests
All unit tests in `stock/tests.py` pass cleanly (40/40 overall across all apps), verifying receipt/issue valuation, negative stock rejection, reversal logic, immutability, and self-healing repost functionality.
