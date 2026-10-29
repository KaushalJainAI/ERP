# Database Setup (PostgreSQL)

This ERP runs on **PostgreSQL 16** in production. The Django settings default to
the bundled SQLite (`backend/db.sqlite3`) only when `DATABASE_URL` is absent —
use that for local tinkering, **never** for production.

The database URL is read from `backend/.env` as `DATABASE_URL`
(see [`.env.production.example`](backend/.env.production.example)):

```
DATABASE_URL=postgres://erp_user:STRONG_PASSWORD@127.0.0.1:5432/erp_db
```

There are two supported paths: a native Postgres install on the VPS, or the
containerised Postgres from `docker-compose.yml`. Pick one.

---

## Option A — Native PostgreSQL on the VPS (recommended)

### 1. Install PostgreSQL 16

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### 2. Create the role and database

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE erp_user WITH LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
CREATE DATABASE erp_db OWNER erp_user;
-- Recommended defaults for Django/Postgres:
ALTER ROLE erp_user SET client_encoding TO 'utf8';
ALTER ROLE erp_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE erp_user SET timezone TO 'Asia/Kolkata';
GRANT ALL PRIVILEGES ON DATABASE erp_db TO erp_user;
SQL
```

> On PostgreSQL 15+, also grant schema rights so migrations can create tables:
> ```bash
> sudo -u postgres psql -d erp_db -c "GRANT ALL ON SCHEMA public TO erp_user;"
> ```

### 3. Verify the connection

```bash
psql "postgres://erp_user:CHANGE_ME_STRONG_PASSWORD@127.0.0.1:5432/erp_db" -c '\conninfo'
```

### 4. Point the app at it

Set `DATABASE_URL` in `backend/.env` to the URL above, then run the migrations
and seed step (see [Migrate & seed](#migrate--seed)).

---

## Option B — Dockerised PostgreSQL

The repo's `docker-compose.yml` already defines a `postgres:16-alpine` service
with database `erp_db`, user `erp_user`, password `erp_password`, exposed on
host port **`5433`** (5432 is taken by the Tally extract container `tally-pg`).
Bring it up standalone with:

```bash
docker compose up -d postgres
```

Then in `backend/.env`:

```
DATABASE_URL=postgres://erp_user:erp_password@127.0.0.1:5433/erp_db
```

This is the database that holds the real books (NIDHI GRAH UDYOG, imported from
Tally with `python manage.py import_tally`; see
`backend/docs/tally_import_report.md`). SQLite is for tests and throwaway dev only.

(If the whole stack runs under compose, the backend service reaches it at host
`postgres` instead of `127.0.0.1` — that value is already wired in the compose
file.)

---

## Migrate & seed

From `backend/` with the virtualenv active and `DATABASE_URL` set:

```bash
python manage.py migrate          # create the schema
python manage.py seed_defaults    # roles, accounts, UOMs, default masters
python manage.py createsuperuser  # first admin login
python manage.py verify_integrity # MUST be green
```

`seed_defaults` is idempotent; `verify_integrity` confirms the ledgers and
derived projections are consistent. Both are described in
[backend/docs/seed_defaults.md](backend/docs/seed_defaults.md) and
[backend/docs/self_healing.md](backend/docs/self_healing.md).

---

## Backups & restore

Take regular logical backups (and automate via cron):

```bash
# Backup
pg_dump "postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db" \
  | gzip > /var/backups/erp_db_$(date +%F).sql.gz

# Restore into a fresh database
gunzip -c /var/backups/erp_db_2026-07-22.sql.gz \
  | psql "postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db"
```

Because all balances are **derived projections**, a restored dump can be
re-validated (`verify_integrity`) and, if needed, rebuilt from the immutable
ledgers with `repost_stock` / `repost_gl` — no data is lost by rebuilding
derived state.

---

## Hardening notes

- Keep Postgres bound to `127.0.0.1` (the default) so it is not exposed to the
  internet; the app connects locally.
- Use a strong, unique password and store it only in `backend/.env` (git-ignored).
- Grant `erp_user` rights on `erp_db` only — never use the `postgres` superuser
  as the app account.
- Ensure daily `pg_dump` backups are copied off the VPS.
