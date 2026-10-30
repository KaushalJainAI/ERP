# 3. Production and stock

## Goal

The factory manager can see where everything is: unprocessed, grinding, packing or
ready. Ready goes down as orders dispatch. Stock can never go negative.

"Batch" here means a production run. Lot tracking (`stock.Batch`, `has_batch_no`) stays
off, and nothing links a run to a customer.

## Stock places

| Warehouse | Holds |
|---|---|
| Unprocessed | Purchased raw spices and packing material |
| In Process | Material in grinding or packing |
| Ready | Finished goods that can be dispatched |

All 46 current stock rows sit in `Main Location`. The go-live count (doc 9) places
them into the new warehouses. `Cold Storage` stays for physical storage if still used.

## Batch stages

| Stage | Factory manager enters | Stock posting |
|---|---|---|
| Grinding | Product, quantity; raw material suggested from the BOM | Material Transfer: Unprocessed to In Process |
| Packing | Nothing extra | None |
| Ready | Actual output | Manufacture: inputs consumed, finished goods into Ready, loss recorded |

Reuse `WorkOrder` and `StockEntry` underneath, so costing, repost, `verify_integrity`
and Tally stock journals keep working. Add `WorkOrder.stage` as a fact set by the
stage actions, and keep Work Order wording away from this screen.

Check during build whether `skip_transfer` and the BOM requirement in
`manufacturing/services/production.py` fit this flow.

## Depends on Q2: one run, several pack sizes?

**If a run makes one product:** the table above stands.

**If one grinding run feeds several sizes:** split it in two.

1. Grinding ends with a bulk powder item, such as Haldi Powder (Bulk), which needs new
   item codes.
2. Each Packing run consumes bulk powder and packing material, and produces one size.
   The code allows one finished row per entry, so three sizes means three small runs,
   presented as one screen.

The second shape is more common in spice packing, but only the owner can confirm it.

## Depends on Q3: packets or kg

`Haldi 500gm` is stored in kg today, and some pack items sit under Raw Spices. If
ready stock is counted in packets, those items must change unit before go-live. They
already have stock entries imported from Tally, so this cannot be a simple field edit;
it needs a plan agreed with the accountant, and the opening count then sets the true
quantity.

## Item master clean-up

- Correct item groups: Raw, Packing Material, Finished Goods.
- Retire non-products (`Tally Tax Heads`) from pickers.
- Set HSN and GST rate per product, confirmed by the accountant.
- The owner confirms the 6 estimated BOMs and adds the missing recipes.

## Constraints

- New CHECK: `Bin.actual_qty >= 0`. Safe to add, as no negative rows exist.
- Add a CHECK on the `StockLedgerEntry` running balance if there is none.
- Stages only move forward. Going back means cancelling the batch.
- Batch quantity is above zero; output is no more than input; loss is zero or more.
- Ready needs an output. A cancelled batch cannot move.
- A batch cannot start if the raw material is short.
- `verify_integrity` compares `Bin` with the ledger, as it does today.

## Screens

- **Today's Batches:** cards by stage, with one button to move on.
- **Stock view:** Unprocessed, In Process and Ready per item, with critical items first.
- **Loss report:** expected against actual grinding loss (about 1.25%) per batch.

## Tests

- A full batch moves stock correctly and posts value correctly.
- Short raw material refuses to start.
- Output above input is refused.
- Stages cannot jump or go back.
- A cancel restores stock.

## Done when

- A real batch runs through all three stages on a phone.
- Stock view matches a physical check.
- Its stock journals appear correctly in the Tally export.

## Task cards

### P-1 Warehouses and stock constraints
- **Files:** `backend/core/models.py`, `stock/models.py`, `core/serializers.py`,
  `frontend/src/pages/organisation/CompanyPage.tsx`.
- **Do:** add nullable FKs on `Company`, using string references and `on_delete=PROTECT`:
  `unprocessed_warehouse`, `in_process_warehouse`, `ready_warehouse`. Add to `Bin.Meta.constraints`
  a CHECK `actual_qty >= 0`, named `check_bin_actual_qty_non_negative`. Read `StockLedgerEntry`,
  and add the same CHECK on its running balance field only if none exists. Add a validation
  that a chosen warehouse is non-group and in the same company.
- **Test:** direct `Bin` update to a negative value raises `IntegrityError`; a sale above stock
  fails with the normal message; a group warehouse is refused. Run `stock`, `manufacturing`
  and `selling` tests, and report any test relying on negative stock.

### P-2 Batch stage
- **Files:** `manufacturing/models.py`, `manufacturing/serializers.py`.
- **Do:** add TextChoices `BatchStage` (`Grinding`, `Packing`, `Ready`), and
  `WorkOrder.stage`, default `Grinding`. Add a CHECK that `Ready` requires `produced_qty > 0`.
  Writes after submit use `QuerySet.update()`.
- **Test:** the CHECK; the field is read-only over the API.

### P-3 Batch service (BLOCKED Q2)
- **Depends:** P-1, P-2. **Files:** new `manufacturing/services/batches.py`,
  `manufacturing/views.py`.
- **Read first:** `services/work_orders.py::make_stock_entry`, `_transfer_rows`,
  `_manufacture_rows`; `services/production.py::_validate_manufacture`, `_assert_finished_qty`;
  and how `manufacturing/tests.py` near line 191 runs a full cycle.
- **Do:** write:
  - `start_batch(company, item, qty, user)`: default BOM for the item (error if none), create a
    Work Order with source Unprocessed, WIP In Process, finished Ready, submit it, then
    `make_stock_entry(wo, MATERIAL_TRANSFER_FOR_MANUFACTURE)` and submit that. All one atomic
    block. Insufficient stock rolls it all back.
  - `move_to_packing(wo, user)`: requires stage `Grinding`.
  - `finish_batch(wo, output_qty, user)`: requires stage `Packing`, output above 0; use
    `make_stock_entry(wo, MANUFACTURE, qty=...)` with the loss recorded, then submit; set
    `Ready`.
  - `cancel_batch(wo, user)`: cancels entries newest first, then the Work Order.
  Expose as `POST /api/manufacturing/batches/`, and `.../{id}/to-packing/`, `.../finish/`,
  `.../cancel/`. If output above the planned quantity or loss handling does not fit
  `_assert_finished_qty`, stop and report; do not weaken it.
- **Test:** the design's Tests list, with `verify_integrity` logic run after a full cycle.

### P-4 Today's Batches page
- **Depends:** P-3. **Files:** `frontend/src/pages/manufacturing/BatchesPage.tsx`,
  `routes/AppRoutes.tsx`, i18n files.
- **Do:** route `/manufacturing/batches`. Three columns on desktop, stacked on a phone. New
  Batch form: product, quantity, and raw material preview from the BOM. One button per card
  for the next stage. Finish asks for actual output, and shows the loss. Use no Work Order
  wording.
- **Test:** `npm run build`; check at phone width.

### P-5 Stock view
- **Depends:** P-1. **Do:** `GET /api/stock/reports/stages/` returns per item: unprocessed, in
  process, ready, critical level. Add a `ReportConfig` and sort critical items first. Serve
  this from `Bin` only.
- **Test:** totals equal `Bin`.

### P-6 Loss report
- **Depends:** P-3. **Do:** add to the existing `production-yield` report, or show there,
  expected against actual loss per batch. Read that report first and reuse it.
- **Test:** the expected loss uses `process_loss_percentage`.

### P-7 Item and recipe clean-up (OWNER, BLOCKED Q3)
The owner and accountant correct units, groups, HSN and BOMs. A person applies this to the
real database, after a backup.
