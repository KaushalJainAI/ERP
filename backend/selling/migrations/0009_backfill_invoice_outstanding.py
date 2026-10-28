from decimal import Decimal

from django.db import migrations
from django.db.models import Sum

DRAFT, SUBMITTED, CANCELLED = 0, 1, 2


def backfill(apps, schema_editor):
    """Give existing invoices the derived outstanding amount / status.

    Submitted: ``rounded_total`` less the allocations of *submitted* Payment Entry
    references (floored at 0), then Unpaid / Partly Paid / Paid. Cancelled:
    outstanding 0, status Cancelled. Drafts keep the field defaults (0, Draft).
    Existing GL rows are immutable and are not re-tagged; invoices without a tagged
    party row use this same document formula at runtime (legacy fallback).
    """
    Invoice = apps.get_model("selling", "SalesInvoice")
    Reference = apps.get_model("accounts", "PaymentEntryReference")
    zero = Decimal("0.00")

    Invoice.objects.filter(docstatus=CANCELLED).update(
        outstanding_amount=zero, status="Cancelled"
    )
    Invoice.objects.filter(docstatus=DRAFT).update(
        outstanding_amount=zero, status="Draft"
    )
    for invoice in Invoice.objects.filter(docstatus=SUBMITTED):
        allocated = Reference.objects.filter(
            payment_entry__docstatus=SUBMITTED,
            reference_doctype="selling.SalesInvoice",
            reference_name=invoice.name,
        ).aggregate(t=Sum("allocated_amount"))["t"] or zero
        outstanding = max(invoice.rounded_total - allocated, zero)
        if outstanding == 0:
            status = "Paid"
        elif outstanding < invoice.rounded_total:
            status = "Partly Paid"
        else:
            status = "Unpaid"
        Invoice.objects.filter(pk=invoice.pk).update(
            outstanding_amount=outstanding, status=status
        )


class Migration(migrations.Migration):

    dependencies = [
        ("selling", "0008_invoice_outstanding_fields"),
        ("accounts", "0007_payment_reference_allocated_gt_0"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
