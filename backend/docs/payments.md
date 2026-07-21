# Payments & Billing Documentation (`accounts/`)

## Overview
The payment workflow handles customer receipts, vendor payments, and internal bank transfers via `PaymentEntry` and `PaymentEntryReference`.

## Key Models & Workflows

### 1. Payment Entry (`PaymentEntry` & `PaymentEntryReference`)
- `payment_type`: `Receive` (from Customer), `Pay` (to Supplier), or `Internal Transfer`.
- `paid_from`: Source bank or receivable account.
- `paid_to`: Target bank or payable account.
- `paid_amount`: Monetary value transferred.
- `references`: Maps payment allocations to specific vouchers (`SalesInvoice`, `PurchaseInvoice`).

### 2. General Ledger Posting
- **On Submit (`on_submit()`)**:
  - Debit `paid_to` account (Bank or Creditors).
  - Credit `paid_from` account (Bank or Debtors).
  - Posts immutable `GLEntry` records via `gl_service.post()`.
- **On Cancel (`on_cancel()`)**:
  - Posts reversing `GLEntry` records via `gl_service.reverse()`.

## Verification & Tests
Unit tests in `accounts/tests.py` (60/60 overall test suite) verify payment postings, document reference allocations, reversals, and zero/negative amount error handling.
