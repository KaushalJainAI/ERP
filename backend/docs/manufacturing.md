# Manufacturing and pricing: operator how-to

The full design is in [manufacturing_design.md](manufacturing_design.md); this
page is the short path from a clean database to a finished production run.

## 1. Prerequisites

- A **WIP warehouse** whose `account` is the balance-sheet account
  `Work In Progress`. `seed_defaults` creates the account; the demo seed creates
  the warehouse named `Work In Progress` and links it.
- A **Finished Goods** warehouse, a **source** warehouse holding the components,
  and (only when the BOM has scrap rows) a **Scrap** warehouse.
- Every component item is a stock item with stock in the source warehouse.

## 2. Create a BOM

1. Open **Manufacturing → Bills of Materials → New**.
2. Pick the company, the product item, and the basis quantity. `quantity` is the
   input-equivalent basis; `Process Loss %` is the expected evaporation, so the
   good output is `quantity × (1 − loss/100)`.
3. Add component rows. A sub-BOM is optional and must make the same component
   item. Add scrap rows only for items that are actually stocked and valued.
4. Save. Costs (`Raw Material Cost`, `Scrap Value`, `Unit Cost`) are computed on
   read from the valuation/standard rates; they are never stored on the BOM.

A BOM is a master, not a submitted document. The server locks its recipe once a
submitted Work Order or Stock Entry references it; create a new version instead
of editing a used BOM.

## 3. Run a Work Order

1. **Manufacturing → Work Orders → New**: choose the BOM, quantity (desired good
   output, not gross input), source, WIP, finished-goods and scrap warehouses.
   `Required Items` is server-generated and read-only; the raw requirement is
   grossed up by the BOM loss.
2. **Submit** the Work Order. It moves to *Not Started*.
3. **Make Material Transfer** moves the components from the source warehouse to
   WIP. The transfer is a normal draft Stock Entry; review and submit it.
4. **Make Manufacture Entry** consumes the WIP components, receives the good
   output in Finished Goods and any scrap in the Scrap warehouse, and records the
   process loss. Submit the entry. The Work Order becomes *Completed* when the
   planned output is reached.
5. **Stop / Resume** freeze and unfreeze production. A Work Order cannot be
   cancelled while a submitted production entry references it.

`Make Manufacture Entry` can be run for a partial quantity; each run creates one
Stock Entry and the Work Order's `Produced Qty` / `Process Loss Qty` are rebuilt
from the submitted entries. The server owns these columns, so they are read-only
in the UI.

## 4. Price lists and credit limits

- **Stock → Price Lists** creates a selling and/or buying list. A list currency
  must match the company currency (v1 has no conversion).
- **Stock → Item Prices** adds rates per item, optional UOM and optional date
  window. A blank UOM means the item's stock UOM. The latest `valid_from` wins;
  a UOM-specific row beats a blank-UOM row on the same date.
- Resolution on a document is: explicit header list → customer/supplier default
  → nearest customer-group ancestor → company default.
- New document lines may leave **Rate** blank. The server fills it from the
  resolved list, then from the item's standard rate; a zero standard rate with no
  price row is a 400. **Update Rates** on a draft document re-fetches all lines.
- **Credit limit** on a customer is `0` = no limit. Submitting a Sales Order or
  Sales Invoice is blocked when exposure (live receivable + unbilled submitted
  orders + this document) is strictly above the limit. A superuser or a
  `Credit Controller` may override; the override is written to the audit log.

## 5. Demo data

`python manage.py seed_demo` (DEBUG only) creates a completed Work Order
(`WO-DEMO-0001`) with a WIP transfer and manufacture entry, a draft Work Order,
retail/wholesale/vendor price lists, and a draft over-limit Sales Order
(`SO-DEMO-0004`). Re-running is a no-op; `verify_integrity` stays green.
