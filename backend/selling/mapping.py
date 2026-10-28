"""Sales document mapping (ERPNext ``make_sales_invoice`` / ``get_payment_entry``)."""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import PaymentType
from accounts import mapping
from core.models import DocStatus, NamingSeries
from selling.models import SalesInvoice, SalesInvoiceItem, SalesInvoiceTax


def make_sales_invoice(sales_order):
    """Draft Sales Invoice for the quantity of ``sales_order`` not yet invoiced."""
    if sales_order.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            "Sales Invoice can only be made from a submitted Sales Order."
        )

    invoiced = SalesInvoiceItem.objects.filter(
        sales_invoice__sales_order=sales_order,
        sales_invoice__docstatus=DocStatus.SUBMITTED,
    ).select_related("item")
    lines = mapping.remaining_order_lines(
        sales_order.items.select_related("item", "warehouse"), invoiced
    )
    if not lines:
        raise ValidationError(
            f"Sales Order '{sales_order.name}' is already fully invoiced."
        )

    company = sales_order.company
    with transaction.atomic():
        invoice = SalesInvoice.objects.create(
            name=NamingSeries.next("SINV-2026-"),
            customer=sales_order.customer,
            company=company,
            posting_date=datetime.date.today(),
            sales_order=sales_order,
            taxes_and_charges=sales_order.taxes_and_charges,
            place_of_supply=sales_order.place_of_supply,
            is_export_with_gst=sales_order.is_export_with_gst,
            is_reverse_charge=sales_order.is_reverse_charge,
            update_stock=mapping.lines_update_stock(lines),
            debit_to=mapping.default_receivable_account(company),
            income_account=mapping.default_income_account(company),
        )
        for line in lines:
            SalesInvoiceItem.objects.create(sales_invoice=invoice, **line)
        mapping.copy_order_taxes(sales_order, invoice, SalesInvoiceTax, "sales_invoice")
    return invoice


def make_payment_entry(invoice):
    """Draft 'Receive' Payment Entry for a submitted Sales Invoice's outstanding amount."""
    return mapping.build_payment_entry(
        invoice,
        payment_type=PaymentType.RECEIVE,
        party_type="Customer",
        party_name=invoice.customer.name,
        party_account=invoice.debit_to,
    )
