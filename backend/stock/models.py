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
    critical_stock = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )

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

    # Variants (ERPNext Item Variants). A *template* (``has_variants``) is only a
    # grouping master: it is never stocked, sold, bought or batched. Each *variant*
    # (``variant_of`` -> template) is an ordinary stock item of its own whose
    # attribute values are the ``ItemVariantAttribute`` rows. ``variant_signature``
    # is server-derived ("attr:value|attr:value", ids sorted) and makes the attribute
    # combination UNIQUE per template at the database level.
    has_variants = models.BooleanField(default=False)
    variant_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="variants",
    )
    variant_signature = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(is_nil_exempt=True) & models.Q(is_non_gst=True)),
                name="check_item_not_both_nil_exempt_and_non_gst",
            ),
            models.CheckConstraint(
                check=models.Q(has_variants=False) | models.Q(is_stock_item=False),
                name="check_item_template_not_stock_item",
            ),
            models.CheckConstraint(
                check=models.Q(has_variants=False) | models.Q(variant_of__isnull=True),
                name="check_item_template_not_variant",
            ),
            models.CheckConstraint(
                check=models.Q(variant_of__isnull=False)
                | models.Q(variant_signature=""),
                name="check_item_signature_only_on_variant",
            ),
            models.UniqueConstraint(
                fields=["variant_of", "variant_signature"],
                condition=models.Q(variant_of__isnull=False)
                & ~models.Q(variant_signature=""),
                name="unique_item_variant_combination",
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
            models.CheckConstraint(
                check=models.Q(critical_stock__gte=0),
                name="check_item_critical_stock_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(standard_rate__gte=0),
                name="check_item_standard_rate_non_negative",
            ),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        touches_flags = update_fields is None or bool(
            {"has_batch_no", "has_expiry_date"} & set(update_fields)
        )
        if (
            self.has_variants
            and self.pk
            and StockLedgerEntry.objects.filter(item_id=self.pk).exists()
        ):
            raise ValidationError(
                _(
                    "Item '%(code)s' already has stock ledger entries and cannot "
                    "become a template."
                )
                % {"code": self.code}
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


class ItemAttribute(TimeStampedModel):
    """Item Attribute master (ERPNext): a named dimension of variation such as
    "Pack Size" or "Blend". Its allowed values are ``ItemAttributeValue`` rows.
    (Numeric-range attributes are not built; see erpnext_divergences.md.)"""

    name = models.CharField(max_length=140, unique=True)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class ItemAttributeValue(TimeStampedModel):
    """One allowed value of an attribute; ``abbr`` is what is appended to the
    template code to form a variant code ("500g" -> "500")."""

    attribute = models.ForeignKey(
        ItemAttribute, on_delete=models.CASCADE, related_name="values"
    )
    value = models.CharField(max_length=140)
    abbr = models.CharField(max_length=40)

    class Meta:
        ordering = ("attribute", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "value"], name="unique_attribute_value"
            ),
            models.UniqueConstraint(
                fields=["attribute", "abbr"], name="unique_attribute_abbr"
            ),
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class ItemVariantAttribute(models.Model):
    """ERPNext "Item Variant Attribute" child table.

    On a template the row declares an attribute the variants vary by
    (``value`` is NULL); on a variant it carries the chosen value. Protected
    FKs mean an attribute / value in use can never be deleted.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="attributes")
    attribute = models.ForeignKey(
        ItemAttribute, on_delete=models.PROTECT, related_name="item_rows"
    )
    value = models.ForeignKey(
        ItemAttributeValue,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="item_rows",
    )

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=["item", "attribute"], name="unique_item_attribute"
            )
        ]

    def save(self, *args, **kwargs):
        if self.value_id and self.value.attribute_id != self.attribute_id:
            raise ValidationError(
                _("Value '%(value)s' does not belong to attribute '%(attribute)s'.")
                % {"value": self.value.value, "attribute": self.attribute.name}
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item.code}: {self.attribute.name}={self.value.value if self.value_id else ''}"


class Warehouse(TimeStampedModel):
    """Warehouse tree node.

    ``account`` optionally puts this warehouse on its own balance-sheet account
    (design §14.4: Work In Progress is its own line). ``None`` means the
    company's ``default_inventory_account``. Stock moves between warehouses whose
    effective accounts differ therefore post GL (the account-neutral case posts
    nothing).
    """

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
    account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_warehouse_per_company"
            )
        ]

    def save(self, *args, **kwargs):
        if self.account_id:
            account = self.account
            if account.company_id != self.company_id:
                raise ValidationError(
                    _("Warehouse account '%(account)s' belongs to a different company.")
                    % {"account": account.name}
                )
            if account.is_group:
                raise ValidationError(
                    _("Warehouse account '%(account)s' must not be a group account.")
                    % {"account": account.name}
                )
            if account.account_type != "Stock":
                raise ValidationError(
                    _("Warehouse account '%(account)s' must be a Stock account.")
                    % {"account": account.name}
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class PriceList(TimeStampedModel):
    """A named set of item prices for selling and/or buying."""

    name = models.CharField(max_length=140, unique=True)
    # v1 has no currency conversion: a list must match the company currency.
    currency = models.CharField(max_length=3, default="INR")
    selling = models.BooleanField(default=False)
    buying = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(selling=True) | models.Q(buying=True),
                name="check_price_list_has_direction",
            )
        ]
        ordering = ("name",)

    def __str__(self):
        return self.name


class ItemPrice(TimeStampedModel):
    """One price of an item on a price list, optionally UOM- and date-bounded."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="prices")
    price_list = models.ForeignKey(
        PriceList, on_delete=models.PROTECT, related_name="prices"
    )
    # NULL means the item's stock UOM.
    uom = models.ForeignKey(
        UOM, null=True, blank=True, on_delete=models.PROTECT, related_name="item_prices"
    )
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    valid_from = models.DateField(null=True, blank=True)
    valid_upto = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(rate__gte=0), name="check_item_price_rate_non_negative"
            ),
            models.CheckConstraint(
                check=models.Q(valid_upto__isnull=True)
                | models.Q(valid_from__isnull=True)
                | models.Q(valid_upto__gte=models.F("valid_from")),
                name="check_item_price_dates",
            ),
            models.UniqueConstraint(
                fields=["item", "price_list", "valid_from"],
                condition=models.Q(uom__isnull=True, valid_from__isnull=False),
                name="unique_item_price_from_date",
            ),
            models.UniqueConstraint(
                fields=["item", "price_list"],
                condition=models.Q(uom__isnull=True, valid_from__isnull=True),
                name="unique_item_price_open_ended",
            ),
        ]
        ordering = ("item", "price_list", "valid_from", "pk")

    def __str__(self):
        return f"{self.item.code} @ {self.price_list.name}: {self.rate}"


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
        if self.item.has_variants:
            raise ValidationError(
                _(
                    "Item '%(code)s' is a template with variants: create the batch "
                    "for one of its variants."
                )
                % {"code": self.item.code}
            )
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
                _("StockLedgerEntry rows are immutable and cannot be updated.")
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("StockLedgerEntry rows are immutable and cannot be deleted.")
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
            ),
            models.CheckConstraint(
                check=models.Q(actual_qty__gte=0),
                name="check_bin_actual_qty_non_negative",
            ),
        ]

    def __str__(self):
        return f"Bin {self.item.code} @ {self.warehouse.name}: Qty {self.actual_qty}"


class StockEntryType(models.TextChoices):
    MATERIAL_RECEIPT = "Material Receipt", "Material Receipt"
    MATERIAL_ISSUE = "Material Issue", "Material Issue"
    MATERIAL_TRANSFER = "Material Transfer", "Material Transfer"
    MATERIAL_TRANSFER_FOR_MANUFACTURE = (
        "Material Transfer for Manufacture",
        "Material Transfer for Manufacture",
    )
    MANUFACTURE = "Manufacture", "Manufacture"
    REPACK = "Repack", "Repack"


#: Entry types whose posting is owned by ``manufacturing.services.production``.
MANUFACTURING_ENTRY_TYPES = (
    StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE,
    StockEntryType.MANUFACTURE,
    StockEntryType.REPACK,
)


class StockEntry(SubmittableDocument):
    stock_entry_type = models.CharField(max_length=40, choices=StockEntryType.choices)
    posting_date = models.DateField()
    posting_time = models.TimeField(null=True, blank=True)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="stock_entries"
    )
    # Manufacturing. ``fg_completed_qty`` is the good output completed by this
    # entry (design §14.2 plans by desired output, not by input); it is
    # server-filled from the finished row when it is left at zero.
    # ``process_loss_qty`` is server-owned: the gross input equivalent consumed
    # minus the good output.
    work_order = models.ForeignKey(
        "manufacturing.WorkOrder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_entries",
    )
    bom = models.ForeignKey(
        "manufacturing.BOM",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    fg_completed_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    process_loss_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(fg_completed_qty__gte=0)
                & models.Q(process_loss_qty__gte=0),
                name="check_stockentry_manufacturing_qty_bounds",
            )
        ]

    def validate_before_submit(self):
        items = list(
            self.items.select_related(
                "item", "batch", "source_warehouse", "target_warehouse"
            )
        )
        if not items:
            raise ValidationError(_("Stock Entry must have at least one item."))
        self.company.get_perpetual_accounts(
            need_adjustment=self.stock_entry_type
            in (StockEntryType.MATERIAL_RECEIPT, StockEntryType.MATERIAL_ISSUE)
        )  # raises if perpetual is on but misconfigured
        if self.stock_entry_type in MANUFACTURING_ENTRY_TYPES:
            from manufacturing.services import production

            production.validate_entry(self, items)
            return
        for item in items:
            if (
                self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT
                and not item.target_warehouse
            ):
                raise ValidationError(
                    _("Material Receipt requires a Target Warehouse.")
                )
            elif (
                self.stock_entry_type == StockEntryType.MATERIAL_ISSUE
                and not item.source_warehouse
            ):
                raise ValidationError(_("Material Issue requires a Source Warehouse."))
            elif self.stock_entry_type == StockEntryType.MATERIAL_TRANSFER and (
                not item.source_warehouse or not item.target_warehouse
            ):
                raise ValidationError(
                    _("Material Transfer requires both Source and Target Warehouses.")
                )
            self._validate_batch_line(item)

    def _is_inward_line(self, line) -> bool:
        """True when this row brings stock in (so a batch may be created)."""
        if self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT:
            return True
        if self.stock_entry_type in (
            StockEntryType.MANUFACTURE,
            StockEntryType.REPACK,
        ):
            return bool(line.is_finished_item or line.is_scrap_item)
        return False

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
        if (
            self.stock_entry_type == StockEntryType.MATERIAL_RECEIPT
            or self._is_inward_line(line)
        ):
            # A new batch will be created at submit: refuse now if it cannot be
            # given an expiry date.
            batch_service.resolve_dates(
                item,
                line.manufacturing_date,
                line.expiry_date,
                default_manufacturing_date=self.posting_date,
            )
            return
        # An outward line of a batch-tracked item may leave the batch blank: the
        # ledger service then picks the oldest manufactured batches first (FIFO).
        return

    def on_submit(self):
        from manufacturing.services import production

        if self.stock_entry_type in (
            StockEntryType.MANUFACTURE,
            StockEntryType.REPACK,
        ):
            sles = production.post_manufacture(self)
        elif self.stock_entry_type == (
            StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE
        ):
            sles = production.post_transfer_for_manufacture(self)
        else:
            sles = self._post_regular()
        self._post_inventory_gl(sles)

    def _post_regular(self):
        """Material Receipt / Issue / Transfer posting (unchanged engine)."""
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
                        # A blank batch was FIFO-picked on the way out: carry the
                        # same batch(es) into the target warehouse.
                        "mirror_outgoing": True,
                    }
                )
        return stock_service.post(
            self,
            sle_list,
            posting_date=self.posting_date,
            posting_time=self.posting_time,
        )

    def _post_inventory_gl(self, sles):
        """Post the value each warehouse account gained or lost (design §14.4).

        Every leg contributes its ``stock_value_difference`` to its warehouse's
        effective account (the warehouse's own ``account`` or the company
        inventory account). Accounts whose net is zero post nothing, so a transfer
        between warehouses that share the inventory account stays GL-neutral and a
        manufacturing voucher posts ``Dr Finished Goods / Cr Work In Progress``.
        A non-zero net (a receipt or an issue) is balanced against the stock
        adjustment account.
        """
        from accounts.services import gl as gl_service

        perpetual = self.company.get_perpetual_accounts(
            need_adjustment=self.stock_entry_type
            in (StockEntryType.MATERIAL_RECEIPT, StockEntryType.MATERIAL_ISSUE)
        )
        if not perpetual:
            return
        inventory = perpetual["default_inventory_account"]
        balances = {}
        for sle in sles:
            account = sle.warehouse.account or inventory
            balances[account] = balances.get(account, Decimal("0.00")) + (
                sle.stock_value_difference
            )

        zero = Decimal("0.00")
        gl_entries = []
        for account, value in balances.items():
            value = value.quantize(Decimal("0.01"))
            if value > 0:
                gl_entries.append({"account": account, "debit": value, "credit": zero})
            elif value < 0:
                gl_entries.append({"account": account, "debit": zero, "credit": -value})

        net = sum(balances.values(), Decimal("0.00")).quantize(Decimal("0.01"))
        if net:
            adjustment = perpetual["stock_adjustment_account"]
            if net > 0:
                gl_entries.append({"account": adjustment, "debit": zero, "credit": net})
            else:
                gl_entries.append(
                    {"account": adjustment, "debit": -net, "credit": zero}
                )
        if gl_entries:
            gl_service.post(self, gl_entries, posting_date=self.posting_date)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from manufacturing.services import production
        from stock.services import ledger as stock_service

        stock_service.reverse(self)
        gl_service.reverse(
            self
        )  # no-op when the entry posted no GL (transfer / perpetual off)
        if self.work_order_id:
            production.refresh_bound_work_order(self)

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

    # Manufacturing (design §3.2): one finished row per Manufacture/Repack, any
    # number of consumption rows, optional scrap rows. ``cost_allocation_per`` is
    # used only by Repack with several finished rows.
    is_finished_item = models.BooleanField(default=False)
    is_scrap_item = models.BooleanField(default=False)
    cost_allocation_per = models.DecimalField(
        max_digits=7, decimal_places=3, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True)
                | models.Q(manufacturing_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("manufacturing_date")),
                name="check_stockentryitem_expiry_after_manufacturing",
            ),
            models.CheckConstraint(
                check=~(models.Q(is_finished_item=True) & models.Q(is_scrap_item=True)),
                name="check_stockentryitem_finished_xor_scrap",
            ),
            models.CheckConstraint(
                check=models.Q(cost_allocation_per__isnull=True)
                | (
                    models.Q(cost_allocation_per__gte=0)
                    & models.Q(cost_allocation_per__lte=100)
                ),
                name="check_stockentryitem_cost_allocation_per",
            ),
            models.CheckConstraint(
                check=models.Q(qty__gt=0),
                name="check_stockentryitem_qty_positive",
            ),
            models.CheckConstraint(
                check=models.Q(valuation_rate__gte=0),
                name="check_stockentryitem_valuation_rate_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.stock_entry.name} - {self.item.code} ({self.qty})"


class ReconciliationPurpose(models.TextChoices):
    OPENING_STOCK = "Opening Stock", "Opening Stock"
    STOCK_RECONCILIATION = "Stock Reconciliation", "Stock Reconciliation"


class StockReconciliation(SubmittableDocument):
    """Physical count / opening stock: each line states the absolute counted
    quantity and valuation rate of an item in a warehouse (ERPNext "Stock
    Reconciliation"). Submit posts one SLE per changed line that moves the Bin
    to the counted state (``stock.services.ledger.post_reconciliation``) and
    the GL difference against ``expense_account``.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="stock_reconciliations"
    )
    posting_date = models.DateField()
    posting_time = models.TimeField(null=True, blank=True)
    purpose = models.CharField(
        max_length=24,
        choices=ReconciliationPurpose.choices,
        default=ReconciliationPurpose.STOCK_RECONCILIATION,
    )
    # The difference account. Defaults to the company's Stock Adjustment account;
    # an Opening Stock reconciliation needs a balance-sheet (e.g. Temporary
    # Opening) account instead.
    expense_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Derived: sum of the lines' ``amount_difference`` (refreshed on save/submit).
    difference_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(purpose__in=ReconciliationPurpose.values),
                name="check_stockreco_purpose",
            )
        ]

    def validate_before_submit(self):
        from stock.services import reconciliation

        reconciliation.prepare_for_submit(self)

    def on_submit(self):
        from stock.services import reconciliation

        reconciliation.post(self)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

        stock_service.reverse(self)
        gl_service.reverse(self)  # no-op when perpetual inventory is off

    def __str__(self):
        return f"{self.name} [{self.purpose}]"


class StockReconciliationItem(models.Model):
    reconciliation = models.ForeignKey(
        StockReconciliation, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="stock_reconciliation_items"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="stock_reconciliation_items"
    )
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_reconciliation_items",
    )
    # What was counted. Blank = keep the current value (a rate-only correction
    # leaves ``qty`` blank, a quantity-only count leaves the rate blank).
    qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    valuation_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    # Server-derived snapshot of the stock before / after, for display and audit.
    current_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    current_valuation_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    quantity_difference = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    current_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    amount_difference = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.CheckConstraint(
                check=models.Q(qty__isnull=True) | models.Q(qty__gte=0),
                name="check_stockrecoitem_qty_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(valuation_rate__isnull=True)
                | models.Q(valuation_rate__gte=0),
                name="check_stockrecoitem_rate_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(qty__isnull=False)
                | models.Q(valuation_rate__isnull=False),
                name="check_stockrecoitem_qty_or_rate",
            ),
            # No duplicate item+warehouse(+batch) rows; NULL batches are not
            # equal in a plain UNIQUE, so the batch-less case is its own index.
            models.UniqueConstraint(
                fields=["reconciliation", "item", "warehouse", "batch"],
                condition=models.Q(batch__isnull=False),
                name="unique_stockrecoitem_with_batch",
            ),
            models.UniqueConstraint(
                fields=["reconciliation", "item", "warehouse"],
                condition=models.Q(batch__isnull=True),
                name="unique_stockrecoitem_without_batch",
            ),
        ]

    def __str__(self):
        return f"{self.reconciliation.name} - {self.item.code} @ {self.warehouse.name}"
