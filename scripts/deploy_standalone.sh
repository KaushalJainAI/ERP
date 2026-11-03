#!/usr/bin/env bash
# ==============================================================================
# Path B: Standalone ERP Deployment Script
# Provisions a dedicated, production-ready ERP instance on a fresh Linux VM.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "============================================================"
echo "    Standalone ERP Provisioning & Deployment (Path B)       "
echo "============================================================"

# 1. Check prerequisites
command -v docker >/dev/null 2>&1 || { echo >&2 "[ERROR] Docker is not installed. Run: curl -fsSL https://get.docker.com | sh"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo >&2 "[ERROR] Docker Compose v2 is required."; exit 1; }

# 2. Gather Configuration
DOMAIN="${ERP_DOMAIN:-}"
COMPANY_NAME="${COMPANY_NAME:-}"
COMPANY_ABBR="${COMPANY_ABBR:-}"
STATE_CODE="${STATE_CODE:-23}"
GSTIN="${GSTIN:-}"
PAN="${PAN:-}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"

if [[ -z "${DOMAIN}" ]]; then
    read -rp "Enter Domain Name (e.g. erp.mycompany.com): " DOMAIN
fi

if [[ -z "${COMPANY_NAME}" ]]; then
    read -rp "Enter Full Company Name (e.g. Apex Spices Pvt Ltd): " COMPANY_NAME
fi

if [[ -z "${COMPANY_ABBR}" ]]; then
    read -rp "Enter Company Abbreviation (e.g. ASPL): " COMPANY_ABBR
fi

if [[ -z "${ADMIN_PASSWORD}" ]]; then
    read -s -rp "Enter Admin Password for '${ADMIN_USER}': " ADMIN_PASSWORD
    echo ""
fi

if [[ -z "${ADMIN_EMAIL}" ]]; then
    ADMIN_EMAIL="${ADMIN_USER}@${DOMAIN}"
fi

# 3. Generate .env.production if missing
ENV_FILE="${ROOT_DIR}/.env.production"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[INFO] Creating secure .env.production..."
    SECRET_KEY="$(python3 -c "import secrets; print(secrets.token_urlsafe(50))" 2>/dev/null || openssl rand -base64 40)"
    POSTGRES_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))" 2>/dev/null || openssl rand -base64 20)"

    cat > "${ENV_FILE}" <<EOF
# Production ERP Settings
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=${DOMAIN},localhost,127.0.0.1,backend

# Public URL and CORS
ERP_DOMAIN=${DOMAIN}
ERP_URL=https://${DOMAIN}
CORS_ALLOWED_ORIGINS=https://${DOMAIN}
CSRF_TRUSTED_ORIGINS=https://${DOMAIN}

# Database
POSTGRES_DB=erp_db
POSTGRES_USER=erp_user
POSTGRES_PASSWORD=${POSTGRES_PASS}
DATABASE_URL=postgresql://erp_user:${POSTGRES_PASS}@db:5432/erp_db

# Image Tags
ERP_BACKEND_TAG=latest
ERP_FRONTEND_TAG=latest
EOF
    chmod 600 "${ENV_FILE}"
    echo "[OK] Generated .env.production with mode 600"
else
    echo "[INFO] Using existing .env.production"
fi

# 4. Start Database
echo "[INFO] Starting database..."
docker compose -f docker-compose.standalone.yml --env-file .env.production up -d db

echo "[INFO] Waiting for PostgreSQL to become healthy..."
until docker compose -f docker-compose.standalone.yml --env-file .env.production exec -T db pg_isready -U erp_user -d erp_db >/dev/null 2>&1; do
    sleep 2
done
echo "[OK] Database is healthy"

# 5. Build images and run migrations
echo "[INFO] Running database migrations..."
docker compose -f docker-compose.standalone.yml --env-file .env.production run --rm -T backend python manage.py migrate --noinput

echo "[INFO] Seeding global system defaults (Languages, Roles, Permissions, UOMs)..."
docker compose -f docker-compose.standalone.yml --env-file .env.production run --rm -T backend python manage.py seed_defaults

# 6. Bootstrap Company
echo "[INFO] Bootstrapping company: ${COMPANY_NAME} (${COMPANY_ABBR})..."
BOOTSTRAP_ARGS=(
    --name "${COMPANY_NAME}"
    --abbr "${COMPANY_ABBR}"
    --state "${STATE_CODE}"
    --admin-user "${ADMIN_USER}"
    --admin-email "${ADMIN_EMAIL}"
    --admin-password "${ADMIN_PASSWORD}"
    --deactivate-others
)

if [[ -n "${GSTIN}" ]]; then
    BOOTSTRAP_ARGS+=(--gstin "${GSTIN}")
fi
if [[ -n "${PAN}" ]]; then
    BOOTSTRAP_ARGS+=(--pan "${PAN}")
fi

docker compose -f docker-compose.standalone.yml --env-file .env.production run --rm -T backend python manage.py bootstrap_company "${BOOTSTRAP_ARGS[@]}"

# 7. Collect Static Files
echo "[INFO] Collecting backend static assets..."
docker compose -f docker-compose.standalone.yml --env-file .env.production run --rm -T backend python manage.py collectstatic --noinput

# 8. Start All Services
echo "[INFO] Starting all services (backend, scheduler, frontend, caddy)..."
docker compose -f docker-compose.standalone.yml --env-file .env.production up -d

# 9. Completion Notice
echo ""
echo "============================================================"
echo "    [SUCCESS] Standalone ERP Instance is Deployed!          "
echo "============================================================"
echo "URL:             https://${DOMAIN}"
echo "Admin User:      ${ADMIN_USER}"
echo "Admin Email:     ${ADMIN_EMAIL}"
echo "Company:         ${COMPANY_NAME} (${COMPANY_ABBR})"
echo "Mode:            Single-Company Locked"
echo "SSL/TLS:         Automated via Caddy Let's Encrypt"
echo "============================================================"
