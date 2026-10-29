"""Immutable history carried over from TallyPrime (written only by import_tally).

``TallyRecord`` is the lossless archive: every Tally master and voucher of every
year, with the record exactly as Tally exported it (``raw``) and a link to the
ERP document it became. Fields the importer does not map are therefore never
lost and can be promoted later without going back to Tally.

``TallyItemLine`` is the item-wise sales / purchase / stock-journal history
(qty, rate, party, account) that the GL-only import of the old years cannot
show. It carries no ledger effect: the accounting of those vouchers is in the
``TLY-*`` Journal Entries.
"""

from django.core.exceptions import ValidationError
from django.db import models

from buying.models import Supplier
from core.models import Company
from selling.models import Customer
from stock.models import Item


class _AppendOnly(models.Model):
    """Rows are facts from the old books: insert once, never update or delete."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                f"{type(self).__name__} rows are immutable and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            f"{type(self).__name__} rows are immutable and cannot be deleted."
        )


class TallyRecordKind(models.TextChoices):
    COMPANY = "company", "Company"
    GROUP = "group", "Group"
    LEDGER = "ledger", "Ledger"
    STOCK_GROUP = "stock_group", "Stock Group"
    STOCK_ITEM = "stock_item", "Stock Item"
    UNIT = "unit", "Unit"
    GODOWN = "godown", "Godown"
    VOUCHER_TYPE = "voucher_type", "Voucher Type"
    COST_CATEGORY = "cost_category", "Cost Category"
    CURRENCY = "currency", "Currency"
    VOUCHER = "voucher", "Voucher"


class TallyRecord(_AppendOnly):
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tally_records"
    )
    kind = models.CharField(max_length=20, choices=TallyRecordKind.choices)
    #: Tally data-folder id; one folder per financial year.
    company_year = models.CharField(max_length=10)
    fiscal_year = models.CharField(max_length=7)  # "2024-25"
    #: Tally GUID; unique within a year only (vouchers carry over between years).
    guid = models.CharField(max_length=100)
    name = models.CharField(max_length=200, blank=True, default="")
    # -- voucher header (blank for masters) --
    voucher_type = models.CharField(max_length=100, blank=True, default="")
    base_voucher_type = models.CharField(max_length=40, blank=True, default="")
    voucher_no = models.CharField(max_length=100, blank=True, default="")
    posting_date = models.DateField(null=True, blank=True)
    party = models.CharField(max_length=200, blank=True, default="")
    #: Tally sign convention: negative = debit, positive = credit.
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    reference = models.CharField(max_length=200, blank=True, default="")
    narration = models.TextField(blank=True, default="")
    is_cancelled = models.BooleanField(default=False)
    #: ERP documents this record became, ``[{"doctype": ..., "name": ...}]``.
    erp_links = models.JSONField(default=list, blank=True)
    #: Primary ERP document name (the TLY-* Journal Entry for a voucher).
    erp_name = models.CharField(max_length=60, blank=True, default="", db_index=True)
    #: The record exactly as Tally exported it (XML).
    raw = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "company_year", "kind", "guid"],
                name="unique_tally_record_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "fiscal_year"]),
            models.Index(fields=["voucher_no"]),
            models.Index(fields=["posting_date"]),
            models.Index(fields=["party"]),
        ]
        ordering = ("fiscal_year", "kind", "posting_date", "id")

    def __str__(self):
        if self.kind == TallyRecordKind.VOUCHER:
            return f"{self.fiscal_year} {self.voucher_type} {self.voucher_no}"
        return f"{self.fiscal_year} {self.kind} {self.name}"


class TallyItemLine(_AppendOnly):
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tally_item_lines"
    )
    record = models.ForeignKey(
        TallyRecord, on_delete=models.PROTECT, related_name="item_lines"
    )
    fiscal_year = models.CharField(max_length=7)
    posting_date = models.DateField()
    #: Tally's base type: Sales, Purchase, Credit Note, Debit Note, Stock Journal.
    voucher_type = models.CharField(max_length=40)
    voucher_no = models.CharField(max_length=100, blank=True, default="")
    party = models.CharField(max_length=200, blank=True, default="")
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tally_item_lines",
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tally_item_lines",
    )
    item_name = models.CharField(max_length=200)
    item = models.ForeignKey(
        Item,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tally_item_lines",
    )
    #: True = stock in (purchase, sales return), False = stock out.
    is_inward = models.BooleanField()
    qty = models.DecimalField(max_digits=18, decimal_places=3, null=True)
    billed_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True)
    uom = models.CharField(max_length=20, blank=True, default="")
    rate = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    discount_percent = models.DecimalField(
        max_digits=9, decimal_places=4, null=True, blank=True
    )
    #: Line value before tax, always positive; the direction is ``is_inward``.
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    #: The sales / purchase ledger the line was booked to.
    ledger = models.CharField(max_length=200, blank=True, default="")
    place_of_supply = models.CharField(max_length=60, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0)
                & (models.Q(qty__isnull=True) | models.Q(qty__gte=0))
                & (models.Q(billed_qty__isnull=True) | models.Q(billed_qty__gte=0))
                & (models.Q(rate__isnull=True) | models.Q(rate__gte=0)),
                name="check_tally_item_line_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(customer__isnull=True) | models.Q(supplier__isnull=True),
                name="check_tally_item_line_one_party",
            ),
        ]
        indexes = [
            models.Index(fields=["item", "posting_date"]),
            models.Index(fields=["customer", "posting_date"]),
            models.Index(fields=["supplier", "posting_date"]),
            models.Index(fields=["fiscal_year", "voucher_type"]),
            models.Index(fields=["item_name"]),
        ]
        ordering = ("posting_date", "id")

    def __str__(self):
        return f"{self.posting_date} {self.voucher_type} {self.voucher_no} {self.item_name}"
