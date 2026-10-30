"""Purchase document mapping (ERPNext ``make_purchase_invoice`` / ``get_payment_entry``)."""

import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.db import transaction

from accounts.models import PaymentType
from accounts import mapping
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, PurchaseInvoiceTax
from core.models import DocStatus, NamingSeries


def make_purchase_invoice(purchase_order):
    """Draft Purchase Invoice for the quantity of ``purchase_order`` not yet invoiced."""
    if purchase_order.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            _("Purchase Invoice can only be made from a submitted Purchase Order.")
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
            _("Purchase Order '%(name)s' is already fully invoiced.")
            % {"name": purchase_order.name}
        )

    company = purchase_order.company
    update_stock = mapping.lines_update_stock(lines)
    posting_date = datetime.date.today()
    from accounts.services import terms as terms_service

    due_date = terms_service.calculate_due_date(
        "Supplier", purchase_order.supplier, posting_date
    )
    with transaction.atomic():
        invoice = PurchaseInvoice.objects.create(
            name=NamingSeries.next(mapping.financial_year_series("PINV", posting_date)),
            supplier=purchase_order.supplier,
            company=company,
            posting_date=posting_date,
            due_date=due_date,
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
            line_data = dict(line)
            line_data.pop("order_item_id", None)
            PurchaseInvoiceItem.objects.create(purchase_invoice=invoice, **line_data)
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
    posting_date = max(datetime.date.today(), invoice.posting_date)
    with transaction.atomic():
        ret = PurchaseInvoice.objects.create(
            name=NamingSeries.next(mapping.financial_year_series("PRET", posting_date)),
            supplier=invoice.supplier,
            company=invoice.company,
            posting_date=posting_date,
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
