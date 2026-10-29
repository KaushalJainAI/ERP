"""Guard: a document that a live reconciliation entry refers to cannot be cancelled
until the reconciliation is cancelled (unreconciled) first -- the same rule as an
invoice with a submitted payment, applied to payments, journals and invoices alike.

A ``pre_save`` hook is used so no model of another app has to change; it runs inside
the cancel transaction, so raising rolls the cancel back.
"""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils.translation import gettext as _

from core.models import DocStatus

GUARDED = (
    "accounts.PaymentEntry",
    "accounts.JournalEntry",
    "selling.SalesInvoice",
    "buying.PurchaseInvoice",
)


@receiver(pre_save, dispatch_uid="payment_reconciliation_cancel_guard")
def block_cancel_while_reconciled(sender, instance, **kwargs):
    label = f"{sender._meta.app_label}.{sender._meta.object_name}"
    if label not in GUARDED or not instance.pk:
        return
    if getattr(instance, "docstatus", None) != DocStatus.CANCELLED:
        return
    previous = (
        sender.objects.filter(pk=instance.pk)
        .values_list("docstatus", flat=True)
        .first()
    )
    if previous != DocStatus.SUBMITTED:
        return

    from payment_reconciliation.models import PaymentReconciliationAllocation

    names = sorted(
        set(
            PaymentReconciliationAllocation.objects.filter(
                reconciliation__docstatus=DocStatus.SUBMITTED
            )
            .filter(
                Q(source_doctype=label, source_name=instance.name)
                | Q(invoice_doctype=label, invoice_name=instance.name)
            )
            .values_list("reconciliation__name", flat=True)
        )
    )
    if names:
        raise ValidationError(
            _(
                "Cannot cancel '%(name)s': unreconcile the payment reconciliation "
                "entries first (%(entries)s)."
            )
            % {"name": instance.name, "entries": ", ".join(names)}
        )
