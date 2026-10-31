"""Opening bills: the outstanding bills carried in from the books before go-live.

An :class:`accounts.models.OpeningBill` posts no GL, so its ``outstanding_amount`` is
a projection of the payment allocations made against it, exactly as an invoice's is
projected from the ledger:

    outstanding = amount - sum(allocated_amount of submitted payment references)

It is written only here, under the bill's row lock, and can be rebuilt at any time
with :func:`repost` (``repost_outstanding``).
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from accounts.models import OpeningBill, PaymentEntryReference
from core.models import DocStatus

ZERO = Decimal("0.00")


def allocated(bill, exclude_payment=None):
    """Sum of the submitted payment allocations against this opening bill, not
    counting ``exclude_payment`` (the payment being submitted, whose status is already
    SUBMITTED when its allocations are checked)."""
    refs = PaymentEntryReference.objects.filter(
        payment_entry__docstatus=DocStatus.SUBMITTED,
        reference_doctype=OpeningBill.voucher_label(),
        reference_name=bill.name,
    )
    if exclude_payment is not None:
        refs = refs.exclude(payment_entry_id=exclude_payment.pk)
    return refs.aggregate(t=Sum("allocated_amount"))["t"] or ZERO


def derive(bill):
    """The outstanding amount a bill must carry, from its docstatus and allocations.

    A draft or cancelled bill owes nothing. A submitted bill owes its amount less what
    submitted payments have allocated to it.
    """
    if bill.docstatus != DocStatus.SUBMITTED:
        return ZERO
    return bill.amount - allocated(bill)


def current_outstanding(bill, exclude_payment=None):
    """Outstanding of a bill before ``exclude_payment`` is counted, i.e. what a payment
    being submitted may still allocate (the caller holds the row lock)."""
    if bill.docstatus != DocStatus.SUBMITTED:
        return ZERO
    return bill.amount - allocated(bill, exclude_payment)


def refresh(bill):
    """Recompute and store the outstanding amount of one opening bill.

    Locks the row first so concurrent payments serialise. Updates the in-memory
    ``bill`` too, so callers see the new value.
    """
    with transaction.atomic():
        locked = OpeningBill.objects.select_for_update().get(pk=bill.pk)
        outstanding = derive(locked)
        if locked.outstanding_amount != outstanding:
            OpeningBill.objects.filter(pk=locked.pk).update(
                outstanding_amount=outstanding
            )
    bill.outstanding_amount = outstanding
    return outstanding


def repost(company=None):
    """Rebuild the stored outstanding of every opening bill (optionally of one
    company). Returns the number of bills whose stored value changed."""
    changed = 0
    qs = OpeningBill.objects.all()
    if company is not None:
        qs = qs.filter(company=company)
    for pk in list(qs.order_by("pk").values_list("pk", flat=True)):
        with transaction.atomic():
            locked = OpeningBill.objects.select_for_update().get(pk=pk)
            before = locked.outstanding_amount
            if before != refresh(locked):
                changed += 1
    return changed
