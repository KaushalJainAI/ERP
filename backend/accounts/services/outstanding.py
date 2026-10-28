"""Invoice outstanding amount, payment status and ageing.

``outstanding_amount`` / ``status`` on a Sales or Purchase Invoice are **derived
projections** of the immutable General Ledger (ERPNext's ``update_voucher_outstanding``):
the sum of the non-cancelled GL rows on the invoice's party account that are tagged
``against_voucher`` = the invoice -- its own party line (tagged at submit) plus the
split lines of every Payment Entry allocated to it. They are only ever written here,
inside the posting transaction, and can be rebuilt at any time (``repost``) or
checked for drift (``check_integrity``).

Legacy data (invoices submitted before the tagging existed) has no tagged own party
row; for those the document formula ``rounded_total - submitted allocations`` is used.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from accounts.models import (
    GLEntry,
    InvoiceStatus,
    PaymentEntryReference,
)
from core.models import DocStatus

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

#: Ageing buckets by days past due: label -> inclusive upper bound (None = open end).
BUCKETS = (("0-30", 30), ("31-60", 60), ("61-90", 90), ("90+", None))


def invoice_models():
    """The payable document models (Sales Invoice, Purchase Invoice)."""
    from buying.models import PurchaseInvoice
    from selling.models import SalesInvoice

    return (SalesInvoice, PurchaseInvoice)


def _is_tagged(invoice):
    """True if the invoice's own party GL row carries its ``against_voucher`` tag."""
    label = invoice.voucher_label()
    return GLEntry.objects.filter(
        voucher_type=label,
        voucher_no=invoice.name,
        account_id=invoice.party_account.pk,
        against_voucher_type=label,
        against_voucher_no=invoice.name,
        is_cancelled=False,
    ).exists()


def document_outstanding(invoice):
    """Legacy formula: ``rounded_total`` less the submitted payment allocations."""
    allocated = PaymentEntryReference.objects.filter(
        payment_entry__docstatus=DocStatus.SUBMITTED,
        reference_doctype=invoice.voucher_label(),
        reference_name=invoice.name,
    ).aggregate(t=Sum("allocated_amount"))["t"]
    return invoice.rounded_total - (allocated or ZERO)


def gl_outstanding(invoice):
    """Raw (unclamped) outstanding of a submitted invoice from the ledger.

    Debit minus credit of the live GL rows on the party account tagged against the
    invoice (credit minus debit for a payable). Untagged legacy invoices fall back
    to :func:`document_outstanding`.
    """
    label = invoice.voucher_label()
    if not _is_tagged(invoice):
        return document_outstanding(invoice)
    agg = GLEntry.objects.filter(
        account_id=invoice.party_account.pk,
        against_voucher_type=label,
        against_voucher_no=invoice.name,
        is_cancelled=False,
    ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    net = (agg["dr"] or ZERO) - (agg["cr"] or ZERO)
    return (net * invoice.OUTSTANDING_SIGN).quantize(CENT)


def status_for(invoice, outstanding):
    """Stored status for a submitted invoice with this ``outstanding``."""
    if outstanding == 0:
        return InvoiceStatus.PAID
    if outstanding < invoice.rounded_total:
        return InvoiceStatus.PARTLY_PAID
    return InvoiceStatus.UNPAID


def derive(invoice):
    """``(outstanding_amount, status)`` an invoice must carry, from its docstatus and
    the ledger. Never negative: the stored column is CHECKed ``>= 0``."""
    if invoice.docstatus == DocStatus.DRAFT:
        return ZERO, InvoiceStatus.DRAFT
    if invoice.docstatus == DocStatus.CANCELLED:
        return ZERO, InvoiceStatus.CANCELLED
    outstanding = max(gl_outstanding(invoice), ZERO)
    return outstanding, status_for(invoice, outstanding)


def current_outstanding(invoice):
    """Outstanding of a submitted invoice, from the ledger (callers hold the row lock)."""
    return derive(invoice)[0]


def refresh(invoice):
    """Recompute and store ``outstanding_amount`` / ``status`` of one invoice.

    Locks the row first so concurrent postings serialise; also updates the
    in-memory ``invoice`` so callers (serializers) see the new values.
    """
    model = type(invoice)
    with transaction.atomic():
        locked = model.objects.select_for_update().get(pk=invoice.pk)
        outstanding, status = derive(locked)
        if (locked.outstanding_amount, locked.status) != (outstanding, status):
            model.objects.filter(pk=locked.pk).update(
                outstanding_amount=outstanding, status=status
            )
    invoice.outstanding_amount = outstanding
    invoice.status = status
    return outstanding, status


def repost(company=None):
    """Rebuild the stored outstanding / status of every invoice (optionally of one
    company). Returns the number of invoices that actually changed."""
    changed = 0
    for model in invoice_models():
        qs = model.objects.all()
        if company is not None:
            qs = qs.filter(company=company)
        for pk in list(qs.order_by("pk").values_list("pk", flat=True)):
            with transaction.atomic():
                locked = model.objects.select_for_update().get(pk=pk)
                before = (locked.outstanding_amount, locked.status)
                if before != refresh(locked):
                    changed += 1
    return changed


def check_integrity():
    """Compare every invoice's stored outstanding / status with the ledger-derived
    value. Returns the list of drift messages (empty = consistent)."""
    errors = []
    for model in invoice_models():
        label = model.voucher_label()
        for invoice in model.objects.all().order_by("pk"):
            expected, status = derive(invoice)
            if (invoice.outstanding_amount, invoice.status) != (expected, status):
                errors.append(
                    f"Invoice Outstanding Drift for {label}:{invoice.name}: stored "
                    f"{invoice.outstanding_amount} / {invoice.status} != ledger "
                    f"{expected} / {status}"
                )
            elif (
                invoice.docstatus == DocStatus.SUBMITTED
                and gl_outstanding(invoice) < 0
            ):
                errors.append(
                    f"Invoice Over-settled: {label}:{invoice.name} has a negative "
                    f"ledger outstanding ({gl_outstanding(invoice)})"
                )
    return errors


# ----------------------------------------------------------------------- ageing


def _bucket(days):
    for label, upper in BUCKETS:
        if upper is None or days <= upper:
            return label
    return BUCKETS[-1][0]  # pragma: no cover


def _empty_buckets():
    return {label: ZERO for label, _upper in BUCKETS}


def ageing_report(party_type, as_on=None, company_id=None, party=None):
    """Accounts receivable (``Customer``) / payable (``Supplier``) ageing.

    One row per submitted invoice with an outstanding amount that was posted on or
    before ``as_on``; the **current** outstanding is used (not the historical
    balance at ``as_on``). ``days_overdue`` is ``as_on`` less the due date (the
    posting date if there is none), floored at 0; invoices not yet due fall in the
    first bucket, as in ERPNext.
    """
    from buying.models import PurchaseInvoice
    from selling.models import SalesInvoice

    as_on = as_on or datetime.date.today()
    model, party_field = {
        "Customer": (SalesInvoice, "customer"),
        "Supplier": (PurchaseInvoice, "supplier"),
    }[party_type]
    qs = (
        model.objects.filter(
            docstatus=DocStatus.SUBMITTED,
            outstanding_amount__gt=0,
            posting_date__lte=as_on,
        )
        .select_related(party_field)
        .order_by(f"{party_field}__name", "posting_date", "name")
    )
    if company_id:
        qs = qs.filter(company_id=company_id)
    if party:
        qs = qs.filter(**{f"{party_field}__name": party})

    rows = []
    parties = {}
    totals = {"rounded_total": ZERO, "outstanding": ZERO, "buckets": _empty_buckets()}
    for inv in qs:
        base = inv.due_date or inv.posting_date
        days = max((as_on - base).days, 0)
        label = _bucket(days)
        party_name = getattr(inv, party_field).name
        buckets = _empty_buckets()
        buckets[label] = inv.outstanding_amount
        rows.append(
            {
                "invoice": inv.name,
                "party": party_name,
                "posting_date": str(inv.posting_date),
                "due_date": str(inv.due_date) if inv.due_date else None,
                "rounded_total": str(inv.rounded_total),
                "outstanding": str(inv.outstanding_amount),
                "days_overdue": days,
                "buckets": {k: str(v) for k, v in buckets.items()},
            }
        )
        entry = parties.setdefault(
            party_name,
            {
                "party": party_name,
                "rounded_total": ZERO,
                "outstanding": ZERO,
                "buckets": _empty_buckets(),
            },
        )
        for agg in (entry, totals):
            agg["rounded_total"] += inv.rounded_total
            agg["outstanding"] += inv.outstanding_amount
            agg["buckets"][label] += inv.outstanding_amount

    def money(agg):
        return {
            **agg,
            "rounded_total": str(agg["rounded_total"]),
            "outstanding": str(agg["outstanding"]),
            "buckets": {k: str(v) for k, v in agg["buckets"].items()},
        }

    return {
        "party_type": party_type,
        "as_on": str(as_on),
        "rows": rows,
        "parties": [money(p) for p in parties.values()],
        "total": money(totals),
    }
