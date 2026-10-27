"""Purchase document mapping (ERPNext ``make_purchase_invoice`` / ``get_payment_entry``)."""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import PaymentType
from accounts import mapping
from buying.models import PurchaseInvoice, PurchaseInvoiceItem
from core.models import DocStatus, NamingSeries


def make_purchase_invoice(purchase_order):
    """Draft Purchase Invoice for the quantity of ``purchase_order`` not yet invoiced."""
    if purchase_order.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            "Purchase Invoice can only be made from a submitted Purchase Order."
        )

    invoiced = PurchaseInvoiceItem.objects.filter(
        purchase_invoice__purchase_order=purchase_order,
        purchase_invoice__docstatus=DocStatus.SUBMITTED,
    ).select_related("item")
    lines = mapping.remaining_order_lines(
        purchase_order.items.select_related("item", "warehouse"), invoiced
    )
    if not lines:
        raise ValidationError(
            f"Purchase Order '{purchase_order.name}' is already fully invoiced."
        )

    company = purchase_order.company
    update_stock = mapping.lines_update_stock(lines)
    with transaction.atomic():
        invoice = PurchaseInvoice.objects.create(
            name=NamingSeries.next("PINV-2026-"),
            supplier=purchase_order.supplier,
            company=company,
            posting_date=datetime.date.today(),
            purchase_order=purchase_order,
            update_stock=update_stock,
            credit_to=mapping.default_payable_account(company),
            expense_account=mapping.default_expense_account(company, update_stock),
        )
        for line in lines:
            PurchaseInvoiceItem.objects.create(purchase_invoice=invoice, **line)
        invoice.grand_total = sum(i.amount for i in invoice.items.all())
        invoice.save(update_fields=["grand_total"])
    return invoice


def make_payment_entry(invoice):
    """Draft 'Pay' Payment Entry for a submitted Purchase Invoice's outstanding amount."""
    return mapping.build_payment_entry(
        invoice,
        payment_type=PaymentType.PAY,
        party_type="Supplier",
        party_name=invoice.supplier.name,
        party_account=invoice.credit_to,
    )
