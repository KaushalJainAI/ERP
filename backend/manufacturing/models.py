"""BOM (a master) and Work Order (a submittable document).

See ``docs/manufacturing_design.md`` sections 2.1 and 3.1.  The BOM is
deliberately **not** a :class:`core.models.SubmittableDocument`: costs are
derived on read and the usage lock (``services.boms``) protects a recipe that a
submitted Work Order or Stock Entry already references.

The derived Work Order progress columns (``produced_qty``,
``process_loss_qty``, ``status`` and the per-row transferred/consumed
quantities) are written with ``QuerySet.update()`` by
``services.work_orders.refresh_progress`` because
``SubmittableDocument.save()`` refuses non-draft updates.
"""

from decimal import Decimal

from django.db import models, transaction

from core.models import DocStatus, SubmittableDocument, TimeStampedModel


class RateBasis(models.TextChoices):
    VALUATION_RATE = "Valuation Rate", "Valuation Rate"
    STANDARD_RATE = "Standard Rate", "Standard Rate"


class BOM(TimeStampedModel):
    """Bill of Materials: the recipe for one output item, per company.

    ``quantity`` is the input-equivalent basis: the rows produce ``quantity``
    units of work of which ``process_loss_percentage`` is expected to evaporate,
    so good output is ``quantity × (1 − loss/100)``.
    """

    name = models.CharField(max_length=60, unique=True)
    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="boms"
    )
    item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="boms"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    process_loss_percentage = models.DecimalField(
        max_digits=7, decimal_places=3, default=Decimal("0.000")
    )
    rate_basis = models.CharField(
        max_length=20, choices=RateBasis.choices, default=RateBasis.VALUATION_RATE
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    description = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "BOM"
        verbose_name_plural = "BOMs"
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name="check_bom_quantity_positive"
            ),
            models.CheckConstraint(
                check=models.Q(process_loss_percentage__gte=0)
                & models.Q(process_loss_percentage__lt=100),
                name="check_bom_process_loss_percentage",
            ),
            models.CheckConstraint(
                check=models.Q(is_default=False) | models.Q(is_active=True),
                name="check_bom_default_is_active",
            ),
            models.UniqueConstraint(
                fields=["company", "item"],
                condition=models.Q(is_default=True),
                name="unique_default_bom_per_company_item",
            ),
        ]

    def __str__(self):
        return self.name


class BOMItem(models.Model):
    """One raw material / component consumed per ``BOM.quantity``."""

    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="items")
    idx = models.PositiveIntegerField(default=0)
    item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="bom_items"
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    # Optional sub-assembly: this row is made by another BOM (multi-level rollup).
    bom_no = models.ForeignKey(
        BOM,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["idx", "pk"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(qty__gt=0), name="check_bomitem_qty_positive"
            ),
            models.UniqueConstraint(
                fields=["bom", "item"], name="unique_bom_item_per_item"
            ),
        ]

    def __str__(self):
        return f"{self.bom.name} - {self.item.code} ({self.qty})"


class BOMScrapItem(models.Model):
    """A by-product / scrap item actually stocked, valued per unit."""

    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="scrap_items")
    idx = models.PositiveIntegerField(default=0)
    item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="bom_scrap_items"
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )

    class Meta:
        ordering = ["idx", "pk"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(qty__gt=0) & models.Q(rate__gte=0),
                name="check_bomscrapitem_qty_rate",
            ),
            models.UniqueConstraint(
                fields=["bom", "item"], name="unique_bom_scrap_item_per_item"
            ),
        ]

    def __str__(self):
        return f"{self.bom.name} scrap - {self.item.code} ({self.qty})"


class BatchStage(models.TextChoices):
    GRINDING = "Grinding", "Grinding"
    PACKING = "Packing", "Packing"
    READY = "Ready", "Ready"


class WorkOrderStatus(models.TextChoices):
    DRAFT = "Draft", "Draft"
    NOT_STARTED = "Not Started", "Not Started"
    IN_PROCESS = "In Process", "In Process"
    COMPLETED = "Completed", "Completed"
    STOPPED = "Stopped", "Stopped"
    CANCELLED = "Cancelled", "Cancelled"


class WorkOrder(SubmittableDocument):
    """A production order for one BOM (single-level; multi-level is chained WOs)."""

    company = models.ForeignKey(
        "core.Company", on_delete=models.PROTECT, related_name="work_orders"
    )
    production_item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="work_orders"
    )
    bom = models.ForeignKey(BOM, on_delete=models.PROTECT, related_name="work_orders")
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    source_warehouse = models.ForeignKey(
        "stock.Warehouse",
        on_delete=models.PROTECT,
        related_name="source_work_orders",
    )
    wip_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wip_work_orders",
    )
    fg_warehouse = models.ForeignKey(
        "stock.Warehouse",
        on_delete=models.PROTECT,
        related_name="fg_work_orders",
    )
    scrap_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="scrap_work_orders",
    )
    skip_transfer = models.BooleanField(default=False)
    planned_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    # A fact set by the Stop / Resume actions (stored, audited).
    stopped = models.BooleanField(default=False)

    # Derived projection. Rebuilt from submitted Stock Entries by
    # ``services.work_orders.refresh_progress`` / ``repost_work_orders``.
    produced_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    process_loss_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    status = models.CharField(
        max_length=20,
        choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.DRAFT,
    )
    stage = models.CharField(
        max_length=20,
        choices=BatchStage.choices,
        default=BatchStage.GRINDING,
    )

    class Meta:
        verbose_name = "Work Order"
        constraints = [
            models.CheckConstraint(
                check=models.Q(qty__gt=0), name="check_workorder_qty_positive"
            ),
            models.CheckConstraint(
                check=models.Q(produced_qty__gte=0)
                & models.Q(process_loss_qty__gte=0)
                & models.Q(produced_qty__lte=models.F("qty")),
                name="check_workorder_progress_bounds",
            ),
            models.CheckConstraint(
                check=~models.Q(stage=BatchStage.READY) | models.Q(produced_qty__gt=0),
                name="check_workorder_ready_requires_produced_qty",
            ),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        is_lifecycle_write = update_fields is not None
        if self.docstatus == DocStatus.DRAFT and not is_lifecycle_write:
            self.status = WorkOrderStatus.DRAFT
            self.stopped = False
        with transaction.atomic():
            super().save(*args, **kwargs)
            # Draft required items are server-generated and read-only. A draft
            # cannot have submitted entries, so regenerating on every draft
            # save never destroys real progress.
            if self.docstatus == DocStatus.DRAFT and not is_lifecycle_write:
                from manufacturing.services.work_orders import generate_required_items

                generate_required_items(self)

    def cancel(self, user=None):
        """Refuse cancellation while submitted Stock Entries reference this WO."""
        from manufacturing.services.work_orders import cancel_guard

        with transaction.atomic():
            cancel_guard(self)
            super().cancel(user=user)
        self.refresh_from_db()
        return None

    def on_submit(self):
        from manufacturing.services.work_orders import refresh_progress

        refresh_progress(self)

    def validate_before_submit(self):
        from manufacturing.services.work_orders import validate_work_order

        validate_work_order(self)

    def on_cancel(self):
        from manufacturing.services.work_orders import refresh_progress

        refresh_progress(self)

    def amend(self, new_name=None):
        amendment = super().amend(new_name)
        # A cancelled WO has no live progress (cancel is refused while entries
        # exist); reset the stop fact and let save() regenerate required items.
        amendment.stopped = False
        amendment.status = WorkOrderStatus.DRAFT
        amendment.save()
        return amendment

    def __str__(self):
        return f"{self.name} [{self.status}]"


class WorkOrderItem(models.Model):
    """A required raw material, generated from the BOM on draft save."""

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name="required_items"
    )
    idx = models.PositiveIntegerField(default=0)
    item = models.ForeignKey(
        "stock.Item", on_delete=models.PROTECT, related_name="work_order_items"
    )
    # Row override; the header ``WorkOrder.source_warehouse`` is the default.
    source_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_order_items",
    )
    required_qty = models.DecimalField(max_digits=18, decimal_places=3)
    # Derived projection, written by refresh_progress.
    transferred_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )
    consumed_qty = models.DecimalField(
        max_digits=18, decimal_places=3, default=Decimal("0.000")
    )

    class Meta:
        ordering = ["idx", "pk"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(required_qty__gt=0)
                & models.Q(transferred_qty__gte=0)
                & models.Q(consumed_qty__gte=0),
                name="check_workorderitem_qty_bounds",
            ),
            models.UniqueConstraint(
                fields=["work_order", "item"], name="unique_workorder_item"
            ),
        ]

    def __str__(self):
        return f"{self.work_order.name} - {self.item.code} ({self.required_qty})"
