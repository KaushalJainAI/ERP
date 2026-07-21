# Frontend Architecture Documentation (`frontend/`)

## Overview
The frontend is a metadata-driven Single Page Application (SPA) built with React 18, Vite, TypeScript, and TanStack Query. It uses a modern dark-mode aesthetic with glassmorphism and HSL-tailored colors.

## Core Features & Infrastructure

### 1. Authentication & JWT Interceptor (`src/api/client.ts`, `src/hooks/useAuth.ts`)
- Axios client configured with `baseURL = /api`.
- Request Interceptor: Attaches `Authorization: Bearer <token>` and `Accept-Language` header automatically.
- Response Interceptor: Listens for `401 Unauthorized` responses and automatically attempts token refresh at `/api/auth/token/refresh/`. On failure, clears token storage and redirects to `/login`.

### 2. Metadata-Driven UI Components
- `<DataTable>` (`src/components/DataTable.tsx`): Generic data grid rendering any master data or submittable document list from serializer metadata. Features live search, status badges (`Draft`, `Submitted`, `Cancelled`), currency formatting, and action triggers.
- `<DocForm>` (`src/components/DocForm.tsx`): Generic document form rendering fields from metadata schemas. Manages document lifecycle buttons (`Save Draft`, `Submit Document`, `Cancel Document`, `Amend Document`) and enforces edit locks for non-draft documents (`docstatus != 0`).

### 3. Internationalization (i18n) & RTL (`src/i18n/i18n.ts`, `src/components/LanguageSwitcher.tsx`)
- Configured via `react-i18next` supporting 13 languages.
- Detects language setting from `localStorage`, `User.language`, or `Accept-Language` header.
- Dynamic RTL Layout: When Urdu (`ur`) is selected, `document.documentElement.dir` is set to `'rtl'`.

### 4. Indian Currency & Number Formatting (`src/lib/numberFormat.ts`)
- `formatIndianCurrency(amount)`: Formats monetary amounts using Indian lakh/crore grouping (`₹12,34,567.89`).
