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

> Update the compose `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, and DB password
> before exposing it publicly — the committed values are for local use only.

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
