# New Company Provisioning & Deployment Guide

This guide details how to onboard and run a new company on the ERP system.

There are two primary deployment models supported:
- **Path A: In-Database Multi-Tenant Provisioning** — Adding a new company to an existing running deployment (sharing database and server infrastructure, isolated by `Company` tenant scoping and `UserCompany` permissions).
- **Path B: Standalone Instance Deployment** — Provisioning a completely separate, dedicated ERP instance for an independent client or organisation (independent VM, dedicated PostgreSQL database, isolated domain/subdomain, independent SSL certificate).

---

## Architecture: Multi-Company Scoping & Tenancy

In this ERP, the tenant boundary is defined by the `core.Company` model:

1. **Global Master Data** (shared across companies in the same database):
   - Items (`stock.Item`), Item Groups, Item Attributes, Units of Measure (`stock.UOM`).
   - Global System Roles & Permissions (`users.Role`, `users.RolePermission`).
   - System Languages (`core.Language`), GST States (`core.State`), and statutory GST UQCs.
   - System User Accounts (`users.User`).

2. **Company-Scoped Data** (strictly partitioned by `company_id`):
   - Chart of Accounts (`accounts.Account`) and General Ledger Entries (`accounts.GLEntry`).
   - Documents: Sales Invoices, Purchase Invoices, Sales Orders, Purchase Orders, Delivery Notes, Purchase Receipts, Payment Entries, Journal Entries.
   - Warehouses (`stock.Warehouse`) and Stock Ledger Entries (`stock.StockLedgerEntry`, `stock.Bin`).
   - Opening Bills (`accounts.OpeningBill`).
   - Fiscal Years (`core.FiscalYear`) and GST Tax Templates (`gst.SalesTaxTemplate`, `gst.PurchaseTaxTemplate`).
   - HR Pay Structures, Salary Slips, Expense Categories, and Bank Accounts.

3. **User Access Boundary (`core.UserCompany`)**:
   - A user cannot read or write data for any company unless they have an active record in `core.UserCompany(user=user, company=company)`.
   - When only **one company** has `is_active=True`, the frontend automatically locks to that company globally with no company switch prompt.
   - When **multiple companies** have `is_active=True`, the frontend renders the company switcher in the header and scopes all queries with `X-Company-ID: <id>`.

---

## Path A: Adding a New Company to an Existing ERP

Follow these steps when adding Company 2 (or N) into the current production or staging environment.

### 1. Create the Company Record

Run a script or enter the Django shell:

```bash
docker exec -it erp-backend-1 python manage.py shell
```

```python
from core.models import Company, State, GSTCategory

# 1. Resolve State
state = State.objects.filter(code="23").first()  # e.g., 23 for MP, 27 for MH, 07 for DL

# 2. Create the Company
company = Company.objects.create(
    name="New Company Pvt Ltd",
    abbr="NCPL",
    default_currency="INR",
    country="India",
    is_active=True,
    # Statutory & GST Details
    legal_name="NEW COMPANY PRIVATE LIMITED",
    gstin="23ABCDE1234F1Z5",
    pan="ABCDE1234F",
    cin="U12345MP2026PTC012345",
    state=state,
    gst_category=GSTCategory.REGISTERED_REGULAR,
    # Address & Contact
    address_line1="123 Industrial Area",
    city="Indore",
    pincode="452001",
    email="accounts@newcompany.com",
    phone_no="07311234567",
    # Accounting Preferences
    enable_perpetual_inventory=True,
    enable_rounded_total=True,
    round_off_gst_values=False,
    hsn_min_digits=6,
)
print(f"Created company {company.name} (ID: {company.id}, Abbr: {company.abbr})")
```

---

### 2. Seed the Chart of Accounts & Default Accounts

Every company requires its own Chart of Accounts (CoA) root tree and standard accounts:

```python
from accounts.models import Account, AccountRootType, AccountType
from accounts.services.closing import ensure_closing_account

# 1. CoA Roots
root_nodes = {}
for name, rtype in [
    ("Application of Funds (Assets)", AccountRootType.ASSET),
    ("Source of Funds (Liabilities)", AccountRootType.LIABILITY),
    ("Equity", AccountRootType.EQUITY),
    ("Income", AccountRootType.INCOME),
    ("Expenses", AccountRootType.EXPENSE),
]:
    acc, _ = Account.objects.get_or_create(
        company=company,
        name=name,
        defaults={"root_type": rtype, "is_group": True},
    )
    root_nodes[rtype] = acc

# 2. Core Asset Accounts
bank, _ = Account.objects.get_or_create(
    company=company,
    name="Bank Account",
    defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.BANK},
)
cash, _ = Account.objects.get_or_create(
    company=company,
    name="Cash Account",
    defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.CASH},
)
debtors, _ = Account.objects.get_or_create(
    company=company,
    name="Debtors",
    defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.RECEIVABLE},
)
stock_assets, _ = Account.objects.get_or_create(
    company=company,
    name="Stock Assets",
    defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.STOCK},
)
stock_adj, _ = Account.objects.get_or_create(
    company=company,
    name="Stock Adjustment",
    defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.STOCK_ADJUSTMENT},
)
wip_account, _ = Account.objects.get_or_create(
    company=company,
    name="Work In Progress",
    defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.STOCK},
)

# 3. Core Liability Accounts
creditors, _ = Account.objects.get_or_create(
    company=company,
    name="Creditors",
    defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.PAYABLE},
)

# 4. Core Income & Expense Accounts
sales, _ = Account.objects.get_or_create(
    company=company,
    name="Sales Account",
    defaults={"parent": root_nodes[AccountRootType.INCOME], "root_type": AccountRootType.INCOME},
)
cogs, _ = Account.objects.get_or_create(
    company=company,
    name="Cost of Goods Sold",
    defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.COST_OF_GOODS_SOLD},
)
round_off, _ = Account.objects.get_or_create(
    company=company,
    name="Round Off",
    defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.ROUND_OFF},
)

# 5. Wire Company Account Defaults
company.default_inventory_account = stock_assets
company.stock_adjustment_account = stock_adj
company.default_expense_account = cogs
company.default_receivable_account = debtors
company.default_payable_account = creditors
company.default_income_account = sales
company.round_off_account = round_off
company.save()

# 6. Ensure Retained Earnings closing account
ensure_closing_account(company)
```

---

### 3. Generate GST Accounts & Tax Templates

Run the idempotent GST go-live command for the new company:

```bash
docker exec -it erp-backend-1 python manage.py setup_gst_accounts --company NCPL
```

This automatically:
- Creates `Duties & Taxes` liability parent account.
- Creates separate Input and Output accounts for `CGST`, `SGST`, `IGST`, and `RCM`.
- Sets up standard `5%`, `12%`, and `18%` Sales Tax Templates (CGST+SGST for intra-state, IGST for inter-state).
- Sets up standard `5%`, `12%`, and `18%` Purchase Tax Templates.

---

### 4. Create Standard Warehouses & Production Locations

```python
from stock.models import Warehouse
from manufacturing.services.batches import ensure_loss_warehouse
from buying.services.charges import ensure_charge_setup
from hr.services.setup import ensure_hr_setup

# 1. Warehouses
fg_wh, _ = Warehouse.objects.get_or_create(
    company=company,
    name=f"Finished Goods - {company.abbr}",
    defaults={"account": stock_assets},
)
rm_wh, _ = Warehouse.objects.get_or_create(
    company=company,
    name=f"Raw Material - {company.abbr}",
    defaults={"account": stock_assets},
)
wip_wh, _ = Warehouse.objects.get_or_create(
    company=company,
    name=f"Work In Progress - {company.abbr}",
    defaults={"account": wip_account},
)

# 2. Manufacturing loss warehouse & defaults
ensure_loss_warehouse(company)
company.ready_warehouse = fg_wh
company.unprocessed_warehouse = rm_wh
company.in_process_warehouse = wip_wh
company.save()

# 3. Setup standard HR & freight/hamali charge templates
ensure_hr_setup(company)
ensure_charge_setup(company)
```

---

### 5. Provision Fiscal Year & Price Lists

```python
import datetime
from core.models import FiscalYear
from stock.models import PriceList

# Fiscal Year
FiscalYear.objects.get_or_create(
    name="2026-2027",
    defaults={
        "start_date": datetime.date(2026, 4, 1),
        "end_date": datetime.date(2027, 3, 31),
        "is_closed": False,
    },
)

# Selling & Buying Price Lists
std_selling, _ = PriceList.objects.get_or_create(
    name=f"Standard Selling ({company.abbr})",
    defaults={"currency": "INR", "selling": True, "buying": False},
)
std_buying, _ = PriceList.objects.get_or_create(
    name=f"Standard Buying ({company.abbr})",
    defaults={"currency": "INR", "selling": False, "buying": True},
)
company.default_selling_price_list = std_selling
company.default_buying_price_list = std_buying
company.save()
```

---

### 6. Assign User Access (`UserCompany`)

Users will not see this company unless granted membership in `UserCompany`:

```python
from users.models import User
from core.models import UserCompany

# Grant access to specific users
for username in ["kaushal", "ankur"]:
    u = User.objects.filter(username=username).first()
    if u:
        UserCompany.objects.get_or_create(
            user=u,
            company=company,
            defaults={"is_default": False},
        )
```

---

### 7. Cutover Data Onboarding

1. **Customers & Suppliers**: Import Parties with their registered State and GSTIN.
2. **Opening Balances (Receivables & Payables)**:
   - For party-wise open balances, insert into `accounts.OpeningBill` or post cutover Journal Entries dated as of cutover date (e.g. `2026-10-19`).
3. **Opening Stock**:
   - Use `python manage.py build_stock_count_sheet --warehouse <ID>` and `python manage.py load_stock_count` to record initial physical stock count.
4. **Bank & Ledger Opening**:
   - Create and submit an Opening Journal Entry against `Temporary Opening Balance` or `Equity/Capital Account`.

---

### 8. Integrity Check

Run the system integrity verifier to ensure 0 drift on all ledgers:

```bash
docker exec -it erp-backend-1 python manage.py verify_integrity
```

---

## Path B: Deploying a Standalone Fresh ERP Instance

Follow these steps when provisioning a fresh production VM for an independent company.

```
                  ┌──────────────────────────────────────────────┐
                  │                 Public Internet              │
                  └───────────────────────┬──────────────────────┘
                                          │ HTTPS (443)
                                          ▼
                      ┌──────────────────────────────────────┐
                      │  Caddy Container (Auto Let's Encrypt)│
                      └───────────────────┬──────────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
       ┌─────────────────────┐                         ┌─────────────────────┐
       │ erp-frontend (Vite) │                         │ erp-backend (Django)│
       │   Port 80 (internal)│                         │  Port 8000 (Gunicorn│
       └─────────────────────┘                         └──────────┬──────────┘
                                                                  │
                                                                  ▼
                                                       ┌─────────────────────┐
                                                       │ erp-db (Postgres 16)│
                                                       └─────────────────────┘
```

---

### Option 1: Fast Automated Turnkey Deployment (Recommended)

We provide an all-in-one standalone provisioning script and Compose stack (`docker-compose.standalone.yml` + `Caddyfile.standalone` + `scripts/deploy_standalone.sh`).

On the target fresh Ubuntu VM:

```bash
# 1. Clone repository
git clone <REPO_URL> /home/$USER/ERP
cd /home/$USER/ERP

# 2. Run the deployment script
chmod +x scripts/deploy_standalone.sh
./scripts/deploy_standalone.sh
```

The script interactively prompts for:
- Domain name (e.g. `erp.mycompany.com`)
- Company Name & Abbreviation (e.g. `Apex Spices Pvt Ltd`, `ASPL`)
- Admin user & password
- GST state code & optional GSTIN/PAN

It will automatically:
1. Generate a hardened `.env.production` file with cryptographically secure database credentials and Django `SECRET_KEY`.
2. Start the PostgreSQL 16 database container.
3. Run all migrations (`python manage.py migrate`).
4. Seed global defaults (`python manage.py seed_defaults`).
5. Execute `python manage.py bootstrap_company` to create the company, complete Chart of Accounts, GST accounts & tax templates, warehouses, admin superuser, and lock single-company mode.
6. Collect all backend static assets.
7. Start Caddy with automatic SSL (Let's Encrypt) on ports 80/443.

---

### Option 2: Step-by-Step Manual Deployment

If you prefer manual control or need custom configurations:

#### 1. VM & DNS Prerequisites

- **Server**: Ubuntu 22.04 or 24.04 LTS (minimum 2 vCPU, 4GB RAM, 50GB SSD).
- **DNS**: Add an `A` record with your DNS registrar:
  - Host: `erp` (or `@`)
  - Value: `<YOUR_SERVER_IP>`
  - Domain: `erp.newcompany.com`
- **Install Docker & Git**:
  ```bash
  sudo apt update && sudo apt install -y git curl ufw
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER
  ```

#### 2. Clone Code & Setup Directory

```bash
git clone <REPO_URL> /home/$USER/ERP
cd /home/$USER/ERP
```

#### 3. Production Environment Configuration (`.env.production`)

Generate strong secrets and create `.env.production`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Create `/home/$USER/ERP/.env.production` (chmod `600`):

```ini
# Django Core
SECRET_KEY=<GENERATED_STRONG_SECRET_KEY>
DEBUG=False
ALLOWED_HOSTS=erp.newcompany.com,localhost,127.0.0.1,backend

# Application URLs & CORS
ERP_DOMAIN=erp.newcompany.com
ERP_URL=https://erp.newcompany.com
CORS_ALLOWED_ORIGINS=https://erp.newcompany.com
CSRF_TRUSTED_ORIGINS=https://erp.newcompany.com

# PostgreSQL 16
POSTGRES_DB=erp_db
POSTGRES_USER=erp_user
POSTGRES_PASSWORD=<STRONG_POSTGRES_PASSWORD>
DATABASE_URL=postgresql://erp_user:<STRONG_POSTGRES_PASSWORD>@db:5432/erp_db

# Container tags
ERP_BACKEND_TAG=latest
ERP_FRONTEND_TAG=latest

# Email / SMTP Outbox
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=notifications@newcompany.com
EMAIL_HOST_PASSWORD=<APP_PASSWORD>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=notifications@newcompany.com
```

#### 4. Docker Compose Deployment & Automated Bootstrap

```bash
cd /home/$USER/ERP

# 1. Start the PostgreSQL database
docker compose --env-file .env.production -f docker-compose.standalone.yml up -d db

# 2. Run Database Migrations
docker compose --env-file .env.production -f docker-compose.standalone.yml run --rm -T backend python manage.py migrate --noinput

# 3. Seed Base System Defaults (Languages, Roles, Permissions, UOMs, GST States)
docker compose --env-file .env.production -f docker-compose.standalone.yml run --rm -T backend python manage.py seed_defaults

# 4. Bootstrap the New Company & Admin User in 1 Command:
docker compose --env-file .env.production -f docker-compose.standalone.yml run --rm -T backend python manage.py bootstrap_company \
  --name "Apex Spices Pvt Ltd" \
  --abbr "ASPL" \
  --state "23" \
  --gstin "23ABCDE1234F1Z5" \
  --pan "ABCDE1234F" \
  --admin-user "admin" \
  --admin-email "admin@apexspices.com" \
  --admin-password "StrongPassword123" \
  --deactivate-others

# 5. Collect Static Assets
docker compose --env-file .env.production -f docker-compose.standalone.yml run --rm -T backend python manage.py collectstatic --noinput

# 6. Start the Remaining Services (Backend, Scheduler, Frontend, Caddy)
docker compose --env-file .env.production -f docker-compose.standalone.yml up -d
```

---

### 5. Setup Reverse Proxy & Automatic SSL

#### Option 1: Using Caddy (Recommended for Auto-HTTPS)

Install Caddy:
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Add site block to `/etc/caddy/Caddyfile`:

```caddyfile
erp.newcompany.com {
    encode zstd gzip
    header Strict-Transport-Security "max-age=31536000"
    request_body {
        max_size 30MB
    }

    # Proxy to the Docker frontend container on host port (or bridge network)
    reverse_proxy localhost:8080
}
```

Reload Caddy:
```bash
sudo caddy reload
```

#### Option 2: Using Nginx + Certbot

```nginx
server {
    server_name erp.newcompany.com;
    client_max_body_size 30M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Obtain certificate:
```bash
sudo certbot --nginx -d erp.newcompany.com
```

---

### 6. Single-Company Mode Lockdown

For a client with a single company:

1. **Configure their primary company** with legal name, GSTIN, PAN, and address (using the script in Path A).
2. **Deactivate Template / Demo Company**:
   ```python
   Company.objects.filter(abbr="ACME").update(is_active=False)
   ```
3. **Verify single-company response**:
   `GET /api/core/companies/?is_active=true` returns strictly **1 company**. The frontend will automatically lock into this company everywhere.
4. **Lockdown Public User Registration**:
   Ensure users can only be created by the Superuser via the Admin console (`/admin/users/user/`) or Staff management screen.

---

### 7. Automated Backups & Integrity Scheduling

1. Set up daily automated database backups:
   ```bash
   crontab -e
   ```
   Add:
   ```cron
   # Nightly DB and media backup at 02:00 AM
   0 2 * * * /home/ubuntu/ERP/scripts/backup_erp.sh >> /home/ubuntu/ERP/backups/backup.log 2>&1
   ```
2. The built-in `scheduler` container will also run nightly integrity audits and notify administrators if any ledger drift occurs.

---

## Summary Checklist for Go-Live

- [ ] Domain resolves to VPS IP (`dig +short erp.yourdomain.com`).
- [ ] TLS / HTTPS certificate verified in browser.
- [ ] Company GSTIN and State configured; `setup_gst_accounts` executed.
- [ ] GSTR-3B and GSTR-1 reports open with HTTP 200 without warning.
- [ ] Opening balances (Customer/Supplier bills, Bank, Stock) loaded.
- [ ] Single active company confirmed (`is_active=True` count is 1).
- [ ] `python manage.py verify_integrity` returns `0 Drifts`.
- [ ] Backups configured and tested.
