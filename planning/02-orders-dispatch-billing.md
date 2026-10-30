# 2. Orders, dispatch and billing

## Goal

An approved order waits as Pending. The factory manager ticks it dispatched, and one
action reduces Ready stock, creates the bill and completes the order. The bill later
goes to Tally.

## Flow

1. The order is created by voice (doc 8) or by hand, and saved as a draft.
2. The order entry person checks it and taps **Approve** (submits the Sales Order).
3. It appears in **Pending Orders**, with ready and short quantities for each line.
4. The factory manager opens it, adjusts sent quantities if needed, and taps
   **Dispatch**.
5. The bill is ready to print. The remainder, if any, stays pending.
6. The accountant later runs the Tally export.

## What exists

| Piece | Status |
|---|---|
| Sales Order, Sales Invoice, stock and GL posting on invoice submit | Built |
| Invoicing the uninvoiced remainder (`selling/mapping.py`) | Built |
| Refusing negative stock in the posting service | Built |
| Credit check at submit | Built; Tally held no credit limits, so most customers have none |
| Print and Tally export | Built; export never run on the real books |

## What to build

**Order status.** Add a stored projection `SalesOrder.dispatch_status`: Pending, Part
Dispatched, Dispatched. It is derived from submitted invoices, written with
`QuerySet.update()`, and rebuildable. Add it to `verify_integrity`. A stored field
is needed so lists can filter on it.

**Dispatch endpoint.** `POST /selling/sales-orders/{id}/dispatch/` with lines and
quantities. In one `transaction.atomic()` block it will:

1. Lock the order with `select_for_update()`.
2. Check each quantity against what remains on the order.
3. Create the invoice, with the due date from doc 4.
4. Submit the invoice, which posts stock and GL.
5. Refresh `dispatch_status`.

Anything failing rolls everything back and shows a plain message naming the short
item.

**Pending Orders screen.** Oldest first, large tap targets, with ready and short
quantities per line.

**Defaults.** Order lines default to the Ready warehouse. `make_sales_invoice` must
set `due_date`, which it leaves empty today.

**Bill numbering.** `make_sales_invoice` hardcodes `SINV-2026-`. Replace this with a
series that follows the financial year, so bill numbers stay sequential each year.

## Rules

- Sent quantity is above zero and no more than the remaining quantity.
- Only an approved order can be dispatched.
- Ready stock must cover the quantity; partial dispatch is allowed.
- Two people cannot dispatch the same line at once (row lock plus a test).
- Cancelling a dispatch uses the normal invoice cancel path and restores stock.

## Depends on Q1

If an e-way bill or e-invoice applies, it must exist before goods move, but Tally
export happens later. Options if the answer is yes:

- Generate them in the ERP at dispatch.
- Export that bill to Tally at once and have the accountant generate them before the
  vehicle leaves.

Vehicle and transporter fields return only in this case.

## Tests

- Full dispatch, partial dispatch, then the remainder.
- Short stock refuses and changes nothing.
- Concurrent double dispatch produces one bill.
- Cancel restores stock, GL and order status.
- Rebuilding status matches the stored value.

## Done when

- A real order goes from approval to printed bill with one tap.
- The exported bill imports into Tally and the accountant accepts it.

## Task cards

### O-1 Dispatch status
- **Files:** `backend/selling/models.py`, new `backend/selling/services/dispatch.py`,
  `selling/serializers.py`, `selling/views.py`, `verify_integrity.py`.
- **Do:** add TextChoices `DispatchStatus` (`Pending`, `Part Dispatched`, `Dispatched`) and
  `SalesOrder.dispatch_status`, default `Pending`. Write `compute_status(order)`, using
  `mapping.remaining_order_lines` over submitted invoices of the order. Write
  `refresh_status(order)` using `SalesOrder.objects.filter(pk=...).update(...)`, and
  `integrity_errors()`. Call `refresh_status` from `SalesInvoice.on_submit` and `on_cancel`
  when `sales_order_id` is set. Add a command `repost_orders`, following `repost_stock`. Add
  the field to the serializer as read-only, and to `filterset_fields`.
- **Test:** status for none, part and full billing, after cancel, and that stored drift is
  reported by `integrity_errors()`.

### O-2 Pending orders service
- **Depends:** O-1, P-1. **Files:** `selling/services/dispatch.py`, `selling/views.py`,
  `selling/urls.py`.
- **Do:** `pending_orders(company)` returns submitted orders that are not `Dispatched`, oldest
  first. Each line has `order_item_id`, item, remaining quantity, `ready_qty` (from `Bin` at
  the line's warehouse) and `short_qty`. Expose it as `GET
  /api/selling/sales-orders/pending/`, with read permission. Avoid one query per line.
- **Test:** quantities across two orders for one item, and query count.

### O-3 Dispatch action
- **Depends:** O-1, M-1. **Files:** `selling/mapping.py`, `accounts/mapping.py`,
  `selling/services/dispatch.py`, `selling/views.py`.
- **Do:**
  1. Add `"order_item_id": line.pk` to each dict from `remaining_order_lines`, and pop it in
     every caller before `SalesInvoiceItem.objects.create`.
  2. Give `make_sales_invoice` an optional `quantities` argument: `{order_item_id: qty}`.
     Lines omitted are skipped.
  3. Set `due_date` with `accounts.services.terms.due_date_for(customer, posting_date)`.
  4. `dispatch_order(order, quantities, user)`, inside `transaction.atomic()`: lock the order
     with `select_for_update()`, require `docstatus == SUBMITTED`, require each quantity above
     0 and not above remaining, require a warehouse on each stock line, make the invoice,
     `invoice.submit(user=user)`, then refresh status. Return the invoice.
  5. Add `POST /api/selling/sales-orders/{id}/dispatch/`, body `{"lines": [{"order_item_id":
     1, "qty": "5.000"}]}`, flag `can_submit`. Empty lines mean all remaining. Validation
     errors return 400 with a translated message naming the item.
- **Test:** all in the design's Tests list. Mark the concurrency test to skip on SQLite.

### O-4 Bill numbering
- **Files:** `selling/mapping.py`, and `buying/mapping.py` if it has the same pattern.
- **Do:** replace `NamingSeries.next("SINV-2026-")` with a helper using the Indian financial
  year of the posting date: `SINV-2627-` for April 2026 to March 2027. Stop and report if the
  total can exceed 16 characters.
- **Test:** 31 March and 1 April fall in different series.

### O-5 Pending Orders page
- **Depends:** O-2, O-3. **Files:** new `frontend/src/pages/selling/PendingOrdersPage.tsx`,
  `routes/AppRoutes.tsx`, `resources/selling.ts`, i18n files.
- **Do:** route `/selling/pending-orders`. Cards: customer, order date, lines with ordered,
  ready and short. A quantity box per line, prefilled with what can be sent. A large
  **Dispatch** button with `ConfirmDialog`. On success, show a toast and a **Print bill** link
  to `/print/<kind>/<id>` (check the kind used by `PrintButton.tsx`). Show server errors with
  `normaliseError`. No prices on this page.
- **Test:** `npm run build`; check at phone width.

### O-6 Ready warehouse default
- **Depends:** P-1. **Files:** `selling/serializers.py`.
- **Do:** when a Sales Order line has no warehouse, fill `company.ready_warehouse`, using the
  existing default-filling pattern in the serializer.
- **Test:** a line with no warehouse gets Ready; an explicit one stays.

### O-7 Real Tally test (OWNER)
The accountant imports one exported bill into Tally and confirms it. Q1 must be answered by
then.
