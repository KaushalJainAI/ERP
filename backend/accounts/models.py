from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel, SubmittableDocument, Company
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

    def validate_before_submit(self):
        if self.paid_amount <= 0:
            raise ValidationError("Paid amount must be greater than zero.")
        if self.paid_from.is_group or self.paid_to.is_group:
            raise ValidationError("Posting to group accounts is not allowed.")
        if self.paid_from_id == self.paid_to_id:
            raise ValidationError("Paid From and Paid To accounts must be different.")
        if (
            self.paid_from.company_id != self.company_id
            or self.paid_to.company_id != self.company_id
        ):
            raise ValidationError(
                "Payment accounts must belong to the payment's company."
            )

    def on_submit(self):
        from accounts.services import gl as gl_service

        entries = [
            {
                "account": self.paid_to,
                "debit": self.paid_amount,
                "credit": Decimal("0.00"),
                "party_type": self.party_type,
                "party": self.party_name,
            },
            {
                "account": self.paid_from,
                "debit": Decimal("0.00"),
                "credit": self.paid_amount,
                "party_type": self.party_type,
                "party": self.party_name,
            },
        ]
        gl_service.post(
            self,
            entries,
            posting_date=self.posting_date,
            remarks=f"Payment {self.payment_type}: {self.reference_no or ''}",
        )

    def on_cancel(self):
        from accounts.services import gl as gl_service

        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} [{self.payment_type}] - {self.paid_amount}"


class PaymentEntryReference(models.Model):
    payment_entry = models.ForeignKey(
        PaymentEntry, on_delete=models.CASCADE, related_name="references"
    )
    reference_doctype = models.CharField(max_length=60)
    reference_name = models.CharField(max_length=60)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)

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
