"""Dispatch tracking and operations for Sales Orders (Card O-1, O-2, O-3)."""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Sum
from django.utils.translation import gettext as _
from core.models import Company, DocStatus
from accounts import mapping
from selling.models import (
    DispatchStatus,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import Bin, Warehouse


def compute_status(order):
    """Derive dispatch_status for a Sales Order.

    Checks submitted SalesInvoices referencing order.
    If none submitted -> PENDING.
    If all lines fully invoiced/dispatched -> DISPATCHED.
    Else -> PART_DISPATCHED.
    """
    if isinstance(order, (int, str)):
        order = SalesOrder.objects.get(pk=order)

    submitted_invoices = SalesInvoice.objects.filter(
        sales_order=order,
        docstatus=DocStatus.SUBMITTED,
    )
    if not submitted_invoices.exists():
        return DispatchStatus.PENDING

    invoiced = SalesInvoiceItem.objects.filter(
        sales_invoice__sales_order=order,
        sales_invoice__docstatus=DocStatus.SUBMITTED,
    ).select_related("item")

    remaining_lines = mapping.remaining_order_lines(
        order.items.select_related("item", "warehouse"),
        invoiced,
    )
    if not remaining_lines:
        return DispatchStatus.DISPATCHED
    return DispatchStatus.PART_DISPATCHED


def refresh_status(order):
    """Recompute and update SalesOrder.dispatch_status with QuerySet.update()."""
    if isinstance(order, (int, str)):
        order = SalesOrder.objects.get(pk=order)
    status = compute_status(order)
    SalesOrder.objects.filter(pk=order.pk).update(dispatch_status=status)
    order.dispatch_status = status
    return status


def integrity_errors():
    """Check all submitted SalesOrders comparing stored dispatch_status vs compute_status(order)."""
    errors = []
    orders = SalesOrder.objects.filter(docstatus=DocStatus.SUBMITTED).prefetch_related(
        "items"
    )
    for order in orders:
        expected = compute_status(order)
        if order.dispatch_status != expected:
            errors.append(
                f"Sales Order {order.name} dispatch_status drift: "
                f"stored '{order.dispatch_status}' != computed '{expected}'"
            )
    return errors


def pending_orders(company):
    """Return submitted SalesOrders where dispatch_status != DISPATCHED, oldest first.

    Each line returns:
    - order_item_id
    - item (id, code, name)
    - warehouse (id, name)
    - qty (ordered qty)
    - remaining_qty
    - ready_qty (from Bin at line's warehouse or company's ready_warehouse)
    - short_qty = max(0, remaining_qty - ready_qty)

    Avoids N+1 queries by batching orders, items, invoice aggregates and bins.
    """
    if isinstance(company, (int, str)):
        if isinstance(company, str) and not company.isdigit():
            company = Company.objects.get(abbr=company)
        else:
            company = Company.objects.get(pk=int(company))

    orders = list(
        SalesOrder.objects.filter(
            company=company,
            docstatus=DocStatus.SUBMITTED,
        )
        .exclude(dispatch_status=DispatchStatus.DISPATCHED)
        .select_related("customer")
        .order_by("transaction_date", "created_at", "pk")
    )

    if not orders:
        return []

    order_ids = [o.pk for o in orders]
    order_items = list(
        SalesOrderItem.objects.filter(sales_order_id__in=order_ids)
        .select_related("item", "warehouse")
        .order_by("sales_order_id", "pk")
    )

    invoiced_items = (
        SalesInvoiceItem.objects.filter(
            sales_invoice__sales_order_id__in=order_ids,
            sales_invoice__docstatus=DocStatus.SUBMITTED,
        )
        .values("sales_invoice__sales_order_id", "item_id")
        .annotate(billed_qty=Sum("qty"))
    )
    billed_map = {
        (row["sales_invoice__sales_order_id"], row["item_id"]): row["billed_qty"]
        for row in invoiced_items
    }

    default_ready_wh = None
    if company.ready_warehouse_id:
        default_ready_wh = company.ready_warehouse
    else:
        default_ready_wh = (
            Warehouse.objects.filter(company=company, is_group=False)
            .order_by("pk")
            .first()
        )
    default_ready_wh_id = default_ready_wh.pk if default_ready_wh else None

    item_ids = set()
    warehouse_ids = set()
    for line in order_items:
        if line.item.is_stock_item:
            wh_id = line.warehouse_id or default_ready_wh_id
            if wh_id:
                item_ids.add(line.item_id)
                warehouse_ids.add(wh_id)

    bins = Bin.objects.filter(
        item_id__in=item_ids,
        warehouse_id__in=warehouse_ids,
    ).values("item_id", "warehouse_id", "actual_qty")

    stock_pool = {
        (b["item_id"], b["warehouse_id"]): max(Decimal("0.000"), b["actual_qty"])
        for b in bins
    }

    order_lines_map = {}
    for line in order_items:
        order_lines_map.setdefault(line.sales_order_id, []).append(line)

    result = []
    for order in orders:
        lines_data = []
        lines = order_lines_map.get(order.pk, [])
        for line in lines:
            pool_key = (order.pk, line.item_id)
            billed = billed_map.get(pool_key, Decimal("0.000"))
            used = min(line.qty, billed)
            billed_map[pool_key] = billed - used
            remaining_qty = (line.qty - used).quantize(Decimal("0.001"))

            if remaining_qty <= 0:
                continue

            wh_id = line.warehouse_id or default_ready_wh_id
            wh_name = (
                line.warehouse.name
                if line.warehouse
                else (default_ready_wh.name if default_ready_wh else None)
            )

            if line.item.is_stock_item:
                avail = stock_pool.get((line.item_id, wh_id), Decimal("0.000"))
                ready_qty = min(remaining_qty, avail)
                stock_pool[(line.item_id, wh_id)] = avail - ready_qty
            else:
                ready_qty = remaining_qty

            short_qty = max(Decimal("0.000"), remaining_qty - ready_qty)

            lines_data.append(
                {
                    "order_item_id": line.pk,
                    "item": line.item_id,
                    "item_id": line.item_id,
                    "item_code": line.item.code,
                    "item_name": line.item.name,
                    "warehouse": wh_id,
                    "warehouse_name": wh_name,
                    "qty": line.qty,
                    "remaining_qty": remaining_qty,
                    "ready_qty": ready_qty,
                    "short_qty": short_qty,
                }
            )

        result.append(
            {
                "id": order.pk,
                "name": order.name,
                "customer": order.customer_id,
                "customer_name": order.customer.name,
                "transaction_date": order.transaction_date,
                "delivery_date": order.delivery_date,
                "grand_total": order.grand_total,
                "dispatch_status": order.dispatch_status,
                "lines": lines_data,
            }
        )

    return result


def dispatch_order(order, quantities=None, user=None):
    """Dispatch a submitted SalesOrder into a submitted SalesInvoice and update stock and status.

    Parameters:
    - order: SalesOrder instance or ID
    - quantities: optional dict of {order_item_id: qty}. If None, dispatches all remaining.
    - user: User submitting the invoice

    Returns:
    - The created and submitted SalesInvoice
    """
    from selling.mapping import make_sales_invoice

    order_pk = order.pk if hasattr(order, "pk") else order

    with transaction.atomic():
        if connection.features.has_select_for_update:
            locked_order = SalesOrder.objects.select_for_update().get(pk=order_pk)
        else:
            locked_order = SalesOrder.objects.get(pk=order_pk)

        if locked_order.docstatus != DocStatus.SUBMITTED:
            raise ValidationError(_("Only submitted Sales Orders can be dispatched."))

        # Check remaining lines
        invoiced = SalesInvoiceItem.objects.filter(
            sales_invoice__sales_order=locked_order,
            sales_invoice__docstatus=DocStatus.SUBMITTED,
        ).select_related("item")
        remaining = mapping.remaining_order_lines(
            locked_order.items.select_related("item", "warehouse"), invoiced
        )
        if not remaining:
            raise ValidationError(
                _("Sales Order '%(name)s' is already fully invoiced.")
                % {"name": locked_order.name}
            )

        qty_map = None
        if quantities is not None:
            qty_map = {int(k): Decimal(str(v)) for k, v in quantities.items()}
            rem_map = {line["order_item_id"]: line for line in remaining}
            invalid_ids = set(qty_map.keys()) - set(rem_map.keys())
            if invalid_ids:
                raise ValidationError(
                    _("Order item %(id)s is not available for dispatch.")
                    % {"id": next(iter(invalid_ids))}
                )
            for item_id, req_qty in qty_map.items():
                line = rem_map[item_id]
                if req_qty <= Decimal("0.000"):
                    raise ValidationError(
                        _(
                            "Dispatch quantity must be greater than zero for item '%(code)s'."
                        )
                        % {"code": line["item"].code}
                    )
                if req_qty > line["qty"]:
                    raise ValidationError(
                        _(
                            "Cannot dispatch %(qty)s for '%(code)s'; only %(remaining)s remaining."
                        )
                        % {
                            "qty": req_qty,
                            "code": line["item"].code,
                            "remaining": line["qty"],
                        }
                    )

        # Validate warehouse on each stock line that will be dispatched
        for line in remaining:
            line_id = line["order_item_id"]
            if qty_map is not None and line_id not in qty_map:
                continue
            if line["item"].is_stock_item and not line["warehouse"]:
                raise ValidationError(
                    _("Warehouse is required for item '%(code)s'.")
                    % {"code": line["item"].code}
                )

        invoice = make_sales_invoice(locked_order, quantities=quantities)
        invoice.submit(user=user)
        refresh_status(locked_order)
        if hasattr(order, "refresh_from_db"):
            order.refresh_from_db()
        return invoice
