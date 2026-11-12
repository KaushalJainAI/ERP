# ERP Deployment on the Shared NGU VM

Production runs on the Ubuntu VM at `34.0.5.162`, shared with NGU and AIAAS. NGU's `~/edge/Caddyfile` owns public ports 80/443. This ERP uses its own Docker Compose project (`erp`) and the existing `ngu_ngu-network`; it does not publish database or app ports on the host.

The ERP site is `https://erp.nidhimasala.com`. Its Caddy site block proxies to the Compose alias `erp-frontend`. The root `nidhimasala.com` DNS/redirect remains independent.

## Server files and services

- App: `/home/kaushaljain7000/ERP`
- Compose: `docker-compose.prod.yml`, always run with `--env-file .env.production -p erp`
- Secrets: `.env.production`, mode `600`; start from `.env.production.example`
- Persistent volumes: `erp_erp_postgres_data`, `erp_erp_media`, `erp_erp_static`, `erp_erp_backups`
- Proxy: `~/edge/Caddyfile`; back it up and edit it in place so the bind-mounted inode stays the same
- Backups: the scheduler writes database dumps into `erp_erp_backups` nightly; `scripts/backup_erp.sh` writes database and media copies to `~/ERP/backups/` with 30-day retention

The scheduled backups currently stay on the VM's disk. Keep an offsite copy as a separate recovery measure; the initial deployment dump is also retained on the development workstation.

## First setup

The production database is PostgreSQL 16. Restore the supplied custom-format database backup before running Django migrations. Run:

```bash
cd ~/ERP
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml up -d db
docker cp erp_db_backup.dump erp-db-1:/tmp/erp-import.dump
docker exec erp-db-1 pg_restore -U erp_user -d erp_db --clean --if-exists --no-owner --no-privileges --exit-on-error /tmp/erp-import.dump
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py migrate --noinput
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py seed_defaults
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py collectstatic --noinput
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py verify_integrity
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml up -d --no-build backend scheduler frontend
```

Copy any existing private uploads into `/app/media` in the backend container before enabling the Caddy site.

## Updating the app

Build images on the development machine for the VM's `linux/amd64` platform, save/load them as Docker archives, and use distinct immutable values for `ERP_BACKEND_TAG` and `ERP_FRONTEND_TAG` in `.env.production`.

> **Building from Git Bash on Windows:** run `export MSYS_NO_PATHCONV=1` first. Without it Git Bash
> rewrites `--build-arg VITE_API_URL=/api` into `C:/Program Files/Git/api`, the browser refuses
> every API call, and sign-in shows "Cannot reach the server" (this shipped on 2026-11-11).
> `vite.config.ts` now fails the build on such a value. Check a built image with
> `docker run --rm --entrypoint sh erp-frontend:<tag> -c 'grep -c "Program Files" /usr/share/nginx/html/assets/index-*.js'`
> (must print 0).

After transferring and loading images, run:

```bash
cd ~/ERP
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py migrate --noinput
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py collectstatic --noinput
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml run --rm -T --no-deps backend python manage.py verify_integrity
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml up -d --no-build backend scheduler frontend
docker compose --env-file .env.production -p erp -f docker-compose.prod.yml ps
```

Run `./scripts/backup_erp.sh` before a database-changing deploy. A container image rollback does not undo a migration; restore the pre-deploy dump into a scratch database first.

## Release: ease of use and schema safety (2026-11-11)

This release adds database rules, so follow the order. `C` below is the compose prefix
`docker compose --env-file .env.production -p erp -f docker-compose.prod.yml`.

1. Back up: `./scripts/backup_erp.sh`.
2. Build and load the new `backend` and `frontend` images as in "Updating the app" (new
   tags in `.env.production`). `requirements.txt` now lists `openpyxl` (bulk import).
3. Before migrating, count rows that would break the new rules. Both commands only read:
   ```bash
   $C run --rm -T --no-deps backend python manage.py check_constraint_candidates
   $C run --rm -T --no-deps backend python manage.py check_accounts_constraints
   ```
   Every count must be 0. If one is not, stop: the migration would fail (and roll back).
   Fix or report those rows first; a duplicate customer or supplier name (ignoring case)
   is for the accountant to merge.
4. Migrate (this also adds the `day_summary` alert rule), then make sure the next fiscal
   years exist. Do **not** run `seed_defaults` on the real books: it would recreate the
   demo company "Acme Corp" and could add a second 2026-27 fiscal year under another name.
   ```bash
   $C run --rm -T --no-deps backend python manage.py migrate --noinput
   $C run --rm -T --no-deps backend python manage.py ensure_fiscal_years
   $C run --rm -T --no-deps backend python manage.py collectstatic --noinput
   $C run --rm -T --no-deps backend python manage.py verify_integrity
   ```
   `verify_integrity` must report 0 drifts. Its `[setup warning]` lines list what the
   company still lacks (default accounts, Ready / Unprocessed warehouses, tax templates);
   the same list shows on the Admin home.
5. Start: `$C up -d --no-build backend scheduler frontend`. The scheduler now also creates
   fiscal years ahead and sends the evening day summary.
6. After the deploy: in Alert settings choose who receives the day summary. Document numbers
   now carry the financial year (`SO-2627-0001`), so orders, payments and journals start
   again at 0001 under the new prefix.

## Caddy and HTTPS

Add the host-specific site to the shared Caddyfile, leaving the NGU, AIAAS, and `:80` sites intact:

```caddyfile
erp.nidhimasala.com {
    encode zstd gzip
    header Strict-Transport-Security "max-age=31536000"
    request_body {
        max_size 30MB
    }
    reverse_proxy erp-frontend:80
}
```

Create a backup first. Edit the bind-mounted Caddyfile in place (do not use `sed -i` or an editor that replaces the file), then validate and reload:

```bash
docker exec edge-caddy caddy validate --config /etc/caddy/Caddyfile
docker exec edge-caddy caddy reload --config /etc/caddy/Caddyfile
```

Caddy obtains and renews the Let's Encrypt certificate automatically. Verify `/`, `/admin/login/`, `/api/schema/`, and `/static/admin/css/base.css` over HTTPS.
