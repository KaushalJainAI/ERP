# 0. Implementer guide

**Read this completely before touching any file.** It is written for an agent that has
never seen this repository. Follow it literally. When something here does not match
the code, stop and report; do not guess.

## How the plan is organised

- Each document `01` to `09` holds a design followed by **task cards**.
- Card IDs show their workstream: `A` access, `O` orders, `P` production, `M` money,
  `E` expenses, `T` taxes, `N` notifications, `H` Hindi agent, `L` language, `G` go-live.
- Do one card at a time, in order, and only when its **Depends** cards are done.
- Cards marked **OWNER** need a human decision or real data. Do not do them; report them.
- Cards marked **BLOCKED Qn** wait for that question in [README.md](README.md). If the
  answer is not recorded there, skip the card and report it.

## Project in one paragraph

Django + DRF + React modular monolith with one Postgres database. Documents inherit
`core.models.SubmittableDocument` and move Draft (0) → Submitted (1) → Cancelled (2). On
submit, they post immutable ledger rows (`GLEntry`, `StockLedgerEntry`) through services.
Balances such as `Bin` and invoice outstanding are derived from those ledgers. The React
frontend renders most screens from config in `frontend/src/resources/*.ts`.

## Absolute rules

1. **Never touch the real database.** `backend/.env` points at the real books on port
   5433. Do not run `migrate`, `seed_defaults`, `seed_demo`, data-changing shell commands
   or any write against it. Only `makemigrations` and tests are allowed. Real data
   changes are done by a human through OWNER cards.
2. **Never write `GLEntry`, `StockLedgerEntry` or `Bin` directly.** Use
   `accounts.services.gl`, `stock.services.ledger` or a document's `submit()` and `cancel()`.
3. **Never use `float` for money or quantity.** Use `Decimal`: money `(18,2)`, quantity
   `(18,3)`, rate and valuation `(18,6)`.
4. **Never edit a submitted document in place.** Derived columns are written with
   `QuerySet.update()`. Corrections are cancel and amend.
5. **All database-level rules go in `Meta.constraints`**, as well as in the serializer.
6. **Every multi-write operation is inside `transaction.atomic()`.**
7. **External effects (email) go through `core.Outbox`**, never directly in a request.
8. **Do not touch `erpnext/`.** Read it only, for behaviour reference.
9. **Do not read or print secrets** (`.env`, keys, passwords).
10. **Do not delete features.** To simplify, hide them with `hideInSidebar`.
11. **Do not add lot tracking, delivery notes, or anything after dispatch.** The owner
    rejected these.
12. **Do not change permission logic.** It stays in place even with one role.

## User-facing text

There must be no hardcoded text.

- Backend messages: `from django.utils.translation import gettext as _`, with named
  placeholders: `_("Only %(qty)s of %(item)s is ready.") % {"qty": q, "item": i}`.
  Never use f-strings inside `_()`.
- Frontend text: add a key to the right `frontend/src/i18n/en.*.ts`, and to `hi.ts` in
  `frontend/src/i18n/locales/`, then call `t("key")`.
- Write Hindi for daily-use text as a person would say it, not as a formal translation.

## Recipes

### Add a model field

1. Edit `models.py`, plus a `CheckConstraint` if the value has limits.
2. Add it to the serializer's `fields` in `serializers.py`.
3. Run `python manage.py makemigrations <app> --name <short_name>`.
4. Open the migration, and check it only does what you expect.
5. Add it to `frontend/src/resources/<module>.ts`, and to `frontend/src/types` if typed.
6. Add label keys in `en.*.ts` and `locales/hi.ts`.

### Add a backend action on a document

Follow `SalesOrderViewSet.make_sales_invoice` in `backend/selling/views.py`:

1. Put logic in `backend/<app>/services/<name>.py`, not in the view.
2. Add `@action(detail=True, methods=["post"], url_path="...")`.
3. Add the action to `action_permission_flags` with the right flag (`can_read`,
   `can_write`, `can_submit`).
4. Convert `django.core.exceptions.ValidationError` to a 400 response, like its neighbours.
5. Add a row to `backend/docs/API.md` in the same commit.

### Add a derived column

Copy `WorkOrder.status` and `services.work_orders.refresh_progress`:

1. Write a pure `compute_x(doc)` and a `refresh_x(doc)` that uses `QuerySet.update()`.
2. Add `integrity_errors()`, which compares stored with computed values.
3. Register that in `backend/core/management/commands/verify_integrity.py`, following the
   `manufacturing_integrity` block.

### Add a list screen

Add a `ResourceConfig` in `frontend/src/resources/<module>.ts`, and spread it into
`RESOURCES` in `registry.ts`. Routes and sidebar entries are generated.

### Add a custom page

1. Create it in `frontend/src/pages/...`, copying `pages/hr/MusterRollPage.tsx` for the
   imports, `Layout`, `useQuery`, `apiClient` and `useToast` patterns.
2. Add a `<Route>` in `frontend/src/routes/AppRoutes.tsx` inside `ProtectedRoute`.
3. Mobile first: large buttons, cards rather than wide tables, and RTL-safe classes
   (`ms-`, `me-`, `text-start`).

### Add a report

Add a `ReportConfig` (see `frontend/src/types/report.ts`) and a DRF endpoint. The server
calculates all totals; the client never adds up money.

## Tests

Test style: `BaseAPITest` in `backend/core/test_e2e.py` provides a company, accounts,
items and JWT login for HTTP tests. `backend/selling/test_pricing_credit.py` shows unit
style.

Each card lists its tests. Every card must also test the failure path, and that a
failed action leaves no partial data.

## Commands

Run from `backend/`. In a worktree, use the main checkout's Python:
`C:\Users\91700\Desktop\ERP\backend\venv\Scripts\python.exe`, written below as `python`.
A worktree has no `.env`, so it safely defaults to SQLite.

```
python -m pytest <app> -q              # the app you changed
python -m pytest -q                    # everything, before finishing a workstream
python manage.py makemigrations --check --dry-run
python -m ruff check .
python -m black --check .
python -m mypy .
```

From `frontend/`: `npm run lint`, then `npm run build`.

If you are running in the main checkout, set `DATABASE_URL` to a SQLite file first:
`$env:DATABASE_URL = "sqlite:///test_local.sqlite3"`.

`SELECT ... FOR UPDATE` is ignored on SQLite. Concurrency tests must be marked to skip
there, and be run on Postgres before go-live.

## Per-card routine

1. Read the design section and every file the card lists.
2. Search for an existing helper before writing one.
3. Implement the smallest change that satisfies the card.
4. Write the tests, and run them.
5. Run lint for what you touched.
6. Update `backend/docs/API.md` if routes, permissions or serializers changed.
7. Tick the card in `checklist.md`.
8. Commit once, with message `feat(<area>): <card id> <what changed>`, ending with the
   attribution line your instructions give you.

## Stop and report when

- A file, function or field named in a card does not exist, or behaves differently.
- A test unrelated to your change fails, and it was passing before.
- A change needs a `GLEntry`, `Bin` or tax rule not described in the card.
- You would have to edit a migration that is already committed.
- `verify_integrity` logic would need weakening.
- The same fix has failed twice.
- A legal rule seems wrong or unclear.

Report: card ID, what you tried, the exact error, and what you need. Never mark a card
done when its tests have not passed. Never disable, skip or weaken a test to get green.
