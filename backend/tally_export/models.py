"""ERP -> Tally Prime export: ledger / stock-item maps and the immutable export log.

The ERP is the data-entry system; the accountant keeps the official books in
Tally Prime. A Tally voucher is *derived* from the immutable GL (and stock)
ledgers, never stored; the only state kept here is

* how ERP accounts / parties / items are named in Tally (``TallyLedgerMap``,
  ``TallyStockItemMap``, ``TallyExportSettings``), and
* what was handed over and when (``TallyExportBatch`` + ``TallyExportedVoucher``,
  append-only), from which "pending" is derived.
"""

import uuid

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.db import models

from core.models import Company, TimeStampedModel


class _AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            _("Export log rows are immutable and cannot be updated.")
        )

    def delete(self):
        raise ValidationError(
            _("Export log rows are immutable and cannot be deleted.")
        )


class _AppendOnly(models.Model):
    """Rows are facts about a hand-over: insert once, never update or delete."""

    objects = _AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                _(
                    "%(model)s rows are immutable and cannot be updated."
                )
                % {"model": type(self).__name__}
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _(
                "%(model)s rows are immutable and cannot be deleted."
            )
            % {"model": type(self).__name__}
        )


class TallyExportSettings(TimeStampedModel):
    """Per-company export settings (one row per company)."""

    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name="tally_export_settings"
    )
    #: ``SVCURRENTCOMPANY``: the company exactly as named in Tally Prime.
    tally_company_name = models.CharField(max_length=200, blank=True, default="")
    #: ERP document -> Tally voucher type overrides, merged over
    #: ``constants.DEFAULT_VOUCHER_TYPES``. Keys are ``"app.Model"`` or
    #: ``"app.Model:variant"``, e.g. ``{"selling.SalesInvoice": "Sales Gst"}`` for a
    #: company whose Tally sales voucher type is custom.
    voucher_type_overrides = models.JSONField(default=dict, blank=True)
    #: Vouchers whose ERP name starts with one of these are never exported:
    #: ``TLY-`` documents are the ten years already imported *from* Tally.
    excluded_prefixes = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name_plural = "Tally export settings"

    def __str__(self):
        return f"Tally export settings: {self.company.name}"


class TallyLedgerMap(TimeStampedModel):
    """How an ERP account (optionally one party) is named in Tally.

    Party lines (Debtors / Creditors) map per party: ``party_type`` + ``party``
    set. Everything else maps per account (both blank).
    """

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="tally_ledger_maps"
    )
    account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="tally_ledger_maps"
    )
    party_type = models.CharField(max_length=20, blank=True, default="")
    party = models.CharField(max_length=200, blank=True, default="")
    tally_ledger = models.CharField(max_length=200)
    #: Parent group used only when the ledger master has to be created in Tally.
    #: Blank = derived from the account (see ``services.maps.suggest_group``).
    tally_group = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(max_length=10, default="manual")  # manual|legacy|auto
    #: The export batch that carried this ledger's master to Tally (if any).
    master_batch = models.ForeignKey(
        "TallyExportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "account", "party_type", "party"],
                name="unique_tally_ledger_map",
            ),
            models.CheckConstraint(
                check=(models.Q(party_type="") & models.Q(party=""))
                | (~models.Q(party_type="") & ~models.Q(party="")),
                name="check_tally_ledger_map_party_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(tally_ledger=""), name="check_tally_ledger_named"
            ),
        ]
        indexes = [models.Index(fields=["company", "party_type", "party"])]

    def clean(self):
        if self.account_id and self.account.company_id != self.company_id:
            raise ValidationError(
                {"account": _("The account belongs to a different company.")}
            )
        if self.account_id and self.account.is_group:
            raise ValidationError({"account": _("Group accounts are not posted to.")})

    def __str__(self):
        who = f" [{self.party}]" if self.party else ""
        return f"{self.account.name}{who} -> {self.tally_ledger}"


class TallyStockItemMap(TimeStampedModel):
    """How an ERP item is named in Tally (stock item name + unit)."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="tally_item_maps"
    )
    item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="tally_item_maps"
    )
    tally_item = models.CharField(max_length=200)
    tally_unit = models.CharField(max_length=40, blank=True, default="")
    source = models.CharField(max_length=10, default="manual")
    master_batch = models.ForeignKey(
        "TallyExportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "item"], name="unique_tally_item_map"
            ),
            models.CheckConstraint(
                check=~models.Q(tally_item=""), name="check_tally_item_named"
            ),
        ]

    def __str__(self):
        return f"{self.item.code} -> {self.tally_item}"


def _batch_path(instance, filename):
    return f"tally_export/{instance.company_id}/{filename}"


class TallyExportBatch(_AppendOnly):
    """One hand-over of vouchers to Tally (the stored file is what was sent)."""

    class Kind(models.TextChoices):
        RANGE = "range", "Date range"
        REEXPORT = "reexport", "Re-export"

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tally_export_batches"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.RANGE)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    #: For a re-export of a whole batch: the batch it repeats.
    source_batch = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    #: Voucher import file (REPORTNAME Vouchers) and its SHA-256.
    file = models.FileField(upload_to=_batch_path, max_length=300)
    file_sha256 = models.CharField(max_length=64)
    #: Masters import file (REPORTNAME All Masters), only when masters were requested.
    masters_file = models.FileField(upload_to=_batch_path, max_length=300, blank=True)
    masters_sha256 = models.CharField(max_length=64, blank=True, default="")
    voucher_count = models.PositiveIntegerField(default=0)
    cancel_count = models.PositiveIntegerField(default=0)
    ledger_master_count = models.PositiveIntegerField(default=0)
    item_master_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "Tally export batches"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                check=models.Q(from_date__isnull=True)
                | models.Q(to_date__isnull=True)
                | models.Q(from_date__lte=models.F("to_date")),
                name="check_tally_batch_dates",
            ),
        ]

    def __str__(self):
        return f"Tally export #{self.pk} ({self.voucher_count} vouchers)"


class TallyExportedVoucher(_AppendOnly):
    """One voucher inside a batch (Create or Cancel), as it was exported."""

    class Action(models.TextChoices):
        CREATE = "Create", "Create"
        CANCEL = "Cancel", "Cancel"

    batch = models.ForeignKey(
        TallyExportBatch, on_delete=models.PROTECT, related_name="vouchers"
    )
    doctype = models.CharField(max_length=60)  # "selling.SalesInvoice"
    name = models.CharField(max_length=60)
    #: The ERP document's docstatus when exported (1 submitted, 2 cancelled).
    docstatus = models.PositiveSmallIntegerField()
    action = models.CharField(max_length=6, choices=Action.choices)
    tally_voucher_type = models.CharField(max_length=100)
    posting_date = models.DateField()
    remote_id = models.CharField(max_length=64)
    #: SHA-256 of the voucher's XML as exported.
    voucher_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "doctype", "name"], name="unique_tally_batch_voucher"
            ),
            models.CheckConstraint(
                check=models.Q(docstatus__in=[1, 2]),
                name="check_tally_exported_docstatus",
            ),
        ]
        indexes = [models.Index(fields=["doctype", "name"])]

    def __str__(self):
        return f"{self.action} {self.doctype} {self.name}"


def remote_id_for(company, doctype: str, name: str) -> str:
    """Stable Tally REMOTEID / GUID of a voucher: the same document always gets
    the same id, so a re-import alters the voucher instead of duplicating it."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"erp-tally:{company.abbr}:{doctype}:{name}")
    )
