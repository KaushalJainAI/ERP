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

### 5. Server-side document preview and field checks (`ResourceConfig.preview`, `FieldDef.check`)
The server is the single source of truth for derived figures (GST taxes, totals). Instead of re-implementing the engine in the client, a resource can declare a dry-run `preview` (types in `src/types/index.ts`, used by `DocumentPage`):
- `preview: { endpoint, buildRequest(values, manual), apply(response, { values, tables, manual }), rederiveOn?, debounceMs? }`. While a draft is new or has unsaved changes, `DocumentPage` debounces a POST of `buildRequest(...)` (return `null` = not ready) and merges the response with `apply(...)` (header `values` and/or whole replacement `tables`). Responses that no longer match the form are dropped. `renderSummary` receives `preview: { data, errors, pending }`. `buildRequest` must read INPUT fields only (never what `apply` writes), otherwise it would loop.
- `ChildTableDef.derived`: rows the server derives (the GST tax table). While derived they come from the preview and are omitted from the save payload (the server re-derives them). Editing a row by hand makes the table "manual" (sent as edited, `manual` set passed to `buildRequest`). A new document starts derived; a saved one starts manual until a field in `rederiveOn` changes.
- `FieldDef.check`: `{ endpoint, param, okKey, interpret(response, values) }` runs a GET when a text input loses focus (GSTIN validation, state auto-fill) and shows the result under the input. It never blocks saving.
- GST wiring lives in `src/resources/gst.ts` (`taxPreview`, `taxHeaderFields`, `taxesTable`, `taxTotalFields`, `partyGstFields`) and `src/components/gst/GstSummary.tsx`; the masters (Item/Sales/Purchase tax templates) are in `src/resources/gstMasters.ts` under the "GST" sidebar module.
