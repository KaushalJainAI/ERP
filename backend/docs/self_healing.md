# Self-Healing & Invariant Toolbox Documentation

## Overview
The ERP system includes real-time integrity verification and self-healing tools to enforce all system invariants defined in `plan.md §6`.

## Key Commands & Services

### 1. Integrity Verification Command (`verify_integrity`)
`python manage.py verify_integrity`
Runs 5 key invariant checks across financial and stock ledgers:
1. **GL Balance Invariant**: Ensures `Σ debit == Σ credit` for every non-cancelled voucher.
2. **Stock Bin Accuracy Invariant**: Compares derived `Bin.actual_qty` and `Bin.stock_value` against exact sums from non-cancelled `StockLedgerEntry` logs.
3. **No Negative Stock Invariant**: Ensures `Bin.actual_qty >= 0`.
4. **Outbox Queue Health**: Validates no dead events without retry.
5. **System Health Logging**: Automatically records execution timestamp and status (`ok` or `alert`) in `SystemHealthCheck` table.

### 2. Stock Repost Self-Healing Command (`repost_stock`)
`python manage.py repost_stock [--item ITEM_ID] [--warehouse WAREHOUSE_ID]`
Rebuilds derived `Bin` stock balances and valuation rates from `StockLedgerEntry` logs in chronological order. Self-heals any stock drift or database corruption.

### 3. GL Repost Command (`repost_gl`)
`python manage.py repost_gl [--company COMPANY_ID] [--account ACCOUNT_ID]`
Replays `GLEntry` logs and verifies balance consistency across all vouchers.

## Verification & Tests
Unit test `test_verify_integrity_and_repost_self_healing_flow` in `core/tests.py` verifies:
1. Initial clean ledger passes `verify_integrity`.
2. Artificial corruption of `Bin.actual_qty` causes `verify_integrity` to raise `CommandError`.
3. Running `repost_stock` self-heals the derived `Bin` back to exact `StockLedgerEntry` sums.
4. `verify_integrity` passes cleanly post-healing.
