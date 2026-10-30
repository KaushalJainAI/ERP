"""Dispatch tracking and operations for Sales Orders (Card O-1, O-2, O-3)."""

from core.models import DocStatus
from accounts import mapping
from selling.models import DispatchStatus, SalesInvoice, SalesInvoiceItem, SalesOrder


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
