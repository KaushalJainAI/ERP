# ERP — Indian-Market ERP on Django + DRF + React

A custom ERP for the Indian market that **replicates ERPNext's functionality** on
our own stack, then diverges to fit our requirements. It is a Django **modular
monolith** (one process, one Postgres DB, apps as bounded contexts) with a
**metadata-driven** React SPA frontend.

Every transactional document moves `Draft → Submitted → Cancelled` and posts
**immutable** ledger entries on submit (reversals on cancel). All balances are
**derived projections** that can be rebuilt from the ledgers.

> **Start here:** [CLAUDE.md](CLAUDE.md) is the canonical brief — the goal, the
> architecture, the three mandates, and the non-negotiable invariants. Read it
> before changing anything. **The code is the source of truth**, not the docs.

---

## Documentation map

### Root guides
| Doc | What it's for |
| --- | --- |
| [CLAUDE.md](CLAUDE.md) | Canonical brief: goal, architecture, mandates, invariants, commands. |
| [goals.md](goals.md) | Why the project exists: higher-level goals, success criteria, non-goals, progress snapshot. |
| [plan.md](plan.md) | *Historical* design notes — original intent and rationale. Context, **not authoritative**. |
| [instructions.md](instructions.md) | How to implement: golden rules, document-building recipe, Definition of Done. |
| [checklist.md](checklist.md) | Ordered task list. Work top-to-bottom, one item per commit. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deploy on a VPS: Nginx + Gunicorn + systemd, Certbot/HTTPS, domain DNS. |
| [DATABASE.md](DATABASE.md) | PostgreSQL role/database setup, migrate & seed, backups. |

### Backend docs (`backend/docs/`)
| Doc | Topic |
| --- | --- |
| [core_models.md](backend/docs/core_models.md) | Base document model, submittable lifecycle, mixins. |
| [accounts_gl.md](backend/docs/accounts_gl.md) | General ledger (`GLEntry`) posting. |
| [stock_sle.md](backend/docs/stock_sle.md) | Stock ledger (`StockLedgerEntry`), `Bin`, valuation. |
| [selling.md](backend/docs/selling.md) · [buying.md](backend/docs/buying.md) | Sales and purchase documents. |
| [payments.md](backend/docs/payments.md) | Payments and outstanding tracking. |
| [reports.md](backend/docs/reports.md) | Reporting. |
| [users_auth_rbac.md](backend/docs/users_auth_rbac.md) | Users, auth, least-privilege RBAC. |
| [self_healing.md](backend/docs/self_healing.md) | `verify_integrity` and the `repost_*` rebuild flow. |
| [i18n.md](backend/docs/i18n.md) | 13-language support (incl. RTL `ur`) and `FieldTranslation`. |
| [erpnext_divergences.md](backend/docs/erpnext_divergences.md) | **Deliberate** deviations from ERPNext. |
| [ops.md](backend/docs/ops.md) · [scaffold.md](backend/docs/scaffold.md) · [seed_defaults.md](backend/docs/seed_defaults.md) | Ops, scaffolding, seed data. |

### Frontend docs (`frontend/docs/`)
| Doc | Topic |
| --- | --- |
| [architecture.md](frontend/docs/architecture.md) | Metadata-driven `<DocForm>` / `<DataTable>` SPA architecture. |

---

## Running the system

### Option A — Docker (everything, one command)

Brings up Postgres, the backend (migrates + seeds + gunicorn), and the frontend.

```bash
docker compose up --build
```

- Backend API → http://localhost:8000
- Frontend → http://localhost:5173

### Option B — Local dev

**Prerequisites:** Python 3.11+, Node 18+, and (optionally) PostgreSQL 16.
Without a `DATABASE_URL`, the backend falls back to the bundled SQLite
(`backend/db.sqlite3`).

**Backend** (from `backend/`):

```bash
python -m venv venv
# Windows PowerShell:  .\venv\Scripts\Activate.ps1
# macOS/Linux:         source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_defaults
python manage.py seed_demo          # optional, DEV ONLY: demo data + login below
python manage.py runserver          # http://localhost:8000
```

`seed_demo` is idempotent and refuses to run unless `DEBUG` is true. It loads
realistic Indian demo data (10 items, 4 warehouses, 5 customers, 3 suppliers,
opening stock, purchase and sales chains with GST, a partial Payment Entry, open
and draft Sales Orders, price lists, credit limits, a completed Work Order with a
WIP transfer and manufacture entry, a draft Work Order, and Journal Entries) by
submitting normal documents, so the ledgers stay consistent
(`python manage.py verify_integrity` is green afterwards).
Demo login (superuser): **`admin` / `admin123`**.

**Frontend** (from `frontend/`):

```bash
npm install
npm run dev                         # http://localhost:5173
npm run build                       # production build
npm run lint                        # tsc --noEmit type check
```

---

## Developer workflow & quality gates

Run from `backend/`:

```bash
python manage.py verify_integrity   # MUST be green before ticking any task
pytest                              # run the suite (one test: pytest path::test_name)
ruff check . && black --check . && mypy .
```

**Self-healing** — derived state is always rebuildable from the immutable ledgers:

```bash
python manage.py verify_integrity   # detect drift
python manage.py repost_stock       # rebuild stock ledger projections
python manage.py repost_gl          # rebuild GL projections
```

A task is **done** only when the DB constraints exist, the three mandates hold,
`pytest` + `verify_integrity` + lint pass, and it reproduces the ERPNext
behaviour (any deliberate deviation recorded in
[erpnext_divergences.md](backend/docs/erpnext_divergences.md)).

---

## Repository layout

```
ERP/
├── CLAUDE.md              # canonical brief (read first)
├── plan.md / instructions.md / checklist.md
├── docker-compose.yml     # Postgres + backend + frontend
├── backend/               # Django + DRF modular monolith
│   ├── config/            # settings, urls, wsgi
│   ├── core/              # base documents, mixins, verify_integrity, seed_defaults
│   ├── accounts/          # GL, repost_gl
│   ├── stock/             # stock ledger, Bin, repost_stock
│   ├── selling/ buying/   # sales & purchase documents
│   ├── contacts/ users/   # masters, auth, RBAC
│   └── docs/              # backend documentation
└── frontend/              # metadata-driven React + Vite SPA
    ├── src/
    └── docs/
```

> **Reference only:** `erpnext/` is a read-only, git-ignored copy of ERPNext used
> to answer "how does ERPNext do it?". Never edit, import, or ship it.
