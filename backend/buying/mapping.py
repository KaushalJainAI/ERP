"""Purchase document mapping (ERPNext ``make_purchase_invoice`` / ``get_payment_entry``)."""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import PaymentType
from accounts import mapping
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, PurchaseInvoiceTax
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
            price_list=purchase_order.price_list,
            taxes_and_charges=purchase_order.taxes_and_charges,
            place_of_supply=purchase_order.place_of_supply,
            is_export_with_gst=purchase_order.is_export_with_gst,
            is_reverse_charge=purchase_order.is_reverse_charge,
            update_stock=update_stock,
            credit_to=mapping.default_payable_account(company),
            expense_account=mapping.default_expense_account(company, update_stock),
        )
        for line in lines:
            PurchaseInvoiceItem.objects.create(purchase_invoice=invoice, **line)
        mapping.copy_order_taxes(
            purchase_order, invoice, PurchaseInvoiceTax, "purchase_invoice"
        )
    return invoice


def make_purchase_return(invoice):
    """Draft Purchase Return (debit note) for the not-yet-returned part of a
    submitted Purchase Invoice (ERPNext ``make_purchase_return``). Quantities are
    negative; a batch-tracked line keeps the batch the purchase created."""
    mapping.require_returnable(invoice)
    lines = mapping.return_line_values(invoice)
    with transaction.atomic():
        ret = PurchaseInvoice.objects.create(
            name=NamingSeries.next("PRET-2026-"),
            supplier=invoice.supplier,
            company=invoice.company,
            posting_date=max(datetime.date.today(), invoice.posting_date),
            price_list=invoice.price_list,
            taxes_and_charges=invoice.taxes_and_charges,
            place_of_supply=invoice.place_of_supply,
            is_reverse_charge=invoice.is_reverse_charge,
            update_stock=invoice.update_stock,
            credit_to=invoice.credit_to,
            expense_account=invoice.expense_account,
            is_return=True,
            return_against=invoice,
            update_outstanding_for_self=True,
        )
        for line in lines:
            PurchaseInvoiceItem.objects.create(purchase_invoice=ret, **line)
        mapping.copy_order_taxes(invoice, ret, PurchaseInvoiceTax, "purchase_invoice")
    return ret


def make_payment_entry(invoice):
    """Draft 'Pay' Payment Entry for a submitted Purchase Invoice's outstanding amount."""
    return mapping.build_payment_entry(
        invoice,
        payment_type=PaymentType.PAY,
        party_type="Supplier",
        party_name=invoice.supplier.name,
        party_account=invoice.credit_to,
    )
