from decimal import ROUND_HALF_UP, Decimal
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
            raise ValidationError("GLEntry rows are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("GLEntry rows are immutable and cannot be deleted.")

    def __str__(self):
        return f"{self.posting_date} - {self.voucher_type}:{self.voucher_no} - {self.account.name} (Dr:{self.debit}/Cr:{self.credit})"


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

    def recompute_totals(self):
        """Set ``total_debit``/``total_credit`` from the lines (they are derived
        values, never trusted from a client or a prior write)."""
        lines = list(self.lines.all())
        self.total_debit = sum((line.debit for line in lines), Decimal("0.00"))
        self.total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    def validate_before_submit(self):
        lines = self.lines.all()
        if not lines.exists():
            raise ValidationError("Journal Entry must have at least one line.")
        for line in lines:
            # Keep every line inside the entry's company; gl.post already blocks
            # group accounts, but nothing else stops a cross-company posting.
            if line.account.company_id != self.company_id:
                raise ValidationError(
                    f"Account '{line.account.name}' does not belong to the entry's company."
                )
        dr_sum = sum(line.debit for line in lines)
        cr_sum = sum(line.credit for line in lines)
        if dr_sum != cr_sum:
            raise ValidationError(
                f"Journal Entry is unbalanced: Total Debit ({dr_sum}) != Total Credit ({cr_sum})."
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

    #: ``reference_doctype`` -> (payment_type, party_type, invoice party-account field)
    ALLOCATION_TARGETS = {
        "selling.SalesInvoice": (PaymentType.RECEIVE, "Customer", "debit_to"),
        "buying.PurchaseInvoice": (PaymentType.PAY, "Supplier", "credit_to"),
    }

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
            if self.payment_type != payment_type or self.party_type != party_type:
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
            account_field = self.ALLOCATION_TARGETS[ref.reference_doctype][2]
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
            if invoice.party.name != self.party_name:
                raise ValidationError(
                    _("Invoice '%(name)s' belongs to a different party.")
                    % {"name": ref.reference_name}
                )
            if getattr(invoice, f"{account_field}_id") != party_account.pk:
                raise ValidationError(
                    _(
                        "Invoice '%(name)s' is booked to a different account than "
                        "this payment's party account."
                    )
                    % {"name": ref.reference_name}
                )
            open_amount = outstanding_service.current_outstanding(invoice)
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
        from accounts.services import outstanding as outstanding_service

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
        party_lines = [party_line(ref.allocated_amount, ref) for ref, _inv in allocations]
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
            outstanding_service.refresh(invoice)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from accounts.services import outstanding as outstanding_service

        gl_service.reverse(self)
        for ref in self.references.all().order_by("reference_doctype", "reference_name"):
            if ref.reference_doctype not in self.ALLOCATION_TARGETS:
                continue
            invoice = (
                self.invoice_model(ref.reference_doctype)
                .objects.filter(name=ref.reference_name)
                .first()
            )
            if invoice is not None:
                outstanding_service.refresh(invoice)

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

    def __str__(self):
        return f"{self.payment_entry.name} -> {self.reference_doctype}:{self.reference_name} ({self.allocated_amount})"


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

    class Meta:
        abstract = True

    @classmethod
    def voucher_label(cls):
        """``app_label.ObjectName``: the ``voucher_type`` / ``against_voucher_type``
        / ``PaymentEntryReference.reference_doctype`` string of this document."""
        return f"{cls._meta.app_label}.{cls._meta.object_name}"

    @staticmethod
    def outstanding_constraint():
        return models.CheckConstraint(
            check=models.Q(outstanding_amount__gte=0),
            name="%(app_label)s_%(class)s_outstanding_gte_0",
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
    def constraints_for(cls, parent_field):
        """Base constraints plus the per-parent uniqueness rules."""
        return [
            *TaxLine.Meta.constraints,
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

    def compute_amount(self):
        """``qty * rate`` quantized to a money amount (the line's taxable value).

        Half up, like the tax engine and the stock ledger (divergences G3), so
        the sum of line amounts equals the stock value the SLE books per line.
        """
        return (self.qty * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
