# Users, Auth & RBAC Documentation (`users/`)

## Overview
The `users` app handles authentication and Role-Based Access Control (RBAC).

## Architecture & Data Schema

### 1. Custom User Model (`User`)
- Extends `AbstractUser`.
- `email`: Unique, automatically lowercased on `save()`.
- `language`: User's preferred language code (default `'en'`).

### 2. Role-Based Access Control (RBAC)
- `Role`: Represents a role (`name`, `description`).
- `UserRole`: M2M mapping table between `User` and `Role` with `UNIQUE(user, role)`.
- `RolePermission`: Granular model-level permissions for each role:
  - `model_label`: e.g. `'stock.Item'` or `'accounts.SalesInvoice'`.
  - Flags: `can_read`, `can_create`, `can_write`, `can_submit`, `can_cancel`, `can_delete`.
  - `UNIQUE(role, model_label)`.

### 3. Permission Enforcement (`HasRolePermission`)
`users.permissions.HasRolePermission` checks requested HTTP action against `RolePermission` for the requesting user's assigned roles:
- `GET`, `HEAD`, `OPTIONS` -> `can_read`
- `POST` (standard) -> `can_create`
- `POST` `/submit/` -> `can_submit`
- `POST` `/cancel/` -> `can_cancel`
- `PUT`, `PATCH` -> `can_write`
- `DELETE` -> `can_delete`
*Superusers and staff bypass RBAC for admin endpoints.*

### 4. Auth & JWT Endpoints
- `POST /api/auth/token/`: Obtain JWT access token & refresh token.
- `POST /api/auth/token/refresh/`: Refresh expired access token.
- `GET/PATCH /api/auth/me/`: Current user profile & role list.

## Tests & Verification
All unit tests in `users/tests.py` pass cleanly (19/19 overall across core and users), verifying email normalization, RBAC constraints, permission evaluation, and JWT authentication.
