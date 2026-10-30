# gap_plan.md — the plan to close the gaps

> **Status:** living plan. Started 2026-10-29.
>
> **What this is:** the *sequencing and decision layer* over the existing gap
> docs. It does not replace them:
> - [goals.md](goals.md) — why the project exists, success criteria, non-goals.
> - [checklist.md](checklist.md) — the atomic task list; one commit per task.
> - [backend/docs/erpnext_gap_analysis.md](backend/docs/erpnext_gap_analysis.md) — feature gaps vs ERPNext (P1–P3).
> - [backend/docs/tally_data_gaps.md](backend/docs/tally_data_gaps.md) — what Tally still holds (T1–T6) and model gaps (G1–G8).
>
> **Rule:** the code is the source of truth. Tick a task only when its acceptance
> gate passes on the running system.

---

## 0. Where we are

The books are done: 10 years of Tally history in PostgreSQL, 54,501 vouchers,
294,133 GL entries, 0 drifts, ₹0.00 reconciliation. The **record layer is
trustworthy**. The **operating layer is not yet live**: the ERP cannot print or
issue a GST invoice, has no bank reconciliation or period close, and has no
invoice documents for history.

The gap is not "more modules". It is the short list that stops a normal working
day from happening in the ERP.

---

## 0A. Operating plan (owner decisions, 2026-10-31)

**This section wins** wherever it disagrees with the rest of this file. The current,
detailed plan is in [planning/](planning/README.md); where that differs from this
section, `planning/` wins. Added there on the same day: 90-day customer payment cycle,
expenses as a main goal, email notifications modelled on NGU, and a tax scope of only
employee TDS, GST input credit and GST payable.

### The working day

| Who | Does | The ERP does |
|---|---|---|
| Order entry | Says the order in Hindi, checks the prefilled page, approves | Creates the Sales Order |
| Factory manager | Moves batches through Grinding → Packing → Ready | Moves stock; loss is recorded |
| Factory manager | Ticks a pending order as dispatched | In one transaction: stock leaves Ready, the bill is created, the order completes |
| Factory manager | Opens Workers | Shows attendance and pay on one page |
| Accountant | Enters purchase bills, receipts, payments, expenses, tax paid | Proof is compulsory; everything queues for Tally |
| Admin | Can do all of the above | Has all three workspaces |

### Decisions

| # | Decision | Effect on the plan |
|---|---|---|
| D1 | **One access level.** All staff are trusted; no per-role permissions for now | Keep the RBAC code, seed one role that can do everything. Workspaces only choose the landing page; they are not permissions. Deliberate exception to mandate 2's least privilege: update `CLAUDE.md`, `goals.md` and `erpnext_divergences.md`. Audit log, DB constraints and submitted-document immutability stay. Portal worker logins keep having no role |
| D2 | **No lot tracking.** `stock.Batch`, `Item.has_batch_no` and expiry stay off | C3 and lot tracing dropped. "Batch" now means a production run (D3). Its number remains in production records, but is not linked to customers |
| D3 | **Three batch stages:** Grinding, Packing, Ready, plus Unprocessed stock | Built on Work Order + Stock Entry so costing, repost, `verify_integrity` and Tally export keep working. Hide Work Order vocabulary from the factory screen |
| D4 | **Nothing is tracked after dispatch** | Vehicle and transporter fields dropped unless the e-way bill answer requires them. Delivery Note (C2) dropped: dispatch and bill are one action. Purchase Receipt dropped too |
| D5 | **Collections and supplier payments are in scope**, with overdue alerts to the responsible user | New work, `planning/04` |
| D6 | **Opening balances for accounts and stock are required** | New work, `planning/09` |
| D7 | **Non-negative stock enforced in the database**, with all sensible constraints | `Bin` has no quantity CHECK today (only the posting service guards it). Card P-1 |
| D8 | **Backend messages must be translated** | Cards L-1 to L-6 |

### Stock flow

Warehouses: **Unprocessed → In Process → Ready**. The 46 current stock rows all sit
in `Main Location` and are split at the go-live count.

| Stage | Stock posting | Entered by the factory manager |
|---|---|---|
| Grinding | Material Transfer: raw moves Unprocessed → In Process | Product, quantity, raw used (suggested from the BOM) |
| Packing | None; stage change only | Nothing beyond the move |
| Ready | Manufacture: raw and packing material consumed, finished goods into Ready, loss recorded | Actual output |

Open question Q2 below can change this table.

### Constraints to enforce

- `Bin.actual_qty >= 0`, plus the running balance on `StockLedgerEntry` if it has no
  CHECK yet. The migration is safe: no negative rows exist on 2026-10-31.
- Production: quantity > 0; output ≤ input; loss ≥ 0; stages only move forward; Ready
  needs an output; a cancelled batch cannot move.
- Dispatch: dispatched ≤ ordered per line; a line cannot be dispatched twice; the
  order must be approved; insufficient Ready stock refuses the whole dispatch.
- Money: allocation ≤ outstanding; due date ≥ bill date; amounts > 0; proof present
  before a cash, bank, expense or tax voucher can be submitted.
- Opening: one opening balance per party and company; opening stock posts once.
- Alerts: unique per rule, record and day, so reminders never duplicate.

### Why overdue needs opening bills

On 2026-10-31 the books show 324 customers owing ₹5,64,22,429 and 53 suppliers owed
₹1,60,06,453, but as **party balances only**, with no bill or due date behind them
(Tally recorded just 125 bill allocations). Overdue alerts read invoices, so this
money would be invisible. [planning/09-go-live.md](planning/09-go-live.md) therefore
estimates opening bills from each party's ledger, for the accountant to confirm, with
one bill per party as the fallback. Receipts settle the oldest bill first.
These figures must be retaken at cutover: Tally entries are still being made, and
the ERP's ledger currently ends on 2026-10-19.

Opening documents must never go to Tally, which already holds these balances.
`NEVER_EXPORTED_DOCTYPES` / the excluded prefixes need to cover them, with a test.

### Superseded or moved

| Item | Now |
|---|---|
| A1, section 2 (mirror 27,575 historical invoices) | Dropped: the `legacy` tables it depends on were removed on 2026-10-30, and opening bills cover the operating need |
| C2 Delivery Note + Purchase Receipt | Dropped (D4) |
| C3 Quality Inspection | Dropped for now (D2) |
| W0 Company isolation | Remaining work deferred: one real company, trusted staff. Reopen before adding a second company |
| G1 Assistant (P3) | Raised to P0: it is how orders enter the system |
| A4 e-Way Bill / e-Invoice | Skipped, **pending Q1** |

### Open questions

| # | Question | Owner | Blocks |
|---|---|---|---|
| – | Tracked in [planning/README.md](planning/README.md) | – | – |

Partial dispatch is allowed: bill what was sent and leave the rest pending.

---

## 1. The gap register

Severity: **P0** = cannot go live without it · **P1** = needed in the first
month · **P2** = next quarter · **P3** = later.

| ID | Gap | Why it matters | Sev | Effort | Wave |
|---|---|---|---|---|---|
| **W0** | **Company isolation** — no membership model, unfiltered lists, company accepted from the client | Multi-company data would leak or mix; wrong-company postings are possible | **P0** | 6–9 d | 0 |
| A1 | Historical invoice documents (27,575 sales/purchase vouchers) | Invoice-level history, registers, party drill-down; Selling/Buying modules empty today | P1 | 4–6 d | 1 |
| A2 | Print formats: GST Tax Invoice, Delivery Challan, PO, Receipt | Cannot issue or send a bill | **P0** | 4–5 d | 1 |
| A3 | Separate Input/Output GST accounts + templates for NIDHI GRAH UDYOG; company GSTIN/PAN/address | GST reports cannot split Tally-era tax; compliant invoices need the company's own details | **P0** | 1–2 d | 1 |
| A4 | e-Way Bill / e-Invoice | Mandatory above thresholds — but Tally currently does statutory filing | P3 | — | skipped |
| A5 | Purchase invoice extra charges (freight, hamali, loading) into stock valuation | True landed cost of raw material | P1 | 3 d | 3 |
| B1 | Bank Account + Bank Reconciliation (statement import, clearance dates) | Monthly bank reconciliation; cheque tracing | **P0** | 4–5 d | 2 |
| B2 | Period Closing Voucher (P&L → retained earnings) | Year-end close inside the ERP, not by importer JE | P1 | 2 d | 2 |
| B3 | Payment Reconciliation tool (unallocated receipts → invoices) | Receipts often arrive before allocation | P1 | 2 d | 2 |
| B4 | TDS (194Q / 194C / 194J) | Applies at turnover/supplier thresholds; accountant to confirm | P2 | 3 d | 4 |
| C1 | Stock Reconciliation + physical count for the 7 negative-stock items | Opening stock truth; negative stock is currently hidden | **P0** | 1–2 d | 2 |
| C2 | Delivery Note + Purchase Receipt (dispatch/GRN separate from bill) | Goods arrive before the bill; standard flow | P1 | 4–5 d | 3 |
| C3 | Quality Inspection (moisture, colour, foreign matter) | FSSAI / lotted raw-material traceability | P2 | 4 d | 4 |
| C4 | Gross Profit and item-wise sales reports | Margin per product/distributor | P1 | 2 d | 3 |
| C5 | Data import/export (CSV/Excel) | Bulk price updates, new parties | P2 | 3 d | 3 |
| C6 | Contact (people, phones, WhatsApp) | Only addresses exist today | P2 | 2 d | 3 |
| D1 | HR screens + locale files | Payroll API exists; staff cannot use it | P1 | 4–5 d | 4 |
| D2 | Leave (types, allocation, application) | Monthly staff; daily wage absence is unpaid | P2 | 3 d | 4 |
| D3 | Salary/wage registers | Statutory and management reporting | P1 | 2 d | 4 |
| D4 | MP minimum wage + wage-definition rule | Compliance | P2 | 2 d | 4 |
| D5 | PF / ESI / PT / LWF (flags, returns) | Not applicable today; switch-on later | P3 | 5–7 d | later |
| D6 | Piece-rate linked to Work Orders | Labour cost per batch | P3 | 5 d | later |
| E1 | Tally Day Book + All Masters XML (T1–T4) | Stock-journal lines, bank instruments, invoice extras, company master | P1 | 5–8 d + external | 5 |
| E2 | Reclassify 20 ledgers that change type between years | Profit differs in 3 years; accountant decision | P1 | 1 d | 5 |
| E3 | Translate new `lg.*` and JE labels into 12 locales | Mandate: no hardcoded user-facing text | P1 | 2 d | 5 |
| E4 | Company GSTIN/PAN/address typed in | Invoices and GST reports need it | **P0** | 0.5 d | 1 |
| F1 | Backup/restore drill + scheduled Postgres dumps | Disaster recovery | **P0** | 1 d | 2 |
| F2 | Integrity alerting (cron `verify_integrity`) | Drift should page someone, not wait for a human | P1 | 1 d | 2 |
| G1 | Assistant (voice + chat, no write tools) | Adoption for warehouse/billing staff | P3 | 15–20 d | 6 |

**Deliberately skipped** (user decisions, 2026-10-28): e-Way Bill/e-Invoice
(Tally files), UOM conversion per item (operators do carton maths).

---

## 2. The decision that gates Wave 1

### Historical invoices: how do we backfill 27,575 Tally vouchers?

| Option | What it means | Verdict |
|---|---|---|
| **A. Mirror invoices** | Create `SalesInvoice`/`PurchaseInvoice` rows at `SUBMITTED` with `is_historical=True` and a link to the existing `TLY-*` Journal Entry. **No GL/SLE posting.** Read-only, excluded from ageing and `verify_integrity`'s ledger check. | **Recommended** |
| B. Real invoices | Reverse the `TLY-*` JEs and re-post every invoice through the normal posting service. | Rejected: changes closed years, GST split is unreconstructable (combined ledgers), 27,575 docs of reconciliation risk |
| C. Read-only Tally invoice register | Group `TallyItemLine` by voucher in a report; no invoice rows. | Fallback only: Selling/Buying stay empty |

**Recommendation: A.** It gives the modules real invoice documents, preserves the
exact ledger, and is reversible (delete the historical rows and re-import). It is
an explicit, documented exception to "every submitted invoice posts GL" — the
exception is the whole point, and it must be enforced by a flag plus tests, not
by convention.

Rules for the mirror:
- `is_historical = True`, `legacy_journal_entry` FK, `docstatus = SUBMITTED`.
- Created **only** by the import command, directly (never through `.submit()`).
- `outstanding_amount = 0`, `status = "Historical"` (or Paid); excluded from
  ageing, payment allocation and `check_integrity`'s live-invoice loop.
- Cancel/amend/delete blocked; API read-only; print marked
  "Historical — reproduced from Tally".
- GST is **not** split (Tally used combined ledgers). Historical invoices show
  pre-tax item lines and the voucher total; GST reports start at go-live.
- The backfill command is idempotent and has a `--dry-run` that reports clean vs
  unmatched vouchers before writing.

**Outstanding policy (superseded 2026-10-31, section 0A):** party balances are
exact. Each party gets one opening bill for its balance at cutover, and receipts
settle the oldest bill first. Still do not invent bill-wise allocations — Tally
recorded only 125.

---

## 3. The waves

### Wave 0 — Company isolation (foundation)

**Why first:** every list, create, report and import below must be scoped to one
company. Retrofitting this after invoice backfill is more expensive and risks
cross-company data.

**Model**
- `UserCompany(user, company, is_default, is_active)` with `UNIQUE(user, company)`
  and one default per user.
- Seed membership for existing users; superusers see all companies.

**Request context**
- `X-Company-ID` header, validated against membership; fall back to the user's
  default; 403 if none.
- A `CompanyScopedMixin` on every company-owned viewset: filter queries, inject
  `company` on create, make it read-only, 404 cross-company detail.
- Reports and the dashboard use the active company; a `?company=` param is
  ignored for normal users (allowed for superusers).
- `GET /api/core/companies/` returns only the caller's companies.

**Frontend**
- Company gate after login when the user has more than one company; auto-enter
  when there is exactly one.
- Read-only company badge in the header; "Switch company" returns to the gate,
  clears the React Query cache and warns on a dirty form.
- Forms no longer choose a company; the server supplies it.
- Deep links without an active company redirect to the gate.

**Acceptance**
- A member of A cannot list/read/create/update B's records with a tampered
  header or `?company=`.
- Dashboard and every report return only the active company.
- One-company users never see the gate.
- `pytest`, `verify_integrity`, `lint`, `build` green.

**Estimate:** 6–9 dev-days.

---

### Wave 1 — Can bill

**Goal:** issue a real GST invoice from the ERP.

1. **A2 Print formats.** Start with browser print (HTML + print CSS), matching the
   existing register print pattern. GST Tax Invoice, Delivery Challan, Purchase
   Order, Payment Receipt. Server-side PDF (`WeasyPrint`) only when email/WhatsApp
   sending is wired; browser print handles Indic scripts better and has no native
   dependency.
2. **A3 GST setup.** Run `seed_gst` for NIDHI GRAH UDYOG; verify nine tagged
   accounts and six templates; set company GSTIN, state, PAN, address.
3. **A1 Invoice backfill.** Dry-run command → review counts → write mirror
   invoices → prove GL totals unchanged.
4. **E4 Company master.** Type GSTIN/PAN/address; validate GSTIN checksum.

**Acceptance**
- A draft invoice can be created, submitted, printed, and shows the correct GST
  split and company details.
- The GL total and trial balance are byte-for-byte unchanged by the backfill.
- Historical invoice count and value reconcile to `TallyItemLine`.
- The accountant accepts one printed invoice.

**Estimate:** 8–12 dev-days.

---

### Wave 2 — Can close the month

1. **B1 Bank Account + reconciliation.** Statement import (CSV), match/unmatch,
   clearance date, difference report.
2. **B2 Period Closing Voucher.** P&L → retained earnings; year lock stays.
3. **B3 Payment reconciliation.** Match unallocated receipts to invoices.
4. **C1 Stock count.** Physical count + Stock Reconciliation for the 7 negative
   items; then no negative stock.
5. **F1/F2 Backup + integrity alerting.** Nightly `pg_dump`, restore drill,
   cron `verify_integrity` with a non-zero exit alert.

**Acceptance**
- One month's bank statement reconciles to zero difference.
- A closed year still balances; P&L is zero and retained earnings carries it.
- No negative stock remains.
- A restore from the nightly dump passes `verify_integrity`.

**Estimate:** 9–12 dev-days.

---

### Wave 3 — Can run operations

1. **C2 Delivery Note + Purchase Receipt.**
2. **A5 Landed cost on purchase invoices.**
3. **C4 Gross profit / item-wise sales.**
4. **C5 Data import/export.**
5. **C6 Contacts.**

**Acceptance:** order → dispatch → invoice → receipt and PO → GRN → bill →
payment run end to end; gross profit per item/distributor is answerable.

**Estimate:** 12–15 dev-days.

---

### Wave 4 — Can run payroll

1. **D1 HR screens + locale files.**
2. **D2 Leave.**
3. **D3 Salary/wage registers.**
4. **D4 MP minimum wage + wage definition.**
5. **B4 TDS** (if the accountant confirms).
6. **C3 Quality Inspection.**

**Acceptance:** one payroll run for a month, paid and posted, with a wage
register the accountant accepts.

**Estimate:** 12–16 dev-days.

---

### Wave 5 — Can retire Tally

1. **E1 Tally Day Book + All Masters XML.** External dependency: sender export
   (`Desktop\Tally\SENDER_REQUEST.md`). Extend the importer; close T1–T4.
2. **E2 Ledger reclassification** with the accountant.
3. **E3 Translation pass.**
4. **Parallel run:** one full month in the ERP with Tally kept read-only.

**Acceptance:** one month closed in the ERP with no Tally data entry; the
accountant files GST from the ERP; the Tally archive is cold backup only.

**Estimate:** 8–12 dev-days + external wait.

---

### Wave 6 — Assistant (later)

Phase 8 in `checklist.md`. No write tools; the user always presses Save. Only
start after Waves 0–2 are stable — adoption tooling on an unbillable system is
premature.

---

## 4. Critical path

```
Wave 0 (company isolation)
   └─> Wave 1 (GST setup + print + invoice backfill)
          └─> Wave 2 (bank rec + period close + stock count + backup)
                 └─> GO-LIVE (parallel month)
                        ├─> Wave 3 (operations)
                        ├─> Wave 4 (payroll)
                        └─> Wave 5 (Tally XML / reclass / translations)
                               └─> Wave 6 (assistant)
```

Waves 3 and 4 can run in parallel with Wave 5 once Wave 2 is stable. Wave 5's
Tally XML is the only external blocker, and it is **not** on the billing critical
path.

**Calendar view (one developer + agent):**

| Week | Work |
|---|---|
| 1 | Wave 0 backend + frontend + tests |
| 2–3 | Wave 1 print, GST, company data, invoice backfill |
| 4–5 | Wave 2 bank rec, period close, stock count, backup drill |
| 6 | Go-live rehearsal + one parallel month starts |
| 7–9 | Wave 3 operations; Wave 5 XML when the sender delivers |
| 10–12 | Wave 4 payroll; Wave 5 close-out |
| Later | Wave 6 assistant |

Estimates are effort, not promises. External waits (Tally export, accountant,
physical count) are the usual overrun.

---

## 5. External dependencies and owners

| Dependency | Owner | Needed by |
|---|---|---|
| Company GSTIN / PAN / address | Owner | Wave 1 |
| Accountant sign-off on invoice format | Accountant | Wave 1 |
| Decision on mirror vs real invoices | Owner | Wave 1 |
| Physical count of 7 negative-stock items | Warehouse | Wave 2 |
| Bank statements (CSV) | Accountant | Wave 2 |
| Tally Day Book + All Masters XML | Licensed Tally operator | Wave 5 |
| Ledger reclassification decision | Accountant | Wave 5 |
| MP minimum-wage rates | HR | Wave 4 |

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Double posting during invoice backfill | GL totals hash before/after; backfill writes no GL; `verify_integrity` must stay green |
| Historical GST cannot be split | Do not promise GST history; mark historical invoices; GST reports start at go-live |
| Bill-wise allocation is incomplete | Party-level opening balances; zero outstanding on historical invoices; no invented allocations |
| Wrong-company posting | Wave 0 server-side scoping + tampered-header tests; company field read-only |
| Tally XML delayed | Billing does not depend on it; keep the archive as cold backup; close T1–T4 later |
| Print format rejected by CA | Paper proof before go-live; CA signs the format |
| Scope creep | `goals.md` non-goals; e-way/UOM already skipped |
| Single developer bottleneck | One wave at a time; checklist tasks atomic; no parallel schema churn |

---

## 7. Go-live definition of done

The business stops opening Tally for daily work when **all** are true:

- [ ] A GST invoice can be created, submitted and printed with the correct
      company GSTIN, HSN, tax split and totals.
- [ ] Purchase invoices can be entered and paid; returns work both ways.
- [ ] Party outstanding and ageing are correct, including opening bills.
- [ ] Opening bills and opening stock match the cutover figures, and neither was
      exported to Tally.
- [ ] One real order has gone from Hindi voice to dispatch, bill and Tally import.
- [ ] Pending order, critical stock, procurement and overdue alerts reach the
      assigned user.
- [ ] Bank reconciliation for one full month is zero-difference.
- [ ] The year-end close posts P&L to retained earnings; trial balance still
      balances.
- [ ] No negative stock; the 7 items are physically counted.
- [ ] `verify_integrity` is green; the nightly backup restores.
- [ ] One full month closed in the ERP and accepted by the accountant.

`goals.md` G3 is met only then. Until then, the ERP is a trustworthy record and
analysis layer.

---

## 8. First ten working days

> Superseded 2026-10-31: follow [checklist.md](checklist.md) Phase 10. Kept for history.

| Day | Work |
|---|---|
| 1 | `UserCompany` model + migration + admin; membership seed; company endpoint |
| 2 | `CompanyScopedMixin`; scope accounts, selling, buying, stock viewsets |
| 3 | Scope manufacturing, HR, legacy, tally-export, addresses; reports/dashboard |
| 4 | Backend tests: tampered header, cross-company list/detail/create, reports |
| 5 | Frontend company context + gate + header switch + cache clear |
| 6 | Remove company pickers from forms; dashboard cleanup; i18n strings |
| 7 | Full test/lint/build; fix regressions; document the isolation rules |
| 8 | Wave 1: GST accounts/templates for the real company; company GSTIN/PAN/address |
| 9 | Wave 1: invoice print format (GST Tax Invoice) + paper proof |
| 10 | Wave 1: invoice backfill dry-run; review counts with the owner |

---

## Change log

| Date | Change |
|---|---|
| 2026-10-29 | Plan created; Wave 0 (company isolation) started as the foundation. |
| 2026-10-30 | Added Phase 9: Mobile UI Overhaul (compact 2-column cards, Top/Bottom navigation bars) & NGU Chatbot Experience Parity (bottom-sheet, suggestion chips, formatted bubbles, interactive proposal cards). |
| 2026-10-31 | Added section 0A and checklist Phase 10 from the owner's real workflow: one access level, no lot tracking, three batch stages, collections, supplier payments, opening balances, translated server messages. Dropped historical invoices, Delivery Note, Purchase Receipt and Quality Inspection; deferred the rest of company isolation; raised the assistant to P0. |
| 2026-10-31 | Moved the detailed plan to `planning/` with implementer guide, task cards and a subagent prompt; checklist Phase 10 now lists those cards. |
