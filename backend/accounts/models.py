from decimal import ROUND_HALF_UP, Decimal
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from core.models import DocStatus, TimeStampedModel, SubmittableDocument, Company
from gst.constants import (
    AddDeduct,
    ChargeType,
    GSTComponent,
    GSTDirection,
    GSTSupplyType,
    GSTTreatment,
    TaxCategory,
    TemplateType,
)


class AccountRootType(models.TextChoices):
    ASSET = "Asset", "Asset"
    LIABILITY = "Liability", "Liability"
    EQUITY = "Equity", "Equity"
    INCOME = "Income", "Income"
    EXPENSE = "Expense", "Expense"


class AccountType(models.TextChoices):
    RECEIVABLE = "Receivable", "Receivable"
    PAYABLE = "Payable", "Payable"
    BANK = "Bank", "Bank"
    CASH = "Cash", "Cash"
    STOCK = "Stock", "Stock"
    TAX = "Tax", "Tax"
    COST_OF_GOODS_SOLD = "Cost of Goods Sold", "Cost of Goods Sold"
    ROUND_OFF = "Round Off", "Round Off"


class Account(TimeStampedModel):
    """Chart of Accounts tree node."""

    name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="accounts"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
    )
    root_type = models.CharField(max_length=12, choices=AccountRootType.choices)
    account_type = models.CharField(
        max_length=40, choices=AccountType.choices, null=True, blank=True
    )
    account_number = models.CharField(max_length=40, null=True, blank=True)
    is_group = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # GST tags: identify which account carries which GST head so the tax engine can
    # map a tax row / item-tax-template component to the company's account.
    gst_direction = models.CharField(
        max_length=16, choices=GSTDirection.choices, null=True, blank=True
    )
    gst_component = models.CharField(
        max_length=5, choices=GSTComponent.choices, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_account_per_company"
            ),
            # Both tags or neither.
            models.CheckConstraint(
                check=(
                    models.Q(gst_direction__isnull=True, gst_component__isnull=True)
                    | models.Q(gst_direction__isnull=False, gst_component__isnull=False)
                ),
                name="check_account_gst_tags_together",
            ),
            # A GST-tagged account is a Tax account.
            models.CheckConstraint(
                check=models.Q(gst_direction__isnull=True)
                | models.Q(account_type="Tax"),
                name="check_account_gst_tag_is_tax_type",
            ),
            # One account per (company, direction, component).
            models.UniqueConstraint(
                fields=["company", "gst_direction", "gst_component"],
                condition=models.Q(gst_direction__isnull=False),
                name="unique_account_gst_tag_per_company",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class GLEntry(models.Model):
    """
    Immutable General Ledger Entry (append-only fact).
    Never updated or deleted over API or direct SQL.
    """

    posting_date = models.DateField(db_index=True)
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="gl_entries"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="gl_entries"
    )
    party_type = models.CharField(max_length=20, null=True, blank=True)
    party = models.CharField(max_length=200, null=True, blank=True)
    debit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    credit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    voucher_type = models.CharField(max_length=60)
    voucher_no = models.CharField(max_length=60)
    against_voucher_type = models.CharField(max_length=60, null=True, blank=True)
    against_voucher_no = models.CharField(max_length=60, null=True, blank=True)
    is_cancelled = models.BooleanField(default=False)
    fiscal_year = models.CharField(max_length=40, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(debit__gte=0)
                & models.Q(credit__gte=0)
                & ~models.Q(debit__gt=0, credit__gt=0),
                name="check_gl_entry_debit_credit",
            )
        ]
        indexes = [
            models.Index(fields=["account", "posting_date"]),
            models.Index(fields=["voucher_type", "voucher_no"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_allow_internal_cancellation", False):
            raise ValidationError(
                _("GLEntry rows are immutable and cannot be updated.")
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("GLEntry rows are immutable and cannot be deleted."))

    def __str__(self):
        return f"{self.posting_date} - {self.voucher_type}:{self.voucher_no} - {self.account.name} (Dr:{self.debit}/Cr:{self.credit})"


class JournalEntryType(models.TextChoices):
    JOURNAL_ENTRY = "Journal Entry", "Journal Entry"
    BANK_ENTRY = "Bank Entry", "Bank Entry"
    CASH_ENTRY = "Cash Entry", "Cash Entry"
    CONTRA_ENTRY = "Contra Entry", "Contra Entry"
    CREDIT_NOTE = "Credit Note", "Credit Note"
    DEBIT_NOTE = "Debit Note", "Debit Note"
    OPENING_ENTRY = "Opening Entry", "Opening Entry"
    DEPRECIATION_ENTRY = "Depreciation Entry", "Depreciation Entry"
    WRITE_OFF_ENTRY = "Write Off Entry", "Write Off Entry"


class JournalEntry(SubmittableDocument):
    posting_date = models.DateField()
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="journal_entries"
    )
    total_debit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total_credit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    user_remark = models.TextField(null=True, blank=True)
    # ERPNext's entry type; informational (it does not change the posting).
    voucher_type = models.CharField(
        max_length=30,
        choices=JournalEntryType.choices,
        default=JournalEntryType.JOURNAL_ENTRY,
    )
    # ERPNext labels these "Reference Number" / "Reference Date".
    cheque_no = models.CharField(max_length=140, null=True, blank=True)
    cheque_date = models.DateField(null=True, blank=True)
    # The supplier's own bill number / date being booked.
    bill_no = models.CharField(max_length=140, null=True, blank=True)
    bill_date = models.DateField(null=True, blank=True)

    def recompute_totals(self):
        """Set ``total_debit``/``total_credit`` from the lines (they are derived
        values, never trusted from a client or a prior write)."""
        lines = list(self.lines.all())
        self.total_debit = sum((line.debit for line in lines), Decimal("0.00"))
        self.total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields and "journal_entry" in update_fields:
            TaxPayment.objects.filter(pk=self.pk).update(
                journal_entry=self.journal_entry
            )
            return
        super().save(*args, **kwargs)

    def validate_before_submit(self):
        if self.company.require_proof:
            from core.attachments import has_proof

            if not has_proof(self) and not (self.name and self.name.startswith("TLY-")):
                raise ValidationError(
                    _("Proof attachment is required before submission.")
                )
        lines = self.lines.all()
        if not lines.exists():
            raise ValidationError(_("Journal Entry must have at least one line."))
        for line in lines:
            # Keep every line inside the entry's company; gl.post already blocks
            # group accounts, but nothing else stops a cross-company posting.
            if line.account.company_id != self.company_id:
                raise ValidationError(
                    _("Account '%(account)s' does not belong to the entry's company.")
                    % {"account": line.account.name}
                )
        dr_sum = sum(line.debit for line in lines)
        cr_sum = sum(line.credit for line in lines)
        if dr_sum != cr_sum:
            raise ValidationError(
                _(
                    "Journal Entry is unbalanced: Total Debit (%(debit)s) != Total Credit (%(credit)s)."
                )
                % {"debit": dr_sum, "credit": cr_sum}
            )
        # Persist the derived header totals while the row is still a draft.
        self.recompute_totals()
        self.save(update_fields=["total_debit", "total_credit"])

    def on_submit(self):
        from accounts.services import gl as gl_service

        entries = []
        for line in self.lines.all():
            entries.append(
                {
                    "account": line.account,
                    "debit": line.debit,
                    "credit": line.credit,
                    "party_type": line.party_type,
                    "party": line.party,
                }
            )
        gl_service.post(
            self, entries, posting_date=self.posting_date, remarks=self.user_remark
        )

    def on_cancel(self):
        from accounts.services import gl as gl_service

        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} ({self.posting_date})"


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.CASCADE, related_name="lines"
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="journal_lines"
    )
    debit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    credit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    party_type = models.CharField(max_length=20, null=True, blank=True)
    party = models.CharField(max_length=200, null=True, blank=True)

    def __str__(self):
        return f"{self.journal_entry.name} line: {self.account.name} (Dr:{self.debit}/Cr:{self.credit})"


class PaymentType(models.TextChoices):
    RECEIVE = "Receive", "Receive"
    PAY = "Pay", "Pay"
    INTERNAL_TRANSFER = "Internal Transfer", "Internal Transfer"


class PaymentEntry(SubmittableDocument):
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices)
    posting_date = models.DateField()
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="payment_entries"
    )
    party_type = models.CharField(max_length=20, null=True, blank=True)
    party_name = models.CharField(max_length=200, null=True, blank=True)
    paid_from = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="paid_from_payments"
    )
    paid_to = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="paid_to_payments"
    )
    paid_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    received_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    reference_no = models.CharField(max_length=100, null=True, blank=True)

    # ------------------------------------------------------------- allocation

    #: ``reference_doctype`` -> (payment_type, party_type, party-account field). An
    #: opening bill has no fixed direction (a Customer's is a receipt, a Supplier's a
    #: payment), so its entry is ``(None, None, ...)`` and :meth:`target_flow` reads it
    #: from the bill.
    ALLOCATION_TARGETS = {
        "selling.SalesInvoice": (PaymentType.RECEIVE, "Customer", "debit_to"),
        "buying.PurchaseInvoice": (PaymentType.PAY, "Supplier", "credit_to"),
        "accounts.OpeningBill": (None, None, "account"),
    }

    #: Settling a return (credit / debit note) is the opposite flow of an invoice.
    REFUND_FLOW = {
        PaymentType.RECEIVE: PaymentType.PAY,
        PaymentType.PAY: PaymentType.RECEIVE,
    }

    #: Direction of a party type's bills (used by opening bills).
    FLOW_BY_PARTY_TYPE = {
        "Customer": (PaymentType.RECEIVE, "Customer"),
        "Supplier": (PaymentType.PAY, "Supplier"),
    }

    @classmethod
    def target_flow(cls, reference_doctype, document=None):
        """``(payment_type, party_type, party-account field)`` of an allocation target,
        or ``None`` for a doctype that is not one. An opening bill needs its ``document``
        (its party type sets the direction)."""
        target = cls.ALLOCATION_TARGETS.get(reference_doctype)
        if target is None:
            return None
        payment_type, party_type, account_field = target
        if payment_type is None:
            if document is None:
                return None
            payment_type, party_type = cls.FLOW_BY_PARTY_TYPE[document.party_type]
        return payment_type, party_type, account_field

    @staticmethod
    def _party_name_of(invoice):
        return invoice.party if isinstance(invoice, OpeningBill) else invoice.party.name

    @staticmethod
    def _refresh_reference(invoice):
        """Re-derive a referenced document's outstanding after a payment changed."""
        from accounts.services import opening as opening_service
        from accounts.services import outstanding as outstanding_service

        if isinstance(invoice, OpeningBill):
            opening_service.refresh(invoice)
        else:
            outstanding_service.refresh(invoice)

    @property
    def party_account(self):
        """The party-side (receivable / payable) leg of the voucher: ``paid_from`` for a
        Receive (the debtor is credited), ``paid_to`` for a Pay (the creditor is
        debited). The other leg is the bank / cash account."""
        if self.payment_type == PaymentType.RECEIVE:
            return self.paid_from
        return self.paid_to

    @staticmethod
    def invoice_model(reference_doctype):
        from django.apps import apps

        return apps.get_model(reference_doctype)

    def _locked_allocations(self):
        """``[(reference, locked invoice)]``, validated against this entry.

        The invoice rows are locked (``select_for_update``, always in the same
        order) so that two payments cannot both allocate the same outstanding
        amount; the outstanding amount is read *after* the lock, from the ledger.
        """
        from accounts.services import outstanding as outstanding_service

        refs = list(self.references.all().order_by("pk"))
        if not refs:
            return []
        if self.payment_type == PaymentType.INTERNAL_TRANSFER:
            raise ValidationError(
                _("An internal transfer cannot be allocated against invoices.")
            )
        seen = set()
        total = Decimal("0.00")
        for ref in refs:
            target = self.ALLOCATION_TARGETS.get(ref.reference_doctype)
            if target is None:
                raise ValidationError(
                    _(
                        "Payments can only be allocated against a Sales Invoice or "
                        "a Purchase Invoice, not '%(doctype)s'."
                    )
                    % {"doctype": ref.reference_doctype}
                )
            payment_type, party_type, _account_field = target
            # A return (credit / debit note) is settled by the opposite flow -- a
            # refund -- so either direction passes here; the invoice-level check
            # below picks the right one once the invoice is loaded. An opening bill
            # (no fixed flow) is checked against its own party type below.
            if payment_type is not None and (
                self.payment_type not in (payment_type, self.REFUND_FLOW[payment_type])
                or self.party_type != party_type
            ):
                raise ValidationError(
                    _(
                        "A %(payment_type)s payment for a %(party_type)s cannot be "
                        "allocated against %(doctype)s '%(name)s'."
                    )
                    % {
                        "payment_type": self.payment_type,
                        "party_type": self.party_type,
                        "doctype": ref.reference_doctype,
                        "name": ref.reference_name,
                    }
                )
            key = (ref.reference_doctype, ref.reference_name)
            if key in seen:
                raise ValidationError(
                    _("Invoice '%(name)s' is referenced more than once.")
                    % {"name": ref.reference_name}
                )
            seen.add(key)
            if ref.allocated_amount is None or ref.allocated_amount <= 0:
                raise ValidationError(
                    _("The amount allocated to '%(name)s' must be greater than zero.")
                    % {"name": ref.reference_name}
                )
            total += ref.allocated_amount
        if total > self.paid_amount:
            raise ValidationError(
                _("Total allocated (%(allocated)s) exceeds the paid amount (%(paid)s).")
                % {"allocated": total, "paid": self.paid_amount}
            )

        party_account = self.party_account
        allocations = []
        for ref in sorted(refs, key=lambda r: (r.reference_doctype, r.reference_name)):
            model = self.invoice_model(ref.reference_doctype)
            invoice = (
                model.objects.select_for_update()
                .filter(name=ref.reference_name)
                .first()
            )
            if invoice is None:
                raise ValidationError(
                    _("Invoice '%(name)s' does not exist.")
                    % {"name": ref.reference_name}
                )
            if invoice.docstatus != DocStatus.SUBMITTED:
                raise ValidationError(
                    _("Invoice '%(name)s' is not submitted.")
                    % {"name": ref.reference_name}
                )
            if invoice.company_id != self.company_id:
                raise ValidationError(
                    _("Invoice '%(name)s' belongs to a different company.")
                    % {"name": ref.reference_name}
                )
            if self._party_name_of(invoice) != self.party_name:
                raise ValidationError(
                    _("Invoice '%(name)s' belongs to a different party.")
                    % {"name": ref.reference_name}
                )
            payment_type, party_type, account_field = self.target_flow(
                ref.reference_doctype, invoice
            )
            if getattr(invoice, f"{account_field}_id") != party_account.pk:
                raise ValidationError(
                    _(
                        "Invoice '%(name)s' is booked to a different account than "
                        "this payment's party account."
                    )
                    % {"name": ref.reference_name}
                )
            expected_type = (
                self.REFUND_FLOW[payment_type] if invoice.is_return else payment_type
            )
            if self.payment_type != expected_type or self.party_type != party_type:
                raise ValidationError(
                    _(
                        "A %(payment_type)s payment for a %(party_type)s cannot be "
                        "allocated against %(doctype)s '%(name)s'."
                    )
                    % {
                        "payment_type": self.payment_type,
                        "party_type": self.party_type,
                        "doctype": ref.reference_doctype,
                        "name": ref.reference_name,
                    }
                )
            # A return's own outstanding is negative (a credit / debit balance); the
            # refund may settle up to its absolute value.
            open_amount = outstanding_service.current_outstanding(
                invoice, exclude_payment=self
            )
            if invoice.is_return:
                open_amount = -open_amount
            if ref.allocated_amount > open_amount:
                raise ValidationError(
                    _(
                        "Allocated amount (%(allocated)s) exceeds the outstanding "
                        "amount (%(outstanding)s) of '%(name)s'."
                    )
                    % {
                        "allocated": ref.allocated_amount,
                        "outstanding": open_amount,
                        "name": ref.reference_name,
                    }
                )
            allocations.append((ref, invoice))
        return allocations

    def validate_before_submit(self):
        if self.company.require_proof:
            from core.attachments import has_proof

            if not has_proof(self) and not (self.name and self.name.startswith("TLY-")):
                raise ValidationError(
                    _("Proof attachment is required before submission.")
                )
        if self.paid_amount <= 0:
            raise ValidationError(_("Paid amount must be greater than zero."))
        if self.paid_from.is_group or self.paid_to.is_group:
            raise ValidationError(_("Posting to group accounts is not allowed."))
        if self.paid_from_id == self.paid_to_id:
            raise ValidationError(
                _("Paid From and Paid To accounts must be different.")
            )
        if (
            self.paid_from.company_id != self.company_id
            or self.paid_to.company_id != self.company_id
        ):
            raise ValidationError(
                _("Payment accounts must belong to the payment's company.")
            )
        self._locked_allocations()

    def on_submit(self):
        from accounts.services import gl as gl_service

        zero = Decimal("0.00")
        allocations = self._locked_allocations()
        party = {"party_type": self.party_type, "party": self.party_name}
        party_account = self.party_account
        receive = self.payment_type == PaymentType.RECEIVE

        def party_line(amount, ref=None):
            line = {
                "account": party_account,
                "debit": zero if receive else amount,
                "credit": amount if receive else zero,
                **party,
            }
            if ref is not None:
                line["against_voucher_type"] = ref.reference_doctype
                line["against_voucher_no"] = ref.reference_name
            return line

        # The bank / cash leg is one line. The party leg is split into one line per
        # allocated invoice (tagged ``against_voucher_*``, so the invoice's tagged
        # rows net to its outstanding) plus an untagged remainder (an advance / on
        # account, as in ERPNext). Still one balanced voucher.
        other_line = {
            "account": self.paid_to if receive else self.paid_from,
            "debit": self.paid_amount if receive else zero,
            "credit": zero if receive else self.paid_amount,
            **party,
        }
        party_lines = [
            party_line(ref.allocated_amount, ref) for ref, _inv in allocations
        ]
        remainder = self.paid_amount - sum(
            (ref.allocated_amount for ref, _inv in allocations), zero
        )
        if remainder > 0:
            party_lines.append(party_line(remainder))
        entries = [other_line, *party_lines] if receive else [*party_lines, other_line]
        gl_service.post(
            self,
            entries,
            posting_date=self.posting_date,
            remarks=f"Payment {self.payment_type}: {self.reference_no or ''}",
        )
        for _ref, invoice in allocations:
            self._refresh_reference(invoice)

    def on_cancel(self):
        from accounts.services import gl as gl_service

        gl_service.reverse(self)
        for ref in self.references.all().order_by(
            "reference_doctype", "reference_name"
        ):
            if ref.reference_doctype not in self.ALLOCATION_TARGETS:
                continue
            invoice = (
                self.invoice_model(ref.reference_doctype)
                .objects.filter(name=ref.reference_name)
                .first()
            )
            if invoice is not None:
                self._refresh_reference(invoice)

    def __str__(self):
        return f"{self.name} [{self.payment_type}] - {self.paid_amount}"


class PaymentEntryReference(models.Model):
    payment_entry = models.ForeignKey(
        PaymentEntry, on_delete=models.CASCADE, related_name="references"
    )
    reference_doctype = models.CharField(max_length=60)
    reference_name = models.CharField(max_length=60)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(allocated_amount__gt=0),
                name="check_payment_reference_allocated_gt_0",
            )
        ]

    def clean(self):
        super().clean()
        if self.reference_doctype == "PurchaseInvoice":
            self.reference_doctype = "buying.PurchaseInvoice"
        elif self.reference_doctype == "SalesInvoice":
            self.reference_doctype = "selling.SalesInvoice"

    def save(self, *args, **kwargs):
        if self.reference_doctype == "PurchaseInvoice":
            self.reference_doctype = "buying.PurchaseInvoice"
        elif self.reference_doctype == "SalesInvoice":
            self.reference_doctype = "selling.SalesInvoice"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.payment_entry.name} -> {self.reference_doctype}:{self.reference_name} ({self.allocated_amount})"


class OpeningBill(SubmittableDocument):
    """An outstanding bill carried over from the books before go-live (planning 09).

    The party's balance is already in the ledger (carried in up to ``as_on``), so an
    opening bill **posts no GL**. It tells the ageing report, Money to Collect and
    Bills to Pay which bill a receipt or payment settles, so old debts show as old.

    ``outstanding_amount`` is a derived projection: ``amount`` less the live
    allocations of submitted payment entries. It is refreshed by
    ``accounts.services.opening`` and checked by ``verify_integrity``.
    """

    PARTY_TYPES = (("Customer", "Customer"), ("Supplier", "Supplier"))

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="opening_bills"
    )
    party_type = models.CharField(max_length=20, choices=PARTY_TYPES)
    party = models.CharField(max_length=200)
    #: The party's control account: receivable for a Customer, payable for a Supplier.
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="opening_bills"
    )
    bill_date = models.DateField()
    due_date = models.DateField()
    #: The ledger date the bills reconcile to (the cutover date of the build).
    as_on = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    outstanding_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    #: Where the bill came from, e.g. the Tally voucher number. Free text.
    reference = models.CharField(max_length=140, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(party_type__in=("Customer", "Supplier")),
                name="check_openingbill_party_type",
            ),
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="check_openingbill_amount_gt_0",
            ),
            models.CheckConstraint(
                check=models.Q(outstanding_amount__gte=0)
                & models.Q(outstanding_amount__lte=models.F("amount")),
                name="check_openingbill_outstanding_range",
            ),
            models.CheckConstraint(
                check=models.Q(due_date__gte=models.F("bill_date")),
                name="check_openingbill_due_not_before_bill",
            ),
        ]

    @classmethod
    def voucher_label(cls):
        """``accounts.OpeningBill``: the ``reference_doctype`` of a payment allocation."""
        return f"{cls._meta.app_label}.{cls._meta.object_name}"

    @property
    def is_return(self):
        """An opening bill is never a return; this lets payment allocation treat every
        reference the same way."""
        return False

    def _party_model(self):
        from django.apps import apps

        if self.party_type == "Customer":
            return apps.get_model("selling.Customer")
        return apps.get_model("buying.Supplier")

    def validate_before_submit(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError(_("Opening bill amount must be greater than zero."))
        if self.due_date < self.bill_date:
            raise ValidationError(_("Due date cannot be before the bill date."))
        if self.bill_date > self.as_on:
            raise ValidationError(_("The bill date cannot be after the as-on date."))
        if self.account.company_id != self.company_id or self.account.is_group:
            raise ValidationError(
                _("The party account must be a posting account of this company.")
            )
        wanted = (
            AccountType.RECEIVABLE
            if self.party_type == "Customer"
            else AccountType.PAYABLE
        )
        if self.account.account_type != wanted:
            raise ValidationError(
                _("The party account must be a %(kind)s account.")
                % {"kind": wanted.label}
            )
        if (
            not self._party_model()
            .objects.filter(company_id=self.company_id, name=self.party)
            .exists()
        ):
            raise ValidationError(
                _("'%(party)s' is not a %(party_type)s of this company.")
                % {"party": self.party, "party_type": self.party_type}
            )

    def on_submit(self):
        from accounts.services import opening as opening_service

        opening_service.refresh(self)

    def on_cancel(self):
        from accounts.services import opening as opening_service

        payments = sorted(
            set(
                PaymentEntryReference.objects.filter(
                    payment_entry__docstatus=DocStatus.SUBMITTED,
                    reference_doctype=self.voucher_label(),
                    reference_name=self.name,
                ).values_list("payment_entry__name", flat=True)
            )
        )
        if payments:
            raise ValidationError(
                _(
                    "Cannot cancel '%(name)s': cancel the linked payment entries "
                    "first (%(payments)s)."
                )
                % {"name": self.name, "payments": ", ".join(payments)}
            )
        opening_service.refresh(self)

    def amend(self, new_name=None):
        """Amend, then reset the derived outstanding of the copy (it is a new draft)."""
        with transaction.atomic():
            amendment = super().amend(new_name=new_name)
            type(self).objects.filter(pk=amendment.pk).update(
                outstanding_amount=Decimal("0.00")
            )
            amendment.outstanding_amount = Decimal("0.00")
        return amendment

    def __str__(self):
        return f"{self.name} [{self.party_type}: {self.party}] {self.amount}"


# --------------------------------------------------------------------------- GST
# Tax masters. Item Tax Templates are global (an Item has no company) and keyed by
# GST component; Tax Templates are per company and keyed by account.


class ItemTaxTemplate(TimeStampedModel):
    """Per-item GST rate set, e.g. "GST 18%" = CGST 9 / SGST 9 / IGST 18."""

    name = models.CharField(max_length=100, unique=True)
    gst_treatment = models.CharField(
        max_length=12, choices=GSTTreatment.choices, default=GSTTreatment.TAXABLE
    )
    is_active = models.BooleanField(default=True)
    # Superseded slabs (12% / 28% after GST 2.0) kept for older invoices.
    is_legacy = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class ItemTaxTemplateRow(models.Model):
    template = models.ForeignKey(
        ItemTaxTemplate, on_delete=models.CASCADE, related_name="rows"
    )
    gst_component = models.CharField(max_length=5, choices=GSTComponent.choices)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "gst_component"],
                name="unique_item_tax_template_component",
            ),
            models.CheckConstraint(
                check=models.Q(tax_rate__gte=0) & models.Q(tax_rate__lte=100),
                name="check_item_tax_template_rate_0_100",
            ),
        ]

    def __str__(self):
        return f"{self.template.name}: {self.gst_component} {self.tax_rate}%"


class TaxTemplate(TimeStampedModel):
    """Sales / Purchase Taxes and Charges Template (per company)."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="tax_templates"
    )
    title = models.CharField(max_length=100)
    template_type = models.CharField(max_length=8, choices=TemplateType.choices)
    gst_supply_type = models.CharField(
        max_length=12, choices=GSTSupplyType.choices, blank=True, default=""
    )
    is_reverse_charge = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    disabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "title"], name="unique_tax_template_title"
            ),
            models.UniqueConstraint(
                fields=[
                    "company",
                    "template_type",
                    "gst_supply_type",
                    "is_reverse_charge",
                ],
                condition=models.Q(is_default=True),
                name="unique_default_tax_template",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.company.abbr})"


class TaxTemplateRow(models.Model):
    template = models.ForeignKey(
        TaxTemplate, on_delete=models.CASCADE, related_name="rows"
    )
    idx = models.PositiveSmallIntegerField()
    # v1: "On Net Total" only.
    charge_type = models.CharField(
        max_length=24, choices=ChargeType.choices, default=ChargeType.ON_NET_TOTAL
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=200, blank=True, default="")
    rate = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    add_deduct_tax = models.CharField(
        max_length=6, choices=AddDeduct.choices, default=AddDeduct.ADD
    )
    # v1: "Total" only ("Valuation and Total" is reserved).
    category = models.CharField(
        max_length=24, choices=TaxCategory.choices, default=TaxCategory.TOTAL
    )

    class Meta:
        ordering = ["idx"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "idx"], name="unique_tax_template_row_idx"
            ),
            models.UniqueConstraint(
                fields=["template", "account"], name="unique_tax_template_row_account"
            ),
            models.CheckConstraint(
                check=models.Q(charge_type="On Net Total"),
                name="check_tax_template_row_charge_type",
            ),
            models.CheckConstraint(
                check=models.Q(category="Total"),
                name="check_tax_template_row_category",
            ),
            models.CheckConstraint(
                check=models.Q(add_deduct_tax__in=["Add", "Deduct"]),
                name="check_tax_template_row_add_deduct",
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0) & models.Q(rate__lte=100),
                name="check_tax_template_row_rate_0_100",
            ),
        ]

    def __str__(self):
        return f"{self.template.title} #{self.idx}: {self.account.name} {self.rate}%"


# ------------------------------------------------------- taxable documents
# Abstract building blocks mixed into Sales/Purchase Order and Invoice (and their
# line and tax-row tables). The engine is ``accounts.services.taxes`` (pure maths)
# driven by ``gst.services.supply`` (supply kind, templates, validation); see
# docs/gst_design.md sections 2 and 3.

#: Tolerance of the header identity CHECKs. Amounts are 2-dp values; SQLite does
#: DECIMAL arithmetic in floating point, so an exact ``=`` could spuriously fail
#: (0.1 + 0.2 != 0.3). Half a paisa is exact on PostgreSQL's numeric too.
_IDENTITY_EPS = Decimal("0.005")


class TaxableDocument(models.Model):
    """Header fields of a document that carries tax rows (``taxes``) and rounding.

    Concrete models must declare ``grand_total`` (tax-inclusive), a reverse ``items``
    and ``taxes`` relation, ``company`` and a party FK, and set ``GST_DIRECTION``
    (``"Sales"`` / ``"Purchase"``) and ``party_field``. They inherit this ``Meta``.
    """

    GST_DIRECTION = ""
    party_field = ""

    taxes_and_charges = models.ForeignKey(
        "accounts.TaxTemplate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    place_of_supply = models.ForeignKey(
        "core.State",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Snapshots of the party / company GST identity at the time the totals were
    # last calculated. "" = legacy / pre-GST document.
    gst_category = models.CharField(max_length=24, blank=True, default="")
    party_gstin = models.CharField(max_length=20, null=True, blank=True)
    company_gstin = models.CharField(max_length=20, null=True, blank=True)
    # SEZ / Overseas customers: True = IGST charged, False = zero-rated (LUT / bond).
    is_export_with_gst = models.BooleanField(default=False)
    # Purchase documents only in v1.
    is_reverse_charge = models.BooleanField(default=False)
    # Σ line amount; grand_total = net_total + total_taxes_and_charges (reverse-charge
    # memo rows excluded); rounded_total = grand_total + rounding_adjustment.
    net_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total_taxes_and_charges = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    rounding_adjustment = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    rounded_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    grand_total__gt=models.F("net_total")
                    + models.F("total_taxes_and_charges")
                    - _IDENTITY_EPS
                )
                & models.Q(
                    grand_total__lt=models.F("net_total")
                    + models.F("total_taxes_and_charges")
                    + _IDENTITY_EPS
                ),
                name="%(app_label)s_%(class)s_grand_total_identity",
            ),
            models.CheckConstraint(
                check=models.Q(
                    rounded_total__gt=models.F("grand_total")
                    + models.F("rounding_adjustment")
                    - _IDENTITY_EPS
                )
                & models.Q(
                    rounded_total__lt=models.F("grand_total")
                    + models.F("rounding_adjustment")
                    + _IDENTITY_EPS
                ),
                name="%(app_label)s_%(class)s_rounded_total_identity",
            ),
            models.CheckConstraint(
                check=~(
                    models.Q(is_reverse_charge=True) & models.Q(is_export_with_gst=True)
                ),
                name="%(app_label)s_%(class)s_rcm_not_export",
            ),
        ]

    # ------------------------------------------------------------ introspection

    @property
    def party(self):
        return getattr(self, self.party_field)

    @classmethod
    def item_model(cls):
        return cls._meta.get_field("items").related_model

    @classmethod
    def tax_model(cls):
        return cls._meta.get_field("taxes").related_model

    @classmethod
    def tax_fk_name(cls):
        return cls._meta.get_field("taxes").field.name

    @classmethod
    def item_fk_name(cls):
        return cls._meta.get_field("items").field.name

    # ------------------------------------------------------------------ engine

    HEADER_UPDATE_FIELDS = (
        "taxes_and_charges",
        "place_of_supply",
        "gst_category",
        "party_gstin",
        "company_gstin",
        "net_total",
        "total_taxes_and_charges",
        "grand_total",
        "rounding_adjustment",
        "rounded_total",
    )

    def calculate_taxes_and_totals(
        self,
        *,
        resolve_lines=None,
        expand_template=False,
        auto_select=False,
        for_submit=False,
    ):
        """Recompute every derived tax / total field of this **draft** from its rows.

        The single server-side source of truth (called on create / update, submit
        and amend). One atomic block: snapshots, place of supply, template
        expansion, line GST resolution, the pure engine, then persistence.

        ``resolve_lines``: re-read HSN / treatment / rates from the Item master
        (``True`` on create / update), keep the stored line snapshots (``False``,
        submit and amend), or ``None`` = resolve only if this document was never
        calculated before (``gst_category == ""``: ORM-created or legacy drafts).
        ``expand_template`` replaces the rows with ``taxes_and_charges``'s rows;
        ``auto_select`` picks the company's default template when none is chosen.
        ``for_submit`` also runs the submit gate (place of supply, HSN, GSTIN, ...).
        """
        from django.db import transaction
        from gst.services import supply

        if resolve_lines is None:
            resolve_lines = self.gst_category == ""
        with transaction.atomic():
            fk = self.item_fk_name()
            lines = list(
                self.item_model()
                .objects.filter(**{fk: self})
                .select_related("item", "item__item_tax_template")
                .order_by("pk")
            )
            rows = list(
                self.tax_model()
                .objects.filter(**{self.tax_fk_name(): self})
                .select_related("account")
                .order_by("idx", "pk")
            )
            outcome = supply.compute(
                self,
                lines,
                rows,
                resolve_lines=resolve_lines,
                expand_template=expand_template,
                auto_select=auto_select,
                for_submit=for_submit,
            )
            supply.persist(self, outcome)
        return outcome


class InvoiceStatus(models.TextChoices):
    """Stored payment status of an invoice. *Overdue* is deliberately not stored (it
    depends on today's date); see ``PayableDocument.display_status``."""

    DRAFT = "Draft", "Draft"
    UNPAID = "Unpaid", "Unpaid"
    PARTLY_PAID = "Partly Paid", "Partly Paid"
    PAID = "Paid", "Paid"
    CANCELLED = "Cancelled", "Cancelled"
    # Returns (ERPNext statuses): the credit / debit note itself, and an invoice
    # that is fully settled and has a submitted return against it.
    RETURN = "Return", "Return"
    CREDIT_NOTE_ISSUED = "Credit Note Issued", "Credit Note Issued"
    DEBIT_NOTE_ISSUED = "Debit Note Issued", "Debit Note Issued"


class PayableDocument(models.Model):
    """Receivable / payable invoice header: a **derived** ``outstanding_amount`` and
    ``status``.

    Both are projections of the ledger (``accounts.services.outstanding`` rebuilds
    them from the GL rows tagged ``against_voucher`` = this invoice) -- never
    written by a client. Concrete models set ``PARTY_TYPE`` (``"Customer"`` /
    ``"Supplier"``), ``PARTY_ACCOUNT_FIELD`` (``"debit_to"`` / ``"credit_to"``) and
    ``OUTSTANDING_SIGN`` (+1 where the party line is a debit, -1 where it is a
    credit), and declare ``posting_date`` / ``due_date``.
    """

    PARTY_TYPE = ""
    PARTY_ACCOUNT_FIELD = ""
    OUTSTANDING_SIGN = 1

    outstanding_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT
    )
    # Returns (credit note on a Sales Invoice, debit note on a Purchase Invoice),
    # as in ERPNext v15. A return carries NEGATIVE quantities and totals;
    # ``return_against`` is optional (a standalone return is allowed).
    is_return = models.BooleanField(default=False)
    return_against = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="returns",
    )
    # True: the return keeps its own (negative) outstanding. False and
    # ``return_against`` set: it reduces the original's outstanding instead.
    update_outstanding_for_self = models.BooleanField(default=True)

    class Meta:
        abstract = True

    @classmethod
    def voucher_label(cls):
        """``app_label.ObjectName``: the ``voucher_type`` / ``against_voucher_type``
        / ``PaymentEntryReference.reference_doctype`` string of this document."""
        return f"{cls._meta.app_label}.{cls._meta.object_name}"

    @staticmethod
    def outstanding_constraint():
        """A normal invoice owes ``>= 0``; a return's own outstanding is ``<= 0``
        (it is a credit / debit balance)."""
        return models.CheckConstraint(
            check=(
                models.Q(is_return=False, outstanding_amount__gte=0)
                | models.Q(is_return=True, outstanding_amount__lte=0)
            ),
            name="%(app_label)s_%(class)s_outstanding_sign",
        )

    @staticmethod
    def return_constraints():
        """DB guarantees of the return fields and of the sign convention: a normal
        invoice can never carry negative totals, a return never positive ones."""
        return [
            models.CheckConstraint(
                check=models.Q(return_against__isnull=True) | models.Q(is_return=True),
                name="%(app_label)s_%(class)s_return_against_needs_is_return",
            ),
            models.CheckConstraint(
                check=~models.Q(return_against=models.F("id")),
                name="%(app_label)s_%(class)s_return_not_self",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        is_return=False,
                        net_total__gte=0,
                        grand_total__gte=0,
                        rounded_total__gte=0,
                    )
                    | models.Q(
                        is_return=True,
                        net_total__lte=0,
                        grand_total__lte=0,
                        rounded_total__lte=0,
                    )
                ),
                name="%(app_label)s_%(class)s_amount_sign",
            ),
        ]

    @property
    def against_voucher(self):
        """``(voucher_type, voucher_no)`` the party GL line is tagged with: the
        invoice itself, except a return that updates the original's outstanding
        (``update_outstanding_for_self`` off) -- ERPNext's ``against_voucher``."""
        if (
            self.is_return
            and self.return_against_id
            and not self.update_outstanding_for_self
        ):
            return self.voucher_label(), self.return_against.name
        return self.voucher_label(), self.name

    def ensure_no_linked_returns(self):
        """ERPNext: an invoice with a submitted return cannot be cancelled."""
        names = sorted(
            type(self)
            .objects.filter(return_against=self, docstatus=DocStatus.SUBMITTED)
            .values_list("name", flat=True)
        )
        if names:
            raise ValidationError(
                _(
                    "Cannot cancel '%(name)s': cancel the linked returns first "
                    "(%(returns)s)."
                )
                % {"name": self.name, "returns": ", ".join(names)}
            )

    @property
    def party_account(self):
        return getattr(self, self.PARTY_ACCOUNT_FIELD)

    @property
    def is_overdue(self):
        return bool(
            self.docstatus == DocStatus.SUBMITTED
            and self.outstanding_amount > 0
            and self.due_date is not None
            and self.due_date < timezone.localdate()
        )

    @property
    def display_status(self):
        """The stored status, or ``"Overdue"`` while unpaid past the due date."""
        return "Overdue" if self.is_overdue else self.status

    @property
    def payment_status(self):
        """Computed payment status label for bills (Card M-3)."""
        if self.docstatus == DocStatus.DRAFT:
            return "Draft"
        if self.docstatus == DocStatus.CANCELLED:
            return "Cancelled"

        today = timezone.localdate()
        outstanding = abs(self.outstanding_amount)
        total = abs(self.grand_total)

        if outstanding > 0 and self.due_date and self.due_date < today:
            return "Overdue"
        if outstanding == 0:
            return "Paid"
        if outstanding < total:
            return "Part Paid"
        return "Due"

    @property
    def payment_label(self):
        """Paid, Due, Draft, Cancelled (Card M-3)."""
        if self.docstatus == DocStatus.DRAFT:
            return "Draft"
        if self.docstatus == DocStatus.CANCELLED:
            return "Cancelled"
        if abs(self.outstanding_amount) == 0:
            return "Paid"
        return "Due"

    @property
    def days_late(self):
        """0 if not late, else days past due_date."""
        if (
            self.docstatus != DocStatus.SUBMITTED
            or self.outstanding_amount == 0
            or not self.due_date
        ):
            return 0
        diff = (timezone.localdate() - self.due_date).days
        return max(0, diff)

    def ensure_no_linked_payments(self):
        """ERPNext's default: an invoice with a submitted payment cannot be cancelled."""
        linked = PaymentEntryReference.objects.filter(
            payment_entry__docstatus=DocStatus.SUBMITTED,
            reference_doctype=self.voucher_label(),
            reference_name=self.name,
        ).values_list("payment_entry__name", flat=True)
        names = sorted(set(linked))
        if names:
            raise ValidationError(
                _(
                    "Cannot cancel '%(name)s': cancel the linked payment entries "
                    "first (%(payments)s)."
                )
                % {"name": self.name, "payments": ", ".join(names)}
            )

    def amend(self, new_name=None):
        """Amend, then reset the derived payment fields: the copy of a cancelled
        invoice must not carry the old ``Cancelled`` status."""
        with transaction.atomic():
            amendment = super().amend(new_name=new_name)
            type(self).objects.filter(pk=amendment.pk).update(
                outstanding_amount=Decimal("0.00"), status=InvoiceStatus.DRAFT
            )
            amendment.outstanding_amount = Decimal("0.00")
            amendment.status = InvoiceStatus.DRAFT
        return amendment


class TaxLine(models.Model):
    """One row of a document's tax table (concrete classes add the parent FK).

    ``rate`` / ``account`` / ``add_deduct_tax`` are inputs; ``net_amount``,
    ``tax_amount`` and ``total`` are server-calculated. The amount is **not** called
    ``amount`` so generic "sum the child ``amount``" code never picks it up.
    """

    idx = models.PositiveSmallIntegerField()
    # v1: "On Net Total" / "Total" only (CHECK constraints).
    charge_type = models.CharField(
        max_length=24, choices=ChargeType.choices, default=ChargeType.ON_NET_TOTAL
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=200, blank=True, default="")
    rate = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    add_deduct_tax = models.CharField(
        max_length=6, choices=AddDeduct.choices, default=AddDeduct.ADD
    )
    category = models.CharField(
        max_length=24, choices=TaxCategory.choices, default=TaxCategory.TOTAL
    )
    net_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    tax_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        abstract = True
        ordering = ["idx"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(charge_type="On Net Total"),
                name="%(app_label)s_%(class)s_charge_type",
            ),
            models.CheckConstraint(
                check=models.Q(category="Total"),
                name="%(app_label)s_%(class)s_category",
            ),
            models.CheckConstraint(
                check=models.Q(add_deduct_tax__in=["Add", "Deduct"]),
                name="%(app_label)s_%(class)s_add_deduct",
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0) & models.Q(rate__lte=100),
                name="%(app_label)s_%(class)s_rate_0_100",
            ),
            models.CheckConstraint(
                check=models.Q(tax_amount__gte=0),
                name="%(app_label)s_%(class)s_tax_amount_gte_0",
            ),
        ]

    @classmethod
    def constraints_for(cls, parent_field, *, signed=False):
        """Base constraints plus the per-parent uniqueness rules.

        ``signed=True`` (invoice tax rows) drops the ``tax_amount >= 0`` CHECK: a
        return's tax rows are negative. The sign is then guaranteed at the header
        (``PayableDocument.return_constraints``)."""
        base = [
            c
            for c in TaxLine.Meta.constraints
            if not (signed and c.name.endswith("_tax_amount_gte_0"))
        ]
        return [
            *base,
            models.UniqueConstraint(
                fields=[parent_field, "idx"],
                name="%(app_label)s_%(class)s_unique_idx",
            ),
            models.UniqueConstraint(
                fields=[parent_field, "account"],
                name="%(app_label)s_%(class)s_unique_account",
            ),
        ]

    def __str__(self):
        return f"#{self.idx} {self.account_id} {self.rate}% = {self.tax_amount}"


class TaxableLine(models.Model):
    """GST columns of a document line (snapshots + server-calculated amounts).

    The per-line columns make GSTR-1 / 3B plain ``GROUP BY gst_rate`` aggregates.
    """

    gst_hsn_code = models.CharField(max_length=8, blank=True, default="")
    gst_treatment = models.CharField(
        max_length=12, choices=GSTTreatment.choices, default=GSTTreatment.TAXABLE
    )
    item_tax_template = models.ForeignKey(
        "accounts.ItemTaxTemplate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # {"CGST": "9.000", "SGST": "9.000", "IGST": "18.000"} snapshot of the rates.
    item_tax_rate = models.JSONField(default=dict, blank=True)
    gst_rate = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    cgst_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    sgst_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    igst_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    GST_LINE_FIELDS = (
        "gst_hsn_code",
        "gst_treatment",
        "item_tax_template",
        "item_tax_rate",
        "gst_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
    )

    class Meta:
        abstract = True
        # PostgreSQL does not guarantee heap order, and the tax engine updates
        # some lines in place. Without this, a saved invoice can return its lines
        # in a different order from the form (and from the dry-run preview).
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(gst_rate__gte=0) & models.Q(gst_rate__lte=100),
                name="%(app_label)s_%(class)s_gst_rate_0_100",
            ),
            models.CheckConstraint(
                check=models.Q(cgst_amount__gte=0)
                & models.Q(sgst_amount__gte=0)
                & models.Q(igst_amount__gte=0),
                name="%(app_label)s_%(class)s_gst_amounts_gte_0",
            ),
            # Only a Taxable line can carry GST.
            models.CheckConstraint(
                check=models.Q(gst_treatment="Taxable")
                | (
                    models.Q(cgst_amount=0)
                    & models.Q(sgst_amount=0)
                    & models.Q(igst_amount=0)
                ),
                name="%(app_label)s_%(class)s_only_taxable_has_gst",
            ),
            # Intra-state (CGST+SGST) and inter-state (IGST) never mix on a line.
            models.CheckConstraint(
                check=~(
                    models.Q(igst_amount__gt=0)
                    & (models.Q(cgst_amount__gt=0) | models.Q(sgst_amount__gt=0))
                ),
                name="%(app_label)s_%(class)s_no_igst_with_cgst",
            ),
        ]

    @classmethod
    def signed_constraints(cls):
        """Constraints of an invoice line: like ``Meta.constraints`` but the GST
        amounts may be negative (a return line), though never of mixed sign."""
        out = []
        for c in TaxableLine.Meta.constraints:
            if c.name.endswith("_gst_amounts_gte_0"):
                c = models.CheckConstraint(
                    check=(
                        models.Q(cgst_amount__gte=0)
                        & models.Q(sgst_amount__gte=0)
                        & models.Q(igst_amount__gte=0)
                    )
                    | (
                        models.Q(cgst_amount__lte=0)
                        & models.Q(sgst_amount__lte=0)
                        & models.Q(igst_amount__lte=0)
                    ),
                    name="%(app_label)s_%(class)s_gst_amounts_same_sign",
                )
            out.append(c)
        return out

    def compute_amount(self):
        """``qty * rate`` quantized to a money amount (the line's taxable value).

        Half up, like the tax engine and the stock ledger (divergences G3), so
        the sum of line amounts equals the stock value the SLE books per line.
        """
        return (self.qty * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------- Bank reconciliation
# ERPNext keeps ``clearance_date`` on the submitted Payment / Journal Entry. Those
# documents are immutable here, so clearance is its own append-only fact
# (``BankClearance``, latest record per voucher wins) and a statement line is an
# immutable ``BankTransaction``. Everything "reconciled" is derived from them.


class BankAccount(TimeStampedModel):
    """A company bank account: the bank-facing details of a Bank GL account."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="bank_accounts"
    )
    # The GL account whose ledger is reconciled against the bank statement.
    account = models.OneToOneField(
        Account, on_delete=models.PROTECT, related_name="bank_account"
    )
    bank_name = models.CharField(max_length=140)
    account_number = models.CharField(max_length=40, blank=True, default="")
    ifsc_code = models.CharField(max_length=11, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Vouchers dated on or before this day are taken as already reconciled (e.g. the
    # history carried over from Tally) and never listed as "uncleared".
    reconciled_up_to = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(is_default=True),
                name="unique_default_bank_account_per_company",
            ),
        ]

    @property
    def account_number_last4(self):
        return self.account_number[-4:] if self.account_number else ""

    def clean(self):
        errors = {}
        account = self.account if self.account_id else None
        if account is not None:
            if account.account_type != AccountType.BANK:
                errors["account"] = _("The GL account must be of type Bank.")
            elif account.is_group:
                errors["account"] = _("The GL account must not be a group.")
            elif account.company_id != self.company_id:
                errors["account"] = _("The GL account must belong to the company.")
        if self.ifsc_code and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", self.ifsc_code):
            errors["ifsc_code"] = _("Enter a valid IFSC code (e.g. HDFC0001234).")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.ifsc_code:
            self.ifsc_code = self.ifsc_code.strip().upper()
        self.full_clean(exclude=["created_by", "modified_by"], validate_unique=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.bank_name} ({self.account.name})"


class BankStatementImport(models.Model):
    """One uploaded statement file. Immutable: a record of what was imported."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="bank_statement_imports"
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="statement_imports"
    )
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    column_mapping = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(_("Bank statement imports are immutable."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Bank statement imports cannot be deleted."))

    def __str__(self):
        return f"{self.file_name} ({self.imported_count} rows)"


class BankTransaction(models.Model):
    """A bank statement line. Immutable once imported (a fact from the bank)."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="bank_transactions"
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="transactions"
    )
    statement_import = models.ForeignKey(
        BankStatementImport, on_delete=models.PROTECT, related_name="transactions"
    )
    row_no = models.PositiveIntegerField(default=0)
    transaction_date = models.DateField(db_index=True)
    description = models.TextField(blank=True, default="")
    reference_no = models.CharField(max_length=140, blank=True, default="")
    withdrawal = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    deposit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    balance = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    # sha256(date|withdrawal|deposit|reference|description) + ":<n>", n being the
    # occurrence of that identical line in its file, so re-importing the same or an
    # overlapping statement is a no-op while two genuinely identical lines survive.
    dedupe_key = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bank_account", "dedupe_key"],
                name="unique_bank_transaction_per_account",
            ),
            models.CheckConstraint(
                check=models.Q(withdrawal__gte=0)
                & models.Q(deposit__gte=0)
                & ~models.Q(withdrawal__gt=0, deposit__gt=0)
                & (models.Q(withdrawal__gt=0) | models.Q(deposit__gt=0)),
                name="check_bank_transaction_one_side",
            ),
        ]
        indexes = [models.Index(fields=["bank_account", "transaction_date"])]

    @property
    def amount(self):
        """Signed amount: deposits positive, withdrawals negative."""
        return self.deposit - self.withdrawal

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(_("Bank transactions are immutable."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Bank transactions cannot be deleted."))

    def __str__(self):
        return f"{self.transaction_date} {self.amount} {self.reference_no}"


class BankClearance(models.Model):
    """Append-only fact: voucher X cleared the bank on date D (or, with a null date,
    was un-cleared). The latest record per (bank account, voucher) wins."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="bank_clearances"
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="clearances"
    )
    voucher_type = models.CharField(max_length=60)
    voucher_no = models.CharField(max_length=60)
    clearance_date = models.DateField(null=True, blank=True)
    # The statement line this clearance was matched to (null for a manual clearing).
    bank_transaction = models.ForeignKey(
        BankTransaction,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="clearances",
    )
    remarks = models.CharField(max_length=255, blank=True, default="")
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    set_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # An un-clear record links to no statement line.
            models.CheckConstraint(
                check=models.Q(bank_transaction__isnull=True)
                | models.Q(clearance_date__isnull=False),
                name="check_bank_clearance_txn_needs_date",
            ),
        ]
        indexes = [
            models.Index(fields=["bank_account", "voucher_type", "voucher_no", "id"])
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(_("Bank clearances are immutable; add a new record."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Bank clearances cannot be deleted."))

    def __str__(self):
        return f"{self.voucher_type}:{self.voucher_no} -> {self.clearance_date}"


# --------------------------------------------------------- Period closing


class PeriodClosingVoucher(SubmittableDocument):
    """Year-end close (ERPNext Period Closing Voucher): zeroes every Income and
    Expense balance of the fiscal year into the closing account head."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="period_closing_vouchers"
    )
    fiscal_year = models.ForeignKey(
        "core.FiscalYear", on_delete=models.PROTECT, related_name="closing_vouchers"
    )
    # Closes from the fiscal year start up to (and including) this day; also the
    # GL posting date.
    period_end_date = models.DateField()
    closing_account_head = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="closing_vouchers"
    )
    remarks = models.TextField(null=True, blank=True)
    # Derived at submit: net profit (+) / loss (-) moved to the closing account.
    net_profit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        constraints = [
            # One live closing per company and fiscal year (docstatus 1 = Submitted).
            models.UniqueConstraint(
                fields=["company", "fiscal_year"],
                condition=models.Q(docstatus=1),
                name="unique_submitted_pcv_per_company_year",
            ),
        ]

    def validate_before_submit(self):
        from accounts.services import closing

        closing.validate(self)
        self.net_profit = closing.compute(self)["net_profit"]
        self.save(update_fields=["net_profit"])

    def on_submit(self):
        from accounts.services import closing
        from accounts.services import gl as gl_service

        plan = closing.compute(self)
        gl_service.post(
            self,
            plan["entries"],
            posting_date=self.period_end_date,
            remarks=self.remarks
            or _("Period closing %(fy)s") % {"fy": self.fiscal_year.name},
        )

    def on_cancel(self):
        from accounts.services import closing
        from accounts.services import gl as gl_service

        closing.validate_cancel(self)
        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} ({self.fiscal_year.name})"


class TaxType(models.TextChoices):
    GST = "GST", "GST"
    TDS = "TDS", "TDS"


class TaxPayment(SubmittableDocument):
    """GST or TDS Tax Payment with challan details and GL posting via JournalEntry."""

    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="tax_payments"
    )
    tax_type = models.CharField(max_length=10, choices=TaxType.choices)
    period = models.DateField()  # first day of the month
    posting_date = models.DateField()
    challan_no = models.CharField(max_length=64)
    paid_from = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tax_payments",
    )
    igst_cash = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    cgst_cash = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    sgst_cash = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    igst_credit_used = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    cgst_credit_used = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    sgst_credit_used = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    tds_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    journal_entry = models.OneToOneField(
        "accounts.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tax_payment",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "tax_type", "period", "challan_no"],
                condition=~models.Q(docstatus=DocStatus.CANCELLED),
                name="unique_tax_payment_challan_per_period",
            ),
            models.CheckConstraint(
                check=models.Q(igst_cash__gte=0)
                & models.Q(cgst_cash__gte=0)
                & models.Q(sgst_cash__gte=0)
                & models.Q(igst_credit_used__gte=0)
                & models.Q(cgst_credit_used__gte=0)
                & models.Q(sgst_credit_used__gte=0)
                & models.Q(tds_amount__gte=0),
                name="check_tax_payment_amounts_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(igst_cash__gt=0)
                | models.Q(cgst_cash__gt=0)
                | models.Q(sgst_cash__gt=0)
                | models.Q(igst_credit_used__gt=0)
                | models.Q(cgst_credit_used__gt=0)
                | models.Q(sgst_credit_used__gt=0)
                | models.Q(tds_amount__gt=0),
                name="check_tax_payment_has_non_zero_amount",
            ),
            models.CheckConstraint(
                check=~models.Q(tax_type="GST") | models.Q(tds_amount=Decimal("0.00")),
                name="check_tax_payment_gst_no_tds",
            ),
            models.CheckConstraint(
                check=~models.Q(tax_type="TDS")
                | (
                    models.Q(igst_cash=Decimal("0.00"))
                    & models.Q(cgst_cash=Decimal("0.00"))
                    & models.Q(sgst_cash=Decimal("0.00"))
                    & models.Q(igst_credit_used=Decimal("0.00"))
                    & models.Q(cgst_credit_used=Decimal("0.00"))
                    & models.Q(sgst_credit_used=Decimal("0.00"))
                ),
                name="check_tax_payment_tds_no_gst",
            ),
        ]

    def clean(self):
        super().clean()
        if self.period and self.period.day != 1:
            self.period = self.period.replace(day=1)
        if self.tax_type == "GST":
            if self.tds_amount and self.tds_amount > 0:
                raise ValidationError(_("GST tax payments cannot have TDS amount."))
            total_cash = self.igst_cash + self.cgst_cash + self.sgst_cash
            total_amount = (
                total_cash
                + self.igst_credit_used
                + self.cgst_credit_used
                + self.sgst_credit_used
            )
            if total_amount <= 0:
                raise ValidationError(
                    _("Tax payment must have at least one non-zero amount.")
                )
            if total_cash > 0 and not self.paid_from:
                raise ValidationError(
                    _("Paid From account is required when paying tax in cash.")
                )
        elif self.tax_type == "TDS":
            if (
                self.igst_cash > 0
                or self.cgst_cash > 0
                or self.sgst_cash > 0
                or self.igst_credit_used > 0
                or self.cgst_credit_used > 0
                or self.sgst_credit_used > 0
            ):
                raise ValidationError(_("TDS tax payments cannot have GST amounts."))
            if self.tds_amount <= 0:
                raise ValidationError(_("TDS amount must be greater than zero."))
            if not self.paid_from:
                raise ValidationError(
                    _("Paid From account is required for TDS payment.")
                )
        if self.paid_from:
            if self.paid_from.company_id != self.company_id:
                raise ValidationError(
                    _("Paid From account does not belong to company '%(company)s'.")
                    % {"company": self.company.name}
                )
            if self.paid_from.is_group:
                raise ValidationError(_("Paid From account cannot be a group account."))

    def validate_before_submit(self):
        if self.company.require_proof:
            from core.attachments import has_proof

            if not has_proof(self):
                raise ValidationError(
                    _("Proof attachment is required before submission.")
                )
        self.clean()

    def on_submit(self):
        from accounts.services.tax_payment import submit_tax_payment

        submit_tax_payment(self)

    def on_cancel(self):
        from accounts.services.tax_payment import cancel_tax_payment

        cancel_tax_payment(self)

    def __str__(self):
        return f"{self.name} ({self.tax_type} - {self.challan_no})"
