# Accounts & General Ledger (GL) Documentation (`accounts/`)

## Overview
The `accounts` app implements the Chart of Accounts tree structure, the immutable `GLEntry` ledger, and the GL posting service.

## Key Models & Services

### 1. Chart of Accounts (`Account`)
- Tree model representing Chart of Accounts.
- `root_type`: `Asset`, `Liability`, `Equity`, `Income`, `Expense`.
- `account_type`: `Receivable`, `Payable`, `Bank`, `Cash`, `Stock`, `Tax`, `Cost of Goods Sold`.
- `is_group`: Group accounts hold sub-accounts; postings directly to group accounts are rejected.
- `UNIQUE(company, name)` constraint.

### 2. Immutable Ledger (`GLEntry`)
- Append-only facts representing financial transactions.
- Fields: `posting_date`, `account`, `company`, `party_type`, `party`, `debit`, `credit`, `voucher_type`, `voucher_no`, `is_cancelled`, `fiscal_year`.
- **Database Constraints**:
  - `CHECK (debit >= 0 AND credit >= 0 AND NOT (debit > 0 AND credit > 0))`
  - Index on `(account, posting_date)`
  - Index on `(voucher_type, voucher_no)`
- **Immutability Mandate**: `save()` and `delete()` raise `ValidationError` if an entry is updated or deleted directly. Reversals are posted as new rows with swapped debit/credit and `is_cancelled=True`.

### 3. GL Posting Service (`accounts/services/gl.py`)
- `post(voucher, entries)`: Validates that `Σ debit == Σ credit` and that no entry targets a group account. Posts immutable `GLEntry` records inside `transaction.atomic()`.
- `reverse(voucher)`: Locates active entries for the voucher and posts reversing entries with `is_cancelled=True`.
- `repost(company_id, account_id)`: Replays `GLEntry` logs and verifies balance consistency across all vouchers.

## Verification & Tests
All unit tests in `accounts/tests.py` pass cleanly (32/32 overall), verifying balanced posting, group node rejection, reversal logic, immutability locks, and repost validation.
