# GST Support Design (v1)


Reference: ERPNext generic tax engine (`erpnext/erpnext/controllers/taxes_and_totals.py`, `sales_taxes_and_charges*`, `item_tax_template`) plus India Compliance (IC) behaviour from memory. Where I rely on memory rather than the clone, I say so.

---

## 0. Decisions at a glance

| Question | Decision |
|---|---|
| Address model in v1? | **No.** Party-level `gstin` + `gst_category` + `state`. Documents snapshot `party_gstin`, `place_of_supply`, `gst_category`, so a later `contacts.Address` only becomes a default source. See §2.4. |
| Where does generic tax logic live? | `accounts` app: `TaxTemplate`, `ItemTaxTemplate`, `TaxLine`/`TaxableDocument` abstract models, `accounts/services/taxes.py` (pure engine). Mirrors ERPNext. |
| Where does India-specific logic live? | New **`gst` app, services and views only, no models**: GSTIN validation, supply-kind and place-of-supply rules, seeding, integrity checks, GSTR reports. Mirrors the ERPNext / India Compliance split. |
| States | `core.State` model with `code` (2-char) as **primary key**, seeded by a data migration and re-ensured by `seed_defaults`. A primary key on `code` means DRF serialises it natively and `place_of_supply` stores the readable code. |
| Tax account identification | Two nullable fields on `Account`: `gst_direction` (Input / Output / Reverse Charge) and `gst_component` (CGST / SGST / IGST / CESS). Unique per company. This replaces IC's "GST Settings → GST Accounts" table. |
| Item tax templates | **Global**, rows keyed by `gst_component` and not by account, because `Item` has no company. The engine maps component to the company's account at run time. |
| Source of truth | Server engine `TaxableDocument.calculate_taxes_and_totals()`, called on create, update and submit. The client mirrors `On Net Total` for previews only. A stateless dry-run endpoint is provided so the client never has to re-implement rounding. |
| `rounded_total` | **Always populated** (`= grand_total + rounding_adjustment`), unlike ERPNext, which zeroes it when rounding is disabled. Everything downstream (GL debit, outstanding, payment mapping) uses `rounded_total`. |
| Rounding on/off | `Company.enable_rounded_total`, default False (same precedent as `enable_perpetual_inventory`, G4). `seed_defaults` and `seed_demo` turn it on. |
| GL | The party line posts `rounded_total`. Tax lines post per account. A round-off line posts to `Company.round_off_account`. The PI expense line becomes `net_total − stock_value` (today it uses `grand_total`). |
| Reports | Three function views under `/api/gst/reports/`: `gstr1`, `gstr3b`, `tax-ledger`. GSTR-1 and 3B aggregate the line-level GST columns. The tax ledger aggregates GL entries on tagged accounts. |

Pitfalls found in the current code that this design must fix:

1. `SubmittableDocument._recompute_derived_totals` (called from `amend()`) sums any child field named `amount` into `grand_total`. A tax child must not use a field named `amount` (I use `tax_amount`), and the method must delegate to the engine.
2. `DraftNestedWriteMixin` supports exactly one child table, and `refresh_totals` sums `c.amount`. It needs multi-table support and an engine hook.
3. `SubmittableViewSetMixin.update` turns **any** Django `ValidationError` into 409. Tax-input errors would surface as 409 instead of 400. Convert them to DRF `ValidationError` inside the serializer's atomic block.
4. `PurchaseInvoice.on_submit` computes `expense_amount = grand_total - stock_value`. With tax this would put recoverable tax into expense and stock-adjacent value. Change it to `net_total`.
5. `selling/mapping.py` and `buying/mapping.py` set `grand_total` directly. `accounts/mapping.invoice_outstanding` uses `grand_total`. `core/views.py` (lines ~95, 177, 182) sums `grand_total` for "sales/purchases" and open-order logic. `seed_demo` line ~599 sets `so.grand_total` by hand. All of these must change or the new header CHECK constraint is violated.
6. `frontend/src/lib/decimal.ts` rounds half-even. The server uses `ROUND_HALF_UP` (G3). A tax preview needs a half-up option, or previews will disagree on exact-half cases such as 18% of 0.25.
7. Existing test fixtures use GSTINs with invalid checksums (`27AAACR0000A1Z5`, `06AAACJ0000A1Z2`). So GSTIN checksum validation must be **serializer / field-validator level, not `Model.save()` and not a DB check**. `core/tally_import` also writes GSTINs up to 20 chars through the ORM.
8. `ChildColumnDef.preview(row)` receives only the row, so it cannot see the other lines. Header-level previews (`FieldDef.preview(values)`) can. `types/index.ts`, `ChildTable.tsx` and `DocumentPage.tsx` currently have uncommitted edits by another session, so Task 4b must rebase on them.

---

## 1. Masters

### 1.1 `core.State` (new, seeded)

```
State
code               CharField(2, primary_key=True)   # GST state code "01".."38","96","97"
name               CharField(60, unique)
abbr               CharField(3, blank)              # "MH"
is_union_territory BooleanField(default=False)
is_active          BooleanField(default=True)       # False for retired codes 25, 28 (still valid inside old GSTINs)
CHECK Length(code) = 2
```

- Seed list: 01 Jammu & Kashmir, 02 Himachal Pradesh, 03 Punjab, 04 Chandigarh (UT), 05 Uttarakhand, 06 Haryana, 07 Delhi, 08 Rajasthan, 09 Uttar Pradesh, 10 Bihar, 11 Sikkim, 12 Arunachal Pradesh, 13 Nagaland, 14 Manipur, 15 Mizoram, 16 Tripura, 17 Meghalaya, 18 Assam, 19 West Bengal, 20 Jharkhand, 21 Odisha, 22 Chhattisgarh, 23 Madhya Pradesh, 24 Gujarat, 25 Daman & Diu (inactive), 26 Dadra & Nagar Haveli and Daman & Diu (UT), 27 Maharashtra, 28 Andhra Pradesh (old, inactive), 29 Karnataka, 30 Goa, 31 Lakshadweep (UT), 32 Kerala, 33 Tamil Nadu, 34 Puducherry (UT), 35 Andaman & Nicobar Islands (UT), 36 Telangana, 37 Andhra Pradesh, 38 Ladakh (UT), 96 Other Countries, 97 Other Territory.
- Delivery: one data migration (`core.0008_seed_states`) so tests and fresh databases always have the rows, plus an idempotent `ensure_states()` called from `seed_defaults`. The list lives in `core/gst_states.py` as a constant.
- API: `GET /api/core/states/` (read-only, search by name/code, no pagination needed but the default is fine).
- Rejected alternative: a `CharField(choices)` plus a CHECK of 40 literals. That duplicates the list in migrations and has no FK integrity.

### 1.2 Company (`core.Company`), new fields

```
state                   FK(core.State, null, PROTECT, related_name="+")
gst_category            CharField(24, choices=GSTCategory, default="Unregistered")   # v1 supports Registered Regular and Unregistered
hsn_min_digits          PositiveSmallIntegerField(default=6, choices=(4,6,8))
enable_rounded_total    BooleanField(default=False)
round_off_account       FK(accounts.Account, null, PROTECT, related_name="+")        # account_type "Round Off"
round_off_gst_values    BooleanField(default=False)                                  # IC defaults ON; see §3.4
gstin                   (existing CharField(20))
```

- `Company.clean()`: gstin format and checksum, gstin[:2] == state_id, and gstin set means gst_category must be Registered Regular (v1).
- Admin form runs `clean()` automatically (Company is admin-managed; its API is read-only).
- Extend `core.views.CompanySerializer` (read-only) with `state`, `gst_category`, `enable_rounded_total`, `round_off_account`.
- `GSTCategory` (TextChoices) is defined in `core/models.py` so `selling` and `buying` can import it next to `Company`:
`REGISTERED_REGULAR="Registered Regular"`, `REGISTERED_COMPOSITION="Registered Composition"`, `UNREGISTERED="Unregistered"`, `CONSUMER="Consumer"`, `SEZ="SEZ"`, `OVERSEAS="Overseas"`.
Deemed Export, UIN holder and Tax Deductor are deferred (§12).
- Composition **company** (`gst_category=Registered Composition`) is accepted in the master but rejected on GST-bearing invoices in v1 (§12).

### 1.3 Customer and Supplier (`selling.Customer`, `buying.Supplier`), new fields

```
gst_category   CharField(24, choices=GSTCategory, default="Unregistered")
state          FK(core.State, null, PROTECT, related_name="+")     # the party's location; also its default place of supply (sales)
gstin          (existing, max_length stays 20)
```

- No DB constraint on `gstin` length or category pairing (see Pitfall 7). Validation is layered:
- **Field validator** `validate_gstin_format` on the model field. DRF picks model-field validators up automatically, so the API gets format and checksum with no extra code.
- **Serializer `validate()`** calls `gst.validators.validate_party_gst(gstin, gst_category, state)`:
  - Registered Regular / Composition / SEZ require a GSTIN.
  - Unregistered / Consumer / Overseas must have no GSTIN (Overseas may use `tax_id`).
  - If `state` is omitted and a GSTIN is given, state is auto-filled from `gstin[:2]`.
  - A mismatch raises `{state: "GSTIN prefix 27 is Maharashtra"}`.
  - Overseas parties: `state` is forced to `96` or left null (decision: null, with place of supply computed as `96`).
- **Submit gate** on each document re-validates the snapshot GSTIN, because ORM and Tally-import paths bypass the serializer.
- `Consumer` behaves exactly like `Unregistered` in every report. It exists only as a UI label for walk-in B2C parties.
- Search fields already include `gstin`. Add `filterset_fields` `gst_category` and `state`.

### 1.4 Do we need an Address model? Recommendation: no, not for v1

Reasons:

- A single registered GSTIN and a single state per party covers most small and mid-size ERP customers.
- IC stores GSTIN on Address because one customer can have many GSTINs (branches) and billing differs from shipping. That is a real gap, but building Address and Contact doubles the surface (CRUD, dynamic links, RBAC, i18n, forms) before any tax is computed.
- The `contacts` app already exists but is empty (no models, no routes), so adding it later has no migration conflicts.

How v1 stays upgrade-safe:

- Every document stores snapshots: `place_of_supply`, `party_gstin`, `company_gstin`, `gst_category`. Reports read the snapshots, never the party master.
- `place_of_supply` is editable on the document. This covers "ship to another state" for B2C without an Address.

The upgrade later: add `contacts.Address(party_type, party_id, gstin, gst_category, state, is_billing, is_shipping)` and `billing_address` / `shipping_address` FKs on documents. Party fields remain as the "primary" fallback and nothing in the engine changes.

### 1.5 Item (`stock.Item`) and UOM

```
Item.gst_hsn_code        CharField(8, blank=True, default="")      # validator: digits only, length in (4,6,8); SAC for services is 6 digits
Item.is_nil_exempt       BooleanField(default=False)
Item.is_non_gst          BooleanField(default=False)
Item.item_tax_template   FK(accounts.ItemTaxTemplate, null, PROTECT, related_name="+")
CHECK NOT (is_nil_exempt AND is_non_gst)
UOM.gst_uqc              CharField(10, blank=True)                 # "NOS", "KGS", "MTR", "LTR", "BOX", "SET"; seeded for the 6 seeded UOMs
```

Line treatment resolution (`gst.services.supply.resolve_line`), in priority order:

1. `is_non_gst` → treatment `Non-GST`, rates 0.
2. `is_nil_exempt` → treatment from the item tax template if it is Nil-Rated or Exempted, else Nil-Rated, rates 0.
3. Otherwise `Taxable`, with rates from `item_tax_template` if set, else the document tax row's own rate (ERPNext fallback).

Serializer additions: `ItemSerializer` exposes `gst_hsn_code`, `is_nil_exempt`, `is_non_gst`, `item_tax_template`, `item_tax_template_name`, and a read-only `item_tax_rate` dict, e.g. `{"CGST":"9.000","SGST":"9.000","IGST":"18.000"}`. The frontend line preview uses it.

### 1.6 Item Tax Template (global master)

```
ItemTaxTemplate                      (accounts app)
name           CharField(100, unique)          # "GST 18%"
gst_treatment  CharField(12, choices)          # Taxable | Nil-Rated | Exempted | Non-GST
is_active      BooleanField(default=True)
ItemTaxTemplateRow
template       FK CASCADE related_name="rows"
gst_component  CharField(5, choices CGST/SGST/IGST/CESS)
tax_rate       DecimalField(7,3)
UNIQUE(template, gst_component)
CHECK 0 <= tax_rate <= 100
```

- Seeded templates (all with 3 rows CGST/SGST/IGST): `GST 0%`, `GST 0.25%` (0.125 / 0.125 / 0.25), `GST 3%` (1.5 / 1.5 / 3), `GST 5%` (2.5 / 2.5 / 5), `GST 12%` (6 / 6 / 12), `GST 18%` (9 / 9 / 18), `GST 28%` (14 / 14 / 28), plus `GST 40%` (20 / 20 / 40), plus `Nil Rated` / `Exempted` / `Non-GST` (treatment set, rates 0).
- The 40% slab goes beyond the requested list. As of GST 2.0 (22 Sep 2025, from memory) the main slabs are 5% and 18% with a 40% slab for sin and luxury goods. 12% and 28% stay seeded for historical and residual use. Please confirm.
- Divergence from ERPNext: ERPNext templates are per company and keyed by account (`tax_type`). Here `Item` is global, so rows are keyed by component and resolved to the company's tagged account at calculation time.
- API: `/api/accounts/item-tax-templates/` CRUD with nested `rows`. This needs a small `NestedMasterWriteMixin` in `core/mixins.py`: it behaves like `DraftNestedWriteMixin` but has no docstatus guard and no naming series.

### 1.7 Sales / Purchase Taxes and Charges Template (per company)

```
TaxTemplate                           (accounts app)
company            FK(core.Company, CASCADE)
title              CharField(100)                  UNIQUE(company, title)
template_type      CharField(8)                    "Sales" | "Purchase"
gst_supply_type    CharField(12, blank)            "" | "Intra-State" | "Inter-State"   (used for auto-selection)
is_reverse_charge  BooleanField(default=False)     purchase RCM templates
is_default         BooleanField(default=False)
disabled           BooleanField(default=False)
UNIQUE(company, template_type, gst_supply_type, is_reverse_charge) WHERE is_default   (partial unique)
TaxTemplateRow
template           FK CASCADE related_name="rows"
idx                PositiveSmallInteger            UNIQUE(template, idx)
charge_type        CharField(24, choices)          v1: "On Net Total" only (CHECK)
account            FK(accounts.Account, PROTECT)
description        CharField(200, blank)
rate               DecimalField(7,3)               CHECK 0..100
add_deduct_tax     CharField(6, default="Add")     "Add" | "Deduct"
category           CharField(24, default="Total")  v1: "Total" only (CHECK); "Valuation and Total" reserved
```

- ERPNext keeps separate Sales and Purchase template doctypes. One table with `template_type` is equivalent and halves the CRUD surface.
- Seeded per company (§10.1):
- Sales: `Output GST In-state` (Output CGST 9 + Output SGST 9, Intra-State, default) and `Output GST Out-state` (Output IGST 18, Inter-State, default).
- Purchase: `Input GST In-state`, `Input GST Out-state`, plus RCM variants `Input GST In-state RCM` and `Input GST Out-state RCM` (`is_reverse_charge=True`).
- Rates on template rows are the fallback for items with no item tax template. The seeded 18% is the ERPNext/IC convention.
- Validation (serializer and document gate): row account belongs to the template company, is a leaf account, `account_type` is Tax, and sales rows use `gst_direction=Output` (purchase rows `Input`) whenever the account is GST-tagged.
- API: `/api/accounts/tax-templates/` CRUD with nested `rows`, filters `company`, `template_type`, `disabled`.

### 1.8 Tax accounts, seeded per company

New fields on `accounts.Account`:

```
gst_direction  CharField(16, null, choices Input|Output|Reverse Charge)
gst_component  CharField(5,  null, choices CGST|SGST|IGST|CESS)
CHECK (gst_direction IS NULL) = (gst_component IS NULL)
CHECK gst_direction IS NULL OR account_type = 'Tax'
UNIQUE(company, gst_direction, gst_component) WHERE gst_direction IS NOT NULL
AccountType += ROUND_OFF = "Round Off"        # choices-only change, no schema impact
```

Seeded for each company that has a chart of accounts (`gst.services.seeding.ensure_gst_accounts(company)`):

| Account | Parent | root_type | direction / component |
|---|---|---|---|
| Output CGST / Output SGST / Output IGST | "Duties and Taxes" (Liability group) | Liability | Output / CGST, SGST, IGST |
| Input CGST / Input SGST / Input IGST | "Input Tax Credit" (Asset group) | Asset | Input / CGST, SGST, IGST |
| RCM Payable CGST / SGST / IGST | "Duties and Taxes" | Liability | Reverse Charge / CGST, SGST, IGST |
| Round Off | Expenses | Expense | account_type "Round Off", linked as `Company.round_off_account` |

- `ensure_gst_accounts` looks up by (company, direction, component) **first** and creates only when missing. The existing seed already creates Liability accounts literally named `CGST`, `SGST`, `IGST` (account_type Tax). A data migration tags those as Output so a legacy database keeps its names and the unique constraint is not tripped. `seed_defaults` stops creating the bare `CGST` / `SGST` / `IGST` names for new databases.
- CESS accounts are in the choices but not seeded in v1.
- UTGST: union territories without a legislature levy UTGST. v1 posts it to the SGST account (§12).

### 1.9 GSTIN validation (`gst/validators.py`)

1. Normalise: strip and uppercase.
2. Regex `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$`. v1 is regular GSTINs only. UIN, TDS/TCS and NRI formats are deferred.
3. State code `gstin[:2]` must exist in `core.State`. Inactive states 25 and 28 are accepted.
4. Checksum, base-36 weighted:
 ```
 chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
 total = 0
 for i, ch in enumerate(gstin[:14]):
     p = chars.index(ch) * (1 if i % 2 == 0 else 2)
     total += p // 36 + p % 36
 check = chars[(36 - total % 36) % 36]
 valid = (check == gstin[14])
 ```
 I verified this against `27AAACR5055K1Z7` and `29AAACH7409R1ZX`, both known-valid.
5. Cross-field: `gstin[:2] == state.code`. PAN is `gstin[2:12]` and is derivable, not stored.
6. Helper endpoint `GET /api/gst/validate-gstin/?gstin=` returns `{valid, reason, state_code, state_name, pan}`. It is cheap and drives inline form feedback.

---

## 2. Document model

### 2.1 Abstract bases (in `accounts/models.py`)

`TaxableDocument` is abstract and mixes into `SalesOrder`, `SalesInvoice`, `PurchaseOrder`, `PurchaseInvoice`. `grand_total` already exists on all four and is not redefined.

```
taxes_and_charges        FK(accounts.TaxTemplate, null, blank, PROTECT, related_name="+")
place_of_supply          FK(core.State, null, blank, PROTECT, related_name="+")
gst_category             CharField(24, blank, default="")      # snapshot of the party category; "" = legacy / pre-GST document
party_gstin              CharField(20, null, blank)            # snapshot
company_gstin            CharField(20, null, blank)            # snapshot
is_export_with_gst       BooleanField(default=False)           # SEZ / Overseas: True = IGST charged, False = zero-rated under LUT/bond
is_reverse_charge        BooleanField(default=False)           # PI only in v1; SO/SI reject True
net_total                DecimalField(18,2, default=0)         # Σ line amount
total_taxes_and_charges  DecimalField(18,2, default=0)         # = grand_total − net_total (RCM memo rows excluded)
rounding_adjustment      DecimalField(18,2, default=0)
rounded_total            DecimalField(18,2, default=0)         # always populated
constraints (via explicit class Meta(TaxableDocument.Meta) on each concrete model, because there are two abstract parents):
CHECK grand_total  = net_total + total_taxes_and_charges
CHECK rounded_total = grand_total + rounding_adjustment
CHECK NOT (is_reverse_charge AND is_export_with_gst)
```

`ERPNext` names are kept (`total_taxes_and_charges`, `rounding_adjustment`, `rounded_total`) instead of a shorter `total_taxes`.

Methods on `TaxableDocument`:

- `calculate_taxes_and_totals()` is the single entry point (§3.1).
- `validate_for_submit()` is called by each document's `validate_before_submit`.

`TaxLine` is abstract. Concrete subclasses and their parent FKs are `SalesOrderTax` (selling, FK `sales_order`), `SalesInvoiceTax` (selling), `PurchaseOrderTax` (buying), `PurchaseInvoiceTax` (buying), all with `related_name="taxes"`:

```
idx             PositiveSmallIntegerField
charge_type     CharField(24, default="On Net Total")             CHECK = 'On Net Total' (v1)
account         FK(accounts.Account, PROTECT, related_name="+")
description     CharField(200, blank)
rate            DecimalField(7,3)                                 CHECK 0..100
add_deduct_tax  CharField(6, default="Add")                       "Add" | "Deduct"
category        CharField(24, default="Total")                    CHECK = 'Total' (v1)
net_amount      DecimalField(18,2, default 0)    # taxable base for the row (server)
tax_amount      DecimalField(18,2, default 0)    # server; NEVER named "amount"
total           DecimalField(18,2, default 0)    # running cumulative (server)
UNIQUE(parent, idx); UNIQUE(parent, account); CHECK tax_amount >= 0
```

Server-owned fields (`net_amount`, `tax_amount`, `total`) are read-only in serializers. The parent FK is inferred by `DraftNestedWriteMixin._parent_fk_name`. The row is a non-submittable CASCADE child, so `SubmittableDocument._copy_child_rows_to` already clones it on amend.

`TaxableLine` is abstract and mixes into `SalesOrderItem`, `SalesInvoiceItem`, `PurchaseOrderItem`, `PurchaseInvoiceItem`:

```
gst_hsn_code       CharField(8, blank, default="")                    # snapshot from Item
gst_treatment      CharField(12, default="Taxable")                   # Taxable | Nil-Rated | Exempted | Non-GST
item_tax_template  FK(accounts.ItemTaxTemplate, null, PROTECT, related_name="+")
item_tax_rate      JSONField(default=dict)                            # {"CGST":"9.000","SGST":"9.000","IGST":"18.000"} snapshot, keyed by component
gst_rate           DecimalField(7,3, default 0)                       # effective total rate: IGST rate, or CGST+SGST
cgst_amount, sgst_amount, igst_amount   DecimalField(18,2, default 0) # server
CHECK 0 <= gst_rate <= 100; amounts >= 0
CHECK gst_treatment = 'Taxable' OR (cgst_amount=0 AND sgst_amount=0 AND igst_amount=0)
CHECK NOT (igst_amount > 0 AND (cgst_amount > 0 OR sgst_amount > 0))
```

Design notes:

- There is no `taxable_value` column. `amount` is the taxable value in v1 (no line discounts). Adding a duplicate would invite drift. When discounts arrive, add `net_amount` and report on it.
- The per-line columns exist so GSTR-1 and 3B are plain SQL `GROUP BY gst_rate` aggregates. ERPNext keeps this in a JSON `item_wise_tax_detail`. IC v15 adds comparable per-line columns.
- All four item models carry the columns (not only SI/PI), so order-level analytics and the mapping path work uniformly.
- Cancelled / reversal documents do not alter the columns; they are immutable with the document.

### 2.2 Supply kind, place of supply, export / SEZ / reverse charge (`gst/services/supply.py`)

```
supply_context(company, party, direction("Sales"|"Purchase"), place_of_supply, is_export_with_gst, is_reverse_charge) -> SupplyContext
```

| Case | Supplier location | Place of supply (default) | Kind | Allowed GST components |
|---|---|---|---|---|
| SI, Registered / Composition / Unregistered / Consumer customer | `company.state` | `customer.state`, else `company.state` (B2C default) | Intra if equal else Inter | Intra: CGST+SGST. Inter: IGST. |
| SI, SEZ customer | `company.state` | `customer.state` | **Always Inter** | `is_export_with_gst`: IGST. Else zero-rated (LUT), no GST rows. |
| SI, Overseas customer | `company.state` | `96` Other Countries | Inter (export) | Same as SEZ. |
| SI, Composition-company or unregistered-company | n/a | n/a | n/a | None. Rows rejected (bill of supply). |
| PI, Registered Regular supplier | `supplier.state` | `company.state` (overridable) | Intra / Inter | As SI. |
| PI, Composition supplier | n/a | n/a | n/a | None. Rows rejected. |
| PI, Unregistered supplier | `supplier.state` | `company.state` | Intra / Inter | Rows allowed **only** with `is_reverse_charge`. |
| PI, SEZ / Overseas supplier | n/a | `company.state` | Always Inter (IGST) | RCM allowed. Import of goods via Bill of Entry is out of scope. |

- Auto-selection: if a document is created with `taxes_and_charges` null **and** the `taxes` key omitted **and** the company has a GSTIN, the server picks the default `TaxTemplate` matching (company, type, supply kind, RCM). Explicit `taxes: []` means "no tax". PATCH that omits `taxes` leaves the existing rows alone.
- If a draft's party or `place_of_supply` changes and the existing rows become inconsistent (e.g. CGST on an inter-state sale), the save fails with 400 and "re-apply the tax template". The server does not silently rewrite rows.
- SO/SI `is_reverse_charge=True` is rejected in v1 (§12). PI RCM semantics are in §4.2.

### 2.3 Required-at-submit rules (`TaxableDocument.validate_for_submit`)

Applies when the company has a GSTIN; otherwise only structural tax checks run.

- `place_of_supply` is required.
- Each non-Non-GST line has an `gst_hsn_code` of digits only, with length in (4, 6, 8) and ≥ `company.hsn_min_digits`.
- `party_gstin` is present and passes checksum for categories that require one, and the state prefix matches.
- Tax rows are consistent with the supply kind (no CGST/SGST on Inter, no IGST on Intra, nothing on zero-rated or non-allowed cases).
- Row accounts belong to the document's company and are leaves of type Tax. Output-tagged accounts only on sales documents, Input-tagged only on purchase documents.
- SI only: document number ≤ 16 characters (GST rule). `SINV-2026-0001` is 14. An amendment suffix such as `-10` would overflow, so surface this as a clear submit error.
- Legacy documents (`gst_category == ""`) are never re-validated; they are immutable.

---

## 3. Tax engine

### 3.1 Layout and flow

```
accounts/services/taxes.py        pure functions: calculate(lines, rows, policy, reverse_charge) -> Result
gst/services/supply.py            prepare(doc) snapshots / defaults / template expand;  validate(doc, result)
accounts/models.py                TaxableDocument.calculate_taxes_and_totals()
```

`calculate_taxes_and_totals()` runs, in one `transaction.atomic` block:

1. `supply.prepare(doc)`:
 - Fill the snapshots (`gst_category`, `party_gstin`, `company_gstin`).
 - Default `place_of_supply`.
 - Auto-expand or auto-select a template (see §2.2).
 - For each line, resolve HSN, treatment and template and write `item_tax_rate` (§1.5).
2. `taxes.calculate(...)` (pure, no I/O). Inputs:
 - lines: `amount`, `qty`, `gst_treatment`, `item_tax_rate`.
 - rows: `idx`, `rate`, `add_deduct_tax`, `gst_component` (from the row's account), `direction`.
 - policy: rounding flags from the company.
3. Persist:
 - Line columns via `queryset.update`.
 - Row `net_amount`, `tax_amount`, `total`.
 - Header via `save(update_fields=[...])`.
4. `supply.validate(doc, result)`.

Child rows are plain rows, not ledger facts. The document is still a draft, so `SubmittableDocument.save` (which guards on the DB's docstatus) permits the header update. `validate_before_submit` already relies on this today.

Call sites (the "source of truth" rule):

- **Create / update:** `DraftNestedWriteMixin.refresh_totals` calls it. When the instance has `calculate_taxes_and_totals`, it replaces the `c.amount` sum. Do **not** name the method `recompute_totals`; the mixin treats that name as the Journal Entry hook.
- **Submit:** each document's `validate_before_submit` calls it, then `validate_for_submit()`. Submit uses the **line snapshots** (`item_tax_rate`), not a re-resolve from the master, so what the user saw is what posts.
- **Amend:** `_recompute_derived_totals` calls it.
- **Mapping:** mapping functions call it instead of setting `grand_total`.

Errors: tax-input `ValidationError`s raised inside the serializer path are converted to DRF `ValidationError` in `refresh_totals` (400 and rollback). On submit they stay Django `ValidationError`, which the existing mixin maps to 400.

### 3.2 `DraftNestedWriteMixin` generalisation (`core/mixins.py`)

- Add `extra_children_fields = ()` (documents set `("taxes",)`).
- `_child_model(field)` and `_parent_fk_name(field)` take the field name.
- `validate()` runs the partial-update completeness check for every child field.
- `_replace_children` assigns `idx = position + 1` when the child model has an `idx` field.
- `create()` and `update()` pop and replace all child fields. Items are replaced first, taxes second, then `refresh_totals`.
- Set `instance._tax_rows_supplied = ("taxes" in validated_data)` before `refresh_totals`, for the auto-select rule.
- Each document serializer gains `extra_children_fields = ("taxes",)`.

### 3.3 Algorithm (mirrors ERPNext `calculate_taxes`, `On Net Total` only)

Let `A_l` = line amount (`quantize(qty × rate)`, unchanged).

1. `net_total = Σ A_l`.
2. For each tax row `r` (in `idx` order) and each line `l`:
 - If `l.gst_treatment != Taxable` and the row's account has a `gst_component`, `rate = 0`.
 - Else if the row's account has a component and `l.item_tax_rate` contains that component, `rate = l.item_tax_rate[component]`.
 - Else `rate = r.rate`.
 - `t_{r,l} = rate / 100 × A_l`, kept **unrounded** (Python `Decimal`, default context precision).
3. Row total, **document-level** rounding as in ERPNext:
 - `r.tax_amount = quantize_money(Σ_l t_{r,l})` (`ROUND_HALF_UP`, 2 dp; import `quantize_money` from `accounts.services.gl`).
 - If `company.round_off_gst_values` and the row is GST-tagged, round to whole rupees instead.
 - `r.net_amount = Σ A_l` over lines with a non-zero rate.
4. Running total (ERPNext `set_cumulative_total`):
 - `r.total = prev_total + (r.tax_amount if Add else −r.tax_amount)`, where `prev_total` for the first row is `net_total`.
 - RCM memo rows (§4.2) do not move the total.
5. `grand_total = last_row.total` (or `net_total` with no rows). `total_taxes_and_charges = grand_total − net_total`.
6. Rounding (`TaxableDocument`):
 - If `company.enable_rounded_total`: `rounded_total = grand_total.quantize(Decimal("1"), ROUND_HALF_UP)` (nearest rupee) and `rounding_adjustment = rounded_total − grand_total`.
 - Else `rounded_total = grand_total` and `rounding_adjustment = 0`.
7. Per-line GST columns:
 - For each line, `component_amount = quantize_money(t_{r,l})` summed over rows of that component. `gst_rate` is the effective total rate (IGST rate, or CGST+SGST).
 - Rounding residual (`r.tax_amount − Σ_l line_amount`) is applied to the **last line with non-zero tax**, as ERPNext `adjust_rounding_in_item_wise_tax_details` does.
 - Guard: if `abs(residual) > 0.5 + 0.01 × n_lines`, raise an internal error ("rounding drift"). The whole-rupee mode can legitimately reach 0.5; larger means a bug.

Tax per item vs per document: v1 matches ERPNext's default, which is **document-level rounding with unrounded per-item accumulation**. A `round_row_wise_tax` mode is not implemented. IC's default of rounding GST rows to whole rupees is off here by default; the setting exists on `Company` (§12).

### 3.4 Dry-run endpoint

`POST /api/accounts/taxes/calculate/` runs the same engine with no DB writes.

Request:
```json
{ "doctype": "selling.SalesInvoice", "company": 1, "party": 3, "posting_date": "2026-10-27",
"place_of_supply": "07", "is_export_with_gst": false, "is_reverse_charge": false,
"taxes_and_charges": 5,
"items": [ {"item": 12, "qty": "2", "rate": "100.00"} ],
"taxes": [ {"account": 41, "rate": "9.000", "charge_type": "On Net Total"} ] }
```
Response:
```json
{ "supply": {"kind": "INTER", "place_of_supply": "07", "gst_category": "Registered Regular",
           "party_gstin": "07AAGCS4567K1ZV", "allowed_components": ["IGST"]},
"items": [ {"item": 12, "amount": "200.00", "gst_treatment": "Taxable", "gst_hsn_code": "1006",
            "gst_rate": "18.000", "cgst_amount": "0.00", "sgst_amount": "0.00", "igst_amount": "36.00"} ],
"taxes": [ {"idx": 1, "account": 43, "description": "Output IGST", "rate": "18.000",
            "tax_amount": "36.00", "total": "236.00"} ],
"net_total": "200.00", "total_taxes_and_charges": "36.00", "grand_total": "236.00",
"rounding_adjustment": "0.00", "rounded_total": "236.00" }
```
Permission: `can_create` on the target document label, via `user_has_model_permission` as `MappedDocumentActionMixin` does. A companion `GET /api/gst/supply-context/?company&party&direction&place_of_supply` returns `{kind, place_of_supply, suggested_template, allowed_components}` for form defaults.

---

## 4. GL posting

`gl.post` already skips zero entries, enforces Dr == Cr and open fiscal year, and `gl.reverse` mirrors every row of a voucher. The tax lines are therefore just more entries in the same voucher, and cancel needs no new code.

### 4.1 Sales Invoice (`SalesInvoice.on_submit`)

With `R = rounded_total`, `N = net_total`, `T_i` = row tax amounts, `A = rounding_adjustment`:

| Line | Dr | Cr |
|---|---|---|
| `debit_to`, party Customer | R | |
| `income_account` | | N |
| Output CGST / SGST / IGST (each Add row) | | T_i |
| Deduct rows | T_i | |
| `Company.round_off_account` | `-A` if A < 0 | `A` if A > 0 |
| COGS Dr / Inventory Cr (perpetual inventory, unchanged) | outgoing_value | outgoing_value |

Check: `R = N + ΣT + A`. Stock, SLE and COGS logic is unchanged because it reads qty and valuation only. Tax never touches the COGS or inventory legs.

### 4.2 Purchase Invoice (`PurchaseInvoice.on_submit`)

Normal (non-RCM):

| Line | Dr | Cr |
|---|---|---|
| Inventory (perpetual, `stock_value` from SLE) | stock_value | |
| `expense_account` | `net_total − stock_value` | (or Cr if negative, as today) |
| Input CGST / SGST / IGST (Add rows) | T_i | |
| Deduct rows | | T_i |
| `credit_to`, party Supplier | | R |
| `round_off_account` | `A` if A > 0 | `-A` if A < 0 |

Check: `R = N + ΣT + A`.

- **Perpetual inventory interaction.** The SLE `valuation_rate` already comes from `item.rate` (basic rate), so recoverable input tax is excluded from stock value by construction. The change is the expense formula: `expense = net_total − stock_value`, not `grand_total − stock_value`. `verify_integrity.perpetual_inventory_balance` stays green.
- Non-recoverable tax (blocked credit, composition buyer) would need the `category = "Valuation and Total"` mode that adds the tax into the SLE rate. v1 rejects it (§12) and the constraint on `category` is the guard.

Reverse charge (`is_reverse_charge=True`):

- The GST rows still compute and show on the document, but they are **memo rows**: they do not move `total`, so `grand_total = net_total` and the supplier is paid the net value only.
- GL posts a pair per GST component:
- `Dr Input X` (ITC) `T_i`.
- `Cr RCM Payable X` `T_i`, with the account looked up as `(company, "Reverse Charge", component)`.
- The RCM liability is cleared later by a manual Journal Entry (Dr RCM Payable, Cr Bank) when the user pays by challan.
- v1 books ITC on the invoice date. Strictly, ITC on RCM is claimable only after the liability is paid in cash (§12).
- `total_taxes_and_charges` is 0 on an RCM invoice, so the UI shows the memo rows plus a note, not an inflated total.

### 4.3 Cancel and amend

- Cancel: `gl.reverse(self)` is unchanged and reverses every row, including tax and round-off. `stock_service.reverse(self)` for `update_stock` is unchanged.
- Amend copies tax rows and re-runs the engine (§3.1).

### 4.4 `verify_integrity` additions (`gst/services/integrity.py`, called from the command)

New checks. Only the first group are hard errors; the soft group is reported as warnings in `SystemHealthCheck` details, not appended to `errors`.

Hard:

1. **Totals identity**, for every non-draft SO/SI/PO/PI: `Σ line.amount == net_total`; `grand_total == net_total + total_taxes_and_charges`; `rounded_total == grand_total + rounding_adjustment`; `Σ(signed non-memo tax_amount) == total_taxes_and_charges`; each tax row's `total` chain is consistent.
2. **Row ties to lines**: for each GST-tagged row, `tax_amount == Σ line.<component>_amount` (to within 0.01 per line when whole-rupee rounding is on).
3. **GL ties to document** (SI and PI, non-cancelled voucher): the party line equals `rounded_total`; Σ Output-tagged credits equals Σ SI tax rows; Σ Input-tagged debits equals Σ PI tax rows; for RCM PIs, Σ RCM-tagged credits equals Σ memo rows; the round-off line equals `|rounding_adjustment|` with the correct side.
4. **Kind consistency** for submitted documents with GST rows: IGST only when Inter (or export / SEZ with payment); CGST == SGST per document (±0.01); no GST rows on zero-rated, non-GST, composition or unregistered cases.
5. **Account tagging**: no `gst_direction` account with a non-Tax type (also a DB CHECK); tax rows reference accounts of the same company.
6. **Line constraint spot check**: no non-Taxable line carries GST amounts (also a DB CHECK).

Soft (warnings):

7. Party and snapshot GSTINs fail checksum or state prefix (catches ORM / Tally-import data).
8. Submitted GST-registered-company invoices with a missing `place_of_supply` or HSN.
9. Count of legacy documents (`gst_category == ""`), informational.

---

## 5. Mapping and payment

- `selling.mapping.make_sales_invoice` and `buying.mapping.make_purchase_invoice`:
- Copy `taxes_and_charges`, `place_of_supply`, `is_export_with_gst`, `is_reverse_charge` from the order.
- Copy the order's tax rows as **inputs only** (`charge_type`, `account`, `description`, `rate`, `add_deduct_tax`, `idx`). Computed fields start at 0.
- Replace the `grand_total = sum(...)` lines with `invoice.calculate_taxes_and_totals()`.
- Partial invoicing (`remaining_order_lines`) needs no special handling: the engine recomputes tax on the remaining quantity.
- Party snapshots (`party_gstin`, `gst_category`) refresh from the master at invoice creation.
- `accounts.mapping.invoice_outstanding(invoice)` returns `invoice.rounded_total − allocated_amount(...)`. `build_payment_entry` sets `paid_amount` and `received_amount` to that tax-inclusive, rounded figure. Payment Entry posting is unchanged (Dr bank, Cr party for the full amount).
- Existing rows are backfilled `rounded_total = grand_total`, so outstanding is unchanged for legacy invoices.
- `core/views.py` dashboard: "total sales / purchases" and the "open order" comparison use `net_total` (GST is a pass-through, not revenue). Receivables and payables stay GL-derived and are therefore tax-inclusive automatically.
- GST on advance receipts (taxing a Payment Entry with no invoice) is out of scope (§12).

---

## 6. Reports

All three are function views under `/api/gst/reports/`, using `require_model_permission(...)` like the existing reports. Dates: `from_date` / `to_date` (ISO), or `month=YYYY-MM` shorthand; malformed input is 400. All money is returned as 2-dp strings, `company` is required, and every query filters `docstatus=SUBMITTED`.

### 6.1 `GET /api/gst/reports/gstr1/?company=&month=2026-10`

Permission: `can_read` on `selling.SalesInvoice`. A company without a GSTIN returns 400.

Classification, per submitted SI in the period (`posting_date`):

| Section | Rule |
|---|---|
| `b2b` | `party_gstin` set and `gst_category` in Registered Regular / Composition / SEZ, not an export. Grouped by `party_gstin`. Invoice type: Regular, "SEZ supplies with payment" or "SEZ supplies without payment". |
| `b2cl` | Unregistered / Consumer, Inter-State (`place_of_supply != company.state`), `rounded_total` above the B2CL threshold. Threshold constant with an effective date: 1,00,000 for invoices on or after 2024-08-01 (reduced from 2,50,000, from memory); 2,50,000 before. |
| `b2cs` | All other unregistered supplies, grouped by (`place_of_supply`, Intra/Inter, `gst_rate`). |
| `exports` | Overseas customers: `WPAY` if `is_export_with_gst`, else `WOPAY`. |
| `nil_exempt_non_gst` | Lines whose treatment is Nil-Rated / Exempted / Non-GST, by four buckets: Inter to registered, Intra to registered, Inter to unregistered, Intra to unregistered. |
| `hsn` | Split into `b2b` and `b2c` (GSTN split Table 12 in 2025, from memory). Grouped by (`gst_hsn_code`, UQC via `item.stock_uom.gst_uqc`, `gst_rate`). |
| `docs_issued` | Per naming prefix: first, last, total count, cancelled count. |
| `warnings` | `legacy_invoices_without_gst_data` (documents with blank `gst_category` are excluded from the tables). |

Response shape:
```json
{
"company": {"id": 1, "name": "Acme Corp", "gstin": "27AABCA1234C1ZF", "state": "27"},
"period": {"from_date": "2026-10-01", "to_date": "2026-10-31"},
"summary": {"invoice_count": 12, "taxable_value": "0.00", "igst": "0.00", "cgst": "0.00", "sgst": "0.00", "invoice_value": "0.00"},
"b2b": [ {"party_gstin": "07AAGCS4567K1ZV", "party_name": "Sharma Traders",
          "invoices": [ {"name": "SINV-2026-0007", "posting_date": "2026-10-12", "invoice_value": "11800.00",
                         "place_of_supply": "07", "reverse_charge": "N", "invoice_type": "Regular",
                         "rates": [ {"rate": "18.000", "taxable_value": "10000.00", "igst": "1800.00", "cgst": "0.00", "sgst": "0.00"} ]} ]} ],
"b2cl": [ {"place_of_supply": "24", "invoices": [ /* same invoice shape */ ]} ],
"b2cs": [ {"place_of_supply": "27", "supply_type": "INTRA", "rate": "5.000", "taxable_value": "...", "igst": "...", "cgst": "...", "sgst": "..."} ],
"exports": [ {"export_type": "WPAY", "invoices": [ /* ... */ ]} ],
"nil_exempt_non_gst": [ {"description": "Inter-State supplies to registered persons", "nil_rated": "0.00", "exempted": "0.00", "non_gst": "0.00"} ],
"hsn": {"b2b": [ {"hsn": "1006", "uqc": "KGS", "total_qty": "0.000", "rate": "5.000", "total_value": "...", "taxable_value": "...", "igst": "...", "cgst": "...", "sgst": "..."} ], "b2c": []},
"docs_issued": [ {"series": "SINV-2026-", "from": "SINV-2026-0001", "to": "SINV-2026-0030", "total": 30, "cancelled": 1} ],
"warnings": {"legacy_invoices_without_gst_data": 4}
}
```

No credit-note sections (CDNR / CDNUR) in v1: there is no credit-note document (§12). Field names deliberately differ from GSTN JSON keys; a `?shape=gstn` variant is a later, additive change.

### 6.2 `GET /api/gst/reports/gstr3b/?company=&month=`

Permission: `can_read` on **both** `selling.SalesInvoice` and `buying.PurchaseInvoice` (small custom permission class that requires both).

```json
{
"company": {...}, "period": {...},
"3.1": {
  "a_outward_taxable":    {"taxable_value": "...", "igst": "...", "cgst": "...", "sgst": "...", "cess": "0.00"},
  "b_outward_zero_rated": {"taxable_value": "...", "igst": "..."},
  "c_other_nil_exempt":   {"taxable_value": "..."},
  "d_inward_rcm":         {"taxable_value": "...", "igst": "...", "cgst": "...", "sgst": "..."},
  "e_non_gst_outward":    {"taxable_value": "..."}
},
"3.2": [ {"place_of_supply": "24", "category": "Unregistered", "taxable_value": "...", "igst": "..."} ],
"4": {
  "A": {"import_goods": zeros, "import_services": zeros, "inward_rcm": {...}, "isd": zeros, "all_other_itc": {...}},
  "B": {"reversed": zeros},
  "C_net_itc": {...},
  "D_ineligible": zeros
},
"5": {"inter_state": "...", "intra_state": "..."},
"6.1": {"igst": {"output": "...", "itc": "...", "net_payable": "..."}, "cgst": {...}, "sgst": {...}}
}
```

Sources:

- 3.1(a), (b), (c), (e) from SI lines grouped by treatment and export status.
- 3.1(d) and 4(A)(3) from RCM PIs.
- 4(A)(5) from non-RCM PI Input rows.
- 5 from PI lines of Nil-Rated / Exempted / Non-GST treatment, and from composition and unregistered suppliers' values.
- 6.1 is informational: output minus ITC per head. It does not model the cash / credit ledger offset order.
- Sections 4(A)(1), (2), (4), 4B and 4D return zeros in v1.

### 6.3 `GET /api/gst/reports/tax-ledger/?company=&from_date=&to_date=&component=CGST&direction=Output&summary=0`

Permission: `can_read` on `accounts.GLEntry`. Source: GL entries (non-cancelled) on accounts with a non-null `gst_direction`.

```json
{
"company": {...}, "period": {...},
"accounts": [ {
  "account_id": 41, "account_name": "Output CGST", "direction": "Output", "component": "CGST",
  "opening_balance": "0.00", "total_debit": "0.00", "total_credit": "900.00", "closing_balance": "-900.00",
  "entries": [ {"posting_date": "2026-10-12", "voucher_type": "selling.SalesInvoice", "voucher_no": "SINV-2026-0007",
                "party": "Sharma Traders", "debit": "0.00", "credit": "900.00", "balance": "-900.00"} ] } ],
"net_by_component": {"CGST": {"output": "900.00", "input": "270.00", "rcm": "0.00", "net_payable": "630.00"},
                     "SGST": {...}, "IGST": {...}}
}
```

Balance convention follows the existing `general_ledger` report (debit minus credit; Output accounts therefore show as negative). `summary=1` omits `entries`.

---

## 7. API surface summary (rows to add to `backend/docs/API.md`)

| Route | Methods | RBAC label |
|---|---|---|
| `/api/core/states/` | GET | `core.State` |
| `/api/accounts/tax-templates/` (+ `{pk}/`) | CRUD, nested `rows` | `accounts.TaxTemplate` |
| `/api/accounts/item-tax-templates/` (+ `{pk}/`) | CRUD, nested `rows` | `accounts.ItemTaxTemplate` |
| `/api/accounts/taxes/calculate/` | POST | `can_create` on the target document |
| `/api/gst/validate-gstin/`, `/api/gst/supply-context/` | GET | authenticated |
| `/api/gst/reports/gstr1/`, `gstr3b/`, `tax-ledger/` | GET | as in §6 |

Serializer changes on SO / SI / PO / PI: add writable `taxes_and_charges`, `place_of_supply`, `is_export_with_gst`, `is_reverse_charge` and nested `taxes`; read-only `gst_category`, `party_gstin`, `company_gstin`, `net_total`, `total_taxes_and_charges`, `rounding_adjustment`, `rounded_total`, `place_of_supply_name`, `taxes_and_charges_name`. Item serializers add read-only `gst_hsn_code`, `gst_treatment`, `item_tax_template`, `gst_rate`, `cgst_amount`, `sgst_amount`, `igst_amount`, `item_tax_rate`.

RBAC seed matrix (`seed_defaults.py`): add `core.State` to `_MASTER_LABELS`; `accounts.TaxTemplate` and `accounts.ItemTaxTemplate` to `_ACCOUNTS_LABELS` (FULL for Accounts Manager) and READ for the Sales / Purchase Manager and Default Role. `validate_model_labels` will confirm the labels resolve.

---

## 8. Frontend

### 8.1 Master configs

- `selling.ts` `customers` and `buying.ts` `suppliers`: add `gst_category` (select), `state` (link to `/core/states/`, `valueField: "code"`, `labelField: "name"`), keep `gstin`. Columns: add `gst_category`, `state_name`. The `gstin` field gets a debounced call to `/api/gst/validate-gstin/` for inline feedback and a state auto-fill.
- `stock.ts` (items): add `gst_hsn_code`, `is_nil_exempt`, `is_non_gst`, `item_tax_template` (link to `/accounts/item-tax-templates/`).
- `accounts.ts`: new resources `tax-templates` (child table `rows`: `account`, `description`, `rate`, `charge_type`) and `item-tax-templates` (child table `rows`: `gst_component` select, `tax_rate`).
- i18n: new keys in the English file and the other locales. No hard-coded text.

### 8.2 Document configs (`selling.ts`, `buying.ts`)

Header fields added to SO / SI / PO / PI:

- `place_of_supply` (link to states), `taxes_and_charges` (link to tax-templates, `filters` = company + template_type + `disabled=false`).
- `taxes_and_charges.fill` returns `{ taxes: record.rows.map(...) }` (the tax-template list serializer includes `rows`). `DocumentPage` already merges `link.fill` output into the form (lines ~188 and ~200).
- `is_export_with_gst` (hidden unless the customer category is SEZ or Overseas, using `FieldDef.hidden`). `is_reverse_charge` on purchase documents only.
- Read-only: `gst_category`, `party_gstin`, `net_total`, `total_taxes_and_charges`, `grand_total`, `rounding_adjustment`, `rounded_total`.
- `customerLink.fill` / `supplierLink.fill` additionally return `{ gst_category, state }`, to default `place_of_supply`.

Child tables:

- `items` (shared `itemsTable`): add read-only columns `gst_hsn_code` and `gst_rate`. `itemLink.fill` also sets `item_tax_rate`, `gst_treatment` and `gst_hsn_code` from the Item record.
- New `taxesTable` (key `taxes`): columns `charge_type` (select, default "On Net Total"), `account` (link with filters company + `account_type=Tax`; `fill` returns `gst_component`), `description`, `rate` (decimal, 3 dp), `tax_amount` (read-only money) and `total` (read-only money).

### 8.3 Client preview (lib `frontend/src/lib/gst.ts`, new)

- Pure function `previewTotals(values)` using the existing BigInt helpers. For each tax row: per-line rate by component (`item_tax_rate[component]`, else row rate; 0 for non-Taxable); sum; **round half-up** once per row.
- Add a `mode: "half-up"` option (or a sibling `roundDecHalfUp`) to `lib/decimal.ts`. The existing implementation is half-even, which would disagree with the server (Pitfall 6).
- Header `FieldDef.preview` for `net_total`, `total_taxes_and_charges`, `grand_total`, `rounded_total` (these receive the whole form values).
- Per-row `tax_amount` / `total` in the taxes table show server values after save. A live per-row preview needs `ChildColumnDef.preview(row, parentValues)`. That is a stretch goal because it edits `types/index.ts` / `ChildTable.tsx`, which have uncommitted changes by another session.
- Optional: the form calls `/api/accounts/taxes/calculate/` on blur for exact rounding fidelity. The server stays authoritative either way.

---

## 9. Migrations, seeding and backfill

### 9.1 Migration order (split schema / data / constraints; never mix DDL and `RunPython` in one migration on Postgres)

1. `core.0007` add `State`, Company fields, `GSTCategory`.
2. `core.0008` seed States (RunPython, idempotent).
3. `accounts.0005` add Account tags and constraints, `ItemTaxTemplate`(+rows), `TaxTemplate`(+rows).
4. `stock.000x` add Item and UOM fields; `selling.000x`, `buying.000x` add party fields.
5. `selling.000x` / `buying.000x` schema: `TaxableDocument` fields and tax child tables and `TaxableLine` columns, **all with defaults**.
6. Backfill data migration (below).
7. Constraints migration: header CHECKs, line CHECKs, tax-row constraints (added **after** backfill, so existing rows already satisfy them).

### 9.2 Backfill (existing data keeps zero tax)

For every existing SO / SI / PO / PI: `net_total = grand_total`, `total_taxes_and_charges = 0`, `rounding_adjustment = 0`, `rounded_total = grand_total`, `gst_category = ""` (legacy marker), no tax rows. Lines keep `gst_treatment = Taxable`, zeros for GST columns, empty `item_tax_rate`.

Master backfill:

- Company: `gst_category = Registered Regular` if `gstin` is set; `state` from `gstin[:2]` when it is a valid `State` code.
- Customer / Supplier: same rule (category from gstin presence, state from prefix).
- Legacy Liability accounts named `CGST` / `SGST` / `IGST` with `account_type=Tax` are tagged `gst_direction=Output`.
- `UOM.gst_uqc` is set for the 6 seeded UOMs.

This leaves GL, Bin and SLE untouched. `verify_integrity` totals identity holds for legacy rows (§4.4).

### 9.3 `seed_defaults` (idempotent additions)

- `ensure_states()`.
- Per company with a chart of accounts: `ensure_gst_accounts(company)` (§1.8), the Round Off account and `Company.round_off_account`, `ensure_tax_templates(company)` (§1.7).
- Global: `ensure_item_tax_templates()` (§1.6) and `UOM.gst_uqc`.
- Fill-if-empty only on the ACME company (`enable_rounded_total=True`, `hsn_min_digits=6`). A company GSTIN and state are **not** invented here (production data); they come from `seed_demo`.

### 9.4 `seed_demo`

- Company ACME: `gstin=27AABCA1234C1ZF`, `state=27` (Maharashtra), `gst_category=Registered Regular`.
- Replace the existing demo GSTINs, which all fail the checksum, with valid ones, and update in place (the seed uses `get_or_create`, so existing databases would otherwise keep the invalid values):

| Party | State | GSTIN | Category |
|---|---|---|---|
| Sharma Traders (customer) | 07 Delhi | 07AAGCS4567K1ZV | Registered Regular |
| Patel General Stores | 24 Gujarat | 24AAFPP8821M1ZB | Registered Regular |
| Iyer & Sons Supermart | 33 Tamil Nadu | 33AABCI5512R1ZE | Registered Regular |
| Banerjee Stationers | 19 West Bengal | 19AAKFB3390D1ZH | Registered Regular |
| **Kulkarni Provision Stores (new, same state)** | 27 | 27AAHFK5521P1ZW | Registered Regular |
| **Walk-in Customer (new)** | none | none | Consumer |
| **Shivalik Exports SEZ (new)** | 24 | 24AAGCS9087Q1ZE | SEZ |
| Ganga Agro Foods (supplier) | 09 | 09AABCG7741N1Z4 | Registered Regular |
| Mahalaxmi Paper Mills (supplier, same state) | 27 | 27AAACM2209L1ZK | Registered Regular |
| Sundaram Wholesale (supplier) | 33 | 33AAECS6038J1ZA | Registered Regular |
| **Pune Packers (new, unregistered, for RCM)** | 27 | none | Unregistered |

(Checksums computed offline with the algorithm in §1.9.)

- Items: set `gst_hsn_code` and `item_tax_template` (rice 5%, stationery 12% or 18%, etc.).
- New demo documents with fixed names, because existing demo invoices are immutable and the seed is name-keyed: an intra-state SI (CGST+SGST), an inter-state SI (IGST), an SEZ-without-payment SI, a B2C SI, an intra-state PI, an inter-state PI, and one RCM PI from the unregistered supplier. Change line ~599 (`so.grand_total = total`) to call `calculate_taxes_and_totals()`.
- Acceptance: `verify_integrity` is green after `seed_defaults` + `seed_demo` on both a fresh database and an already-seeded one.

### 9.5 Other file touches

- `config/settings.py`: `INSTALLED_APPS += "gst"`, and mount `gst.urls` at `/api/gst/`.
- Admin registrations for the new models.
- Docs: new `docs/gst.md`; update `docs/erpnext_divergences.md` (close G5, add this document's §12 as a new section); update `docs/API.md` rows; CLAUDE.md note that percentage fields use `DecimalField(7,3)` (CLAUDE.md declares no percent precision).

### 9.6 Precision table

| Kind | Type |
|---|---|
| Money (taxes, totals, GST amounts) | `DecimalField(18,2)` |
| Quantity | `DecimalField(18,3)` |
| Item rate | existing `(18,2)` on lines |
| Tax / GST rate (percent) | `DecimalField(7,3)` (0.125 and 2.5 representable; max 9999.999 with a 0..100 CHECK) |
| Rounding | `ROUND_HALF_UP` everywhere (G3) |

---

## 10. Implementation plan

Four tasks in sequence. Each is independently testable and leaves the suite green. Task 4 splits into two parallel tracks.

### Task 1: Masters, validation and seeding

Goal: all masters exist, validate and seed; documents untouched.

Files:

- **New app `gst/`**: `__init__.py`, `apps.py`, `constants.py` (regex, categories), `validators.py` (GSTIN format and checksum, `validate_party_gst`), `services/seeding.py`, `views.py` (`validate_gstin`), `urls.py`, `tests/`.
- `core/gst_states.py` (new constant list), `core/models.py` (`State`, `GSTCategory`, Company fields, `clean()`), `core/views.py` (`StateViewSet`, `CompanySerializer` fields), `core/urls.py`, `core/admin.py`.
- `accounts/models.py` (`Account` tags + constraints, `AccountType.ROUND_OFF`, `ItemTaxTemplate`(+row), `TaxTemplate`(+row)), `accounts/serializers.py`, `accounts/views.py`, `accounts/urls.py`, `accounts/admin.py`.
- `stock/models.py` (Item fields, UOM `gst_uqc`), `stock/serializers.py`, `stock/admin.py`.
- `selling/models.py` + `serializers.py` (Customer fields only), `buying/models.py` + `serializers.py` (Supplier fields only).
- `core/mixins.py`: add `NestedMasterWriteMixin` (additive only).
- Migrations as in §9.1 steps 1 to 4, plus the master part of 6.
- `core/management/commands/seed_defaults.py`, `config/settings.py`, `config/urls.py`.

Tests:

- GSTIN: known-valid (`27AAACR5055K1Z7`, `29AAACH7409R1ZX`) pass; bad checksum, bad length, lowercase normalisation, unknown state code, state-prefix mismatch fail.
- Party serializer: category versus gstin rules; auto-fill state from GSTIN; Overseas rules; ORM creation with an invalid GSTIN still works (no model-level validation).
- Account tag constraints: both-or-neither, Tax-type only, unique per company, legacy-tag migration.
- Template constraints: partial-unique default, `charge_type`/`category` CHECKs, nested create / update of rows via API.
- Seeds: idempotent on re-run; fresh and legacy databases both reach the same tagged accounts without a unique violation; 40 State rows; item templates have correct component splits (e.g. 5% is 2.5/2.5/5, 0.25% is 0.125/0.125/0.25).
- RBAC: new labels validate and roles are scoped; read-only `states` endpoint rejects writes.
- `verify_integrity` green; `ruff`, `black`, `mypy` green.

### Task 2: Tax engine and document model (depends on Task 1)

Goal: documents carry taxes and totals, computed server-side; posting is guarded until Task 3.

Files:

- `accounts/models.py` (`TaxableDocument`, `TaxLine`, `TaxableLine` abstracts), `accounts/services/taxes.py` (new), `accounts/serializers.py` (dry-run), `accounts/views.py` + `urls.py` (`taxes/calculate/`).
- `gst/services/supply.py` (new), `gst/views.py` + `urls.py` (`supply-context`).
- `selling/models.py` (SO / SI: mixin, `Meta` inheritance, tax models, line columns, `validate_before_submit` calls the engine) and `selling/serializers.py` (nested `taxes`, new fields). `buying/models.py` and `buying/serializers.py` likewise.
- `core/mixins.py`: `DraftNestedWriteMixin` multi-table + engine hook + Django-to-DRF error conversion; `core/models.py`: `_recompute_derived_totals` delegates to the engine.
- Migrations per §9.1 steps 5 to 7 (schema, backfill, constraints), as three separate migration files per app.
- `selling/views.py`, `buying/views.py`: prefetch `taxes`.
- **Temporary guard**: SI / PI `validate_before_submit` raises "GST posting not yet enabled" if `total_taxes_and_charges` or `rounding_adjustment` is non-zero, so nothing unbalanced can post. Removed in Task 3.

Tests (all through the API and ORM):

- Intra-state CGST+SGST, inter-state IGST, SEZ and export with and without payment, unregistered B2C default place of supply.
- Rate resolution: item template rates override row rate; Nil-Rated / Exempted / Non-GST lines get 0; no-template line uses row rate.
- Rounding: document-level HALF_UP; exact-half cases (e.g. 18% of 0.25); whole-rupee GST option; residual goes to the last taxed line; rounded total and adjustment; rounding disabled gives `rounded_total == grand_total`.
- Recompute on create, PATCH header-only, PATCH items-only (tax rows kept), submit, and amend (amended document totals equal the cancelled one).
- Auto template selection (omitted `taxes`) versus explicit `taxes: []`; inconsistent rows after changing place of supply return 400 (not 409).
- Submit gates: place of supply, HSN digits, GSTIN checksum, document number ≤ 16 characters, cross-company accounts, wrong-direction accounts.
- DB CHECKs fire on direct ORM writes (header identity, line treatment versus amounts, no IGST with CGST).
- Backfill: legacy documents satisfy every CHECK; all existing test-suite documents still behave identically (company with no GSTIN, no taxes).
- Dry-run endpoint returns exactly the values a subsequent create produces.

### Task 3: GL posting, mapping, payments, integrity, demo (depends on Task 2)

Goal: tax posts correctly; end to end flows work.

Files:

- `selling/models.py` (`SalesInvoice.on_submit`), `buying/models.py` (`PurchaseInvoice.on_submit`: new expense formula, RCM pair, round-off); remove the Task 2 guard.
- `selling/mapping.py`, `buying/mapping.py`, `accounts/mapping.py` (outstanding on `rounded_total`).
- `core/views.py` (`net_total` in dashboard sums and open-order comparison).
- `gst/services/integrity.py` (new), `core/management/commands/verify_integrity.py`.
- `core/management/commands/seed_demo.py` (company GST data, parties, items, new GST demo documents, line ~599 fix).
- Docs: `docs/erpnext_divergences.md`, `docs/API.md` rows, `docs/gst.md`.

Tests:

- SI intra / inter / SEZ-LUT / B2C GL: exact Dr / Cr lines, Dr == Cr, account tags used; with rounding adjustment both signs.
- PI with perpetual inventory ON and OFF: inventory GL equals `Bin.stock_value` (tax excluded); expense equals `net_total − stock_value`; ITC debit lines; round-off both signs.
- PI RCM pair posts; supplier payable equals net; memo rows leave `grand_total` unchanged.
- Cancel reverses every line; second cancel fails; closed fiscal year blocks, as today.
- Mapping: SO to SI partial and full copies template, rows and flags; tax recomputed on remaining qty; PO to PI likewise. `make-payment-entry` amount equals `rounded_total` minus allocations; payment submit leaves outstanding 0.
- `verify_integrity` passes on a fully demo-seeded database and **fails** on each deliberately corrupted case (altered tax row, missing tax GL line, mismatched round-off).
- Dashboard: sales total excludes GST; receivables include it.
- Regression: full existing suite unchanged.

### Task 4a: GST reports (backend; depends on Task 3; parallel with 4b)

Files (all in `gst/`, no overlap with other tasks): `gst/reports/gstr1.py`, `gstr3b.py`, `tax_ledger.py`, `gst/views.py` (append), `gst/urls.py` (append), `gst/tests/test_reports.py`, `docs/API.md` rows, `docs/reports.md`.

Tests: each GSTR-1 section against a hand-built fixture set (B2B including SEZ, B2CL above and below the threshold on both sides of 2024-08-01, B2CS grouping, exports WPAY / WOPAY, nil / exempt / non-GST buckets, HSN B2B / B2C split with UQC, docs issued with a cancelled invoice); GSTR-3B 3.1(a) to (e), 3.2, 4A(3) / 4A(5), 5 and 6.1; tax ledger opening, running and closing balances and net-by-component equals GSTR output minus ITC; cancelled documents excluded; legacy documents excluded with a warning; period boundaries; malformed dates give 400; RBAC (403 without the labels); company without GSTIN gives 400.

### Task 4b: Frontend (depends on Task 2's serializer shape; parallel with Task 3 and 4a)

Files: `frontend/src/resources/selling.ts`, `buying.ts`, `stock.ts`, `accounts.ts`, `frontend/src/lib/gst.ts` (new), `frontend/src/lib/decimal.ts` (half-up option), i18n locale files, optional report pages under `frontend/src/pages/` for the three reports, and (stretch) `types/index.ts` / `ChildTable.tsx` / `DocumentPage.tsx` for the parent-aware row preview. The last three have uncommitted edits by another session, so rebase before touching them.

Tests: unit tests for `gst.ts` against the server's expected totals using shared fixtures (including half-up exact-half cases); config tests that the new fields and child tables exist; lint and build green.

### File-conflict matrix

| File group | T1 | T2 | T3 | T4a | T4b |
|---|---|---|---|---|---|
| `core/models.py`, `core/views.py` | yes | yes (models: amend hook) | yes (views) | no | no |
| `accounts/models.py` | yes | yes | no | no | no |
| `selling/models.py`, `buying/models.py` | yes (party only) | yes | yes (`on_submit`) | no | no |
| `selling/serializers.py`, `buying/serializers.py` | yes (party) | yes | no | no | no |
| `core/mixins.py` | yes (additive) | yes | no | no | no |
| `*/mapping.py` | no | no | yes | no | no |
| `gst/` | create | `services/supply.py`, `views.py` | `services/integrity.py` | `reports/*`, `views.py`, `urls.py` | no |
| `seed_defaults.py` | yes | no | no | no | no |
| `seed_demo.py`, `verify_integrity.py` | no | no | yes | no | no |
| `frontend/**` | no | no | no | no | yes |

Tasks 1, 2 and 3 share model files and so run strictly in order. 4a and 4b touch disjoint trees and can run in parallel with each other and with Task 3 (4a needs Task 3's posting only for its end-to-end fixtures, so start it once Task 3's models land).

---

## 11. Divergences from India Compliance (explicit v1 list)

1. No e-Invoice (IRN / QR / signed JSON), no e-Waybill generation or tracking, no GSP / NIC / portal API of any kind.
2. No GSTR-1 / 3B JSON generation or upload, no GSTR-2A / 2B reconciliation or purchase-matching, no GSTR-9 / 9C, no IMS.
3. No Address / Contact doctype: one GSTIN and state per party, no branch GSTINs, no separate shipping address. `place_of_supply` is a manual / defaulted document field.
4. No credit note, debit note or sales return documents, so no GSTR-1 Table 9 / CDNR / CDNUR, and no amendment tables (cancel and amend only).
5. No Cess (choices reserved, not seeded), no UTGST as a separate head (posted to SGST), no GST TCS (Sec 52) or TDS (Sec 51), no income-tax TDS / TCS.
6. No ITC machinery: no blocked credit (Sec 17(5)), no ITC reversal (Rules 42 / 43), no ineligible ITC flag, no ISD, no ITC-04, no cash / credit ledger. RCM ITC is booked on the invoice date rather than after the liability is paid.
7. No import of goods (Bill of Entry), no Deemed Export, UIN holder or Tax Deductor categories, no composition **company** support (SI / PI GST rows rejected for it).
8. No GST on advance payments (Payment Entry untaxed), no advance-adjustment tables.
9. No tax-inclusive rates (`included_in_print_rate`), no line or header discounts in the taxable value, no `Actual` / `On Previous Row` / `On Item Quantity` charge types, no manual per-line GST rate override, no `Valuation and Total` category (non-recoverable tax capitalised into stock).
10. Item tax templates are global and keyed by GST component, not per-company and per-account. Single template per Item (no per-tax-category / `valid_from` table).
11. SO / SI `is_reverse_charge` rejected (only purchase-side RCM).
12. GST settings live on `Company` fields, not a "GST Settings" doctype. `round_off_gst_values` defaults False (IC defaults True).
13. `enable_rounded_total` defaults False on the model (ERPNext defaults rounding on), following the precedent of G4.
14. `rounded_total` is always populated; ERPNext zeroes it when rounding is disabled.
15. Place-of-supply rules for services (IGST Act Sec 12 / 13) are not computed; only the goods rule plus manual override.
16. No HSN master or government-list validation: length and digits only. No SAC / HSN distinction beyond the same field.
17. No print formats ("Tax Invoice" vs "Bill of Supply" titles, QR), no GST-specific invoice-number-series enforcement beyond the 16-character check.
18. Legacy (pre-GST) documents are not retro-classified; reports exclude them with a warning count.

---

## 12. Open questions for the user

1. Confirm adding the 40% slab and keeping 12% / 28% in the seeds (§1.6).
2. Confirm "Consumer" as a UI-only alias of Unregistered (§1.3).
3. Confirm `ROUND_HALF_UP` to the nearest rupee for `rounded_total` and rounding default off at model level, on in seeds (§3.3).
4. Confirm B2CL threshold constants: 1,00,000 from 2024-08-01, 2,50,000 before (from memory of the GST notification, not verifiable in the repo).
5. Confirm the GSTN Table 12 B2B / B2C split of the HSN summary (from memory).
6. CLAUDE.md has no precision rule for percentage fields. I propose `DecimalField(7,3)` and a one-line addition to CLAUDE.md.

---

### Critical Files for Implementation

- C:\Users\91700\Desktop\ERP\backend\core\mixins.py (multi-child `DraftNestedWriteMixin`, engine hook, error mapping, `NestedMasterWriteMixin`)
- C:\Users\91700\Desktop\ERP\backend\core\models.py (`State`, `GSTCategory`, Company GST fields, `_recompute_derived_totals` delegation)
- C:\Users\91700\Desktop\ERP\backend\accounts\models.py (Account tags, templates, `TaxableDocument` / `TaxLine` / `TaxableLine` abstracts) plus the new `C:\Users\91700\Desktop\ERP\backend\accounts\services\taxes.py`
- C:\Users\91700\Desktop\ERP\backend\selling\models.py and C:\Users\91700\Desktop\ERP\backend\buying\models.py (documents, tax tables, `on_submit` GL changes including the PI expense formula)
- C:\Users\91700\Desktop\ERP\backend\core\management\commands\seed_defaults.py, seed_demo.py and verify_integrity.py (seeding, demo documents, new invariants)
