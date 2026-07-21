# Backend Project Scaffold Documentation

## Overview
The backend is structured as a **modular monolith** using Django 5.0 and Django REST Framework (DRF). It provides the core API infrastructure for the ERP system, reproducing ERPNext's business domain and workflows.

## Configuration & Environment (`config/`)
- `config/settings.py`: Env-driven configuration managed via `django-environ`.
  - **Auth**: SimpleJWT (`rest_framework_simplejwt`) using `users.User` custom model.
  - **API Schema**: OpenAPI schema generation via `drf-spectacular`.
  - **CORS**: `django-cors-headers` allowing frontend connections.
  - **i18n**: Configured for 13 supported languages (English + 12 Indian languages) with locale middleware and fallback to English.
  - **Database**: Connects via `DATABASE_URL` (PostgreSQL in production, SQLite fallback for local development).
- `config/urls.py`: Root routing for `/admin/`, OpenAPI schema `/api/schema/`, Swagger UI `/api/docs/`, Redoc `/api/redoc/`, and app endpoints (`/api/auth/`, `/api/core/`, `/api/contacts/`, `/api/accounts/`, `/api/stock/`, `/api/selling/`, `/api/buying/`).
- `config/wsgi.py` & `config/asgi.py`: Entry points for WSGI and ASGI web servers.
- `manage.py`: Django CLI runner.
- `.env.example`: Template for environment variables including `SECRET_KEY`, `DEBUG`, `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`.

## Application Contexts
1. `core`: Shared abstract bases (`TimeStampedModel`, `SubmittableDocument`), NamingSeries, AuditLog, Outbox, SystemHealthCheck.
2. `users`: Auth, custom `User`, Role, UserRole, RolePermission.
3. `contacts`: Address, Contact.
4. `accounts`: Chart of Accounts, GLEntry, Invoices, Payments, Tax.
5. `stock`: Item, Warehouse, StockLedgerEntry, Bin, StockEntry.
6. `selling`: Customer, Quotation, SalesOrder, DeliveryNote.
7. `buying`: Supplier, PurchaseOrder, PurchaseReceipt.
