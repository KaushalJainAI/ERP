# `seed_defaults` Management Command Documentation

## Overview
The `seed_defaults` management command provides idempotent initialization of foundational system data for local development, testing, and production bootstrapping.

## Executed Seed Steps
1. **Default Company**: `Acme Corp` (abbr `ACME`, currency `INR`, country `India`).
2. **Fiscal Year**: `2026-2027` (start `2026-04-01`, end `2027-03-31`).
3. **Languages**: Registers 13 supported languages (en + 12 Indian languages).
4. **Roles & Default Permissions**:
   - `Default Role` (default read and create permissions).
   - Domain Manager roles (`Stock Manager`, `Accounts Manager`, `Sales Manager`, `Purchase Manager`, `System Manager`).
5. **UOMs**: `Nos`, `Kg`, `Meter`, `Litre`, `Box`, `Set`.
6. **Base Chart of Accounts**:
   - Root groups: `Application of Funds (Assets)`, `Source of Funds (Liabilities)`, `Equity`, `Income`, `Expenses`.
   - Default accounts: `Bank Account`, `Cash Account`, `Debtors`, `Stock Assets`, `Creditors`, `Sales Account`, `Cost of Goods Sold`.
7. **Master Group Roots**:
   - `ItemGroup`: `All Item Groups`
   - `CustomerGroup`: `All Customer Groups`
   - `SupplierGroup`: `All Supplier Groups`

## Usage & Idempotency
Run via:
```bash
python manage.py seed_defaults
```
The command uses `get_or_create` and atomic transactions, ensuring re-running the command produces zero duplicate data and zero side effects.
