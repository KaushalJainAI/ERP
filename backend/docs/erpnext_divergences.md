# Architectural Comparison & ERPNext Divergences

## Summary
This document outlines key technical decisions and architectural enhancements made when replicating ERPNext's business domain onto a Django 5 + PostgreSQL + React Vite stack.

## Key Architecture Divergences

### 1. Modern Stack vs Frappe Framework
- **ERPNext**: Built on the Frappe framework (Python backend + custom JS frontend with DocTypes).
- **Custom ERP**: Built on standard Django 5 + Django REST Framework + React 18 Vite + TypeScript + TanStack Query. Provides full type safety, OpenAPI standard documentation (`drf-spectacular`), and standard REST endpoints.

### 2. Immutable Ledger Enforcement
- **ERPNext**: Uses DocType submit/cancel mechanics, updating GL and Stock entry rows.
- **Custom ERP**: Enforces strict append-only immutability on `GLEntry` and `StockLedgerEntry`. Any attempt to update or delete existing ledger rows via API or ORM raises a `ValidationError`. Reversals are recorded as new audit rows with swapped debit/credit or negative quantities and `is_cancelled=True`.

### 3. Self-Healing & Invariant Engine
- Includes automated self-healing tools (`verify_integrity`, `repost_stock`, `repost_gl`) that verify 5 key ledger invariants and automatically rebuild derived projections like `Bin` from `StockLedgerEntry` history.

### 4. Indian Financial & Multilingual Support
- Built-in support for Indian numbering system (Lakh/Crore grouping: `₹12,34,567.89`), GSTIN validation, and 13 supported languages including Indic scripts and RTL layout for Urdu (`ur`).
