"""Crosswalk between the NGU online store's catalogue and ERP items.

Catalogue only: no NGU orders, invoices, payments or stock are imported. The
sellable unit in NGU is ``products_productvariant``; the NGU variant ``id`` is the
identity key (SKUs are blank and names differ from the ERP/Tally names, so the
link to ``stock.Item`` is made by a person). The importer refreshes the NGU-side
columns and never creates, changes or un-links an Item.
"""

from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel
from stock.models import Item


class NguEntryKind(models.TextChoices):
    VARIANT = "variant", "Variant"
    COMBO = "combo", "Combo"


class NguLinkMethod(models.TextChoices):
    NONE = "", "Not linked"
    EXACT_NAME = "exact_name", "Exact name match (importer)"
    MANUAL = "manual", "Manual"


class NguCatalogEntry(TimeStampedModel):
    kind = models.CharField(max_length=10, choices=NguEntryKind.choices)
    #: ``products_productvariant.id`` (or ``products_productcombo.id`` for a combo).
    ngu_id = models.PositiveBigIntegerField()
    ngu_product_id = models.PositiveIntegerField(null=True, blank=True)
    ngu_category_id = models.PositiveBigIntegerField(null=True, blank=True)
    category_name = models.CharField(max_length=200, blank=True, default="")
    product_name = models.CharField(max_length=300)
    weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True, default="")
    sku = models.CharField(max_length=64, blank=True, default="")
    price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    is_active = models.BooleanField(default=True)
    #: Combos only: ``[{"variant_id": .., "quantity": ..}]``.
    components = models.JSONField(default=list, blank=True)
    item = models.ForeignKey(
        Item,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ngu_entries",
    )
    link_method = models.CharField(
        max_length=12, choices=NguLinkMethod.choices, blank=True, default=""
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "NGU catalogue entries"
        ordering = ["kind", "ngu_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "ngu_id"], name="ngu_entry_unique_kind_ngu_id"
            ),
            # One variant maps to one Item, and an Item has at most one variant.
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(item__isnull=False),
                name="ngu_entry_unique_item",
            ),
            # Combos are bundles, not sellable units: they carry no Item link.
            models.CheckConstraint(
                check=Q(kind="variant") | Q(item__isnull=True),
                name="ngu_entry_combo_has_no_item",
            ),
            models.CheckConstraint(
                check=Q(price__gte=0), name="ngu_entry_price_non_negative"
            ),
            models.CheckConstraint(
                check=Q(item__isnull=False) | Q(link_method=""),
                name="ngu_entry_method_needs_item",
            ),
        ]

    def __str__(self):
        return f"{self.kind} {self.ngu_id}: {self.product_name}"
