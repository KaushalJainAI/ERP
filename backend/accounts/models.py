from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel, SubmittableDocument, Company



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


class Account(TimeStampedModel):
    """Chart of Accounts tree node."""
    name = models.CharField(max_length=200)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="accounts")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.RESTRICT, related_name="children")
    root_type = models.CharField(max_length=12, choices=AccountRootType.choices)
    account_type = models.CharField(max_length=40, choices=AccountType.choices, null=True, blank=True)
    account_number = models.CharField(max_length=40, null=True, blank=True)
    is_group = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_account_per_company")
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class GLEntry(models.Model):
    """
    Immutable General Ledger Entry (append-only fact).
    Never updated or deleted over API or direct SQL.
    """
    posting_date = models.DateField(db_index=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="gl_entries")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="gl_entries")
    party_type = models.CharField(max_length=20, null=True, blank=True)
    party = models.CharField(max_length=200, null=True, blank=True)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
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
                check=models.Q(debit__gte=0) & models.Q(credit__gte=0) & ~models.Q(debit__gt=0, credit__gt=0),
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
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="journal_entries")
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    user_remark = models.TextField(null=True, blank=True)

    def validate_before_submit(self):
        lines = self.lines.all()
        if not lines.exists():
            raise ValidationError("Journal Entry must have at least one line.")
        dr_sum = sum(line.debit for line in lines)
        cr_sum = sum(line.credit for line in lines)
        if dr_sum != cr_sum:
            raise ValidationError(f"Journal Entry is unbalanced: Total Debit ({dr_sum}) != Total Credit ({cr_sum}).")

    def on_submit(self):
        from accounts.services import gl as gl_service
        entries = []
        for line in self.lines.all():
            entries.append({
                "account": line.account,
                "debit": line.debit,
                "credit": line.credit,
                "party_type": line.party_type,
                "party": line.party,
            })
        gl_service.post(self, entries, posting_date=self.posting_date, remarks=self.user_remark)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} ({self.posting_date})"


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
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
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payment_entries")
    party_type = models.CharField(max_length=20, null=True, blank=True)
    party_name = models.CharField(max_length=200, null=True, blank=True)
    paid_from = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="paid_from_payments")
    paid_to = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="paid_to_payments")
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    received_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    reference_no = models.CharField(max_length=100, null=True, blank=True)

    def validate_before_submit(self):
        if self.paid_amount <= 0:
            raise ValidationError("Paid amount must be greater than zero.")
        if self.paid_from.is_group or self.paid_to.is_group:
            raise ValidationError("Posting to group accounts is not allowed.")

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
        gl_service.post(self, entries, posting_date=self.posting_date, remarks=f"Payment {self.payment_type}: {self.reference_no or ''}")

    def on_cancel(self):
        from accounts.services import gl as gl_service
        gl_service.reverse(self)

    def __str__(self):
        return f"{self.name} [{self.payment_type}] - {self.paid_amount}"


class PaymentEntryReference(models.Model):
    payment_entry = models.ForeignKey(PaymentEntry, on_delete=models.CASCADE, related_name="references")
    reference_doctype = models.CharField(max_length=60)
    reference_name = models.CharField(max_length=60)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)

    def __str__(self):
        return f"{self.payment_entry.name} -> {self.reference_doctype}:{self.reference_name} ({self.allocated_amount})"


