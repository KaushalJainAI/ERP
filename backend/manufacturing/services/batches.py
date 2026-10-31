"""Production batches: grind a bulk item, then pack it into SKUs (planning doc 03, Q2).

Stock moves only at the posting steps:

* ``start_grinding`` creates and submits the grinding Work Order. It posts nothing.
* ``finish_grinding`` posts one Manufacture with the bulk actually produced. The
  grinding loss is the gap between the BOM's gross input and that output, which
  ``production`` already records as ``process_loss_qty``.
* ``pack`` posts, per SKU, one Manufacture on a packing Work Order. The packing
  loss (bulk used minus bulk packed) is moved to the ``Process Loss`` warehouse
  by a Material Transfer, so In Process matches the physical count. The
  consumption gate is not loosened: each SKU consumes exactly its BOM.
* ``cancel_batch`` cancels every entry of the batch newest first, then its Work
  Orders, which restores stock.

Every function is atomic. A refused step leaves no partial rows.
"""

import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from core.models import DocStatus, NamingSeries
from manufacturing.models import BOM, Batch, BatchStage, BatchStatus, WorkOrder
from manufacturing.services import work_orders as wo_service
from stock.models import (
    Item,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    Warehouse,
)

#: Name of the warehouse that receives packing loss. Data, not interface text.
LOSS_WAREHOUSE_NAME = "Process Loss"


def ensure_loss_warehouse(company) -> Warehouse:
    """Return the company's Process Loss warehouse, creating it when missing."""
    warehouse, _created = Warehouse.objects.get_or_create(
        company=company, name=LOSS_WAREHOUSE_NAME, defaults={"is_group": False}
    )
    return warehouse


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_positive_qty(value, label) -> Decimal:
    if value in (None, ""):
        raise ValidationError(_("%(field)s is required.") % {"field": label})
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(_("%(field)s must be a number.") % {"field": label})
    if qty <= 0:
        raise ValidationError(
            _("%(field)s must be greater than zero.") % {"field": label}
        )
    return wo_service.q3(qty)


def _stage_warehouses(company):
    """(unprocessed, in_process, ready) warehouses set on the company."""
    unprocessed = company.unprocessed_warehouse
    in_process = company.in_process_warehouse
    ready = company.ready_warehouse
    if unprocessed is None or in_process is None or ready is None:
        raise ValidationError(
            _(
                "Set the Unprocessed, In Process and Ready warehouses for the "
                "company first."
            )
        )
    return unprocessed, in_process, ready


def _default_bom(company, item: Item) -> BOM:
    bom = BOM.objects.filter(
        company=company, item=item, is_default=True, is_active=True
    ).first()
    if bom is None:
        raise ValidationError(
            _("Item '%(code)s' has no default BOM.") % {"code": item.code}
        )
    return bom


def _lock_batch(wo: WorkOrder) -> Tuple[Batch, WorkOrder]:
    """Lock the batch and re-read its grinding Work Order."""
    batch = (
        Batch.objects.select_for_update()
        .select_related("company", "bulk_item")
        .filter(grinding_work_order_id=wo.pk)
        .first()
    )
    if batch is None:
        raise ValidationError(_("This Work Order is not a production batch."))
    grinding = WorkOrder.objects.get(pk=wo.pk)
    return batch, grinding


def packing_loss_qty(batch: Batch, grinding: WorkOrder) -> Decimal:
    """Bulk posted to the Process Loss warehouse by this batch's packing."""
    lost = StockEntryItem.objects.filter(
        stock_entry__work_order=grinding,
        stock_entry__docstatus=DocStatus.SUBMITTED,
        stock_entry__stock_entry_type=StockEntryType.MATERIAL_TRANSFER,
        item=batch.bulk_item,
        target_warehouse__company=batch.company,
        target_warehouse__name=LOSS_WAREHOUSE_NAME,
    ).aggregate(total=Sum("qty"))["total"] or Decimal("0")
    return wo_service.q3(lost)


def _bulk_remaining(batch: Batch, grinding: WorkOrder) -> Decimal:
    """Bulk still in the batch: produced, less packed, less moved to loss."""
    packed = StockEntryItem.objects.filter(
        stock_entry__work_order__batch=batch,
        stock_entry__docstatus=DocStatus.SUBMITTED,
        stock_entry__stock_entry_type=StockEntryType.MANUFACTURE,
        item=batch.bulk_item,
        is_finished_item=False,
        is_scrap_item=False,
    ).aggregate(total=Sum("qty"))["total"] or Decimal("0")
    lost = packing_loss_qty(batch, grinding)
    return wo_service.q3(Decimal(grinding.produced_qty) - packed - lost)


# --------------------------------------------------------------------------- #
# Grinding
# --------------------------------------------------------------------------- #


def start_grinding(company, bulk_item: Item, bulk_qty, user=None) -> Batch:
    """Create the batch and its submitted grinding Work Order. Posts no stock."""
    qty = _as_positive_qty(bulk_qty, _("Bulk quantity"))
    unprocessed, in_process, _ready = _stage_warehouses(company)
    bom = _default_bom(company, bulk_item)

    with transaction.atomic():
        wo = WorkOrder.objects.create(
            name=NamingSeries.next("WO-2026-"),
            company=company,
            production_item=bulk_item,
            bom=bom,
            qty=qty,
            source_warehouse=unprocessed,
            fg_warehouse=in_process,
            skip_transfer=True,
            stage=BatchStage.GRINDING,
        )
        wo.submit(user=user)
        return Batch.objects.create(
            name=NamingSeries.next("BATCH-2026-"),
            company=company,
            bulk_item=bulk_item,
            grinding_work_order=wo,
        )


def finish_grinding(wo: WorkOrder, bulk_qty, user=None) -> dict:
    """Post the grinding Manufacture with the bulk actually produced.

    Returns ``{"batch", "grinding_loss", "warnings"}``. The warning is advisory.
    """
    actual = _as_positive_qty(bulk_qty, _("Bulk produced"))
    with transaction.atomic():
        batch, grinding = _lock_batch(wo)
        if batch.status != BatchStatus.GRINDING:
            raise ValidationError(
                _(
                    "Only a batch in Grinding can finish grinding (this batch is %(status)s)."
                )
                % {"status": batch.status}
            )
        planned = Decimal(grinding.qty)
        if actual > planned:
            raise ValidationError(
                _("Bulk produced %(actual)s is more than the %(planned)s planned.")
                % {"actual": actual, "planned": planned}
            )
        entry = wo_service.make_stock_entry(grinding, StockEntryType.MANUFACTURE)
        # The draft carries the planned basis; the posted finished row is the
        # bulk actually produced. The consumption stays the full BOM input.
        StockEntryItem.objects.filter(stock_entry=entry, is_finished_item=True).update(
            qty=actual
        )
        entry.submit(user=user)
        WorkOrder.objects.filter(pk=grinding.pk).update(stage=BatchStage.PACKING)
        Batch.objects.filter(pk=batch.pk).update(status=BatchStatus.PACKING)
        batch.status = BatchStatus.PACKING

        # Advisory only: grinding loss (raw consumed minus bulk produced, as a
        # share of raw consumed) above the grinding BOM's process_loss_percentage.
        # Never blocks posting and never changes a quantity.
        raw_consumed = wo_service.q3(
            StockEntryItem.objects.filter(
                stock_entry=entry, is_finished_item=False, is_scrap_item=False
            ).aggregate(total=Sum("qty"))["total"]
            or Decimal("0")
        )
        allowance_pct = Decimal(grinding.bom.process_loss_percentage or 0)
        loss = raw_consumed - actual
        warnings: List[str] = []
        if raw_consumed > 0 and loss > 0:
            loss_pct = loss * 100 / raw_consumed
            if loss_pct > allowance_pct:
                warnings.append(
                    _(
                        "Grinding loss is %(loss)s%% of the raw consumed, above the %(allowance)s%% allowance."
                    )
                    % {
                        "loss": loss_pct.quantize(Decimal("0.01")),
                        "allowance": allowance_pct.quantize(Decimal("0.01")),
                    }
                )
    return {"batch": batch, "grinding_loss": loss, "warnings": warnings}


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #


def _post_packing_loss(batch: Batch, grinding: WorkOrder, in_process, qty, user):
    """Move the packing loss out of In Process into the Process Loss warehouse."""
    loss_warehouse = ensure_loss_warehouse(batch.company)
    entry = StockEntry.objects.create(
        name=NamingSeries.next("STE-2026-"),
        stock_entry_type=StockEntryType.MATERIAL_TRANSFER,
        posting_date=datetime.date.today(),
        company=batch.company,
        work_order=grinding,
    )
    StockEntryItem.objects.create(
        stock_entry=entry,
        item=batch.bulk_item,
        source_warehouse=in_process,
        target_warehouse=loss_warehouse,
        qty=qty,
        valuation_rate=Decimal("0"),
    )
    entry.submit(user=user)


def _pack_one_sku(batch: Batch, sku: Item, bom: BOM, packets, warehouses, user):
    """One packing Work Order and its Manufacture entry for one SKU."""
    unprocessed, in_process, ready = warehouses
    wo = WorkOrder.objects.create(
        name=NamingSeries.next("WO-2026-"),
        company=batch.company,
        production_item=sku,
        bom=bom,
        qty=packets,
        source_warehouse=in_process,
        fg_warehouse=ready,
        skip_transfer=True,
        stage=BatchStage.PACKING,
        batch=batch,
    )
    # Bulk comes from In Process (the header); packing material comes from
    # Unprocessed.
    for row in wo.required_items.all():
        if row.item_id != batch.bulk_item_id:
            row.source_warehouse = unprocessed
            row.save(update_fields=["source_warehouse"])
    wo.submit(user=user)
    entry = wo_service.make_stock_entry(wo, StockEntryType.MANUFACTURE)
    entry.submit(user=user)
    WorkOrder.objects.filter(pk=wo.pk).update(stage=BatchStage.READY)


def pack(wo: WorkOrder, bulk_used, lines: List[Tuple[Item, object]], user=None) -> dict:
    """Pack bulk into SKUs. ``lines`` is ``[(sku, packets), ...]``.

    Refused, with nothing posted, when the bulk used is more than the batch still
    holds, when the packets need more bulk than was used, or when a SKU has no
    default BOM that uses this batch's bulk item.
    """
    used = _as_positive_qty(bulk_used, _("Bulk used"))
    if not lines:
        raise ValidationError(_("Enter at least one SKU with a packet count."))
    requested = []
    seen = set()
    for sku, packets in lines:
        if sku.pk in seen:
            raise ValidationError(_("%(code)s is listed twice.") % {"code": sku.code})
        seen.add(sku.pk)
        requested.append((sku, _as_positive_qty(packets, _("Packets"))))

    with transaction.atomic():
        batch, grinding = _lock_batch(wo)
        if batch.status != BatchStatus.PACKING:
            raise ValidationError(
                _("Only a batch in Packing can be packed (this batch is %(status)s).")
                % {"status": batch.status}
            )
        warehouses = _stage_warehouses(batch.company)
        _unprocessed, in_process, _ready = warehouses

        plan: List[Tuple[Item, BOM, Decimal, Decimal]] = []
        for sku, packets in requested:
            bom = _default_bom(batch.company, sku)
            row = bom.items.filter(item=batch.bulk_item).first()
            if row is None:
                raise ValidationError(
                    _("The BOM of %(sku)s does not use the bulk item %(bulk)s.")
                    % {"sku": sku.code, "bulk": batch.bulk_item.code}
                )
            good = wo_service._bom_good_output(bom)
            bulk_for_sku = wo_service.q3(Decimal(row.qty) * packets / good)
            plan.append((sku, bom, packets, bulk_for_sku))

        packed_total = sum((p[3] for p in plan), Decimal("0.000"))
        remaining = _bulk_remaining(batch, grinding)
        if used > remaining:
            raise ValidationError(
                _(
                    "Only %(qty)s of %(item)s is left in this batch; %(used)s was entered."
                )
                % {"qty": remaining, "item": batch.bulk_item.code, "used": used}
            )
        loss = wo_service.q3(used - packed_total)
        if loss < 0:
            raise ValidationError(
                _(
                    "These packets need %(need)s of %(item)s, but only %(used)s was entered."
                )
                % {"need": packed_total, "item": batch.bulk_item.code, "used": used}
            )

        # Packing does not warn: loss is measured at grinding (owner decision).
        if loss > 0:
            _post_packing_loss(batch, grinding, in_process, loss, user)
        for sku, bom, packets, _bulk in plan:
            _pack_one_sku(batch, sku, bom, packets, warehouses, user)

        new_status = BatchStatus.DONE if remaining - used == 0 else BatchStatus.PACKING
        Batch.objects.filter(pk=batch.pk).update(status=new_status)
        batch.status = new_status
    return {
        "batch": batch,
        "bulk_used": used,
        "bulk_packed": packed_total,
        "packing_loss": loss,
    }


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #


def cancel_batch(wo: WorkOrder, user=None) -> Batch:
    """Cancel every entry of the batch (newest first), then its Work Orders."""
    with transaction.atomic():
        batch, grinding = _lock_batch(wo)
        if batch.status == BatchStatus.CANCELLED:
            raise ValidationError(_("This batch is already cancelled."))

        packing = list(batch.packing_work_orders.order_by("-pk"))
        work_orders = [grinding.pk] + [p.pk for p in packing]
        entries = StockEntry.objects.filter(
            docstatus=DocStatus.SUBMITTED, work_order_id__in=work_orders
        ).order_by("-pk")
        # Reversing a packing entry drops its output to zero, so the order leaves
        # Ready first (the Ready-needs-output CHECK). Only a cancel goes back.
        for packed_wo in packing:
            WorkOrder.objects.filter(pk=packed_wo.pk).update(stage=BatchStage.PACKING)
        for entry in entries:
            entry.cancel(user=user)
        for packed_wo in packing:
            WorkOrder.objects.get(pk=packed_wo.pk).cancel(user=user)
        WorkOrder.objects.get(pk=grinding.pk).cancel(user=user)

        Batch.objects.filter(pk=batch.pk).update(status=BatchStatus.CANCELLED)
        batch.status = BatchStatus.CANCELLED
    return batch


# --------------------------------------------------------------------------- #
# Loss report (read-only)
# --------------------------------------------------------------------------- #


def _consumed_qty(entries, item=None) -> Decimal:
    """Consumed component quantity of these entries (not finished or scrap rows)."""
    rows = StockEntryItem.objects.filter(
        stock_entry__in=entries, is_finished_item=False, is_scrap_item=False
    )
    if item is not None:
        rows = rows.filter(item=item)
    return Decimal(rows.aggregate(total=Sum("qty"))["total"] or Decimal("0"))


def batch_loss_rows(company_id, from_date=None, to_date=None) -> List[dict]:
    """Expected against actual loss for each batch whose grinding is posted.

    Expected loss is the BOM's ``process_loss_percentage`` applied to what was
    consumed: raw material for grinding (grinding BOM), and bulk for each packing
    entry (that SKU's BOM). Actual loss is what was posted: the grinding entry's
    ``process_loss_qty`` and the packing Material Transfer to Process Loss.

    A batch is dated by its grinding entry. Batches with no posted grinding (not
    started, or cancelled) are left out. Reads only; posts nothing.
    """
    batches = (
        Batch.objects.filter(company_id=company_id)
        .select_related("bulk_item", "grinding_work_order__bom")
        .order_by("created_at", "pk")
    )
    hundred = Decimal("100")
    rows = []
    for batch in batches:
        grinding = batch.grinding_work_order
        entries = StockEntry.objects.filter(
            work_order=grinding,
            docstatus=DocStatus.SUBMITTED,
            stock_entry_type=StockEntryType.MANUFACTURE,
        )
        if from_date:
            entries = entries.filter(posting_date__gte=from_date)
        if to_date:
            entries = entries.filter(posting_date__lte=to_date)
        entry = entries.first()
        if entry is None:
            continue

        grind_pct = Decimal(grinding.bom.process_loss_percentage or 0)
        grind_expected = wo_service.q3(_consumed_qty([entry]) * grind_pct / hundred)
        grind_actual = wo_service.q3(entry.process_loss_qty)

        packing_expected = Decimal("0")
        for packing in batch.packing_work_orders.select_related("bom"):
            pack_pct = Decimal(packing.bom.process_loss_percentage or 0)
            packed_entries = StockEntry.objects.filter(
                work_order=packing,
                docstatus=DocStatus.SUBMITTED,
                stock_entry_type=StockEntryType.MANUFACTURE,
            )
            bulk = _consumed_qty(packed_entries, item=batch.bulk_item)
            packing_expected += bulk * pack_pct / hundred
        packing_expected = wo_service.q3(packing_expected)
        packing_actual = packing_loss_qty(batch, grinding)

        rows.append(
            {
                "batch": batch.name,
                "bulk_item": batch.bulk_item.code,
                "status": batch.status,
                "posting_date": entry.posting_date,
                "grinding_expected_loss_qty": grind_expected,
                "grinding_actual_loss_qty": grind_actual,
                "grinding_variance_qty": wo_service.q3(grind_actual - grind_expected),
                "packing_expected_loss_qty": packing_expected,
                "packing_actual_loss_qty": packing_actual,
                "packing_variance_qty": wo_service.q3(
                    packing_actual - packing_expected
                ),
            }
        )
    return rows
