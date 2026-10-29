"""Payment Reconciliation entry (ERPNext: the Payment Reconciliation tool).

ERPNext's tool is not a stored document: its "Reconcile" button rewrites the
*Payment Ledger Entry* / ``against_voucher`` of already submitted vouchers. Here
submitted vouchers and the General Ledger are immutable, so the reconciliation is
itself a small submittable document whose submit posts a **net-zero** voucher on the
party's control account (see ``docs/erpnext_divergences.md``)::

    Customer:  Cr Debtors  (party)  amount  against_voucher = the invoice
               Dr Debtors  (party)  amount  against_voucher = the receipt / advance / credit note
    Supplier:  the same with Dr / Cr swapped.

The party balance cannot change (one debit and one credit on the same account and
party); the invoice's outstanding (derived from its tagged rows) falls by the amount
and the source's unallocated amount falls by the same. Cancelling the entry reverses
the rows and puts both back (unreconcile).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext as _

from core.models import Company, DocStatus, SubmittableDocument


class PartyType(models.TextChoices):
    CUSTOMER = "Customer", "Customer"
    SUPPLIER = "Supplier", "Supplier"


class PaymentReconciliation(SubmittableDocument):
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="payment_reconciliations"
    )
    posting_date = models.DateField()
    party_type = models.CharField(max_length=20, choices=PartyType.choices)
    party_name = models.CharField(max_length=200)
    #: The receivable / payable control account every row of this entry is posted to.
    party_account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    #: Derived: the sum of the allocation rows.
    total_allocated = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    remarks = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(party_type__in=["Customer", "Supplier"]),
                name="check_payrecon_party_type",
            ),
            models.CheckConstraint(
                check=models.Q(total_allocated__gte=0),
                name="check_payrecon_total_gte_0",
            ),
        ]
        indexes = [models.Index(fields=["company", "party_type", "party_name"])]

    @classmethod
    def voucher_label(cls):
        return f"{cls._meta.app_label}.{cls._meta.object_name}"

    def recompute_totals(self):
        self.total_allocated = sum(
            (row.amount for row in self.allocations.all()), Decimal("0.00")
        )

    def validate_before_submit(self):
        from payment_reconciliation import services

        services.validate_and_lock(self)
        self.recompute_totals()
        self.save(update_fields=["total_allocated"])

    def on_submit(self):
        from payment_reconciliation import services

        services.post(self)

    def on_cancel(self):
        from payment_reconciliation import services

        services.unpost(self)

    def __str__(self):
        return (
            f"{self.name} ({self.party_type} {self.party_name}: {self.total_allocated})"
        )


class PaymentReconciliationAllocation(models.Model):
    """One ``amount`` of a source (payment / advance journal / credit note) applied to
    one invoice."""

    reconciliation = models.ForeignKey(
        PaymentReconciliation, on_delete=models.CASCADE, related_name="allocations"
    )
    #: ``accounts.PaymentEntry`` / ``accounts.JournalEntry`` or, for a credit / debit
    #: note that keeps its own outstanding, the invoice model itself.
    source_doctype = models.CharField(max_length=60)
    source_name = models.CharField(max_length=60)
    invoice_doctype = models.CharField(max_length=60)
    invoice_name = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name="check_payrecon_alloc_amount_gt_0"
            ),
            models.UniqueConstraint(
                fields=[
                    "reconciliation",
                    "source_doctype",
                    "source_name",
                    "invoice_doctype",
                    "invoice_name",
                ],
                name="unique_payrecon_allocation_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["source_doctype", "source_name"]),
            models.Index(fields=["invoice_doctype", "invoice_name"]),
        ]

    def _ensure_draft_parent(self):
        status = (
            PaymentReconciliation.objects.filter(pk=self.reconciliation_id)
            .values_list("docstatus", flat=True)
            .first()
        )
        if status is not None and status != DocStatus.DRAFT:
            raise ValidationError(
                _("The allocations of a submitted reconciliation cannot be changed.")
            )

    def save(self, *args, **kwargs):
        self._ensure_draft_parent()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._ensure_draft_parent()
        super().delete(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.source_doctype}:{self.source_name} -> "
            f"{self.invoice_doctype}:{self.invoice_name} ({self.amount})"
        )
