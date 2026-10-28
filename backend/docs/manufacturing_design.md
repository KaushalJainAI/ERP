# Manufacturing, Batches, Price Lists and Credit Limits Design (v1)

Target: Nidhi Grah Udyog (Nidhi Masala), Bhopal. Spice manufacturer: buy whole spices, clean and grind (about 1.25% process loss), blend, pack, sell to distributors (B2B) and online (B2C).


References read: `CLAUDE.md`, `erpnext_divergences.md`, `gst_design.md`; the models in `core`, `accounts`, `stock`, `selling` and `buying`; `stock/services/ledger.py`, `accounts/services/gl.py`, `core/mixins.py`; the frontend framework (`types/index.ts`, `types/report.ts`, `resources/*.ts`, `ChildTable.tsx`). ERPNext sources read (read-only): `stock_entry.py` (`set_basic_rate`, Repack and Manufacture rates, process loss), `work_order.py` (statuses, `produced_qty`, `process_loss_qty`), `bom.py` (process loss, scrap), `batch.py` and `serial_and_batch_bundle.py` (batch-wise valuation, FEFO auto-pick, expiry), `get_item_details.py` (`get_item_price`, `get_price_list_rate`), `customer.py` (`check_credit_limit`, `get_customer_outstanding`, `get_credit_limit`) and the SO/SI credit hooks. Where I rely on memory of ERPNext rather than the clone, I say so.

---

## 0. Decisions at a glance

| Question | Decision |
|---|---|
| Batch balance: `batch_no` on SLE plus a per-batch Bin, or derived from SLE? | **`StockLedgerEntry.batch` (nullable FK) and a batch balance derived on read** (`SUM(qty)` over `is_cancelled=False`). No `BatchBin` table. Single read seam `stock/services/batches.py::batch_balance()`. See 1.3. |
| Valuation | Stay on **Moving Average per Item+Warehouse Bin** (divergence G1 stays open, FIFO is not attempted). For `has_batch_no` items the **outgoing rate is the batch's own average** (`SUM(value)/SUM(qty)` of that batch in that warehouse). This is ERPNext's default behaviour (`Batch.use_batchwise_valuation = 1`). The Bin stays the sum of its batches. See 1.4. |
| Where do batches live? | `stock.Batch` master. `batch` FK on SLE, `StockEntryItem`, `SalesOrderItem` (optional, informational), `SalesInvoiceItem`, `PurchaseInvoiceItem`. One batch per line; a multi-batch issue is several lines. No Serial and Batch Bundle document. |
| FEFO | Read-only suggestion endpoint plus a FEFO-sorted batch picker. **A batch is required on outward lines of batch items** (no silent auto-split at submit), so the invoice records the lot sold. |
| Expiry blocking | Any stock-out of a batch with `expiry_date < posting_date` is refused in `ledger.post()`. **Exception: Material Issue** (the write-off path, `allow_expired=True`). |
| BOM | New **`manufacturing` app**. `BOM` is a **master** (not submittable, unlike ERPNext) with a usage lock. Costs are **derived on read**, never stored. Multi-level through `BOMItem.bom_no`. No operations, routing, workstations or job cards in v1. |
| Grinding | **A BOM with `process_loss_percentage`**. Blending is a BOM. **Packing is a BOM per pack size** (blend plus pouch plus carton). |
| Repack | Kept as a Stock Entry type (same engine as Manufacture, no BOM, no Work Order) but **not recommended for packing**: ERPNext prices every Repack output at the same rate per unit, which is wrong for 100g vs 500g pouches. Ours allocates by explicit `cost_allocation_per`. |
| Work Order | `manufacturing.WorkOrder` (submittable). Derived projection columns (`produced_qty`, `process_loss_qty`, per-row `transferred_qty` and `consumed_qty`, `status`) are recomputed from submitted Stock Entries by `refresh_progress()` and a `repost_work_orders` command. |
| WIP and GL | WIP is a **Warehouse** under the company's single `default_inventory_account`. Material Transfer for Manufacture and Manufacture post **no GL**: consumed value equals produced value exactly. `Company` has one inventory account today, so this is also what ERPNext does when WIP and stock share an account. |
| FG valuation | `FG value = Σ consumed value − Σ scrap value`, posted through a new explicit `incoming_value` on `ledger.post()` so value is conserved to the paisa. Process loss raises the unit cost. |
| Price lists | `stock.PriceList` and `stock.ItemPrice`. **Distributor-specific pricing = one price list per distributor or tier**, assigned as `Customer.default_price_list`. No party column on Item Price. ERPNext's resolution order, minus party and batch rows. |
| Rate fetch | `GET /api/stock/item-prices/rate/`. The server also fills an omitted line `rate` on save. |
| Credit limit | Fields **on `Customer`** (`credit_limit`, `bypass_credit_limit_check`): our Customer already belongs to one company, so a per-company child table is redundant. Checked in `validate_before_submit` of SO and SI. Exposure = receivable balance plus unbilled open SOs plus this document. Override role `Credit Controller`, audited. |
| Credit exposure source | Seam `receivable_balance()`: **GL party balance on Receivable accounts** now (ERPNext's definition); switch to erp-72's `accounts.services.outstanding` once it exposes a party-level, advance-aware total. See 6.2. |
| Reports | Batch-wise balance, expiry, production yield, BOM cost, plus batch trace and BOM explode. All derived. |
| Frontend | New `manufacturing` module (`resources/manufacturing.ts`), additive changes to `stock.ts`, `selling.ts`, `buying.ts`. Three framework gaps (async fill, action body, report cell render), all small and additive. |

### Pitfalls found in the current code that this design must handle

1. `stock/services/ledger.py` is Moving Average only with one Bin rate. It rounds `qty × rate` per leg, so an in-leg valued from an out-leg can differ by a paisa. Manufacture needs an explicit incoming value.
2. `StockEntry.stock_entry_type` is `CharField(max_length=25)`. "Material Transfer for Manufacture" is 33 characters, so a column widen is part of the migration.
3. `StockEntry.validate_before_submit` demands `stock_adjustment_account` for every non-Transfer type, and `on_submit` posts the net SLE value against it. Manufacture, Repack and Transfer-for-Manufacture must be excluded from both, and net value must be asserted to be zero.
4. `SubmittableDocument.save()` rejects any update of a non-draft document except `docstatus/modified_at/modified_by`. Derived Work Order columns must be written with `QuerySet.update()`. erp-72 has the same need for `outstanding_amount`; if it adds a `projection_fields` allowlist in `core/models.py`, adopt it.
5. `ledger.reverse()` guards only Bin negativity. It must also guard batch negativity and must copy `batch` onto reversal rows. `original_sles` is unordered, which is fine because each leg is checked independently.
6. `PaymentEntry.on_submit` stamps `party_type/party` on **both** GL legs (bank and receivable). A GL party balance that does not filter `account__account_type="Receivable"` would net every payment to zero. The credit fallback must filter on the account type.
7. `Customer` is per company (`UNIQUE(company, name)`), unlike ERPNext, so "credit limit per customer per company" is just fields on `Customer`.
8. Line `rate` is `DecimalField(18,2)` on SO/SI/PO/PI. Price list rates are `(18,6)` per CLAUDE.md. Fetched rates are rounded half-up to 2 dp for the line, with the full-precision value kept in a snapshot column.
9. `SubmittableDocument.submit(user)` does not pass `user` to `validate_before_submit`. The credit override needs the user. SO and SI override `submit()` locally to stash `self._submit_user`, so `core/models.py` is not touched.
10. `LinkDef.fill` and `fetcher` are synchronous and context-free. Rate and default-BOM fetches need an async variant (gap G-F1).
11. Back-dated postings are valued at the current Bin balance (existing behaviour; `repost()` replays facts, it does not re-value). Expiry checks therefore compare against the document's `posting_date`, not today.
12. New backend messages should use `gettext` (mandate 3). Much existing code uses bare strings; do not copy that.

---

## 1. Batches

### 1.1 Masters

**`stock.Item`, new fields**

```
has_batch_no         BooleanField(default=False)
has_expiry_date      BooleanField(default=False)
shelf_life_in_days   PositiveIntegerField(null=True, blank=True)
CHECK: NOT has_expiry_date OR has_batch_no            (expiry lives on the batch)
CHECK: shelf_life_in_days IS NULL OR (has_expiry_date AND shelf_life_in_days > 0)
```

- Toggling `has_batch_no` or `has_expiry_date` is refused by `ItemSerializer.validate` once any SLE exists for the item (ERPNext does the same once transactions exist). There is no stock reconciliation document to batch-assign opening stock, so batch tracking must be on from the first receipt.
- Packaging and pouch items stay `has_batch_no = False`. Raw spices, bulk powders, blends and packed goods are `True` (FSSAI traceability and best-before need it).

**`stock.Batch` (new)**

```
batch_no            CharField(60, unique=True)
item                FK(Item, PROTECT, related_name="batches")
manufacturing_date  DateField(null)
expiry_date         DateField(null, db_index)
supplier_batch_no   CharField(100, blank, default="")   # raw materials: the supplier's lot
reference_doctype   CharField(60, blank)                # provenance, e.g. "buying.PurchaseInvoice"
reference_name      CharField(60, blank)
description         CharField(240, blank)
is_disabled         BooleanField(default=False)
CHECK: expiry_date IS NULL OR manufacturing_date IS NULL OR expiry_date >= manufacturing_date
Index(item, expiry_date)
```

- `batch_no` is globally unique (ERPNext's batch id is the primary key), so a bare `batch_no` is enough in reports and on labels.
- Auto-numbering: `NamingSeries.next(f"{item.code}-{YYMMDD}-", padding=2)`, for example `HALDI-W-261028-01`. A manual `batch_no` is allowed.
- The supplier link is the provenance pair `reference_doctype/reference_name` (the creating Purchase Invoice) plus the free-text `supplier_batch_no`. There is deliberately no `Supplier` FK, which would make `stock` import `buying`.
- Expiry default: if `Item.has_expiry_date` and no `expiry_date` is given, `expiry_date = manufacturing_date + shelf_life_in_days` (`manufacturing_date` defaults to the posting date for an auto-created batch). If there is still no expiry, creation is refused (ERPNext `set_expiry_date`).
- `item` is immutable once the batch has SLEs. `expiry_date` stays editable, with an audit row.
- A disabled batch cannot be used on any line.

### 1.2 Batch on document lines

| Line model | New fields | Meaning |
|---|---|---|
| `stock.StockEntryItem` | `batch` FK null, `supplier_batch_no`, `manufacturing_date`, `expiry_date` | Outward legs: `batch` required for batch items. Inward legs: pick an existing batch **or** enter the three "new batch" fields, and the batch is created at submit. |
| `buying.PurchaseInvoiceItem` | same four | Same inward rule (only when `update_stock`). |
| `selling.SalesInvoiceItem` | `batch` FK null | Outward; required for batch items when `update_stock`. |
| `selling.SalesOrderItem` | `batch` FK null | **Informational preferred batch**, validated for item match only. No stock effect (an SO posts nothing). Copied to the SI line by the order-to-invoice mapping. |

Rules, enforced at the **submit gate** and re-asserted centrally in `ledger.post()`:

- Item `has_batch_no` iff the line carries a batch. A non-batch item with a batch is refused.
- On an inward line with no `batch` and a batch item: the batch is created from the three new-batch fields (item shelf life fills the expiry; manufacturing date defaults to the posting date) and written back to `line.batch` inside the submit transaction (line rows are plain rows; the GST design already snapshots on lines at submit). Picking a batch **and** filling the new-batch fields is a 400.
- An SI with `update_stock = False` must not carry batches (400): no stock moves, so no false traceability.
- Manufacture finished-goods lines follow the inward rule: batch auto-created with `manufacturing_date = posting_date` and expiry from shelf life.
- `amend()` copies `batch` as-is, so a re-submitted amendment reuses the same lot. This is correct, and the batch's `reference_name` still names the first voucher. This is accepted.

### 1.3 Batch balance: SLE-derived versus a per-batch Bin (recommendation: derived)

**Recommendation: no `BatchBin`.** Add `StockLedgerEntry.batch` and derive:

```
batch_balance(item, warehouse, batch, as_of=None) =
  SLE.filter(batch=…, warehouse=…, is_cancelled=False[, posting_date<=as_of]).aggregate(Sum(qty), Sum(stock_value_difference))
```

Justification against the self-healing mandate:

- "Store immutable facts, derive everything else." The SLE already is the fact. A per-batch Bin would be a second projection to keep in sync on `post()`, `reverse()` and `repost()`, and a second thing `verify_integrity` has to police.
- `repost()` and `verify_integrity` need no change for balances: they already replay and check SLE sums per item and warehouse. With the rule "batch-tracked item implies every SLE has a batch", `Σ batch balances == Bin.actual_qty` holds by construction and is asserted as a check.
- ERPNext itself computes batch quantities on read from Serial and Batch Entry rows (`get_batch_qty`); `Batch.batch_qty` is only a cache.
- Concurrency is already solved. `ledger.post()` takes `select_for_update` on the Bin row for (item, warehouse) before computing anything. Every posting that touches a batch in that warehouse is therefore serialised, and the "no negative batch" check (a `SUM` evaluated after the lock) is race-free without a new lock.
- Cost: one indexed aggregate per batch leg. A spice factory posts tens of SLEs a day per batch. Index: partial `Index(fields=["batch", "warehouse"], condition=Q(batch__isnull=False))`, plus `Index(fields=["item", "warehouse", "batch"])`. If it ever gets slow, a projection can replace `batch_balance()` behind the same seam without touching callers.

There is no DB-level "batch balance ≥ 0" constraint (it is derived). The guarantees are: enforced in `ledger.post()` and `reverse()` under the Bin lock, and detected by `verify_integrity`.

### 1.4 Valuation per batch or per item

- **ERPNext:** with Serial and Batch Bundles, new batches default to `use_batchwise_valuation = 1`. Each batch carries its own moving rate, and an issue is costed at that batch's rate. Older or opted-out batches use the item-warehouse method (FIFO queue or moving average).
- **Us (v1):** Moving Average stays the valuation method everywhere (G1, FIFO unimplemented, unchanged). For `has_batch_no` items the outgoing rate is the batch's own average in that warehouse, derived from SLE (`SUM(stock_value_difference)/SUM(qty)` over that batch and warehouse). Two turmeric lots bought at 108 and 114 are therefore costed correctly into the powder and into COGS. Because the rate is taken from facts at the moment of posting and the SLE stores the resulting value, `repost()` still replays plain sums, `Bin.valuation_rate = stock_value/qty` still holds, and `verify_integrity` needs no valuation change. If the batch average is 0 (zero-cost receipt) the existing "use the passed rate" fallback applies.
- A transfer's incoming leg keeps using `use_outgoing_rate`, so a batch transferred to WIP keeps its cost.

### 1.5 `stock/services/ledger.py` changes (the only functional change to the posting core)

New optional keys per SLE dict passed to `post()`:

```
batch          Batch | pk | None
incoming_value Decimal | None     # stock-in only; overrides qty*rate (value is exact, rate = value/qty quantized to 6dp)
allow_expired  bool = False       # stock-out only; Material Issue passes True
```

In `post()`, after the Bin lock and before the valuation math:

1. `batches.validate_leg(item, batch, qty, warehouse, posting_date, allow_expired)`:
 - `item.has_batch_no` iff `batch is not None`; `batch.item_id == item.id`; batch not disabled.
 - stock-out: `batch.expiry_date` set and `< posting_date` raises "Batch X expired on D" unless `allow_expired`; `batch_balance(...) >= abs(qty)` else "Insufficient stock in batch X".
2. Stock-out rate: `batches.batch_rate(...)` for batch items, else `bin.valuation_rate` (current code).
3. Stock-in value: `incoming_value` if given, else `qty × rate` (current code).
4. The created SLE carries `batch`.

In `reverse()`:

- Copy `batch` onto the reversal row.
- After flagging the original cancelled, if the original was an inward leg with a batch, assert `batch_balance(...) >= 0` (else the same "cancel the later transactions first" error). Both guards run inside the existing `transaction.atomic()`.

`repost()` is unchanged. New module `stock/services/batches.py`: `batch_balance`, `batch_rate`, `validate_leg`, `auto_create_batch`, `fefo_candidates`, `trace`. `StockLedgerEntry.save()/delete()` immutability is unchanged.

### 1.6 FEFO suggestion and expiry blocking

Ordering key (ERPNext's auto-pick by expiry): `(expiry_date IS NULL, expiry_date, manufacturing_date, id)`. Batches with no expiry sort last. Expired batches and zero balances are excluded.

- `GET /api/stock/batches/fefo/?item=&warehouse=&qty=&posting_date=`: response `{"item", "warehouse", "requested_qty", "allocated_qty", "shortfall", "allocations": [{"batch": id, "batch_no", "expiry_date", "available_qty", "allocate_qty"}]}`.
- `GET /api/stock/batches/?item=&warehouse=&available=1&posting_date=` returns the same FEFO order (annotated `available_qty`, `days_to_expiry`) for the line batch picker. A standard paginated list, so `LinkField` works unchanged.
- Submit gates tell the user the suggestion: "Batch required for Item X; FEFO suggests B1 (400.000), B2 (100.000)".
- Why no silent auto-allocation at submit: a line has one batch field, so an auto-split would leave the document unable to say which lot was sold. The UI has a "Suggest batches" action (see 9).

---

## 2. BOM

### 2.1 Models (`manufacturing` app)

```
BOM(TimeStampedModel)
 name                     CharField(60, unique)           # NamingSeries "BOM-", e.g. BOM-HALDI-P-001
 company                  FK(core.Company, PROTECT)
 item                     FK(stock.Item, PROTECT, related_name="boms")    # the product made
 quantity                 DecimalField(18,3)              # output basis the rows are written for
 process_loss_percentage  DecimalField(7,3, default=0)    # expected loss on that basis
 rate_basis               CharField(20) {"Valuation Rate","Standard Rate"}  default "Valuation Rate"
 is_active                BooleanField(default=True)
 is_default               BooleanField(default=False)
 description              TextField(blank)
 CHECK quantity > 0
 CHECK process_loss_percentage >= 0 AND < 100
 CHECK NOT (is_default AND NOT is_active)
 UNIQUE(company, item) WHERE is_default = true       (partial unique)

BOMItem
 bom        FK(BOM, CASCADE, related_name="items")
 idx        PositiveIntegerField
 item       FK(stock.Item, PROTECT)
 qty        DecimalField(18,3)                    # per BOM.quantity, in item.stock_uom
 bom_no     FK(BOM, null, PROTECT, related_name="+")   # optional sub-BOM for multi-level
 CHECK qty > 0
 UNIQUE(bom, item)

BOMScrapItem                                      # by-products / scrap actually stocked
 bom        FK(BOM, CASCADE, related_name="scrap_items")
 idx, item FK(PROTECT)
 qty        DecimalField(18,3)                    # per BOM.quantity
 rate       DecimalField(18,6, default=0)         # scrap value per unit
 CHECK qty > 0 AND rate >= 0
 UNIQUE(bom, item)
```

Service-level validation (cross-row, so not a DB CHECK):

- Both `BOM.item` and every row item must be stock items.
- Row item ≠ BOM item.
- If `bom_no` is set: `bom_no.item == row.item`, same company, active.
- The sub-BOM graph is acyclic (DFS at save).

Setting `is_default` atomically unsets the previous default for (company, item) in the serializer, inside one transaction.

### 2.2 BOM is a master, not a submittable (deliberate divergence)

ERPNext's BOM is submittable, and "Update Cost" and `is_default` edits go through `db_set`. Here `SubmittableDocument.save()` would block that, and cost is a derived value anyway. Instead:

- `BOM` is a `TimeStampedModel` master written with `NestedMasterWriteMixin` (the GST Task 1 mixin).
- **Usage lock:** once a BOM is referenced by a submitted Work Order or Stock Entry, the serializer refuses changes to its rows, `quantity` and `process_loss_percentage` (409 "create a new BOM version"). `is_active`, `is_default` and `description` stay editable. Work Orders snapshot their required items, so editing a draft BOM never changes a running order.
- A new version is a new BOM; the old one is deactivated.

### 2.3 Process loss semantics (ERPNext's convention, stated precisely)

`BOM.quantity` is the **input-equivalent ("completed") basis**: the rows produce `quantity` units of work, of which `process_loss_percentage` evaporates or turns to dust. **Good output = quantity × (1 − loss/100).** A Work Order for `qty` consumes `row.qty × qty / bom.quantity` of each row, and expects `qty × (1 − loss/100)` good output. ERPNext's Manufacture entry does the same: `fg_completed_qty` is the basis, and the finished row is `fg_completed_qty − process_loss_qty`.

- Turmeric grinding BOM: `item = HALDI-P`, `quantity = 100.000` Kg, one row `HALDI-W 100.000` Kg, `process_loss_percentage = 1.250`. A WO of 1,000 kg consumes 1,000 kg whole turmeric and expects 987.500 kg powder. 12.500 kg of loss is expected.
- Percentages are `DecimalField(7,3)` (the repo-wide rule from the GST divergences).
- The percentage is the **expected** loss. The **actual** loss is entered on the Manufacture entry (finished quantity), and the yield report compares the two.

### 2.4 Costing (derived on read, `manufacturing/services/costing.py`)

```
row.rate   = sub-BOM unit_cost                      if row.bom_no
         = company-wide weighted valuation of row.item (Σ Bin.stock_value / Σ Bin.actual_qty over company warehouses)
           falling back to Item.standard_rate     if rate_basis = "Valuation Rate"
         = Item.standard_rate                     if rate_basis = "Standard Rate"
row.amount = quantize(row.qty × row.rate, 0.01)
raw_material_cost = Σ row.amount
scrap_value       = Σ scrap.qty × scrap.rate
good_qty          = quantity × (1 − loss/100)
unit_cost         = quantize((raw_material_cost − scrap_value) / good_qty, 0.000001)
```

Multi-level cost rolls up through `bom_no`. Mirroring ERPNext's `set_rate_of_sub_assembly_item_based_on_bom`, plan costing uses the sub-BOM cost; **actual** cost always comes from the ledger at Manufacture time. Nothing is persisted, so a "stale BOM cost" cannot exist. Endpoints: `GET /boms/{id}/cost/` and `GET /boms/{id}/explode/` (flattened multi-level tree: per leaf, qty per BOM basis, and a `needs_sub_assembly` flag when a row has a `bom_no`).

### 2.5 The raw, ground, blended, packed chain

```
Turmeric Whole (Kg, batch)
 │ BOM-HALDI-P-001   quantity 100 Kg, loss 1.250%, row: HALDI-W 100 Kg
Turmeric Powder bulk (Kg, batch)                          ← "ground"
 │ BOM-GM-001        quantity 100 Kg, loss 0.500%, rows: coriander powder 35, cumin powder 20, black pepper powder 10,
 │                   cardamom powder 3, clove powder 2, cinnamon powder 12, fennel powder 18
Garam Masala bulk (Kg, batch)                             ← "blended"
 │ BOM-HALDI-P100-001  quantity 100 Nos, loss 0: HALDI-P 10 Kg, PKG-POUCH-100 100 Nos, PKG-CARTON 4 Nos
Turmeric Powder 100g Pouch (Nos, batch)                   ← "packed"
```

Answers to the BOM questions:

- **Should grinding be a BOM with a loss %?** Yes. It is the only place the 1.25% is modelled, it feeds the unit cost, and the yield report measures actual against it.
- **Should packing be a BOM?** Yes, one BOM per pack size. The pouch and carton are real consumed materials with cost, the recipe differs per size (10 kg powder per 100 × 100g; 20 kg per 100 × 200g; 50 kg per 100 × 500g), and a Work Order gives planned vs actual and batch genealogy. Repack cannot do this (section 4).
- Each level is a separate Work Order. v1 has no Production Plan that auto-creates sub-assembly orders; `explode` tells the planner what to make first.
- **Operations in v1: no.** ERPNext's `with_operations`, routing, workstations, job cards, time logs and operating cost are out. A spice grinder run is one step; conversion cost (labour, power) is an open question (Q2).

---

## 3. Work Order and manufacturing Stock Entries

### 3.1 Models

```
WorkOrder(SubmittableDocument)        # name series "WO-2026-"
 company            FK(Company, PROTECT)
 production_item    FK(Item, PROTECT)
 bom                FK(BOM, PROTECT)
 qty                DecimalField(18,3)          # planned, on the BOM basis (includes expected loss)
 source_warehouse   FK(Warehouse, PROTECT)      # raw materials come from here
 wip_warehouse      FK(Warehouse, null, PROTECT)  # required unless skip_transfer
 fg_warehouse       FK(Warehouse, PROTECT)      # finished goods go here
 scrap_warehouse    FK(Warehouse, null, PROTECT)  # required if the BOM has scrap rows
 skip_transfer      BooleanField(default=False) # consume straight from source_warehouse
 planned_start_date DateField(null), expected_end_date DateField(null)
 stopped            BooleanField(default=False) # a fact set by Stop / Resume actions (audited)
 # ---- derived projection (rebuilt by refresh_progress / repost_work_orders) ----
 produced_qty       DecimalField(18,3, default=0)   # Σ Manufacture fg_completed_qty (basis, includes loss)
 process_loss_qty   DecimalField(18,3, default=0)   # Σ process_loss_qty
 status             CharField(20)  # "Draft","Not Started","In Process","Completed","Stopped","Cancelled"
 CHECK qty > 0
 CHECK produced_qty >= 0 AND process_loss_qty >= 0 AND process_loss_qty <= produced_qty AND produced_qty <= qty

WorkOrderItem                          # required materials, server-generated from the BOM
 work_order   FK(WorkOrder, CASCADE, related_name="required_items")
 idx, item FK(PROTECT)
 source_warehouse FK(null)             # row override, default = header
 required_qty    DecimalField(18,3)    # round_half_up(bom_row.qty × wo.qty / bom.quantity, 3dp)
 transferred_qty DecimalField(18,3, default=0)   # derived
 consumed_qty    DecimalField(18,3, default=0)   # derived
 CHECK required_qty > 0 AND transferred_qty >= 0 AND consumed_qty >= 0
 UNIQUE(work_order, item)
```

- `required_items` are generated on draft save (create, or when `bom` or `qty` change) and are read-only in the API. A submitted WO is frozen, so editing the BOM later does not change it.
- No sales-order link, no overproduction (`produced_qty ≤ qty` CHECK), no operations.
- Derived columns are updated with `QuerySet.update()` (pitfall 4). `amend()` is safe because a WO cannot be cancelled while it has submitted entries, so a cancelled WO has zero progress.

**Status (derived, stored for filtering):**

```
docstatus 0 → "Draft"          docstatus 2 → "Cancelled"
stopped → "Stopped"
produced_qty >= qty → "Completed"
Σ transferred > 0 or produced_qty > 0 → "In Process"
else "Not Started"
```

`refresh_progress(wo)` recomputes `transferred_qty` (Material Transfer for Manufacture rows into WIP), `consumed_qty` (Manufacture consumption rows), `produced_qty`, `process_loss_qty` and `status` from submitted Stock Entries (`docstatus=1`) and runs in the **same transaction** as the entry's submit and cancel. On submit the entry is already flagged submitted; on cancel it is already flagged cancelled, so one function serves both. `manage.py repost_work_orders [--work-order N]` rebuilds it from facts, and `verify_integrity` asserts stored equals recomputed.

**Actions:** `submit` (gates below), `cancel` (refused while any submitted entry references the WO: "cancel the stock entries first"), `amend`, `stop`/`resume` (set `stopped`, then refresh; a stopped WO accepts no new submitted entries), `make-stock-entry`.

**Submit gate:**

- BOM active, same company, `bom.item == production_item`.
- All warehouses are non-group and belong to the company.
- `wip_warehouse` present unless `skip_transfer`; `wip != fg`.
- `scrap_warehouse` present when the BOM has scrap rows.
- `required_items` non-empty.

### 3.2 Stock Entry extensions (`stock/models.py`)

```
StockEntryType += MATERIAL_TRANSFER_FOR_MANUFACTURE ("Material Transfer for Manufacture"), MANUFACTURE ("Manufacture"), REPACK ("Repack")
stock_entry_type max_length 25 → 40

StockEntry, new fields
 work_order        FK("manufacturing.WorkOrder", null, PROTECT, related_name="stock_entries")
 bom               FK("manufacturing.BOM", null, PROTECT, related_name="+")
 fg_completed_qty  DecimalField(18,3, default=0)   # basis completed in this entry
 process_loss_qty  DecimalField(18,3, default=0)   # SERVER-OWNED = fg_completed_qty − Σ finished qty
 CHECK fg_completed_qty >= 0 AND process_loss_qty >= 0 AND process_loss_qty <= fg_completed_qty

StockEntryItem, new fields (besides the batch ones in 1.2)
 is_finished_item    BooleanField(default=False)
 is_scrap_item       BooleanField(default=False)
 cost_allocation_per DecimalField(7,3, null)       # Repack with several finished rows
 CHECK NOT (is_finished_item AND is_scrap_item)
 CHECK cost_allocation_per IS NULL OR (>= 0 AND <= 100)
```

Cross-model migration order matters (the `stock` and `manufacturing` apps reference each other): `stock.0006` (batches), then `manufacturing.0001`, then `stock.0007` (these Stock Entry fields). See 10.1.

### 3.3 Material Transfer for Manufacture

- Requires a submitted, not stopped WO. Rows: `source_warehouse` = the WO source (or row override), `target_warehouse` = `wip_warehouse`, item in the WO required items, batch required for batch items (the **same batch** is on both legs).
- Cap: `qty ≤ required_qty − transferred_qty` per item (checked under `select_for_update` on the WO row).
- Posting is identical to the existing Material Transfer (out leg, then in leg with `use_outgoing_rate`). The batch-wise rate travels with the stock. **No GL** (single inventory account).
- Not needed when `skip_transfer = True`.

### 3.4 Manufacture entry (the core)

Rows:

- **Consumption** rows: `source_warehouse` only (WIP, or the source warehouse when `skip_transfer`), item ⊆ BOM rows, a batch per row.
- Exactly **one finished** row (`is_finished_item`): `target_warehouse` = `fg_warehouse`, item = `bom.item`, batch auto-created.
- Optional **scrap** rows (`is_scrap_item`): `target_warehouse` only, item ⊆ BOM scrap rows, `valuation_rate` = scrap value per unit (prefilled from the BOM).

Server-derived header: `process_loss_qty = fg_completed_qty − finished qty`; refused if negative (cannot produce more than the basis).

WO-bound entries are validated under the WO lock:

- `fg_completed_qty ≤ qty − produced_qty` (partial completion allowed, any number of entries).
- A WIP consumption cannot exceed `transferred_qty − consumed_qty` per item.
- WO not stopped.

**Posting algorithm** (`manufacturing/services/production.py::post_manufacture(se)`, called from `StockEntry.on_submit` for the Manufacture and Repack types):

1. `sles_out = stock_service.post(se, consumption_legs)`; `consumed = −Σ stock_value_difference`. Each leg is costed at its batch's (or Bin's) rate.
2. Scrap value `S = Σ round2(qty × valuation_rate)` over scrap rows.
3. `fg_total = consumed − S`; refuse if `< 0`.
4. Allocate `fg_total` over finished rows. A Manufacture has one row (100%). A Repack uses `cost_allocation_per`, rounded to 2 dp, with the residue going to the last row, so `Σ allocations == fg_total` exactly.
5. `stock_service.post(se, scrap_legs + fg_legs)`, each with `incoming_value` set (scrap: `round2(qty × rate)`; FG: its allocation), so `Σ stock_value_difference` over the voucher is exactly **0.00**.
6. Write back `valuation_rate = value/qty` (6 dp) on the finished and scrap lines for display, and `batch` on auto-created rows.
7. **GL: none.** All legs are on the same inventory account, so value moved but did not change. Assert `Σ == 0` and raise if not, so a rounding bug can never silently post to the adjustment account.
8. `work_orders.refresh_progress(se.work_order)` if bound.

`on_cancel`: `stock_service.reverse(se)` and `gl_service.reverse(se)` (no-op), then `refresh_progress`. The batch negative-balance guard blocks cancelling a Manufacture whose finished goods have already been sold.

**FG valuation is the sum of consumed cost; process loss raises unit cost.** The finished quantity is smaller than the basis and the value is not reduced. A worked example appears in 10.3.

### 3.5 Why no WIP GL account, and what ERPNext does

ERPNext gives a "Work In Progress" warehouse its own account (an asset under Stock Assets). A transfer into WIP therefore posts `Dr WIP / Cr Stock In Hand`, and the Manufacture entry reverses it into finished goods. Our `Company` has a single `default_inventory_account` and no per-warehouse account, so transfers are GL-neutral (exactly what the existing Material Transfer does). The inventory GL still equals `Σ Bin.stock_value`, so `perpetual_inventory_balance` keeps passing. If the accountant wants WIP shown separately on the balance sheet, that needs `Warehouse.account` (v2, Q1).

---

## 4. Repack

- Stock Entry type `Repack`: ≥1 consumption rows, ≥1 finished rows, no BOM, no Work Order. It uses the same `post_manufacture` engine and a `cost_allocation_per` per finished row that sums to 100 (submit gate; a single finished row implies 100). No process loss field (loss is just `fg_completed_qty`, which is 0 for Repack).
- **Why it is not the packing mechanism.** ERPNext's `get_basic_rate_for_repacked_items` gives every finished row `outgoing_cost / total_fg_qty`, the **same rate per unit regardless of pack size**. With outputs in Nos, a 100g and a 500g pouch would be costed identically. It also cannot consume pouches and cartons with a recipe, plan quantities or track planned vs actual. Use Repack only for relabelling or splitting one lot into several items where an explicit allocation makes sense (for example 25 kg bulk into two bulk lots).
- Cost: roughly 20 lines on top of Manufacture. Include it, but the demo and docs steer packing to BOMs.

---

## 5. Price lists

### 5.1 Models (`stock` app, as in ERPNext)

```
PriceList(TimeStampedModel)
 name      CharField(140, unique)
 currency  CharField(3, default="INR")
 selling   BooleanField(default=False)
 buying    BooleanField(default=False)
 enabled   BooleanField(default=True)
 CHECK selling OR buying
 # v1: currency must equal the company currency of any document using it (no conversion)

ItemPrice(TimeStampedModel)
 item        FK(Item, PROTECT, related_name="prices")
 price_list  FK(PriceList, PROTECT, related_name="prices")
 uom         FK(UOM, null, PROTECT)            # null = item's stock UOM
 rate        DecimalField(18,6)
 valid_from  DateField(null)
 valid_upto  DateField(null)
 CHECK rate >= 0
 CHECK valid_upto IS NULL OR valid_from IS NULL OR valid_upto >= valid_from
 UNIQUE(item, price_list, valid_from) WHERE uom IS NULL AND valid_from IS NOT NULL
 UNIQUE(item, price_list)             WHERE uom IS NULL AND valid_from IS NULL
```

Overlapping validity windows are not blocked by the DB (SQLite is used in dev; an exclusion constraint is Postgres-only). The resolver is deterministic about overlap (latest `valid_from` wins), as ERPNext's is.

**Defaults:**

- `Customer.default_price_list` (FK null).
- `CustomerGroup.default_price_list` (FK null).
- `Company.default_selling_price_list` and `Company.default_buying_price_list` (FK null; stands in for Selling and Buying Settings).
- `Supplier.default_price_list`.
- Document headers (`SalesOrder`, `SalesInvoice`, `PurchaseOrder`, `PurchaseInvoice`): `price_list` FK null (user-chosen or defaulted at create).
- Selling and buying lines: `price_list_rate DecimalField(18,6, null)`. This is a snapshot of the fetched rate, so a price-override report is derivable. Rate remains editable.

### 5.2 Distributor-specific pricing

ERPNext's Item Price can also carry a `customer`/`supplier` (and batch). We drop those columns. **A distributor's prices are a price list**, either one per tier ("Distributor Standard", "Distributor Premium", "Super Stockist") or one per distributor, assigned on `Customer.default_price_list`. Reasons: `stock` cannot FK `selling.Customer`; a price list is also what the user sees and exports; "Distributor X gets 3% below tier" is just a copy of the list. B2C online is a plain "Online MRP" list assigned to the online customer.

### 5.3 Rate fetch order (ERPNext, simplified)

1. **Price list** for a selling document: explicit `price_list` on the document, else `Customer.default_price_list`, else the first ancestor `CustomerGroup.default_price_list` (ERPNext reads only the customer's own group; walking up the tree is a small, deliberate improvement), else `Company.default_selling_price_list`. Buying: explicit, `Supplier.default_price_list`, `Company.default_buying_price_list`.
2. **Item Price row** (ERPNext's `get_item_price` ordering minus party and batch): same item and list; `uom IS NULL` or equal to the line UOM; `valid_from <= transaction_date <= valid_upto` (null bounds open); ordered by rows with a `valid_from` first, then `valid_from` descending, then a UOM-specific row beats a blank one, then `pk` descending as the deterministic tie-break.
3. **No row found:** rate is `null`. The client keeps its existing `standard_rate` prefill (ERPNext does not fall back to a standard rate either), and the server, when `rate` was omitted on save, falls back to `Item.standard_rate` if `> 0`, else returns 400 "No price for Item X in Price List Y".
4. **Rounding:** `rate = round_half_up(price_list_rate, 2)` for the line; `price_list_rate` keeps 6 dp.

**Endpoint** `GET /api/stock/item-prices/rate/?item=<id>&type=selling|buying&company=<id>&customer=<id>|supplier=<id>&price_list=<id>&transaction_date=YYYY-MM-DD&uom=<id>`:

```json
{ "item": 12, "price_list": 3, "price_list_name": "Distributor Standard",
"price_list_source": "explicit|customer|customer_group|company",
"item_price": 41, "price_list_rate": "14.500000", "rate": "14.50",
"currency": "INR", "valid_from": "2026-04-01", "valid_upto": null }
```

When nothing is found, `item_price`, `price_list_rate` and `rate` are `null`. Permission: `can_read` on `stock.ItemPrice`.

**Server-side authority.** Selling and buying line serializers make `rate` optional. If omitted, the same resolver runs on create and draft update and fills `rate` and `price_list_rate`. A supplied `rate` is kept (and `price_list_rate` still snapshotted). `POST /api/selling/sales-orders/{id}/update-rates/` (and the SI, PO and PI equivalents), draft only, re-applies the price list to every line (the answer to "customer changed after rows were added").

---

## 6. Credit limits

### 6.1 Fields (on `selling.Customer`)

```
credit_limit                DecimalField(18,2, default=0)    # 0 = no limit (ERPNext)
bypass_credit_limit_check   BooleanField(default=False)
CHECK credit_limit >= 0
```

No per-company child table: a `Customer` row already belongs to exactly one company. A customer-group default and an overdue-billing threshold are out of v1 (divergences).

### 6.2 The check (`selling/services/credit.py`, new)

```
enforce(doc, user):                       # called from validate_before_submit of SO and SI
  c = doc.customer
  if c.bypass_credit_limit_check or c.credit_limit == 0: return
  exposure_before = receivable_balance(c, doc.company)
                  + unbilled_open_orders(c, doc.company, exclude=doc.sales_order_or_self)
  total = exposure_before + doc.rounded_total
  if total <= c.credit_limit: return
  if user is superuser or user has role "Credit Controller":
      record_audit_log(user, "credit_limit_override", doc, changes={limit, exposure_before, doc_total})
      return
  raise ValidationError("Credit limit crossed for X: exposure ₹T (existing ₹E + this document ₹D) exceeds limit ₹L.")
```

- **Where:** `SalesOrder.validate_before_submit` and `SalesInvoice.validate_before_submit`, before any posting. Both override `submit(self, user=None)` to stash `self._submit_user = user` before delegating, so `core/models.py` is untouched. An ORM submit without a user (tests, seeds) is **not** exempt: it fails closed unless the customer has the bypass flag or no limit.
- **Strictly greater than** blocks, as ERPNext (`customer_outstanding > credit_limit`).
- **`unbilled_open_orders`** is ERPNext's `outstanding_based_on_so`: for the customer's other submitted SOs, the not-yet-invoiced share, `rounded_total × Σ remaining line amount ÷ Σ line amount`, using `accounts.mapping.remaining_order_lines` (billed quantity counts only submitted invoices). At SI submit, the invoice's own `sales_order` is excluded (the invoice replaces that unbilled share).
- **ERPNext differences:** ERPNext rechecks an SI only when it has lines not tied to an SO or DN; we always recheck (receivables may have grown since the SO). ERPNext's `bypass_credit_limit_check` skips the SO check and defers to the invoice; ours skips the customer entirely. A one-line switch, recorded as a divergence.
- **Override role:** a seeded role `Credit Controller` (ERPNext's "credit controller" role setting, default Sales Master Manager); name held in `settings.CREDIT_CONTROLLER_ROLE`. Every override is audited.

**Exposure source: the `receivable_balance()` seam**

```
def receivable_balance(customer, company) -> Decimal:
  # 1. erp-72 path, used only when accounts/services/outstanding.py provides a party-level,
  #    advance-aware total (see "switch condition")
  # 2. fallback (shipped now, and what ERPNext defines as customer outstanding):
  GLEntry.filter(company, party_type="Customer", party=customer.name,
                 account__account_type="Receivable", is_cancelled=False)
         .aggregate(Sum(debit) − Sum(credit))
```

- **Shipped and stated:** the GL party balance on Receivable-type accounts. erp-72's `outstanding_amount`/`outstanding.py` does not exist in the repo yet, and ERPNext's own definition is the GL balance.
- **Why the account-type filter:** payments stamp the party on the bank leg too (pitfall 6). Without it a received payment would net to zero.
- **Switch condition:** once erp-72 lands, use its service **only if** it returns a party-level number that equals the GL receivable balance, including unallocated advances and opening-balance journal entries (the Tally import posts those as party JEs). A bare `Σ SalesInvoice.outstanding_amount` ignores both and would understate exposure. Until then keep the GL path, and add a test asserting `Σ SI.outstanding_amount == GL balance` for a customer with no advances.
- **Recommendation to erp-72:** expose `customer_outstanding(customer, company)` defined as the Receivable-account GL party balance. Then the seam is a one-line import.
- `GET /api/selling/customers/{id}/credit-status/` returns `{credit_limit, bypass, receivable_balance, unbilled_orders, exposure, available, source: "gl"|"outstanding"}`. The SO and SI forms show a banner from it.

---

## 7. Reports (all derived, function views, `HasRolePermission` via `require_model_permission`)

| Endpoint | Params | Rows | Permission model |
|---|---|---|---|
| `GET /api/stock/reports/batch-wise-balance/` | `item, warehouse, batch, company, as_of, include_zero=0` | `item_code, item_name, warehouse_name, batch_no, manufacturing_date, expiry_date, days_to_expiry, balance_qty, valuation_rate, stock_value`, plus `total_balance_qty`, `total_stock_value`. One `GROUP BY item, warehouse, batch` over non-cancelled SLE up to `as_of`. | `stock.Bin` |
| `GET /api/stock/reports/expiry/` | `company, warehouse, within_days=60, include_expired=1` | Batches with balance and expiry within the window, plus `status` (`Expired`/`Expiring`/`OK`), `balance_qty`, `stock_value` at risk. Sorted by expiry. | `stock.Bin` |
| `GET /api/manufacturing/reports/production-yield/` | `company, from_date, to_date, item, bom, group_by=stock_entry\|work_order\|item` | Planned qty (WO `qty` when bound), completed basis qty (`fg_completed_qty`), good output, loss qty, **actual loss %**, **expected loss % (BOM)**, **variance (pp)**, RM consumed value, FG unit cost, scrap qty and value. | `manufacturing.WorkOrder` |
| `GET /api/manufacturing/reports/bom-cost/` | `bom` or `company`+`active=1` | Per BOM: components (qty, rate, amount, source), `raw_material_cost`, `scrap_value`, `unit_cost`, `last_actual_unit_cost` (latest submitted Manufacture), default selling price (company default list) and margin %. | `manufacturing.BOM` |
| `GET /api/stock/batches/{id}/trace/` | — | Every SLE of the batch with voucher links, and for Manufacture/Repack vouchers the sibling batches consumed and produced (a recall report). | `stock.Batch` |
| `GET /api/manufacturing/boms/{id}/explode/` | — | Multi-level tree with cumulative qty per BOM basis. | `manufacturing.BOM` |

Quantities are strings (the existing report convention). Cost reports are gated on manufacturing-manager roles (see 11.4).

---

## 8. API surface summary (rows to add to `backend/docs/API.md`)

**`stock`**

- `batches/`, `batches/{id}/` CRUD (no delete once used: PROTECT). `batches/fefo/`, `batches/{id}/balances/` (per-warehouse), `batches/{id}/trace/`.
- `price-lists/`, `item-prices/` CRUD, `item-prices/rate/` GET.
- `reports/batch-wise-balance/`, `reports/expiry/`.
- `items/`: new fields `has_batch_no`, `has_expiry_date`, `shelf_life_in_days`.
- `stock-entries/`: new types, header fields `work_order, bom, fg_completed_qty, process_loss_qty` (last one server-owned) and line fields `batch, supplier_batch_no, manufacturing_date, expiry_date, is_finished_item, is_scrap_item, cost_allocation_per`.
- `sle/`: new read-only `batch`/`batch_no` fields.

**`manufacturing`** (new app, mounted at `/api/manufacturing/`)

- `boms/` CRUD (nested `items`, `scrap_items`, read-only derived `raw_material_cost`, `unit_cost`, per-row `rate/amount`), `boms/{id}/cost/`, `boms/{id}/explode/`.
- `work-orders/` CRUD (nested read-only `required_items`) and `submit/ cancel/ amend/ stop/ resume`.
- `work-orders/{id}/make-stock-entry/` POST `{"stock_entry_type": "Material Transfer for Manufacture"|"Manufacture", "qty": "500.000"}` (qty optional, default remaining) → a **draft** Stock Entry, 201. Rows come from the WO required items scaled to `qty`, capped by transferred/consumed, with raw-material batches FEFO-filled from the source warehouse (any unallocated remainder is a row with no batch, which will fail at submit and show the shortage). For Manufacture: finished row = `qty − expected loss`, scrap rows from the BOM, `process_loss_qty` pre-set. Permission as the other mapping actions: `can_read` on the source and `can_create` on Stock Entry.
- `reports/production-yield/`, `reports/bom-cost/`.

**`selling` / `buying`**

- `customers/`: `credit_limit`, `bypass_credit_limit_check`, `default_price_list`. `customer-groups/`: `default_price_list`. `suppliers/`: `default_price_list`. `customers/{id}/credit-status/`.
- SO/SI/PO/PI: `price_list` header, line `price_list_rate`, `rate` optional on write, `update-rates/` POST. SO/SI lines: `batch`. PI lines: the four batch fields.

All new write endpoints are atomic (nested write mixin or `transaction.atomic()`), errors are 400 (never 500), and non-draft writes are 409 through `SubmittableViewSetMixin`.

---

## 9. Frontend

### 9.1 Resource configs

New `frontend/src/resources/manufacturing.ts` (module `manufacturing`, icon `Factory`, added to `MODULES` and `RESOURCES` in `registry.ts`):

- **`boms`**: header `item` (link, `is_stock_item`), `company`, `quantity`, `process_loss_percentage`, `rate_basis`, `is_active`, `is_default`. Child tables `items` (item link, qty, optional `bom_no` link filtered by `{item: row.item, company, is_active: true}`, read-only server `rate`/`amount`) and `scrap_items` (item, qty, rate). `renderSummary` shows raw cost, scrap value, expected good output (`quantity × (1 − loss%)`) and unit cost from the saved record.
- **`work-orders`** (submittable): `production_item`, `bom` (link filtered by item/company/active, ordered default first), `qty`, `source_warehouse`, `wip_warehouse` (hidden when `skip_transfer`), `fg_warehouse`, `scrap_warehouse`, `skip_transfer`. Read-only `produced_qty`, `process_loss_qty`, `status`. The `required_items` child table is read-only with `transferred_qty` and `consumed_qty` columns. Actions (kind "request"): **Start (transfer materials)** → `make-stock-entry` with `Material Transfer for Manufacture` (hidden when `skip_transfer`), **Finish (manufacture)** → `make-stock-entry` with `Manufacture`, **Stop** / **Resume**; each opens the returned draft Stock Entry via `resultTo`. List `status` column uses a `render` badge.

Additive changes:

- **`stock.ts`**:
- Item fields `has_batch_no`, `has_expiry_date`, `shelf_life_in_days` (`hidden` unless `has_expiry_date`).
- New resources `batches`, `price-lists`, `item-prices`.
- Stock Entry: new type options, header `work_order`, `bom`, `fg_completed_qty` and read-only `process_loss_qty` (all `hidden` by type), and line columns `batch` (link to `/stock/batches/` with filters `{item: row.item, warehouse: row.source_warehouse, available: 1, posting_date: form.posting_date}`, FEFO ordered, for outward legs), `supplier_batch_no/manufacturing_date/expiry_date` (hidden unless an inward leg of a batch item), `is_finished_item`, `is_scrap_item`, `cost_allocation_per` (Repack).
- Reports `batch-wise-balance` and `expiry` (new `ReportConfig`s).
- **`selling.ts`**: Customer `credit_limit`, `bypass_credit_limit_check`, `default_price_list`; SO/SI `price_list` header, line `batch` (FEFO picker) and `price_list_rate` (read-only), a `CreditBanner` rendered through `renderSummary` that queries `credit-status`, and an **Update rates** `DocAction` (request).
- **`buying.ts`**: PI line batch fields, `price_list`.
- `itemLink.fill` additionally carries the item's `has_batch_no` into the row (as `_has_batch_no`, ignored by the server) so `hidden` conditions can read it.

New i18n namespace `mfg.*` in `i18n/en.manufacturing.ts` merged like `en.gst.ts`; other languages fall back to English until translated.

### 9.2 Framework gaps (all small and additive; each is optional)

| Gap | Need | Proposed change |
|---|---|---|
| G-F1 | Rate fetch on item pick and default BOM on production item pick are async and need parent context. `LinkDef.fill` is sync and sees only the record and row. | `LinkDef.fillAsync?: (record, row, parentValues) => Promise<Record<string, any>>`, awaited in `ChildTable` and `DocumentPage` next to `fill`, and merged the same way. |
| G-F2 | DocAction "request" cannot send a body, so a partial Work Order qty cannot be requested. | `DocAction.body?: (doc) => Record<string, any>` plus an optional `prompt` (`{fieldKey, labelKey}`) shown as a small dialog. Without it, "Finish" uses the remaining qty and partial completion is done by editing the draft entry's quantities. |
| G-F3 | Expiry and yield reports want a coloured status cell. | `ReportColumnDef.render?: (value, row) => ReactNode`. |

No gap for: header-hidden fields by type (`FieldDef.hidden`), second child table, read-only derived columns, mapping actions (`kind: "request"` + `resultTo`).

**Overlap with the frontend redesign** (theme, top bar, module pages) is limited to `registry.ts` (adding a module), `types/index.ts`, `ChildTable.tsx`/`DocumentPage.tsx`/`ReportPage.tsx` (the three gaps) and `Sidebar.tsx`. Put new content in new files and rebase before touching the shared ones (section 11).

---

## 10. Migrations, seeding, demo data

### 10.1 Migration order (no `RunPython` mixed with DDL; no backfill is needed, all new columns default safely)

1. `stock.0006_batches`: `Item` fields and CHECKs, `Batch`, `StockLedgerEntry.batch` plus indexes, `StockEntryItem` batch fields.
2. `stock.0007_price_lists`: `PriceList`, `ItemPrice` and constraints.
3. `core.0009_company_price_lists`: `Company.default_selling_price_list`, `default_buying_price_list` (depends on `stock.0007`).
4. `selling.0008_batch_price_credit`, `buying.0008_batch_price`: line `batch`, trio, `price_list_rate`, headers `price_list`, party defaults, `Customer.credit_limit/bypass_credit_limit_check` + CHECK.
5. `manufacturing.0001_initial` (depends on `stock.0006`).
6. `stock.0008_stockentry_manufacturing` (depends on `manufacturing.0001`): `stock_entry_type` max_length 25→40, Stock Entry/Item manufacturing fields and CHECKs.

The `stock` ⇄ `manufacturing` references are string FKs; the order above avoids a migration cycle.

### 10.2 `seed_defaults` (idempotent)

- Roles: `Manufacturing Manager`, `Credit Controller` (no model rights needed; recognised by name).
- Item groups: `Raw Material`, `Semi-Finished Goods`, `Finished Goods`, `Packing Material`.
- RBAC matrix additions (labels validated against the app registry):
- `stock.Batch`, `stock.PriceList`, `stock.ItemPrice`
- `manufacturing.BOM`, `manufacturing.BOMItem`, `manufacturing.BOMScrapItem`, `manufacturing.WorkOrder`, `manufacturing.WorkOrderItem`
- Stock Manager: batches and stock-entries. Manufacturing Manager: BOM, WO, Stock Entry, Batch, `stock.Bin` read (reports). Sales Manager: PriceList and ItemPrice. Accounts Manager: read on all (cost visibility).

### 10.3 `seed_demo` additions (extend the existing demo company; Nidhi Masala items under it)

Warehouses: `Raw Material Store`, `Work In Progress`, `Packing Material Store`, `Finished Goods Store`, `Scrap`.

Items (HSN/GST rates below are from memory and flagged for the CA to confirm: spices 0904-0910 at 5%, plastic pouches 3923 and cartons 4819 at 18%):

| Code | Name | UOM | Batch | Shelf life |
|---|---|---|---|---|
| HALDI-W | Turmeric (Haldi) Whole | Kg | yes | 365 d |
| HALDI-P | Turmeric Powder (bulk) | Kg | yes | 365 d |
| CORI-W, CORI-P | Coriander Seed, Coriander Powder | Kg | yes | 365 d |
| CUMIN-W, CUMIN-P | Cumin (Jeera) Seed, Cumin Powder | Kg | yes | 365 d |
| PEPPER-W, PEPPER-P | Black Pepper whole, powder | Kg | yes | 540 d |
| CARD-P, CLOVE-P, CINN-P, FENN-P | Cardamom, Clove, Cinnamon, Fennel powder (bought ground) | Kg | yes | 365 d |
| GM-BULK | Garam Masala (bulk blend) | Kg | yes | 365 d |
| HALDI-P-100/200/500, GM-100/200 | Packed pouches | Nos | yes | 365 d |
| PKG-POUCH-100/200/500, PKG-CARTON | Packaging | Nos | no | — |

BOMs:

- Grinding, one each for Haldi, Coriander, Cumin, Pepper: `quantity 100 Kg`, one raw row at 100 Kg, `process_loss_percentage 1.250`.
- `BOM-GM-001` (above), loss 0.500.
- Packing BOMs: `HALDI-P-100` (quantity 100 Nos: HALDI-P 10 Kg, POUCH-100 100, CARTON 4), `HALDI-P-200` (20 Kg, POUCH-200 100, CARTON 5), `HALDI-P-500` (50 Kg, POUCH-500 100, CARTON 10), `GM-100`, `GM-200`.

Price lists: `Retail MRP` (100g ₹20, 200g ₹38, 500g ₹90), `Distributor Standard` (₹14.50, ₹27.50, ₹66), `Distributor Premium` (₹14.00, ₹26.50, ₹64), `Online MRP` (selling), `Raw Material Buying` (buying: whole spices).

Customers: `Sharma Traders` (Distributor Standard, `credit_limit 150000`), `Patel General Stores` (Distributor Premium, `credit_limit 50000`), `Online Store (B2C)` (Online MRP, no limit).

Documents (all through the real submit paths):

1. Purchase Invoices with batches: Haldi-W 2,000 kg @ ₹110.00 (supplier lot `NZB-HW-2610A`), Coriander 1,500 @ ₹95, Cumin 800 @ ₹240, Pepper 300 @ ₹520, cardamom/clove/cinnamon/fennel powders, pouches and cartons.
2. **Haldi chain.** WO-1: 1,000 kg `HALDI-W`→`HALDI-P`. Transfer 1,000 kg to WIP. Manufacture #1: `fg_completed 600`, finished 592.500 (loss 7.500, 1.25%). Manufacture #2: `fg_completed 400`, finished 394.000 (loss 6.000, 1.5%, a visible variance). The WO ends Completed (`produced 1000`, `loss 13.5`). Consumed 1,000 × 110 = ₹1,10,000; unit cost of the two batches is 110000×0.6/592.5 = 111.392405 and 110000×0.4/394 = 111.675127 per kg.
3. Packing WOs: 2,000 × 100g (consumes 200 kg powder, 2,000 pouches, 80 cartons), 500 × 200g (100 kg), 200 × 500g (100 kg). Costs: 100g pouch ≈ 10 kg × 111.4 / 100 + 1.10 + 0.88 ≈ ₹13.1; 200g ≈ ₹24.8; 500g ≈ ₹60.7, so distributor lists carry a margin.
4. **Garam Masala chain.** Grinding WOs for coriander, cumin and pepper. Blend WO of 300 kg `GM-BULK`. A `GM-100` packing WO left at Not Started, one at In Process (transfer done, not yet manufactured) and one Draft, to show every status.
5. Sales: Sales Invoices to the distributors from the FEFO-suggested batches at their price lists; one Sales Order that would exceed Patel's limit left as a draft (blocked on submit), one submitted with the Credit Controller override.
6. A near-expiry batch (expiring in 20 days) and an already-expired batch, the latter written off by a Material Issue, to light up the expiry report.

All demo documents run on a fresh database with `verify_integrity` green. Re-running must be idempotent: guard each document by its fixed `name`.

---

## 11. Implementation plan (5 sequential tasks)

**Precondition:** GST Task 3 and erp-72 touch `selling/models.py`, `buying/models.py`, `accounts/models.py`, `accounts/mapping.py`, `seed_demo.py`, `verify_integrity.py` and `core/views.py`. Start Task 1 only after those are committed, rebase first, and keep every change below additive (new fields, new migrations numbered after theirs, no edits to their existing statements except the SLE-dict lines in `on_submit`).

### Task 1: Batches and the ledger (backend; no UI)

Goal: batch masters, batch on SLE and lines, batch-wise balance and valuation, expiry blocking, FEFO, batch reports and integrity.

Files:

- `stock/models.py` (`Item` fields and CHECKs, `Batch`, `StockLedgerEntry.batch` plus indexes, `StockEntryItem` batch fields); `stock/migrations/0006_batches.py`.
- `stock/services/ledger.py` (changes in 1.5); new `stock/services/batches.py`.
- `stock/serializers.py`, `stock/views.py`, `stock/urls.py`, `stock/admin.py` (batch viewset, `fefo`, `balances`, `trace`, two reports, Item toggle guard, StockEntryItem batch rules, SLE serializer fields).
- `stock/models.py` `StockEntry.on_submit/validate_before_submit`: pass `batch`/`allow_expired` (Material Issue → `True`) and create inward batches.
- `selling/models.py` + `selling/serializers.py` (SO/SI line `batch`, SI submit gate and SLE dict) ✱, `buying/models.py` + `buying/serializers.py` (PI line fields, auto-create) ✱; `selling/migrations/0008`, `buying/migrations/0008`; `selling/mapping.py`, `accounts/mapping.py::remaining_order_lines` (carry `batch`) ✱.
- `core/management/commands/verify_integrity.py` ✱ (checks: batch present iff batch item; `batch.item == sle.item`; no negative batch balance; Σ batches == Bin for batch items; expired-stock warning count), `seed_defaults.py` (RBAC labels), `docs/API.md`, `docs/erpnext_divergences.md`.

Tests:

- Ledger: batch required and forbidden by item flag; wrong-item batch; out above batch balance; expired out refused, Material Issue allows it; reversal copies batch and refuses when it would make a batch negative; two batches of one item at different rates are costed per batch (turmeric 108 and 114); transfer keeps the batch cost; `repost_stock` reproduces the Bins; `incoming_value` yields exact value (no paisa drift).
- Documents: PI auto-creates a batch from the new-batch fields (expiry from shelf life; refused with no expiry source); pick-and-enter conflict is a 400; SI without a batch on a batch item is a 400 that quotes the FEFO suggestion; SI with `update_stock=False` rejects a batch; SO batch is informational and mapped to the SI.
- FEFO: ordering (expiry asc, nulls last, then mfg date, then id); excludes expired and empty batches; shortfall reporting; list endpoint `available=1`.
- Item toggle refused once SLEs exist; CHECK constraints fire on direct ORM writes.
- Reports: batch-wise balance equals Bin per item/warehouse and respects `as_of`; expiry statuses; `verify_integrity` green and fails on a deliberately corrupted SLE (missing batch).
- RBAC: new labels validate; unauthorised reads are 403.
- Regression: the full existing suite unchanged (non-batch items behave identically).

### Task 2: BOM, Work Order, manufacturing Stock Entries (backend; depends on Task 1)

Goal: recipe, order, transfer, manufacture (with process loss, scrap, batches), repack, costing and the production reports.

Files:

- New app `manufacturing/`: `apps.py`, `models.py`, `serializers.py`, `views.py`, `urls.py`, `admin.py`, `services/costing.py`, `services/production.py` (`post_manufacture`), `services/work_orders.py` (`generate_required_items`, `refresh_progress`, `make_stock_entry`), `management/commands/repost_work_orders.py`, `tests/`.
- `config/settings.py` (INSTALLED_APPS), `config/urls.py`.
- `stock/models.py` (types, fields, `validate_before_submit` and `on_submit` branches, GL skip for the manufacturing types), `stock/serializers.py`; migrations `manufacturing/0001`, `stock/0008`.
- `core/management/commands/verify_integrity.py` ✱ (Manufacture/Repack net value is exactly 0; WO stored progress equals recomputed; manufacturing vouchers have no GL rows; WIP residue warning), `seed_defaults.py` (roles, groups, RBAC), `docs/API.md`, `docs/erpnext_divergences.md`.

Tests:

- BOM: unique default per (company, item), acyclic sub-BOMs, usage lock after a submitted WO, costing (single and multi-level rollup, scrap, loss changes unit cost, rate-basis switch), explode.
- WO: required qty scaling and rounding, status transitions through every state including Stopped, cancel blocked with entries, amend, `refresh_progress` and `repost_work_orders` agree, derived columns written with `update()`.
- Material Transfer for Manufacture: per-item caps, batch on both legs, cancel blocked after consumption, `skip_transfer` path.
- Manufacture: the 1.25% example to the paisa (value conserved exactly, unit cost = consumed / good qty), process loss derived and negative refused, partial completion (600 then 400), cap at the planned qty, scrap valued and subtracted, scrap value above consumed refused, FG batch auto-created with shelf-life expiry, consumption beyond WIP refused, no GL rows with perpetual inventory on, `perpetual_inventory_balance` stays green, cancel reverses and is blocked when the FG was sold.
- Repack: allocation sums to 100, residue to the last row, single-row shortcut.
- `make-stock-entry`: scaling, FEFO batch fill, shortage row has no batch, permission checks.
- Reports: yield (planned vs actual, loss % vs expected) and BOM cost.
- Regression: existing Stock Entry types untouched, full suite and `verify_integrity` green.

### Task 3: Price lists and credit limits (backend; depends on Task 1 for the migrations; start after erp-72 lands)

Goal: price lists with deterministic rate resolution, server-side rate fill, and the credit check.

Files:

- `stock/models.py` (`PriceList`, `ItemPrice`), `stock/migrations/0007`, `stock/services/pricing.py`, `stock/views.py`/`serializers.py`/`urls.py` (CRUD, `item-prices/rate/`).
- `core/models.py` + `core/migrations/0009` (Company default lists, additive) ✱.
- `selling/models.py` ✱ (Customer fields and CHECK, `CustomerGroup.default_price_list`, header `price_list`, line `price_list_rate`, `submit()` overrides, `validate_before_submit` credit hook), `selling/services/credit.py` (new), `selling/serializers.py` (optional `rate`, server fill, credit-status), `selling/views.py` (`credit-status`, `update-rates`), `selling/mapping.py` ✱ (copy `price_list`/`price_list_rate`), `buying/*` likewise (price list only), migrations.
- `accounts/services/outstanding.py` is **read only** here (the seam); no edits to erp-72 files.
- `seed_defaults.py` (role Credit Controller, RBAC), `docs/API.md`, `docs/erpnext_divergences.md`.

Tests:

- Resolution order: explicit list, customer, group ancestor, company default; validity windows; latest `valid_from` wins; UOM-specific beats blank; disabled list ignored; no price returns null; currency mismatch refused.
- Rate fill: omitted rate is server-filled, a supplied rate is kept with the snapshot, the missing-price 400, `update-rates` on drafts only.
- Credit: below, equal and above the limit (strictly greater blocks); limit 0 and bypass skip; unbilled open orders counted and the invoice's own SO excluded; SO check, SI check, SI-from-SO recheck; override by role and superuser writes an `AuditLog` row; ORM submit without a user fails closed; GL fallback filters Receivable accounts (a payment reduces exposure); `credit-status` payload; cross-company safety.
- Seam: when `accounts.services.outstanding` is present, the test asserts it equals the GL balance for a customer without advances.
- Regression: existing sales tests (customers have limit 0).

### Task 4: Frontend (depends on the serializer shapes of Tasks 1 to 3; can start after Task 1 and proceed in parallel with Tasks 2 and 3)

Files: `frontend/src/resources/manufacturing.ts` (new), `stock.ts`, `selling.ts`, `buying.ts`, `registry.ts` ✱ (module + reports), `types/index.ts` ✱, `types/report.ts`, `components/doc/ChildTable.tsx` ✱, `DocumentPage.tsx` ✱, `ReportPage.tsx` ✱, `components/stock/CreditBanner.tsx` (new), `i18n/en.manufacturing.ts` (new), `i18n/en.ts` merge, locale stubs.

Tests: config tests (every new field and child table exists; `hidden` conditions per Stock Entry type; FEFO picker filters), unit tests for the async-fill merge and the action-body request, `npm run lint` and `npm run build` green, manual check of the full Haldi flow in the UI.

### Task 5: Demo seed, end-to-end test, docs (depends on Tasks 1 to 3)

Files: `core/management/commands/seed_demo.py` ✱ (section 10.3), `core/test_e2e_manufacturing.py` (new), `backend/docs/manufacturing.md` (a short how-to), `docs/API.md`, `docs/erpnext_divergences.md` (final), `checklist.md`.

Tests: `seed_demo` on a fresh DB then `verify_integrity` green; idempotent re-run; the e2e test walks purchase, grind (1.25%), partial second run, blend, pack, FEFO-batch sale at the distributor price list, a credit block and override, batch trace and recall, expiry write-off, cancel of a Manufacture and of a sale, `repost_stock` and `repost_work_orders` reproducing everything.

### File-conflict matrix

✱ = overlaps a file edited by in-flight work. GST = GST Task 3 (uncommitted in the tree); E72 = erp-72 (outstanding, allocation, status); FE = frontend redesign.

| File group | T1 | T2 | T3 | T4 | T5 | In-flight overlap |
|---|---|---|---|---|---|---|
| `stock/models.py` | yes | yes | yes (append) | no | no | none, but T1, T2, T3 run in order |
| `stock/services/ledger.py` | **yes (core change)** | no | no | no | no | none |
| `stock/services/batches.py`, `pricing.py` | create | no | create | no | no | none |
| `stock/serializers.py`, `views.py`, `urls.py` | yes | yes | yes | no | no | none |
| `manufacturing/**` | no | create | no | no | no | none |
| `selling/models.py`, `buying/models.py` | lines + SLE dict | no | yes (fields, `submit`, credit hook) | no | no | GST ✱ (`on_submit`), E72 ✱ (SI/PI fields) |
| `selling/serializers.py`, `buying/serializers.py` | yes | no | yes | no | no | E72 ✱ |
| `selling/mapping.py`, `accounts/mapping.py` | yes (carry `batch`) | no | yes (carry price) | no | no | GST ✱, E72 ✱ (`invoice_outstanding`, payment) |
| `accounts/models.py`, `accounts/services/outstanding.py` | no | no | read only | no | no | E72 ✱ |
| `core/models.py` | no | no | yes (Company fields) | no | no | E72 minor (`projection_fields` if added) |
| `core/views.py` | no | no | no | no | no | GST ✱ (not touched) |
| `core/mixins.py` | no | no | no | no | no | GST ✱ (not touched) |
| `verify_integrity.py` | yes | yes | no | no | no | GST ✱, E72 ✱ |
| `seed_defaults.py` | yes | yes | yes | no | no | none |
| `seed_demo.py` | no | no | no | no | yes | GST ✱, E72 ✱ |
| `config/settings.py`, `config/urls.py` | no | yes | no | no | no | none |
| `docs/API.md`, `erpnext_divergences.md` | yes | yes | yes | no | yes | GST ✱, E72 ✱ (append sections, keep rows separate) |
| `frontend/src/resources/*.ts` | no | no | no | yes | no | FE ✱ (`registry.ts`) |
| `types/index.ts`, `ChildTable.tsx`, `DocumentPage.tsx`, `ReportPage.tsx` | no | no | no | yes | no | FE ✱, GST (earlier uncommitted edits) |
| migrations | `stock.0006`, `selling.0008`, `buying.0008` | `manufacturing.0001`, `stock.0008` | `stock.0007`, `core.0009` (renumber if E72 or GST add first) | no | no | E72, GST ✱ (number collisions) |

Run strictly in the order 1, 2, 3, then 5; Task 4 can overlap 2 and 3. Task 2 and Task 3 both append to `stock/models.py` and migrations: run sequentially or merge carefully. If erp-72 has not landed, Task 3 can still ship with the GL path, and only the `receivable_balance` seam changes later.

### 11.4 RBAC summary

New model labels: `stock.Batch`, `stock.PriceList`, `stock.ItemPrice`, `manufacturing.BOM`, `manufacturing.BOMItem`, `manufacturing.BOMScrapItem`, `manufacturing.WorkOrder`, `manufacturing.WorkOrderItem`.

- Report endpoints are gated by `require_model_permission`: batch and expiry reports on `stock.Bin`; yield on `manufacturing.WorkOrder`; BOM cost on `manufacturing.BOM` (cost data is for Manufacturing, Stock and Accounts managers only).
- Credit override is a role by name, not a flag.
- No ledger is writable over HTTP (SLE and GL unchanged).

---

## 12. Divergences from ERPNext for v1

1. **BOM is a master, not submittable**, with a usage lock instead of submit and amend. Costs derived on read, not stored.
2. **No operations, routing, workstations, job cards, time logs or operating cost.** No BOM Creator, BOM Update Tool, Production Plan, MRP, Blanket Order, subcontracting, disassembly or "Material Consumption for Manufacture".
3. **Single-level Work Orders.** Multi-level is chained Work Orders; `explode` guides planning. No auto sub-assembly orders (`use_multi_level_bom`).
4. **Process loss at BOM level only** (no per-row `process_loss_per`). Scrap rows carry a value rate (no `cost_allocation_per` on BOM; that field exists only for Repack rows).
5. **No overproduction** (`produced_qty ≤ qty`, no tolerance setting). WO has no sales order link and no `required_items` edits.
6. **Valuation stays Moving Average (G1)**; for batch items the outgoing rate is the batch average (as ERPNext's default batch-wise valuation). FIFO queue valuation is not implemented.
7. **No Serial and Batch Bundle document and no serial numbers.** `batch` is a direct FK on SLE and lines; one batch per line; batch balance is derived from SLE, with no batch projection table.
8. **WIP is a warehouse in the single inventory account**: Transfer-for-Manufacture and Manufacture post no GL. No `Warehouse.account`, no additional costs / "Expenses Included In Valuation" (Q1, Q2).
9. **Batch required on outward lines** (no auto-pick at submit). Expired batches are blocked on every outward movement except Material Issue (the write-off). No minimum-remaining-shelf-life rule.
10. **Repack** allocates by explicit `cost_allocation_per`, not ERPNext's equal rate per unit.
11. **Price lists:** no party-specific or batch-specific Item Price rows (a distributor gets a price list), no Pricing Rules, discounts, margin or quantity slabs, no multi-currency conversion (list currency must equal company currency), no auto-insert of price list rates. Price list rate keeps 6 dp, the line 2 dp. Customer group default walks the tree.
12. **Credit limit:** fields on `Customer` (per company by construction), no customer-group limit, no overdue-billing threshold; role fixed by name; invoices always rechecked; `bypass_credit_limit_check` skips the customer wholesale. Exposure = GL receivable party balance + unbilled open SOs (no Delivery Notes exist).
13. **Credit controller notification emails** are not sent (no mail service; ERPNext offers "Send Email").
14. **No B2C channel integration.** Online sales are a customer with the Online MRP list and manual or imported invoices.
15. **Back-dated documents** are still valued at the current balance (existing behaviour, unchanged).
16. **Multilingual:** BOM and Work Order names are document numbers (not translated); Item names already use `FieldTranslation`.

---

## 13. Open questions for the user (recommended default for each)

1. **Should WIP appear as its own balance-sheet line?** Default: **no.** WIP is a warehouse under Stock In Hand and transfers post no GL. If your CA wants it separate, add `Warehouse.account` later (needs a small posting change).
2. **Are grinding or packing costs (labour, power, machine hire) part of product cost?** Default: **no in v1:** unit cost is materials only. If yes, I'll add an `additional_costs` table on the Manufacture entry that posts `Dr Inventory / Cr "Expenses Included In Valuation"` and spreads the cost over finished goods.
3. **Process loss convention:** is the Work Order quantity the **input-equivalent** (ERPNext: ordering 1,000 kg of powder consumes 1,000 kg whole turmeric and yields about 987.5 kg), or do you plan by **desired good output** (order 1,000 kg powder and the system works out 1,012.66 kg of raw)? Default: **ERPNext convention** (input basis), with the UI showing the expected good output next to the quantity.
4. **Credit exposure source:** use the **GL receivable balance** (includes advances and imported Tally opening balances) now, switching to erp-72's `outstanding` only when it offers an equivalent party-level number? Default: **yes, GL now.**
5. **Distributor price lists:** one price list per tier (a few shared lists) or one per distributor? Default: **per tier**, with an individual list only where a distributor has a special deal.
6. **Batch numbering:** is `ITEMCODE-YYMMDD-NN` (for example `HALDI-P-261028-01`) acceptable on pouch labels, or do you want a short plant lot such as `B261028`? Default: the item-code format (globally unique and traceable); a manual `batch_no` is always allowed.

---

### Critical Files for Implementation

- C:\Users\91700\Desktop\ERP\backend\stock\services\ledger.py (batch/`incoming_value`/`allow_expired` keys, batch-wise outgoing rate, reverse guard)
- C:\Users\91700\Desktop\ERP\backend\stock\models.py (Item batch fields, `Batch`, `StockLedgerEntry.batch`, `StockEntry` types and manufacturing fields, `PriceList`/`ItemPrice`)
- C:\Users\91700\Desktop\ERP\backend\selling\models.py (Customer credit/price fields, SO/SI batch and price lines, `submit()` override and credit hook, SI `on_submit` batch pass-through) plus C:\Users\91700\Desktop\ERP\backend\buying\models.py
- C:\Users\91700\Desktop\ERP\backend\core\management\commands\verify_integrity.py and C:\Users\91700\Desktop\ERP\backend\core\management\commands\seed_demo.py (new invariants, demo data)
- C:\Users\91700\Desktop\ERP\frontend\src\types\index.ts (async fill, action body, report render) with the new C:\Users\91700\Desktop\ERP\frontend\src\resources\manufacturing.ts and C:\Users\91700\Desktop\ERP\frontend\src\resources\registry.ts

---

## 14. User decisions (2026-10-28)

These override the defaults in §13.

1. **Product cost: materials only.** Unit cost is the consumed raw materials plus packaging. Labour, power and machine costs stay as P&L expenses, with no `additional_costs` table in v1.
2. **Process loss is planned by desired output, not by input.** A Work Order for 1,000 kg of powder computes the raw requirement as `qty / (1 - process_loss_pct/100)`. At 1.25% loss that is 1,012.658 kg of whole turmeric. `produced_qty` is measured against the desired good output. This is a deliberate divergence from ERPNext, which plans by input. Record it in `erpnext_divergences.md`.
3. **Price lists are per tier.** A few shared lists (for example Distributor A, Distributor B, Retail MRP) are assigned through the customer or customer group. An individual list is created only for special deals.
4. **WIP is its own balance-sheet line** (user decision, 2026-10-28). Add a nullable `Warehouse.account` FK (a Stock-type account; the default is the company inventory account). With perpetual inventory on, every stock movement between warehouses whose accounts differ posts GL: Material Transfer for Manufacture posts Dr "Work In Progress" / Cr "Stock In Hand", and Manufacture posts Dr "Stock In Hand" (finished goods) / Cr WIP (consumed). seed_defaults creates a "Work In Progress" asset account under Current Assets and links the company WIP warehouse to it. verify_integrity checks that Σ Bin.stock_value per warehouse account equals that account's GL balance. This matches ERPNext when a warehouse has its own account. Update §3.5 and §12.8 accordingly.
5. **Credit exposure** uses the GL receivable balance (default from §13 Q4).
6. **Batch numbering** is `ITEMCODE-YYMMDD-NN`, and a manual `batch_no` is allowed (default from §13 Q6).
