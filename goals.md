# goals.md — what this project is for

> **Status:** living document. Last reviewed 2026-10-29.
>
> **Where this sits:** [CLAUDE.md](CLAUDE.md) is the canonical brief (architecture,
> mandates, invariants). [checklist.md](checklist.md) is the ordered task list.
> [plan.md](plan.md) is historical design rationale. **This file answers _why_ and
> _what winning looks like_** — the destinations, the success criteria, and the
> things we deliberately refuse to do. It does not list tasks.
>
> When this file and the code disagree, **the code wins.**

---

## North star

> **Make NIDHI GRAH UDYOG run on a system it owns** — one trustworthy set of
> books, one operational picture of sales, stock and production, usable by
> ordinary staff in their own language, and able to grow past what Tally or a
> licensed ERP would allow.

The first job is to **replace Tally without losing a rupee of history**. The
second is to be **better than Tally at running the business**, not merely equal
to it.

---

## The problem we are solving

| Current state | Why it hurts |
|---|---|
| **Tally** — desktop-bound, editable, limited concurrency, closed format | Books are a file on one machine. Corrections leave no reliable trail. Staff cannot work in parallel. Nothing integrates. |
| **ERPNext** — powerful but heavy, framework-locked, over-scoped | Too much to operate for this business; you inherit a framework you don't control. |
| **Spreadsheets + WhatsApp + memory** | No traceability, no stock truth, no credit control, month-end is reconstruction. |

The gap is not "software". The gap is **a system that is simultaneously
trustworthy, operational, and usable by the people who actually do the work.**

---

## The goals

Each goal is stated as an outcome, with a **"done when"** test that can be
checked against the running system.

### G1 — Books that cannot lie

**Outcome:** every rupee lives in one immutable double-entry ledger; every
reported number is traceable to a voucher; corrections are reversals, never
edits.

**Done when**
- `verify_integrity` is green on every deploy, with zero drifts.
- Trial balance, P&L, balance sheet and stock value reproduce Tally for every
  closed fiscal year to ₹0.00.
- `GLEntry`, `StockLedgerEntry` and `Bin` are written only by posting/repost
  services — never by an API, never updated, never hard-deleted.
- Any derived figure can be rebuilt from the ledgers with `repost_gl` /
  `repost_stock`.

**Today:** ✅ achieved. 294,133 GL entries, 54,501 vouchers, 2017-18 → 2026-27,
0 drifts, reconciliation ₹0.00.

---

### G2 — One operational picture

**Outcome:** accounting, sales, purchasing, stock and production share one
database, so the owner sees one truth instead of reconciling departments.

**Done when**
- One dashboard shows sales, purchases, receivables, payables, stock value, open
  orders and draft documents.
- Stock is valued from the stock ledger, not typed in.
- Item-wise and party-wise margin is answerable without exporting to Excel.
- A document created in sales is visible to stock, accounts and reporting
  immediately.

**Today:** ⚠️ partial. Dashboard, stock ledger and reporting are live; the
invoice layer is still empty (see G3).

---

### G3 — Replace Tally for daily work

**Outcome:** the business can run a normal day — bill a customer, receive goods,
pay a supplier, move stock, handle a return — without opening Tally.

**Done when**
- A GST tax invoice can be **printed** and issued from the ERP.
- Sale: order → invoice → dispatch → receipt, with outstanding tracked per
  invoice.
- Purchase: order → receipt → bill → payment, with landed cost.
- Returns (credit / debit notes) put stock back correctly.
- GSTR-1 and GSTR-3B for a month can be produced from the ERP alone.
- The accountant accepts the output without re-keying it into Tally.

**Today:** ❌ **not met — this is the top priority.** The ERP can compute the
books but cannot yet issue a bill. Until this closes, the system is a record and
analysis layer, not the operating system of the business.

---

### G4 — Correct by construction

**Outcome:** users cannot break the books, and the system can heal itself.

**Done when**
- PostgreSQL constraints (`CHECK` / `UNIQUE` / `ON DELETE PROTECT`) enforce every
  invariant — not UI discipline.
- Every submission is atomic: no half-posted documents.
- Submitted documents are immutable; only drafts can be deleted.
- RBAC is least-privilege: read and draft by default; submit, cancel, delete and
  master-data edits are deliberate grants.
- Audit log answers "who changed what, when".
- A wrong derived value is fixed by replay, never by manual SQL.

**Today:** ✅ largely achieved (RBAC, audit, atomic posting, repost commands).

---

### G5 — Usable by the actual staff

**Outcome:** the warehouse, the billing clerk and the production supervisor can
use it without English fluency or software training.

**Done when**
- 13 languages are complete (English + 12 Indian; Urdu RTL), with no hardcoded
  user-facing text.
- Master data (item and party names) is translatable.
- A stock movement can be recorded by a non-technical user in under a minute.
- The assistant can take a spoken sentence, look things up, and **fill the real
  form** — while being structurally unable to save, submit or delete anything.

**Today:** ⚠️ i18n infrastructure and RTL support are live; translation coverage
is still being completed. The assistant is designed ([assistant_plan.md](assistant_plan.md))
but not built.

---

### G6 — Own the stack, own the data

**Outcome:** no licence, no framework lock-in, no vendor deciding what the
business may do next.

**Done when**
- Runs on our stack: Django + DRF + PostgreSQL, React + Vite + TypeScript.
- Every module is reachable through a documented API.
- ERPNext is reference only — no runtime dependency, no imported code.
- The full Tally history is queryable in our own database, and ERP vouchers can
  be exported back to Tally for the accountant.
- A new document type or integration can be added without waiting for a vendor.

**Today:** ✅ achieved. 63,004 Tally records and 102,681 item lines are archived
in Postgres; `verify_tally_archive` proves the archive row-for-row.

---

### G7 — Bigger than Tally

**Outcome:** capabilities Tally never had, used in daily work — not built for
their own sake.

**Candidates, in value order**
- Production: BOM, work orders, WIP, yield and true cost per batch (where margin
  actually lives in a spice business).
- Print formats and e-way bill / e-invoice.
- Bank reconciliation and cheque tracing.
- HR and payroll inside the same books as labour cost.
- Attachments (bill photos, proof documents) on every transaction.
- Item-wise and distributor-wise gross profit.
- Voice / chat entry in Hindi.

**Done when** at least the production-costing, print and reconciliation
capabilities are in daily use and the business would not go back.

**Today:** ⚠️ models and APIs exist for most; production and HR are unused so
far; print, bank reconciliation and e-way bill are not built.

---

## What this buys — utility by function and role

Goals are intentions; utility is what the business actually gets. Use this as the
test of whether a goal is worth its cost.

### By function

| Function | Utility | Status today |
|---|---|---|
| **Accounting** | Trial balance, P&L, balance sheet, GL, day book, cash book, party statements — every figure traceable to a voucher | ✅ Live on 10 years, 294,133 GL entries, 0 drifts |
| **Receivables / payables** | Who owes you, who you owe, ageing, credit limits | ✅ Party balances exact; invoice-level ageing only for new invoices |
| **Inventory** | Item master (144), warehouses, stock ledger, valuation, batches/expiry, physical count | ✅ Live for FY 2026-27; 7 items need a physical count |
| **Selling** | Customers (694), orders, invoices, price lists, credit control | ⚠️ Built, but no historical invoices and cannot print |
| **Buying** | Suppliers (284), POs, purchase invoices, landed cost | ⚠️ Same gap |
| **Manufacturing** | BOM, work orders, WIP transfer, yield, cost per batch | ⚠️ Built, no real work orders yet |
| **GST** | HSN on 113 items, tax templates, GSTR-1, GSTR-3B, tax ledger | ⚠️ Works for new invoices; Tally-era GST cannot be split |
| **HR / payroll** | Employees, attendance, advances, salary slips | ⚠️ API + tests done, screens pending |
| **Governance** | 8 roles / 153 permissions, audit log, attachments, 13 languages | ✅ Live |
| **Continuity** | 63,004 archived Tally records, 102,681 item lines, Tally export | ✅ Live and verified |

### By role

| Role | What they get |
|---|---|
| **Owner** | One dashboard: sales, purchases, receivables, payables, stock value, open orders, draft documents, integrity status. "Where do we stand?" without asking three people. |
| **Accountant** | Books that must balance. Immutable ledgers, reversals instead of edits, DB constraints instead of discipline. Ten years that tie to the paisa. |
| **Sales** | Full customer history and outstanding before committing to an order — not a WhatsApp scroll. |
| **Warehouse** | Stock by item and warehouse, with valuation and expiry. Negative stock visible as a problem, not hidden. |
| **Production** | BOM cost and yield per batch — where margin actually lives in a spice business. |
| **Staff** | A UI in their own language, and eventually voice entry. Adoption, not decoration. |
| **CA** | Tally export round-trip keeps the filing tool during migration — no big-bang cutover. |

### Why this is better than Tally

1. **Multi-user and web-based** — concurrent roles, not a desktop file.
2. **Immutable audit trail** — Tally data can be edited or corrupted; here,
   corrections are reversals with a record.
3. **Database-enforced correctness** — negative stock, unbalanced vouchers and bad
   references are impossible, not discouraged.
4. **Self-healing** — derived numbers rebuild from ledgers; corruption is a
   repost, not a restore-from-backup.
5. **Extensible** — an API and metadata-driven UI mean new documents and
   integrations (WhatsApp, payment links, e-way bill) are code we own.
6. **No per-user licence, and we own the data.**

### What is still blocked

Today this is **a trustworthy system of record, not yet the system the business
runs on.** Three blockers:

- **It cannot issue a GST invoice** — no print formats, and historical invoices
  exist only as journal entries.
- **No bank reconciliation or e-way bill.**
- **Limited invoice-level history** — Tally bill-wise allocation was never
  recorded, so past-due ageing per invoice is not reconstructable.

### Utility ranking

If the goal is utility per unit of work, the order is:

1. **Print formats + invoice backfill + GST input/output accounts** → turns a
   ledger into a billing system. Highest utility jump by far; this is G3.
2. **Bank reconciliation + e-way bill** → removes the last two reasons to open
   Tally.
3. **HR/payroll screens** → brings labour cost into the same books.
4. **Assistant / voice entry** → adoption utility, only valuable once the above
   works.

---

## Non-goals

Deliberately out of scope — do not build these without an explicit decision:

- **Multi-currency** — India-only, INR.
- **Serial numbers** — not relevant to this business.
- **CRM / leads / campaigns.**
- **Projects and timesheets.**
- **Support desk / ticketing.**
- **Customer website / B2B portal** — on hold.
- **POS / retail billing** — B2C runs through the separate NGU app.

Also refused on principle: **invented data**. Where Tally never recorded
something (bill-wise allocation, bank instruments, invoice consignee), the ERP
does not guess it. Gaps are documented in
[backend/docs/tally_data_gaps.md](backend/docs/tally_data_gaps.md) rather than
filled with plausible fiction.

---

## Success horizons

### Horizon 1 — Trusted record *(achieved)*
Ten years of books, stock and party history in Postgres, reconciled to Tally,
with an integrity check that proves it.

### Horizon 2 — Go-live *(next)*
The business stops opening Tally for daily work.

**Exit criteria**
- Invoice printing and GST-compliant billing.
- Purchase cycle end to end.
- Separate input/output GST accounts; company GSTIN set.
- Bank reconciliation.
- One full month closed in the ERP, accepted by the accountant.

### Horizon 3 — Operationally complete *(year one)*
Production costing, HR/payroll and management reporting in daily use; the
assistant lowers the barrier for warehouse and billing staff; the business can
answer "what did we earn per product and per distributor?" from the system.

---

## Guardrails

These hold regardless of goal or deadline:

1. **Self-healing** — store immutable facts, derive everything else, rebuild on
   drift.
2. **User-error-proofing** — least privilege; the database is the guarantee.
3. **Multilingual** — no hardcoded user-facing text.
4. **Money is `Decimal`** at the declared precision — never `float`.
5. **Submitted documents are immutable** — cancel and amend, never edit.
6. **`verify_integrity` must be green** before any task is called done.
7. **The code is the source of truth** — when docs and code disagree, the code
   wins, and the doc is fixed.

---

## Progress snapshot

| Goal | Status | Evidence |
|---|---|---|
| G1 Books that cannot lie | ✅ | 294,133 GL entries, 0 drifts, ₹0.00 vs Tally |
| G2 One operational picture | ⚠️ | Dashboard + stock + reporting live; invoice layer empty |
| G3 Replace Tally daily | ❌ | Cannot print or issue an invoice yet |
| G4 Correct by construction | ✅ | DB constraints, RBAC, audit, atomic posting |
| G5 Usable by staff | ⚠️ | 13-language infrastructure; assistant not built |
| G6 Own the stack | ✅ | Django/Postgres/React; full Tally archive verified |
| G7 Bigger than Tally | ⚠️ | Production/HR modelled; print, bank rec, e-way missing |

---

## How this document is maintained

- Review it at the start of each phase and whenever a goal is met or dropped.
- Update the **Progress snapshot** with evidence, not optimism.
- A new goal needs a **"done when"** test that can be checked against the
  running system; otherwise it is a wish, not a goal.
- If a task in [checklist.md](checklist.md) serves no goal here, question the
  task — not the goal.

---

## Change log

| Date | Change |
|---|---|
| 2026-10-29 | Initial goals, success horizons and utility map recorded after a full audit of the running system (Tally archive verified, 0 drifts, ₹0.00 reconciliation). |
