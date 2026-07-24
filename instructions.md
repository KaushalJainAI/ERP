# Implementer Instructions

**Audience:** the AI model that writes the code. Read this completely before
touching any file. It tells you *how* to work. The source of truth is the
**code** (models, migrations, tests) — see [CLAUDE.md](CLAUDE.md). [plan.md](plan.md)
is *historical* design background for the *what/why*, not authoritative. The
ordered tasks are in [checklist.md](checklist.md).

---

## 0. The mission — replicate ERPNext, in our stack

Our north star is **functional parity with ERPNext** for the MVP modules
(accounts, stock, selling, buying), rebuilt on Django + DRF + React. First we
*clone* the behaviour; later we *modify* it for our needs. Concretely:

- The **behaviour, business rules, document flows, and field meanings** must
  match ERPNext. When unsure how something should work, the answer is "how does
  ERPNext do it?" — open the reference (§4) and match it.
- We do **not** copy ERPNext's code, frontend, or framework. We reimplement the
  same functionality on our own models (see `backend/*/models.py`).
- If ERPNext does something that conflicts with a rule in this file or our
  existing code/tests, **our rules win** — stop and ask before diverging, and
  record deliberate deviations in `backend/docs/erpnext_divergences.md`.

Keep this goal in mind on every task: *"Does this reproduce what ERPNext does
for this feature?"*

---

## 1. The golden rules (never break these)

1. **The code is the source of truth.** The schema is whatever the Django models
   and migrations define; behaviour is whatever the tests assert. `plan.md` is
   background only — when it and the code disagree, the code wins. Don't redesign
   existing schema without cause; when you do change it, do it via a migration and
   update the affected tests.
2. **Never guess.** If a task is genuinely ambiguous, **stop and ask a human.**
   A wrong guess is worse than a question.
3. **One task at a time.** Take the next unchecked item in
   [checklist.md](checklist.md), do only that, verify it, tick it, commit. Do not
   batch or skip ahead.
4. **The three mandates are law:** self-healing, user-error-proofing,
   multilingual. Every model and endpoint must honour them.
5. **Immutable ledgers are sacred.** `GLEntry` and `StockLedgerEntry` are
   append-only. Never `UPDATE` or hard-`DELETE` them, and never expose write
   endpoints for them. Corrections happen by *reversing* entries only.
6. **Money is `Decimal`, never `float`.** Use the field precisions on the models
   (money `(18,2)`, qty `(18,3)`, rate/valuation `(18,6)`).
7. **All document writes are atomic.** Submit/cancel wrap every write in
   `transaction.atomic()`.
8. **The database enforces correctness**, not just Python. Add `CHECK`, `UNIQUE`,
   and `ON DELETE` constraints as real migrations.
9. **Verify before you tick.** Run the verification for the task (§5). If it
   fails, fix it before moving on. Never tick an unverified box.
10. **erpnext/ is read-only.** Reference it; never edit, import, or ship it.

---

## 2. Environment & commands

Backend (from `backend/`):
```bash
python -m venv venv && source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py seed_defaults          # idempotent base data
python manage.py runserver
python manage.py verify_integrity       # must pass before ticking any box
pytest                                   # run all tests
pytest path/to/test_file.py::test_name   # run one test
ruff check . && black --check . && mypy .
```

Frontend (from `frontend/`):
```bash
npm install
npm run dev
npm run build
npm run lint
```

> If a command or tool named above does not exist yet, the task that creates it
> is earlier in the checklist — do that first, don't invent a workaround.

---

## 3. How to build a new document type (the recipe)

Follow these steps *in order* every time you add a submittable document (Sales
Invoice, Stock Entry, Sales Order, …). This is the pattern; do not deviate.

1. **Reference ERPNext.** Open the matching DocType JSON + controller (§4).
   Check whether the fields you need already exist on our models; `plan.md §3` can
   help you understand the original intent. If a field ERPNext needs for MVP
   behaviour is missing, **ask** — don't add it silently.
2. **Model.** Subclass `core.SubmittableDocument`. Add the columns with the
   correct types, `CHECK`/`UNIQUE`/`ON DELETE` constraints, and indexes (money
   `(18,2)`, qty `(18,3)`, rate/valuation `(18,6)`). Child rows are a separate
   model with `ForeignKey(..., CASCADE)` to the parent only.
3. **Hooks.** Implement `validate_before_submit()` (raise `ValidationError` on
   anything unpostable) and `on_submit()`/`on_cancel()` that call the posting
   service (`accounts/services/gl.py`, `stock/services/ledger.py`) — never write
   ledger rows inline.
4. **Migration.** `makemigrations`, read the generated file, confirm the DB
   constraints are present, `migrate`.
5. **Serializer.** `ModelViewSet` serializer. Mark derived fields
   (`outstanding_amount`, `delivered_qty`, `docstatus`, ledger totals) read-only.
   Apply the translation mixin for user-facing text fields.
6. **ViewSet.** Use `core.mixins.SubmittableViewSet` (gives CRUD + `submit`/`cancel`).
   Enforce `RolePermission`. Register in the app's `urls.py`.
7. **Tests.** Cover: draft create; submit posts correct ledger entries; cancel
   reverses them exactly; `verify_integrity` passes after both; permission denied
   for a role lacking `can_submit`; a submitted doc cannot be edited/deleted.
8. **Verify.** Run `pytest` for the app + `python manage.py verify_integrity`.
   Both green → tick the box → commit.

Master data (Item, Customer, Account…) skips the submit/ledger steps: plain
`ModelViewSet`, but still gets DB constraints, permissions, translation, and tests.

---

## 4. Using the ERPNext reference

- DocType schema: `erpnext/erpnext/<module>/doctype/<name>/<name>.json` — field
  list, types, mandatory flags, links, child tables.
- Business logic: the sibling `<name>.py` controller — validations, on-submit
  side effects, status computation.
- Read them to understand *what the feature must do*, then implement it against
  our schema and services. Translate intent, never source code.

---

## 5. Definition of Done (per task)

A checklist item is done only when **all** apply:
- Schema (types, constraints) is correct and covered by migrations, with no extra scope.
- Migrations generated, reviewed, and applied cleanly.
- The three mandates are honoured (DB constraints present; least-privilege
  permission wired; user-facing strings translatable).
- Tests for the task pass (`pytest`), and `verify_integrity` is green.
- Lint/format/type checks pass (`ruff`, `black`, `mypy`; `npm run lint`).
- The feature reproduces the corresponding ERPNext behaviour (§0).

Only then tick the box in checklist.md and commit.

---

## 6. Coding standards

- **Backend:** PEP 8; `ruff` + `black` + `mypy` clean. Type-hint public
  functions. Keep views thin — business logic lives in `services/` and model
  hooks. Money math uses `Decimal` and `.quantize(Decimal('0.01'))`.
- **Frontend:** TypeScript strict. Data via TanStack Query. Forms render through
  `<DocForm>` from serializer metadata — do not hand-build a form when the
  generic one works. All visible strings go through `react-i18next` (no hardcoded
  English). Assets/fonts bundled locally (CSP forbids CDNs).
- **Commits:** one checklist item per commit; message states the item. Never
  commit secrets; use `.env` (git-ignored) from `.env.example`.
- **Migrations:** forward-only; never edit an applied migration — add a new one.

---

## 7. Things you must never do

- Never change the established schema or architecture on your own initiative;
  propose it, and once agreed, change it via a migration + tests.
- Never write to or expose an API for `GLEntry` / `StockLedgerEntry` / `Bin`
  outside the posting/repost services.
- Never hard-delete transactional data; never allow editing a submitted document.
- Never trust client input — re-validate everything server-side.
- Never grant a role more permission than the task requires.
- Never hardcode user-facing text; never load remote fonts/scripts.
- Never copy ERPNext code into our tree.
- Never tick a checklist box you haven't verified.
- When in doubt: **stop and ask.**
