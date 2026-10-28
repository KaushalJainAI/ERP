"""Credit-limit exposure and the submit gate (design section 6).

Exposure = the customer's live GL receivable balance plus the value of this
customer's other submitted Sales Orders that no submitted invoice has billed
yet, plus the document being submitted.  ``credit_limit == 0`` means "no limit"
(ERPNext's convention).  A superuser or a member of the ``Credit Controller``
role may override, and every override is written to the audit log.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from accounts.mapping import remaining_order_lines
from accounts.services import outstanding
from core.models import DocStatus
from selling.models import SalesInvoiceItem, SalesOrder

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _pk(value):
    return getattr(value, "pk", value)


def receivable_balance(customer, company):
    """The customer's balance on Receivable accounts, from the ledger."""
    return outstanding.customer_outstanding(customer, company).quantize(CENT)


def unbilled_open_orders(customer, company, exclude=None):
    """Value of submitted Sales Orders not yet billed by a submitted invoice.

    The order's ``rounded_total`` is prorated by the remaining line value, so
    taxes and rounding follow the unbilled share.  Only submitted invoices
    count as billed, matching ``accounts.mapping.remaining_order_lines``.
    """
    orders = SalesOrder.objects.filter(
        customer=customer, company=company, docstatus=DocStatus.SUBMITTED
    )
    if exclude is not None:
        orders = orders.exclude(pk=_pk(exclude))
    total = ZERO
    for order in orders.prefetch_related("items__item", "items__warehouse"):
        invoiced = SalesInvoiceItem.objects.filter(
            sales_invoice__sales_order=order,
            sales_invoice__docstatus=DocStatus.SUBMITTED,
        ).select_related("item")
        order_lines = list(order.items.all())
        full_value = sum((line.qty * line.rate for line in order_lines), ZERO)
        if full_value <= 0:
            continue
        remaining = remaining_order_lines(order_lines, invoiced)
        remaining_value = sum((line["qty"] * line["rate"] for line in remaining), ZERO)
        total += (order.rounded_total * remaining_value / full_value).quantize(CENT)
    return total.quantize(CENT)


def exposure(customer, company, *, exclude_order=None):
    return (
        receivable_balance(customer, company)
        + unbilled_open_orders(customer, company, exclude=exclude_order)
    ).quantize(CENT)


def credit_status(customer, company=None):
    """The payload behind ``GET /api/selling/customers/{id}/credit-status/``."""
    company = company or customer.company
    limit = customer.credit_limit or ZERO
    receivable = receivable_balance(customer, company)
    unbilled = unbilled_open_orders(customer, company)
    current = (receivable + unbilled).quantize(CENT)
    return {
        "customer": customer.pk,
        "customer_name": customer.name,
        "company": company.pk,
        "credit_limit": str(limit),
        "bypass": customer.bypass_credit_limit_check,
        "receivable_balance": str(receivable),
        "unbilled_orders": str(unbilled),
        "exposure": str(current),
        "available": str(max(limit - current, ZERO)) if limit else None,
        "source": "gl",
    }


def _has_override_role(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(settings, "CREDIT_CONTROLLER_ROLE", "Credit Controller")
    return user.user_roles.filter(role__name=role).exists()


def enforce(doc, user=None):
    """Block a submit that crosses the customer's credit limit.

    Called from ``SalesOrder.validate_before_submit`` and
    ``SalesInvoice.validate_before_submit`` after the totals are recomputed.
    """
    customer = doc.customer
    limit = customer.credit_limit or ZERO
    if customer.bypass_credit_limit_check or limit == 0:
        return
    company = doc.company
    # At invoice submit the order it bills is no longer "unbilled"; the invoice
    # replaces that share.  A Sales Order is still a draft here, so it is not in
    # the submitted-order queryset.
    exclude_order = getattr(doc, "sales_order", None) if hasattr(doc, "sales_order") else None
    existing = exposure(customer, company, exclude_order=exclude_order)
    document_total = (doc.rounded_total or ZERO).quantize(CENT)
    total = (existing + document_total).quantize(CENT)
    if total <= limit:
        return
    if _has_override_role(user):
        from core.mixins import record_audit_log

        record_audit_log(
            user,
            "credit_limit_override",
            doc,
            changes={
                "credit_limit": str(limit),
                "exposure_before": str(existing),
                "document_total": str(document_total),
                "exposure_after": str(total),
            },
        )
        return
    raise ValidationError(
        _(
            "Credit limit crossed for %(customer)s: exposure %(total)s (existing "
            "%(existing)s + this document %(document)s) exceeds limit %(limit)s."
        )
        % {
            "customer": customer.name,
            "total": total,
            "existing": existing,
            "document": document_total,
            "limit": limit,
        }
    )
