from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext as _
from core.models import TimeStampedModel, SubmittableDocument, Company
from gst.validators import validate_hsn_code


class ValuationMethod(models.TextChoices):
    FIFO = "FIFO", "FIFO"
    MOVING_AVERAGE = "Moving Average", "Moving Average"


class UOM(TimeStampedModel):
    """Unit of Measure."""

    name = models.CharField(max_length=40, unique=True)
    # GST Unique Quantity Code ("NOS", "KGS", "MTR", ...) used in the GSTR-1 HSN summary.
    gst_uqc = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        verbose_name = "Unit of Measure"
        verbose_name_plural = "Units of Measure"

    def __str__(self):
        return self.name


class ItemGroup(TimeStampedModel):
    """Item Group tree node."""

    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
    )
    is_group = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Item(TimeStampedModel):
    """Item master record."""

    code = models.CharField(max_length=140, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    item_group = models.ForeignKey(
        ItemGroup, on_delete=models.PROTECT, related_name="items"
    )
    stock_uom = models.ForeignKey(UOM, on_delete=models.PROTECT, related_name="items")
    is_stock_item = models.BooleanField(default=True)
    valuation_method = models.CharField(
        max_length=15,
        choices=ValuationMethod.choices,
        default=ValuationMethod.FIFO,
    )
    standard_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    is_active = models.BooleanField(default=True)

    # GST: HSN/SAC (digits only, 4/6/8 long when set), nil/exempt/non-GST flags and
    # the item's rate set. All optional so documents created without them still work.
    gst_hsn_code = models.CharField(
        max_length=8, blank=True, default="", validators=[validate_hsn_code]
    )
    is_nil_exempt = models.BooleanField(default=False)
    is_non_gst = models.BooleanField(default=False)
    item_tax_template = models.ForeignKey(
        "accounts.ItemTaxTemplate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # Batch tracking (FSSAI traceability / best-before). Expiry lives on the Batch,
    # so an expiry-tracked item must be batch-tracked. Both flags are frozen once
    # the item has any stock ledger entry (see ``save``): there is no stock
    # reconciliation document to batch-assign existing stock.
    has_batch_no = models.BooleanField(default=False)
    has_expiry_date = models.BooleanField(default=False)
    shelf_life_in_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(is_nil_exempt=True) & models.Q(is_non_gst=True)),
                name="check_item_not_both_nil_exempt_and_non_gst",
            ),
            models.CheckConstraint(
                check=models.Q(has_expiry_date=False) | models.Q(has_batch_no=True),
                name="check_item_expiry_requires_batch",
            ),
            models.CheckConstraint(
                check=models.Q(shelf_life_in_days__isnull=True)
                | (models.Q(has_expiry_date=True) & models.Q(shelf_life_in_days__gt=0)),
                name="check_item_shelf_life_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        touches_flags = update_fields is None or bool(
            {"has_batch_no", "has_expiry_date"} & set(update_fields)
        )
        if self.pk and touches_flags:
            before = (
                Item.objects.filter(pk=self.pk)
                .values("has_batch_no", "has_expiry_date")
                .first()
            )
            if (
                before
                and (
                    before["has_batch_no"] != self.has_batch_no
                    or before["has_expiry_date"] != self.has_expiry_date
                )
                and StockLedgerEntry.objects.filter(item_id=self.pk).exists()
            ):
                raise ValidationError(
                    _(
                        "Batch tracking cannot be changed for item '%(code)s': "
                        "it already has stock ledger entries."
                    )
                    % {"code": self.code}
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Warehouse(TimeStampedModel):
    """Warehouse tree node."""

    name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="warehouses"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
    )
    is_group = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_warehouse_per_company"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class Batch(TimeStampedModel):
    """A lot of one batch-tracked item (manufacturing / expiry / supplier lot).

    The batch *balance* is never stored: it is derived from the ledger
    (``stock.services.batches.batch_balance``).
    """

    batch_no = models.CharField(max_length=60, unique=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="batches")
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    # Raw materials: the supplier's own lot number.
    supplier_batch_no = models.CharField(max_length=100, blank=True, default="")
    # Provenance (e.g. "stock.StockEntry" / "STE-2026-0001"). Deliberately not a
    # Supplier FK, which would make ``stock`` import ``buying``.
    reference_doctype = models.CharField(max_length=60, blank=True, default="")
    reference_name = models.CharField(max_length=60, blank=True, default="")
    description = models.CharField(max_length=240, blank=True, default="")
    is_disabled = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Batches"
        constraints = [
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True)
                | models.Q(manufacturing_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("manufacturing_date")),
                name="check_batch_expiry_after_manufacturing",
            )
        ]
        indexes = [models.Index(fields=["item", "expiry_date"])]

    def save(self, *args, **kwargs):
        if self.pk:
            before = Batch.objects.filter(pk=self.pk).values("item_id").first()
            if (
                before
                and before["item_id"] != self.item_id
                and StockLedgerEntry.objects.filter(batch_id=self.pk).exists()
            ):
                raise ValidationError(
                    _("The item of batch '%(batch)s' cannot change once it has stock.")
                    % {"batch": self.batch_no}
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.batch_no


class StockLedgerEntry(models.Model):
    """
    Immutable Stock Ledger Entry (append-only fact).
    Never updated or deleted directly over API or SQL.
    """

    posting_date = models.DateField(db_index=True)
    posting_time = models.TimeField(db_index=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sle_entries")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="sle_entries"
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)  # Signed: +in / -out
    valuation_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    stock_value_difference = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    voucher_type = models.CharField(max_length=60)
    voucher_no = models.CharField(max_length=60)
    is_cancelled = models.BooleanField(default=False)
    # Set iff the item is batch-tracked (asserted in ledger.post()).
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sle_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["item", "warehouse", "posting_date", "posting_time"]),
            models.Index(fields=["voucher_type", "voucher_no"]),
            models.Index(
                fields=["batch", "warehouse"],
                condition=models.Q(batch__isnull=False),
                name="sle_batch_warehouse_idx",
            ),
            models.Index(
                fields=["item", "warehouse", "batch"], name="sle_item_wh_batch_idx"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_allow_internal_cancellation", False):
            raise ValidationError(
                "StockLedgerEntry rows are immutable and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "StockLedgerEntry rows are immutable and cannot be deleted."
        )

    def __str__(self):
        return f"{self.posting_date} {self.posting_time} - {self.item.code} @ {self.warehouse.name}: Qty {self.qty}"


class Bin(TimeStampedModel):
    """
    Derived stock balance projection per item + warehouse.
    Rebuildable at any time from StockLedgerEntry replay.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="bins")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="bins"
    )
    actual_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    valuation_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    stock_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "warehouse"], name="unique_bin_per_item_warehouse"
            )
        ]

    def __str__(self):
        return f"Bin {self.item.code} @ {self.warehouse.name}: Qty {self.actual_qty}"


class StockEntryType(models.TextChoices):
    MATERIAL_RECEIPT = "Material Receipt", "Material Receipt"
    MATERIAL_ISSUE = "Material Issue", "Material Issue"
    MATERIAL_TRANSFER = "Material Transfer", "Material Transfer"


class StockEntry(SubmittableDocument):
    stock_entry_type = models.CharField(max_length=25, choices=StockEntryType.choices)
    posting_date = models.DateField()
    posting_time = models.TimeField(null=True, blank=True)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="stock_entries"
    )

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Stock Entry must have at least one item.")
        self.company.get_perpetual_accounts(
            need_adjustment=self.stock_entry_type != StockEntryType.MATERIAL_TRANSFER
        )  # raises if perpetual is on but misconfigured
        for item in items:
            if (
                self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT
                and not item.target_warehouse
            ):
                raise ValidationError("Material Receipt requires a Target Warehouse.")
            elif (
                self.stock_entry_type == StockEntryType.MATERIAL_ISSUE
                and not item.source_warehouse
            ):
                raise ValidationError("Material Issue requires a Source Warehouse.")
            elif self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER and (
                not item.source_warehouse or not item.target_warehouse
            ):
                raise ValidationError(
                    "Material Transfer requires both Source and Target Warehouses."
                )
            self._validate_batch_line(item)

    def _validate_batch_line(self, line):
        """Batch rules for one line (the submit gate; ``ledger.post`` re-asserts
        the item/batch invariants centrally)."""
        from stock.services import batches as batch_service

        item = line.item
        new_fields = bool(
            line.supplier_batch_no or line.manufacturing_date or line.expiry_date
        )
        if not item.has_batch_no:
            if line.batch_id or new_fields:
                raise ValidationError(
                    _("Item '%(code)s' is not batch-tracked; remove the batch details.")
                    % {"code": item.code}
                )
            return
        if line.batch_id:
            if new_fields:
                raise ValidationError(
                    _(
                        "Item '%(code)s': pick an existing batch or enter new-batch "
                        "details, not both."
                    )
                    % {"code": item.code}
                )
            return
        if self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT:
            # A new batch will be created at submit: refuse now if it cannot be
            # given an expiry date.
            batch_service.resolve_dates(
                item,
                line.manufacturing_date,
                line.expiry_date,
                default_manufacturing_date=self.posting_date,
            )
            return
        raise ValidationError(
            batch_service.batch_required_message(
                item,
                line.source_warehouse,
                line.qty,
                self.posting_date,
            )
        )

    def on_submit(self):
        from stock.services import batches as batch_service
        from stock.services import ledger as stock_service

        sle_list = []
        for item in self.items.all():
            batch = item.batch
            if (
                self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT
                and item.item.has_batch_no
                and batch is None
            ):
                batch = batch_service.create_batch(
                    item.item,
                    manufacturing_date=item.manufacturing_date,
                    expiry_date=item.expiry_date,
                    supplier_batch_no=item.supplier_batch_no,
                    default_manufacturing_date=self.posting_date,
                    numbering_date=self.posting_date,
                    reference_doctype=f"{self._meta.app_label}.{self._meta.object_name}",
                    reference_name=self.name,
                )
                item.batch = batch
                item.save(update_fields=["batch"])
            if self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT:
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.target_warehouse,
                        "qty": item.qty,
                        "valuation_rate": item.valuation_rate,
                        "batch": batch,
                    }
                )
            elif self.stock_entry_type == StockEntryType.MATERIAL_ISSUE:
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.source_warehouse,
                        "qty": -abs(item.qty),
                        "valuation_rate": item.valuation_rate,
                        "batch": batch,
                        # Material Issue is the write-off path: expired stock may leave.
                        "allow_expired": True,
                    }
                )
            elif self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER:
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.source_warehouse,
                        "qty": -abs(item.qty),
                        "valuation_rate": item.valuation_rate,
                        "batch": batch,
                    }
                )
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.target_warehouse,
                        "qty": abs(item.qty),
                        "valuation_rate": item.valuation_rate,
                        "batch": batch,
                        # Value the receipt at the rate the source warehouse released,
                        # so an internal transfer conserves total stock value.
                        "use_outgoing_rate": True,
                    }
                )
        sles = stock_service.post(
            self,
            sle_list,
            posting_date=self.posting_date,
            posting_time=self.posting_time,
        )

        # Perpetual inventory: receipts/issues change the inventory account against
        # the stock adjustment account; a transfer within one company moves stock
        # between warehouses that share the inventory account, so it posts no GL.
        perpetual = self.company.get_perpetual_accounts(
            need_adjustment=self.stock_entry_type != StockEntryType.MATERIAL_TRANSFER
        )
        if not perpetual or self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER:
            return
        value = sum((s.stock_value_difference for s in sles), Decimal("0.00"))
        if not value:
            return
        inventory = perpetual["default_inventory_account"]
        adjustment = perpetual["stock_adjustment_account"]
        amount = abs(value)
        zero = Decimal("0.00")
        if value > 0:  # Material Receipt
            gl_entries = [
                {"account": inventory, "debit": amount, "credit": zero},
                {"account": adjustment, "debit": zero, "credit": amount},
            ]
        else:  # Material Issue
            gl_entries = [
                {"account": adjustment, "debit": amount, "credit": zero},
                {"account": inventory, "debit": zero, "credit": amount},
            ]
        from accounts.services import gl as gl_service

        gl_service.post(self, gl_entries, posting_date=self.posting_date)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

        stock_service.reverse(self)
        gl_service.reverse(
            self
        )  # no-op when the entry posted no GL (transfer / perpetual off)

    def __str__(self):
        return f"{self.name} [{self.stock_entry_type}]"


class StockEntryItem(models.Model):
    stock_entry = models.ForeignKey(
        StockEntry, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="stock_entry_items"
    )
    source_warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="source_stock_items",
    )
    target_warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="target_stock_items",
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    valuation_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    # Batch tracking. Outward legs: ``batch`` is required for batch items. Inward
    # legs: pick an existing ``batch`` OR fill the new-batch fields below and the
    # batch is created at submit (and written back to ``batch``).
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_entry_items",
    )
    supplier_batch_no = models.CharField(max_length=100, blank=True, default="")
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True)
                | models.Q(manufacturing_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("manufacturing_date")),
                name="check_stockentryitem_expiry_after_manufacturing",
            )
        ]

    def __str__(self):
        return f"{self.stock_entry.name} - {self.item.code} ({self.qty})"
