"""BOM cross-row validation, the usage lock and the default-BOM rule.

Cross-row invariants cannot be DB CHECKs: they compare rows and masters.  They
are asserted here, in one place, from the serializer (so the API returns 400)
and from :meth:`manufacturing.models.BOM.save` through the serializer's
transaction.  The usage lock is a serializer-level 409 because a BOM that a
submitted Work Order already snapshotted must not change.
"""

from typing import Iterable, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import APIException

from manufacturing.models import BOM, BOMItem


class BOMUsageLocked(APIException):
    """409 when a submitted document already references the BOM."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = _("This BOM is used by a submitted document.")
    default_code = "bom_usage_locked"


def _item_pk(row):
    item = row.get("item")
    return getattr(item, "pk", item)


def _bom_pk(row):
    bom = row.get("bom_no")
    return getattr(bom, "pk", bom)


def validate_bom_rows(bom: BOM, rows: Iterable[dict], scrap_rows: Iterable[dict]):
    """Validate a BOM and the exact child rows that are about to be written.

    ``rows`` / ``scrap_rows`` are serializer ``validated_data`` dicts (``item``
    and ``bom_no`` may be model instances or pks).  Raises Django
    ``ValidationError``; the serializer turns it into a 400.  Called before the
    rows replace the existing set.
    """
    if not bom.item.is_stock_item:
        raise ValidationError(
            _("BOM item '%(code)s' must be a stock item.") % {"code": bom.item.code}
        )

    seen_items = set()
    sub_boms = []
    for row in rows:
        item = row["item"]
        item_pk = _item_pk(row)
        if not item.is_stock_item:
            raise ValidationError(
                _("BOM row item '%(code)s' must be a stock item.") % {"code": item.code}
            )
        if item_pk == bom.item_id:
            raise ValidationError(
                _("The product '%(code)s' cannot also be a BOM component.")
                % {"code": bom.item.code}
            )
        if item_pk in seen_items:
            raise ValidationError(
                _("Item '%(code)s' appears twice in the BOM.") % {"code": item.code}
            )
        seen_items.add(item_pk)

        sub_bom_pk = _bom_pk(row)
        if sub_bom_pk is None:
            continue
        if bom.pk is not None and sub_bom_pk == bom.pk:
            raise ValidationError(
                _("A BOM cannot use itself as a sub-assembly.")
            )
        try:
            sub_bom = row["bom_no"]
            if not isinstance(sub_bom, BOM):
                sub_bom = BOM.objects.get(pk=sub_bom_pk)
        except BOM.DoesNotExist:
            raise ValidationError(_("The sub-assembly BOM does not exist."))
        if sub_bom.item_id != item_pk:
            raise ValidationError(
                _(
                    "Sub-BOM '%(bom)s' makes '%(sub)s', not the row item '%(item)s'."
                )
                % {
                    "bom": sub_bom.name,
                    "sub": sub_bom.item.code,
                    "item": item.code,
                }
            )
        if sub_bom.company_id != bom.company_id:
            raise ValidationError(
                _("Sub-BOM '%(bom)s' belongs to a different company.") % {"bom": sub_bom.name}
            )
        if not sub_bom.is_active:
            raise ValidationError(
                _("Sub-BOM '%(bom)s' is inactive.") % {"bom": sub_bom.name}
            )
        sub_boms.append(sub_bom_pk)

    seen_scrap = set()
    for row in scrap_rows:
        item = row["item"]
        item_pk = _item_pk(row)
        if not item.is_stock_item:
            raise ValidationError(
                _("Scrap item '%(code)s' must be a stock item.") % {"code": item.code}
            )
        if item_pk in seen_scrap:
            raise ValidationError(
                _("Scrap item '%(code)s' appears twice in the BOM.") % {"code": item.code}
            )
        seen_scrap.add(item_pk)

    _assert_acyclic(bom.pk, sub_boms)


def _assert_acyclic(bom_pk: Optional[int], sub_bom_pks: Iterable[int]):
    """Refuse a sub-BOM graph that would loop back to this BOM (or loop at all)."""
    def visit(pk, path):
        if pk in path:
            raise ValidationError(
                _("The sub-BOM graph contains a cycle; a BOM cannot contain itself.")
            )
        path = path | {pk}
        for child in BOMItem.objects.filter(bom_id=pk).exclude(
            bom_no__isnull=True
        ).values_list("bom_no_id", flat=True):
            visit(child, path)

    for sub_pk in sub_bom_pks:
        if bom_pk is not None and sub_pk == bom_pk:
            raise ValidationError(_("A BOM cannot use itself as a sub-assembly."))
        visit(sub_pk, {bom_pk} if bom_pk is not None else set())


def ensure_single_default(bom: BOM):
    """Atomically make ``bom`` the only default for its (company, item)."""
    if not bom.is_default:
        return
    with transaction.atomic():
        BOM.objects.select_for_update().filter(
            company_id=bom.company_id, item_id=bom.item_id, is_default=True
        ).exclude(pk=bom.pk).update(is_default=False)


def clear_other_defaults(company_id, item_id, exclude_pk=None):
    """Unset the current default **before** inserting a new one.

    The partial unique constraint ``(company, item) WHERE is_default`` fires at
    INSERT time, so clearing has to happen first; ``ensure_single_default`` alone
    is too late for a new row.
    """
    qs = BOM.objects.filter(company_id=company_id, item_id=item_id, is_default=True)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    qs.update(is_default=False)


def usage_locked(bom: BOM) -> bool:
    """True once a **submitted** Work Order or Stock Entry references the BOM."""
    from manufacturing.models import WorkOrder
    from stock.models import StockEntry

    return (
        WorkOrder.objects.filter(bom_id=bom.pk, docstatus=1).exists()
        or StockEntry.objects.filter(bom_id=bom.pk, docstatus=1).exists()
        or StockEntry.objects.filter(
            work_order__bom_id=bom.pk, docstatus=1
        ).exists()
    )


def assert_usage_unlocked(bom: BOM):
    """BOM rows, quantity and process loss freeze once a submitted doc uses it."""
    if usage_locked(bom):
        raise BOMUsageLocked(
            detail=_(
                "BOM '%(bom)s' is used by a submitted document; create a new BOM "
                "version instead of changing its quantity, loss or components."
            )
            % {"bom": bom.name}
        )
