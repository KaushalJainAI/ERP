# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The goal

Build a custom ERP for the Indian market that **replicates ERPNext's
functionality** on our own stack (Django + DRF + React), then diverges to fit our
requirements. First clone the behaviour; later modify it. When unsure how a
feature should behave, the answer is "how does ERPNext do it?" — consult the
reference in `erpnext/`.

## Read these before doing anything

The design is fully specified. Do not improvise around it.

- **[plan.md](plan.md)** — master design + authoritative DB schema + the three
  mandates. The source of truth for *what* and *why*.
- **[instructions.md](instructions.md)** — how to implement (golden rules, the
  document-building recipe, Definition of Done).
- **[checklist.md](checklist.md)** — ordered task list. Work top-to-bottom, one
  item at a time, verify, tick, commit.

## Architecture in one paragraph

Django **modular monolith**: one process, one Postgres DB, apps as bounded
contexts (`core`, `users`, `contacts`, `accounts`, `stock`, `selling`, `buying`).
Every transactional document inherits `core.SubmittableDocument` and moves
`Draft (0) → Submitted (1) → Cancelled (2)`. On submit it posts **immutable**
ledger entries (`GLEntry`, `StockLedgerEntry`) inside one `transaction.atomic()`
block; on cancel it posts reversing entries. All balances (`Bin`, account
balances, invoice outstanding, order fulfilment) are **derived projections** that
can be rebuilt from the ledgers. Frontend is a **metadata-driven** React SPA:
`<DocForm>`/`<DataTable>` render any resource from serializer metadata.

## The three mandates (never violate — see plan.md §1)

1. **Self-healing.** Store immutable facts (ledgers, audit log); derive
   everything else; if a derived value is wrong, rebuild it via `repost_*`.
   Everything atomic; external effects go through the `Outbox`.
2. **User-error-proofing.** Least-privilege RBAC; correctness enforced by DB
   constraints (`CHECK`/`UNIQUE`/`ON DELETE PROTECT`), not user discipline;
   submitted documents are immutable; users get the least power necessary.
3. **Multilingual.** 13 languages (English + 12 Indian, `ur` is RTL). UI strings
   via gettext / react-i18next; master-data via `FieldTranslation`. No hardcoded
   user-facing text.

## Non-negotiable invariants

- `GLEntry` / `StockLedgerEntry` / `Bin` are written **only** by the posting/repost
  services — never via an API, never `UPDATE`/hard-`DELETE`. Corrections = reversals.
- Money is `Decimal` with the precisions in plan.md §3 — never `float`.
- Only draft documents can be deleted; submitted documents cancel + amend.
- Re-validate all input server-side; the DB constraint is the real guarantee.
- `erpnext/` is **read-only reference** (git-ignored) — never edit, import, or ship it.

## Commands

Backend (`backend/`): `pip install -r requirements.txt` · `python manage.py
migrate` · `python manage.py seed_defaults` · `python manage.py runserver` ·
`python manage.py verify_integrity` (must be green before ticking any task) ·
`pytest` (one test: `pytest path::test_name`) · `ruff check . && black --check . && mypy .`

Frontend (`frontend/`): `npm install` · `npm run dev` · `npm run build` · `npm run lint`

Self-healing: `python manage.py verify_integrity` (detect drift), `repost_stock`,
`repost_gl` (rebuild derived state from the ledgers).

## Working discipline

Follow [checklist.md](checklist.md) in order, one task per commit. A task is done
only when it matches plan.md, DB constraints exist, the three mandates hold, tests
+ `verify_integrity` + lint pass, and it reproduces the ERPNext behaviour. **When a
task is ambiguous or not covered by plan.md, stop and ask — never guess.**
