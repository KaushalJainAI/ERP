# Deployment Guide (VPS)

How to deploy this ERP — Django/DRF backend + React (Vite) frontend — on a single
Ubuntu VPS behind **Nginx**, with **Gunicorn** serving Django via **systemd** and
**HTTPS via Certbot / Let's Encrypt**.

Related docs:
- [DATABASE.md](DATABASE.md) — PostgreSQL role/database setup, migrate & seed, backups.
- [backend/.env.production.example](backend/.env.production.example) — production env template.
- [README.md](README.md) — project overview and the full docs map.
- [backend/docs/ops.md](backend/docs/ops.md) — operational notes.

There are two paths: **native** (Nginx + Gunicorn + systemd, recommended for
control) or **Docker Compose** (fastest). Start with native; the Docker section
is at the end.

---

## 0. Prerequisites

- An Ubuntu 22.04/24.04 VPS with a public IP and `sudo` access.
- A registered domain (examples below use `erp.example.com`).
- Packages:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git nginx postgresql \
                    build-essential libpq-dev
# Node 18+ for building the frontend:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 1. Point your domain at the VPS

In your DNS provider, create **A records** for the apex/subdomain pointing at the
VPS public IP:

| Type | Host | Value            |
| ---- | ---- | ---------------- |
| A    | erp  | `YOUR.VPS.IP`    |
| A    | www  | `YOUR.VPS.IP`    |

Wait for propagation, then confirm before requesting a certificate:

```bash
dig +short erp.example.com    # should print YOUR.VPS.IP
```

Certbot's HTTP-01 challenge will fail if DNS is not resolving yet.

---

## 2. Get the code and set up the backend

```bash
sudo mkdir -p /srv/erp && sudo chown $USER:$USER /srv/erp
git clone <YOUR_REPO_URL> /srv/erp
cd /srv/erp/backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure the environment

```bash
cp .env.production.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # paste into SECRET_KEY
nano .env    # set SECRET_KEY, DEBUG=False, ALLOWED_HOSTS, DATABASE_URL, CORS_ALLOWED_ORIGINS
```

### Database, migrations, static files

Follow [DATABASE.md](DATABASE.md) to create the Postgres role/db, then:

```bash
python manage.py migrate
python manage.py seed_defaults
python manage.py createsuperuser
python manage.py collectstatic --noinput   # -> backend/staticfiles/
python manage.py verify_integrity           # must be green
```

---

## 3. Gunicorn under systemd

Create `/etc/systemd/system/erp.service`:

```ini
[Unit]
Description=ERP Gunicorn (Django)
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/erp/backend
EnvironmentFile=/srv/erp/backend/.env
ExecStart=/srv/erp/backend/venv/bin/gunicorn \
    --workers 3 \
    --bind unix:/run/erp.sock \
    config.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /srv/erp
sudo systemctl daemon-reload
sudo systemctl enable --now erp
sudo systemctl status erp          # confirm it's active
```

Gunicorn is in `requirements.txt`; it listens on a Unix socket that Nginx proxies to.

---

## 4. Build the frontend

The SPA reads its API base from `VITE_API_URL` at build time.

```bash
cd /srv/erp/frontend
echo "VITE_API_URL=https://erp.example.com/api" > .env.production
npm ci
npm run build          # outputs static assets to frontend/dist/
```

Nginx serves `frontend/dist/` directly.

---

## 5. Nginx reverse proxy

Create `/etc/nginx/sites-available/erp`:

```nginx
server {
    listen 80;
    server_name erp.example.com www.erp.example.com;

    # React SPA
    root /srv/erp/frontend/dist;
    index index.html;

    # Django API
    location /api/ {
        proxy_pass http://unix:/run/erp.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Django admin + OpenAPI schema (optional, same backend)
    location /admin/ { proxy_pass http://unix:/run/erp.sock; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme; }

    # Django static files (admin, DRF browsable API)
    location /static/ {
        alias /srv/erp/backend/staticfiles/;
    }

    # SPA history-mode fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    client_max_body_size 20M;
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/erp /etc/nginx/sites-enabled/erp
sudo nginx -t                       # test config
sudo systemctl reload nginx
```

At this point the site should answer over **HTTP**. Verify, then add HTTPS.

---

## 6. HTTPS with Certbot (Let's Encrypt)

Install Certbot and the Nginx plugin:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Request and install the certificate (Certbot edits the Nginx config and adds the
443 server block automatically):

```bash
sudo certbot --nginx -d erp.example.com -d www.erp.example.com
```

Choose **redirect HTTP → HTTPS** when prompted.

### Auto-renewal

Certbot installs a systemd timer that renews automatically. Verify it:

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run        # confirm renewal works
```

### After enabling HTTPS

- Confirm `backend/.env` has `DEBUG=False`, your domain in `ALLOWED_HOSTS`, and
  `https://` origins in `CORS_ALLOWED_ORIGINS`.
- Confirm the frontend was built with `VITE_API_URL=https://erp.example.com/api`.
- Restart the backend after any `.env` change: `sudo systemctl restart erp`.
- Run Django's deployment check; only the HSTS-subdomain/preload and SSL-redirect
  notices (nginx redirects) should remain:
  `cd /srv/erp/backend && .venv/bin/python manage.py check --deploy`.

### Security settings (in `backend/.env`)

The backend **refuses to start** with `DEBUG=False` and a missing, short (< 32
characters) or example `SECRET_KEY`. Defaults when `DEBUG=False`:

| Setting | Default | Notes |
|---|---|---|
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `True` | Set both `False` only for a plain-HTTP LAN install |
| `SECURE_HSTS_SECONDS` | 30 days | Browsers ignore it over plain HTTP |
| `SECURE_SSL_REDIRECT` | `False` | nginx does the redirect |
| `ADMIN_URL` | `admin/` | Change it (e.g. `office-7f3a/`) and the nginx `location /admin/` to match |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | 60 / 7 | Refresh tokens are single-use and revocable |

API docs (`/api/schema/`, `/api/docs/`) need a signed-in user when `DEBUG=False`.
When a staff member leaves, **deactivate** their user: every device they were
signed in on is signed out at once. Staff can do the same for themselves after
losing a phone (`POST /api/auth/me/logout-all/`).

---

## 7. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'         # 80 + 443
sudo ufw enable
```

Postgres stays bound to `127.0.0.1` (see [DATABASE.md](DATABASE.md)) — do **not**
open 5432.

---

## 8. Deploying updates

```bash
cd /srv/erp && git pull
# backend
cd backend && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py verify_integrity
sudo systemctl restart erp
# frontend
cd ../frontend && npm ci && npm run build
sudo systemctl reload nginx
```

---

## 9. Backups, Retention Policy & Restore Drill

### Nightly Backup Procedure

Backups are executed nightly using PostgreSQL's custom format (`pg_dump -Fc`), which supports compressed, verifiable, and selective restoration via `pg_restore`.

#### Automated PowerShell / Cron Job
- **PowerShell (Windows / Cross-platform):**
  ```powershell
  # Nightly run with 30-day retention:
  pwsh ./scripts/backup.ps1 -DatabaseUrl "postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db" -BackupDir "/var/backups/erp" -RetentionDays 30
  ```
- **Linux Crontab (`/etc/cron.d/erp-backup`):**
  ```cron
  0 2 * * * root pwsh /srv/erp/scripts/backup.ps1 -DatabaseUrl "$DATABASE_URL" -BackupDir /var/backups/erp -RetentionDays 30 >> /var/log/erp-backup.log 2>&1
  ```
  Or native bash:
  ```bash
  0 2 * * * erp_user pg_dump -Fc "postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db" > /var/backups/erp/erp_db_$(date +\%Y\%m\%d_\%H\%M\%S).dump && find /var/backups/erp -name "erp_db_*.dump" -mtime +30 -delete
  ```

### Retention Policy
- **Daily retention:** 30 days. Backups older than 30 days are automatically deleted by `scripts/backup.ps1`.
- **Monthly snapshots:** On the 1st day of each month, one backup is archived to `/var/backups/erp/archive/` and kept for 7 years for GST and statutory audit compliance.
- **Off-site sync:** Daily backups must be synced offsite (e.g. AWS S3, Cloudflare R2, or remote NAS via `rclone` or `rsync`).

### Step-by-Step Restore Drill Runbook

> [!WARNING]
> NEVER execute a restore drill against the live production database (`erp_db`). Drills must always be performed in an isolated scratch database.

#### Step 1: Create a scratch database
```bash
sudo -u postgres createdb -O erp_user erp_db_scratch
```

#### Step 2: Restore the dump into scratch database
Using PowerShell helper:
```powershell
pwsh ./scripts/verify_backup.ps1 -BackupFile "/var/backups/erp/erp_db_20261031_020000.dump" -ScratchDbUrl "postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db_scratch"
```
Or directly via `pg_restore`:
```bash
pg_restore --dbname="postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db_scratch" \
  --clean --if-exists --no-owner --no-privileges \
  /var/backups/erp/erp_db_20261031_020000.dump
```

#### Step 3: Verify data integrity in the restored database
Connect the Django environment to the scratch database and run integrity checks:
```bash
cd /srv/erp/backend
source venv/bin/activate
DATABASE_URL="postgres://erp_user:PASSWORD@127.0.0.1:5432/erp_db_scratch" python manage.py verify_integrity
```
`verify_integrity` must complete with zero errors. All stock ledgers, GL postings, and party outstanding balances must match their derived projections.

#### Step 4: Tear down scratch database
```bash
sudo -u postgres dropdb erp_db_scratch
```

---

## Alternative: Docker Compose

The repo ships a `docker-compose.yml` (Postgres + backend + frontend). On the VPS:

```bash
cd /srv/erp
docker compose up -d --build
```

This runs migrate + seed and starts Gunicorn (`:8000`) and the frontend (`:5173`)
in containers. For production you still want Nginx + Certbot **in front** as a
TLS-terminating reverse proxy pointing at those container ports — reuse the Nginx
and Certbot steps above, changing `proxy_pass` to `http://127.0.0.1:8000` for the
API and serving the frontend from its container (or build `frontend/dist/` and
serve it statically as in the native path).

> The committed defaults are for local use only. Before exposing it, export real
> values: `SECRET_KEY=... DEBUG=False POSTGRES_PASSWORD=... docker compose up -d`
> (the compose file reads them from the environment; with `DEBUG=False` the
> backend refuses the default key). Postgres is published on `127.0.0.1:5433`
> only.

---

## Troubleshooting

| Symptom | Check |
| --- | --- |
| 502 Bad Gateway | `sudo systemctl status erp`; is `/run/erp.sock` present? `journalctl -u erp -n 50` |
| `DisallowedHost` error | Add the domain to `ALLOWED_HOSTS` in `.env`, restart `erp`. |
| CORS errors in browser | `CORS_ALLOWED_ORIGINS` must list the exact `https://` origin. |
| Certbot challenge fails | DNS not resolving yet (`dig +short`), or port 80 blocked by firewall. |
| Admin/DRF pages unstyled | `collectstatic` not run, or `/static/` alias path wrong. |
| API 404 from SPA | Frontend built with wrong `VITE_API_URL`; rebuild with the HTTPS URL. |
