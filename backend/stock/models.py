from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel, SubmittableDocument, Company




class ValuationMethod(models.TextChoices):
    FIFO = "FIFO", "FIFO"
    MOVING_AVERAGE = "Moving Average", "Moving Average"


class UOM(TimeStampedModel):
    """Unit of Measure."""
    name = models.CharField(max_length=40, unique=True)

    class Meta:
        verbose_name = "Unit of Measure"
        verbose_name_plural = "Units of Measure"

    def __str__(self):
        return self.name


class ItemGroup(TimeStampedModel):
    """Item Group tree node."""
    name = models.CharField(max_length=200)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.RESTRICT, related_name="children")
    is_group = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Item(TimeStampedModel):
    """Item master record."""
    code = models.CharField(max_length=140, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    item_group = models.ForeignKey(ItemGroup, on_delete=models.PROTECT, related_name="items")
    stock_uom = models.ForeignKey(UOM, on_delete=models.PROTECT, related_name="items")
    is_stock_item = models.BooleanField(default=True)
    valuation_method = models.CharField(
        max_length=15,
        choices=ValuationMethod.choices,
        default=ValuationMethod.FIFO,
    )
    standard_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0.000000"))
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Warehouse(TimeStampedModel):
    """Warehouse tree node."""
    name = models.CharField(max_length=200)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="warehouses")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.RESTRICT, related_name="children")
    is_group = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_warehouse_per_company")
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class StockLedgerEntry(models.Model):
    """
    Immutable Stock Ledger Entry (append-only fact).
    Never updated or deleted directly over API or SQL.
    """
    posting_date = models.DateField(db_index=True)
    posting_time = models.TimeField(db_index=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sle_entries")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="sle_entries")
    qty = models.DecimalField(max_digits=18, decimal_places=3)  # Signed: +in / -out
    valuation_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0.000000"))
    stock_value_difference = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    voucher_type = models.CharField(max_length=60)
    voucher_no = models.CharField(max_length=60)
    is_cancelled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["item", "warehouse", "posting_date", "posting_time"]),
            models.Index(fields=["voucher_type", "voucher_no"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_allow_internal_cancellation", False):
            raise ValidationError("StockLedgerEntry rows are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("StockLedgerEntry rows are immutable and cannot be deleted.")

    def __str__(self):
        return f"{self.posting_date} {self.posting_time} - {self.item.code} @ {self.warehouse.name}: Qty {self.qty}"


class Bin(TimeStampedModel):
    """
    Derived stock balance projection per item + warehouse.
    Rebuildable at any time from StockLedgerEntry replay.
    """
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="bins")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="bins")
    actual_qty = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0.000"))
    valuation_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0.000000"))
    stock_value = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "warehouse"], name="unique_bin_per_item_warehouse")
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
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="stock_entries")

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Stock Entry must have at least one item.")
        for item in items:
            if self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT and not item.target_warehouse:
                raise ValidationError("Material Receipt requires a Target Warehouse.")
            elif self.stock_entry_type == StockEntryType.MATERIAL_ISSUE and not item.source_warehouse:
                raise ValidationError("Material Issue requires a Source Warehouse.")
            elif self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER and (not item.source_warehouse or not item.target_warehouse):
                raise ValidationError("Material Transfer requires both Source and Target Warehouses.")

    def on_submit(self):
        from stock.services import ledger as stock_service
        sle_list = []
        for item in self.items.all():
            if self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT:
                sle_list.append({
                    "item": item.item,
                    "warehouse": item.target_warehouse,
                    "qty": item.qty,
                    "valuation_rate": item.valuation_rate,
                })
            elif self.stock_entry_type == StockEntryType.MATERIAL_ISSUE:
                sle_list.append({
                    "item": item.item,
                    "warehouse": item.source_warehouse,
                    "qty": -abs(item.qty),
                    "valuation_rate": item.valuation_rate,
                })
            elif self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER:
                sle_list.append({
                    "item": item.item,
                    "warehouse": item.source_warehouse,
                    "qty": -abs(item.qty),
                    "valuation_rate": item.valuation_rate,
                })
                sle_list.append({
                    "item": item.item,
                    "warehouse": item.target_warehouse,
                    "qty": abs(item.qty),
                    "valuation_rate": item.valuation_rate,
                    # Value the receipt at the rate the source warehouse released,
                    # so an internal transfer conserves total stock value.
                    "use_outgoing_rate": True,
                })
        stock_service.post(self, sle_list, posting_date=self.posting_date, posting_time=self.posting_time)

    def on_cancel(self):
        from stock.services import ledger as stock_service
        stock_service.reverse(self)

    def __str__(self):
        return f"{self.name} [{self.stock_entry_type}]"


class StockEntryItem(models.Model):
    stock_entry = models.ForeignKey(StockEntry, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_entry_items")
    source_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="source_stock_items")
    target_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="target_stock_items")
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    valuation_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0.000000"))

    def __str__(self):
        return f"{self.stock_entry.name} - {self.item.code} ({self.qty})"

