# Implementation Status

> **Audit date:** 2026-11-02
> **Base commit:** `f7479b9` (`main`), working tree clean before this audit.
> **Replaces:** the 2026-10-29 audit (`b427a08`). Most of its "missing" rows were built
> on 2026-10-31 (Phase 10, see [planning/README.md](planning/README.md)).
>
> Point-in-time snapshot. **The code is the source of truth.** Re-run the checks in
> [§8](#8-how-to-re-verify) before relying on a row.

---

## 1. Headline

The software side of go-live is nearly done. 65 of 77 Phase 10 cards are ticked (L-4
was reopened in this audit), and
printing, dispatch-to-bill, collections, expenses, GST position, alerts, bank
reconciliation, period close, opening bills and the Hindi assistant are all in the code.

What stops the business from running on it today is **not missing modules**. It is:

1. **CI had never passed** (4 of 4 runs on `main` failed, the same 16 tests each time).
   Fixed in the commit that adds this file, see §2.
2. **Owner and accountant inputs** that only the business can give (§4).
3. **Two code cards**: G-3 (opening-bill corrections) and L-4 (Hindi backend messages,
   ticked but never done). Plus a short list of production-hardening items (§5).

---

## 2. Health checks run on 2026-11-02

| Check | Before | After this commit |
|---|---|---|
| `pytest` (SQLite, as CI runs it) | 16 failed, 1651 passed | All pass, except 3 backup-script tests that skip when PowerShell is not installed. GitHub's Ubuntu runner has `pwsh`, so they run in CI |
| `manage.py check`, `makemigrations --check` | clean | clean |
| `verify_integrity` on an empty DB | green | green |
| `verify_integrity` after `seed_demo` | **red**: 1.2e-14 "manufacturing value drift" | green |
| `npm run lint` (`tsc`) and `npm run build` | pass (main chunk > 500 kB) | unchanged |
| GitHub Actions on `main` | **4/4 runs failed** | expected green on next push |

**What the 16 failures were, and what changed:**

| Failure | Real problem? | Fix |
|---|---|---|
| 8 ageing-report tests | **Yes, a user-facing bug.** A due date before the posting date reached the DB CHECK and returned **HTTP 500** instead of a readable error. The test data also broke the rule | `accounts.services.terms.validate_due_date` now returns a 400 field error on sales and purchase invoices. Test data fixed. New `DueDateValidationTests` |
| 2 ageing assertions | No: stale after M-1 (90-day customer default) | Expectations updated: an invoice with no due date is due posting + 90 days |
| `test_no_taxes_means_net…` | **Yes.** `validate_before_submit` compared a date with a string when an invoice was built in code (imports, scripts) | Both invoice models normalise dates before comparing |
| 3 seed/dashboard integrity tests | Partly: on SQLite, `Sum()` returns floats, giving 1.2e-14 drift on a true zero | Manufacturing check now rounds to 2 dp, as the Bin check already did |
| Constraint-count test | No: stale after O-1 (dispatch status) and E-2 (expense category) | Counts updated, with the new constraint named |
| 3 backup-runbook tests | **Yes, for a Linux server.** Tests (and the docs) assumed Windows `powershell`. The default backup folder `..\backups` becomes a literal backslash filename on Linux | Tests use `pwsh` or `powershell`, whichever exists. Scripts use `../backups` |

---

## 3. Progress against goals ([goals.md](goals.md))

| Goal | Status | Evidence, and what is left |
|---|---|---|
| G1 Books that cannot lie | ✅ | Immutable GL/SLE, `verify_integrity` green, nightly run by `run_scheduler` with an instant alert on failure. Since 2026-11-02 it also checks #4 (no order billed beyond what was ordered) and #6 (every live GL/SLE row belongs to a submitted document; a missing document is a warning). #5 and #7 are left to DB constraints (UNIQUE names, submit/cancel guards) |
| G2 One operational picture | ✅ mostly | Home page with figures and needs-attention panels, stock view, Money to Collect, Bills to Pay, GST position. **Gap:** item-wise and distributor-wise gross profit (old C4) is still not a report |
| G3 Replace Tally for daily work | ⚠️ code done, not proven | Print (`/print/<kind>/<id>`, browser Save as PDF), dispatch tick creates the bill, purchase bill with charges, returns, GSTR-1/3B. **Left:** O-7 (real Tally round-trip test), and the accountant accepting one month (G-8) |
| G4 Correct by construction | ✅ | DB CHECKs (non-negative Bin, due ≥ posting, dispatch status, expense category…), audit log, atomic posting. One access level by owner decision D1 |
| G5 Usable by staff | ⚠️ | 13 locales complete in the frontend; Hindi assistant (voice, bill photo, proposals) built. Backend messages in Hindi since 2026-11-02 (other languages still English drafts). **Left:** L-5 Hindi review, H-7 evaluation sentences, pilot with 2–3 staff |
| G6 Own the stack | ✅ | Django/DRF/Postgres + React; Tally export with openings excluded (G-5) |
| G7 Bigger than Tally | ⚠️ | Production stages (P-3/P-4/P-6), bank reconciliation, payroll and TDS built. Not yet in daily use |
| G8 Know where the money goes | ⚠️ code done | Expense categories, Paid/Due, monthly repeats, proof required, pending-bill reminders. **Left:** E-9 category list (owner), N-10 switch on email |

---

## 4. Blocked on the business (only you can unblock these)

Go-live is gated on these, not on code. Ordered by how much they block.

| # | What is needed | From | Unblocks |
|---|---|---|---|
| 1 | **Cutover figures**: party balances and stock count on the cutover date (the ERP ledger ends 2026-10-19) | Accountant + factory | G-2 run, G-6 load, G-8 |
| 2 | **Q6**: sending email account and each staff member's address | Owner | N-10: no reminder or overdue alert reaches anyone until this is set (`EMAIL_BACKEND` defaults to console) |
| 3 | **Q1**: has turnover passed ₹5 crore? Do consignments cross the e-way bill limit? | Accountant | Whether e-invoice / e-way bill becomes mandatory work (O-7) |
| 4 | **Q4**: standard supplier payment days | Owner | Due dates and "Bills to Pay" for suppliers |
| 5 | **Q3**: is Ready stock counted in packets or kg? | Owner | P-7 item and recipe clean-up |
| 6 | **Q5**: which staff are above the income-tax limit | Accountant | Real TDS figures (T-6/T-7 are built) |
| 7 | E-9 expense categories, T-8 tax rule check, L-5 Hindi review, H-7 test sentences, A-7 remove demo company | Owner | Respective cards |
| 8 | Physical count of the 7 negative-stock items; decision on the 20 ledgers that change type between years | Factory, accountant | Clean opening stock and comparable P&L |

---

## 5. Remaining gaps in the software

### 5.1 Before go-live

| Gap | Why it matters | Size |
|---|---|---|
| ~~L-4 Hindi backend catalog~~ **Done 2026-11-02:** all 1,014 server messages in Hindi with staff words (बिल, स्टॉक, बाकी, उधार); placeholders machine-checked; API returns Hindi for `Accept-Language: hi`; the Dockerfile no longer hides compile errors. The other 11 languages remain English drafts (L-6). **Next:** L-5, a staff member reads the 50 most common messages | — | Done |
| ~~G-3 `--from-csv` corrections~~ **Done 2026-11-02** (see `planning/09-go-live.md` G-3 for the accountant's loop) | — | Done |
| ~~Production defaults fail open~~ **Done 2026-11-02:** `DEBUG` defaults to off; the backend refuses to start with a missing/example/short `SECRET_KEY`; secure cookies, HSTS and proxy-SSL settings when `DEBUG=False`; API schema needs sign-in; `ADMIN_URL` configurable; compose reads secrets from the environment and binds Postgres to localhost; refresh tokens single-use and revocable (sign-out, sign-out everywhere, revoked on deactivation and password change) | — | Done |
| ~~Backups are documented, not running~~ **Done 2026-11-02:** `manage.py backup_db` (pg_dump or SQLite online backup, atomic file, retention that never deletes the newest file, optional rclone off-site copy) runs nightly at 01:30 from `run_scheduler`, records a `backup` health check and emails the `backup_failed` alert on failure. **Owner action:** set `BACKUP_RCLONE_REMOTE` so a copy leaves the server | — | Done |
| ~~`psycopg` pin~~ **Done 2026-11-02:** raised to 3.2.13, which has wheels for current Python | — | Done |
| H-6 is committed (`1d15f6b`, `79bfc22`) but not ticked | The checklist under-reports progress | Verify acceptance, then tick |

### 5.2 First months

| Gap | Why it matters |
|---|---|
| Gross profit per item and per distributor (old C4) | The stated Horizon 3 question: "what did we earn per product and per distributor?" |
| ~~`verify_integrity` invariants #4–#7~~ **#4 and #6 done 2026-11-02** (`core/services/ledger_integrity.py`). Run it once on the real books: orphan *warnings* there show which imported vouchers have no document | — |
| No frontend tests; main JS chunk > 500 kB | Factory and order staff use phones; a big first load is slow on mobile data |
| CSV/Excel import and export of masters and price lists (old C5) | Bulk price updates by hand are where errors creep in |
| True FIFO valuation (Moving Average used for FIFO items) | Only matters if the accountant values stock FIFO |

### 5.3 Deliberately out of scope (owner decisions)

Delivery Note / Purchase Receipt (D4), lot tracking and Quality Inspection (D2),
historical invoice mirror (A1, replaced by opening bills), multi-company work (W0),
PF/ESI/PT/LWF until the business crosses those thresholds, and the
[goals.md non-goals](goals.md#non-goals).

---

## 6. How the project is being managed: gaps

| Gap | Effect | Suggested rule |
|---|---|---|
| CI ran only on pushes to `main`/`master`/`dev`, and work merged from agent worktrees without it | 16 failures accumulated across many "done" cards | Now runs on every branch push (this commit). Rule: do not merge or tick a card while CI is red |
| Too many overlapping status docs: `goals.md`, `gap_plan.md`, `improvements.md`, `checklist.md`, `planning/`, this file | Each drifts. `planning/README.md` still listed done cards as remaining; `goals.md` said "cannot print" after printing shipped | Keep three: `goals.md` (why), `checklist.md` (ticks), this file (audit). Mark the others as historical |
| Open questions live in a table nobody owns | Q1, Q3–Q6 have been open since 2026-10-31 and are the actual go-live blockers | Put a date and an owner on each, and review weekly |
| ~~Alert emails were raw keys~~ ("ERP Alert: alert.x / Rule: …", English only) **Done 2026-11-02:** each alert and digest email is a sentence in the recipient's profile language with a full link (`notifications/messages.py`) | — | Done |
| Pilot not scheduled | Built features are not used yet (production, HR, assistant) | Pick a cutover date and run one week of parallel entry with 2–3 staff |

---

## 7. Recommended order

1. Push the CI fix and confirm GitHub Actions is green.
2. Owner and accountant: answer Q6 and Q4, pick a cutover date, and arrange the stock count (§4).
3. Build G-3 and L-4, harden production settings, and automate backups with an off-site copy (§5.1).
4. One-week pilot in parallel with Tally, then G-8 cutover once the accountant signs off.
5. After go-live: gross-profit report, the remaining integrity invariants, frontend performance.

---

## 8. How to re-verify

```bash
cd backend
pytest -q
python manage.py check && python manage.py makemigrations --check --dry-run
python manage.py verify_integrity
```

```bash
cd frontend
npm ci && npm run lint && npm run build
```

```bash
grep -c '^- \[ \] [A-Z]-[0-9]' checklist.md
```
