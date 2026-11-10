"""Defaults for an item saved with only a name, a unit, a GST % and a price.

Mirrors what ``core.management.commands.seed_defaults`` seeds: the "All Item
Groups" root, the UOMs and the ``GST <rate>%`` item tax templates. Nothing here
stores a rate of its own; the item tax template stays the single source.
"""

from decimal import Decimal

from django.db.models import Count

from accounts.models import ItemTaxTemplate
from core.models import NamingSeries
from gst.constants import GSTComponent, GSTTreatment
from stock.models import UOM, Item, ItemGroup

ITEM_CODE_PREFIX = "ITM-"
ROOT_ITEM_GROUP = "All Item Groups"
DEFAULT_ITEM_GROUP = "General"
DEFAULT_UOM = "Nos"

#: GST % values an item can be saved with (the seeded ``GST <rate>%`` slabs).
GST_RATE_CHOICES = ("0", "5", "12", "18", "28")


def next_item_code():
    """Next ``ITM-`` code, skipping any code a person already typed in."""
    while True:
        code = NamingSeries.next(ITEM_CODE_PREFIX)
        if not Item.objects.filter(code=code).exists():
            return code


def default_item_group():
    """The leaf "General" group under the root (created if the seed has not run)."""
    root = ItemGroup.objects.filter(name=ROOT_ITEM_GROUP, is_group=True).first()
    if root is None:
        root = ItemGroup.objects.create(name=ROOT_ITEM_GROUP, is_group=True)
    group, _created = ItemGroup.objects.get_or_create(
        name=DEFAULT_ITEM_GROUP, parent=root, defaults={"is_group": False}
    )
    return group


def default_stock_uom():
    """The unit used by the most existing items, else "Nos"."""
    most_used = (
        Item.objects.order_by()
        .values("stock_uom")
        .annotate(n=Count("pk"))
        .order_by("-n", "stock_uom")
        .first()
    )
    if most_used is not None:
        return UOM.objects.get(pk=most_used["stock_uom"])
    uom, _created = UOM.objects.get_or_create(name=DEFAULT_UOM)
    return uom


def item_tax_template_for_rate(rate: Decimal):
    """The active taxable template whose full (IGST) rate is ``rate``, or None.

    A legacy slab (12% / 28%) is used only when no current one matches.
    """
    return (
        ItemTaxTemplate.objects.filter(
            is_active=True,
            gst_treatment=GSTTreatment.TAXABLE,
            rows__gst_component=GSTComponent.IGST,
            rows__tax_rate=rate,
        )
        .order_by("is_legacy", "pk")
        .first()
    )
