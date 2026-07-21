# Core App Documentation (`core/`)

## Overview
The `core` app provides the base models, abstract patterns, cross-cutting framework tables, and ViewSet mixins used across all ERP modules.

## Key Components

### 1. Abstract Bases
- `TimeStampedModel`: Provides `created_at`, `modified_at`, `created_by`, and `modified_by`.
- `SubmittableDocument`: Subclasses `TimeStampedModel`. Defines the core document lifecycle:
  - `docstatus`: `Draft (0)` -> `Submitted (1)` -> `Cancelled (2)`.
  - `submit()`: Atomically validates via `validate_before_submit()`, updates `docstatus=SUBMITTED`, and invokes `on_submit()`.
  - `cancel()`: Atomically updates `docstatus=CANCELLED` and invokes `on_cancel()`.
  - `amend()`: Creates a new draft document linked via `amended_from` to the cancelled document.
  - **Edit Lock**: Submitted and cancelled documents raise `ValidationError` if non-status fields are modified or if deletion is attempted.

### 2. Core Models
- `Company`: Represents legal entity/company (`name`, `abbr`, `default_currency`, `gstin`, `is_active`).
- `FiscalYear`: Represents accounting fiscal year with DB `CheckConstraint(end_date > start_date)`.
- `NamingSeries`: Atomic document sequence generator via `NamingSeries.next(prefix, padding)`.
- `AuditLog`: Immutable, append-only audit trail logging all mutations (`create`, `update`, `submit`, `cancel`, `delete`). Updates and deletions are strictly blocked.
- `SystemHealthCheck`: Stores integrity check records and drift status for self-healing.
- `Outbox`: Deduplicated transactional outbox for reliable asynchronous external side effects (`dedupe_key`, `channel`, `payload`, `status`, `attempts`).

### 3. ViewSet Mixins (`core/mixins.py`)
- `AuditLogMixin`: Intercepts `perform_create`, `perform_update`, `perform_destroy` to write `AuditLog` rows.
- `SubmittableViewSetMixin`: Exposes `/submit/`, `/cancel/`, `/amend/` custom actions for DRF ViewSets.

## Verification & Tests
All unit tests in `core/tests.py` pass cleanly (11/11), covering positive lifecycles, constraint checks, and negative/illegal state transition handling.
