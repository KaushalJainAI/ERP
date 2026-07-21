# Selling & Revenue Documentation (`selling/`)

## Overview
The `selling` app manages customer master data, Sales Orders, and Sales Invoices.

## Key Models & Workflows

### 1. Masters (`Customer`, `CustomerGroup`)
- `CustomerGroup`: Tree model for segmenting customers.
- `Customer`: `name`, `company`, `customer_group`, `gstin`, `tax_id`, `email`, `phone`. `UNIQUE(company, name)` constraint.

### 2. Document Lifecycle

#### Sales Order (`SalesOrder` & `SalesOrderItem`)
- Document status transitions: `Draft` -> `Submitted` -> `Cancelled`.
- `grand_total`: Recomputed automatically upon submission.

#### Sales Invoice (`SalesInvoice` & `SalesInvoiceItem`)
- Document status transitions: `Draft` -> `Submitted` -> `Cancelled` / `Paid`.
- **On Submit (`on_submit()`)**:
  1. Calls `gl_service.post()` to debit Debtors Receivable (`debit_to`) and credit Sales Income (`income_account`).
  2. If `update_stock=True`, calls `stock_service.post()` to issue stock (-qty) from warehouse.
- **On Cancel (`on_cancel()`)**:
  1. Calls `gl_service.reverse()` to post reversing GL entries.
  2. If `update_stock=True`, calls `stock_service.reverse()` to restore stock balance in `Bin`.

## Verification & Tests
All unit tests in `selling/tests.py` pass cleanly (52/52 overall across all apps), verifying document workflow, GL and stock ledger posting, reversals, and database constraints.
