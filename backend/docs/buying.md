# Buying & Procurement Documentation (`buying/`)

## Overview
The `buying` app manages supplier master data, Purchase Orders, and Purchase Invoices.

## Key Models & Workflows

### 1. Masters (`Supplier`, `SupplierGroup`)
- `SupplierGroup`: Tree model for segmenting vendors.
- `Supplier`: `name`, `company`, `supplier_group`, `gstin`, `tax_id`, `email`, `phone`. `UNIQUE(company, name)` constraint.

### 2. Document Lifecycle

#### Purchase Order (`PurchaseOrder` & `PurchaseOrderItem`)
- Document status transitions: `Draft` -> `Submitted` -> `Cancelled`.
- `grand_total`: Recomputed automatically upon submission.

#### Purchase Invoice (`PurchaseInvoice` & `PurchaseInvoiceItem`)
- Document status transitions: `Draft` -> `Submitted` -> `Cancelled` / `Paid`.
- **On Submit (`on_submit()`)**:
  1. Calls `gl_service.post()` to debit Expense Account (`expense_account`) and credit Creditors Payable (`credit_to`).
  2. If `update_stock=True`, calls `stock_service.post()` to receive stock (+qty) into warehouse.
- **On Cancel (`on_cancel()`)**:
  1. Calls `gl_service.reverse()` to post reversing GL entries.
  2. If `update_stock=True`, calls `stock_service.reverse()` to reverse stock receipt in `Bin`.

## Verification & Tests
All unit tests in `buying/tests.py` pass cleanly (58/58 overall across all apps), verifying procurement workflows, GL and stock ledger posting, reversals, and database constraints.
