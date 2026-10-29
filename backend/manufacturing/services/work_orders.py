"""Work Order services: required items, derived progress and stock-entry mapping.

``refresh_progress`` is the only writer of the derived projection columns.  It
uses ``QuerySet.update()`` because ``SubmittableDocument.save()`` refuses an
update of a submitted document (design pitfall 4).
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from core.models import DocStatus, NamingSeries
from manufacturing.models import (
    BOM,
    WorkOrder,
    WorkOrderItem,
    WorkOrderStatus,
)
from stock.models import StockEntry, StockEntryItem, StockEntryType


def q3(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Required items
# --------------------------------------------------------------------------- #


def _bom_good_output(bom: BOM) -> Decimal:
    """Good output of one BOM basis (design §14.2 plans by desired output)."""
    loss = Decimal(bom.process_loss_percentage or 0)
    good = Decimal(bom.quantity) * (Decimal(1) - loss / Decimal(100))
    if good <= 0:
        raise ValidationError(
            _("BOM '%(bom)s' has no positive good output.") % {"bom": bom.name}
        )
    return good


def generate_required_items(wo: WorkOrder):
    """(Re)generate the read-only required-material rows from the BOM.

    ``required_qty = round_half_up(row.qty × wo.qty / good_output, 3dp)`` where
    ``good_output = bom.quantity × (1 − loss/100)``: the Work Order quantity is
    the **desired good output** (design §14.2), so the raw requirement is grossed
    up by the expected process loss.  Runs on every draft save, so a draft always
    matches its BOM and quantity; a submitted WO is frozen.
    """
    if wo.bom_id is None or wo.qty is None:
        return
    bom = wo.bom
    rows = list(bom.items.select_related("item").order_by("idx", "pk"))
    if not rows:
        WorkOrderItem.objects.filter(work_order=wo).delete()
        return

    good_output = _bom_good_output(bom)
    existing = {
        row.item_id: row for row in WorkOrderItem.objects.filter(work_order=wo)
    }
    keep_ids = []
    for position, row in enumerate(rows, start=1):
        required = q3(
            Decimal(row.qty) * Decimal(wo.qty) / good_output
        )
        if required <= 0:
            continue
        current = existing.get(row.item_id)
        if current is None:
            current = WorkOrderItem.objects.create(
                work_order=wo,
                idx=position,
                item=row.item,
                required_qty=required,
            )
        else:
            # A draft has no live progress; reset the projection to zero.
            WorkOrderItem.objects.filter(pk=current.pk).update(
                idx=position,
                required_qty=required,
                transferred_qty=Decimal("0.000"),
                consumed_qty=Decimal("0.000"),
            )
            current.idx = position
            current.required_qty = required
            current.transferred_qty = Decimal("0.000")
            current.consumed_qty = Decimal("0.000")
        keep_ids.append(current.pk)
    WorkOrderItem.objects.filter(work_order=wo).exclude(pk__in=keep_ids).delete()
    wo._prefetched_objects_cache = {}


# --------------------------------------------------------------------------- #
# Submit gate
# --------------------------------------------------------------------------- #


def _validate_warehouse(warehouse, company, field: str):
    if warehouse is None:
        raise ValidationError(_("%(field)s is required.") % {"field": field})
    if warehouse.is_group:
        raise ValidationError(
            _("%(field)s '%(name)s' is a group warehouse.")
            % {"field": field, "name": warehouse.name}
        )
    if warehouse.company_id != company.pk:
        raise ValidationError(
            _("%(field)s '%(name)s' belongs to a different company.")
            % {"field": field, "name": warehouse.name}
        )


def validate_work_order(wo: WorkOrder):
    """The submit gate from design 3.1."""
    if wo.qty is None or wo.qty <= 0:
        raise ValidationError(_("Work Order quantity must be greater than zero."))
    bom = wo.bom
    if not bom.is_active:
        raise ValidationError(_("BOM '%(bom)s' is inactive.") % {"bom": bom.name})
    if bom.company_id != wo.company_id:
        raise ValidationError(
            _("BOM '%(bom)s' belongs to a different company.") % {"bom": bom.name}
        )
    if bom.item_id != wo.production_item_id:
        raise ValidationError(
            _("BOM '%(bom)s' makes a different item than the production item.")
            % {"bom": bom.name}
        )
    if not wo.production_item.is_stock_item:
        raise ValidationError(_("The production item must be a stock item."))

    _validate_warehouse(wo.source_warehouse, wo.company, _("Source Warehouse"))
    _validate_warehouse(wo.fg_warehouse, wo.company, _("Finished Goods Warehouse"))
    if not wo.skip_transfer:
        _validate_warehouse(wo.wip_warehouse, wo.company, _("WIP Warehouse"))
        if wo.wip_warehouse_id == wo.fg_warehouse_id:
            raise ValidationError(
                _("The WIP Warehouse and the Finished Goods Warehouse must differ.")
            )
    if bom.scrap_items.exists():
        _validate_warehouse(wo.scrap_warehouse, wo.company, _("Scrap Warehouse"))

    if not wo.required_items.exists():
        raise ValidationError(
            _("The Work Order has no required items; check the BOM components.")
        )
    if wo.stopped:
        raise ValidationError(_("A stopped Work Order cannot be submitted."))


# --------------------------------------------------------------------------- #
# Progress projection
# --------------------------------------------------------------------------- #


def _sum_by_item(queryset) -> Dict[int, Decimal]:
    return {
        row["item_id"]: q3(row["total"] or 0)
        for row in queryset.values("item_id").annotate(total=Sum("qty"))
    }


def compute_progress(wo: WorkOrder) -> Dict:
    """Rebuild the derived progress from submitted Stock Entries (pure read)."""
    entries = StockEntry.objects.filter(work_order_id=wo.pk, docstatus=DocStatus.SUBMITTED)
    mtfm_lines = StockEntryItem.objects.filter(
        stock_entry__work_order_id=wo.pk,
        stock_entry__docstatus=DocStatus.SUBMITTED,
        stock_entry__stock_entry_type=StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE,
    )
    manufacture_lines = StockEntryItem.objects.filter(
        stock_entry__work_order_id=wo.pk,
        stock_entry__docstatus=DocStatus.SUBMITTED,
        stock_entry__stock_entry_type=StockEntryType.MANUFACTURE,
    )
    transferred = _sum_by_item(mtfm_lines)
    consumed = _sum_by_item(
        manufacture_lines.filter(is_finished_item=False, is_scrap_item=False)
    )
    manufacture_entries = entries.filter(stock_entry_type=StockEntryType.MANUFACTURE)
    produced_qty = q3(
        manufacture_entries.aggregate(total=Sum("fg_completed_qty"))["total"] or 0
    )
    process_loss_qty = q3(
        manufacture_entries.aggregate(total=Sum("process_loss_qty"))["total"] or 0
    )

    if wo.docstatus == DocStatus.DRAFT:
        status = WorkOrderStatus.DRAFT
    elif wo.docstatus == DocStatus.CANCELLED:
        status = WorkOrderStatus.CANCELLED
    elif wo.stopped:
        status = WorkOrderStatus.STOPPED
    elif produced_qty >= q3(wo.qty):
        status = WorkOrderStatus.COMPLETED
    elif produced_qty > 0 or any(qty > 0 for qty in transferred.values()):
        status = WorkOrderStatus.IN_PROCESS
    else:
        status = WorkOrderStatus.NOT_STARTED

    return {
        "transferred": transferred,
        "consumed": consumed,
        "produced_qty": produced_qty,
        "process_loss_qty": process_loss_qty,
        "status": status,
    }


def refresh_progress(wo: WorkOrder) -> Dict:
    """Recompute and persist the projection; call inside the posting transaction."""
    progress = compute_progress(wo)
    WorkOrder.objects.filter(pk=wo.pk).update(
        produced_qty=progress["produced_qty"],
        process_loss_qty=progress["process_loss_qty"],
        status=progress["status"],
    )
    for row in wo.required_items.all():
        transferred = progress["transferred"].get(row.item_id, Decimal("0.000"))
        consumed = progress["consumed"].get(row.item_id, Decimal("0.000"))
        WorkOrderItem.objects.filter(pk=row.pk).update(
            transferred_qty=transferred, consumed_qty=consumed
        )
        row.transferred_qty = transferred
        row.consumed_qty = consumed
    wo.produced_qty = progress["produced_qty"]
    wo.process_loss_qty = progress["process_loss_qty"]
    wo.status = progress["status"]
    return progress


def cancel_guard(wo: WorkOrder):
    """Refuse cancellation while any submitted Stock Entry references the WO."""
    WorkOrder.objects.select_for_update().filter(pk=wo.pk).values_list(
        "docstatus", flat=True
    ).first()
    if StockEntry.objects.filter(
        work_order_id=wo.pk, docstatus=DocStatus.SUBMITTED
    ).exists():
        raise ValidationError(
            _("Cancel the stock entries of this Work Order first.")
        )


# --------------------------------------------------------------------------- #
# Stop / resume
# --------------------------------------------------------------------------- #


def set_stopped(wo: WorkOrder, stopped: bool) -> WorkOrder:
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=wo.pk)
        if locked.docstatus != DocStatus.SUBMITTED:
            raise ValidationError(
                _("Only a submitted Work Order can be %(action)s.")
                % {"action": _("stopped") if stopped else _("resumed")}
            )
        WorkOrder.objects.filter(pk=wo.pk).update(stopped=stopped)
        wo.stopped = stopped
        refresh_progress(wo)
    return wo


# --------------------------------------------------------------------------- #
# Consumption capacities (used by the Stock Entry submit gate)
# --------------------------------------------------------------------------- #


def transferred_for(wo: WorkOrder, item_id: int) -> Decimal:
    return q3(
        StockEntryItem.objects.filter(
            stock_entry__work_order_id=wo.pk,
            stock_entry__docstatus=DocStatus.SUBMITTED,
            stock_entry__stock_entry_type=StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE,
            item_id=item_id,
        ).aggregate(total=Sum("qty"))["total"]
        or 0
    )


def consumed_for(wo: WorkOrder, item_id: int) -> Decimal:
    return q3(
        StockEntryItem.objects.filter(
            stock_entry__work_order_id=wo.pk,
            stock_entry__docstatus=DocStatus.SUBMITTED,
            stock_entry__stock_entry_type=StockEntryType.MANUFACTURE,
            item_id=item_id,
            is_finished_item=False,
            is_scrap_item=False,
        ).aggregate(total=Sum("qty"))["total"]
        or 0
    )


def consumed_capacity(wo: WorkOrder, row: WorkOrderItem) -> Decimal:
    """How much of one material may still be consumed in a Manufacture entry."""
    already_consumed = consumed_for(wo, row.item_id)
    if wo.skip_transfer:
        return q3(row.required_qty - already_consumed)
    return q3(transferred_for(wo, row.item_id) - already_consumed)


# --------------------------------------------------------------------------- #
# make-stock-entry
# --------------------------------------------------------------------------- #


def _as_qty(value, default=None) -> Decimal:
    if value in (None, ""):
        if default is None:
            raise ValidationError(_("Quantity is required."))
        return q3(default)
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(_("Quantity must be a number."))
    if qty <= 0:
        raise ValidationError(_("Quantity must be greater than zero."))
    return q3(qty)


def _batch_rows(item, source_warehouse, qty, posting_date, base: Dict):
    """One row for the whole quantity. A batch-tracked material is left without a
    batch on purpose: the ledger service FIFO-picks the oldest manufactured lots at
    submit (splitting over several batches when one is not enough), so the draft
    never goes stale and a shortage is reported by the submit gate."""
    return [{**base, "qty": qty, "batch": None}]


def make_stock_entry(
    wo: WorkOrder, stock_entry_type: str, qty=None
) -> StockEntry:
    """Create a **draft** Stock Entry for this Work Order (design §8)."""
    if wo.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(_("Only a submitted Work Order can be mapped."))
    if wo.stopped:
        raise ValidationError(_("A stopped Work Order cannot create stock entries."))

    if stock_entry_type not in (
        StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE,
        StockEntryType.MANUFACTURE,
    ):
        raise ValidationError(
            _("A Work Order can create a Material Transfer for Manufacture or a "
              "Manufacture entry.")
        )

    bom = wo.bom
    posting_date = datetime.date.today()
    remaining_basis = q3(Decimal(wo.qty) - Decimal(wo.produced_qty))
    basis_qty = _as_qty(qty, default=remaining_basis)
    if stock_entry_type == StockEntryType.MANUFACTURE and basis_qty > remaining_basis:
        raise ValidationError(
            _("Only %(qty)s of the Work Order basis remains to be produced.")
            % {"qty": remaining_basis}
        )

    with transaction.atomic():
        entry = StockEntry.objects.create(
            name=NamingSeries.next("STE-2026-"),
            stock_entry_type=stock_entry_type,
            posting_date=posting_date,
            company=wo.company,
            work_order=wo,
            bom=bom,
        )
        rows = []
        if stock_entry_type == StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE:
            rows = _transfer_rows(wo, basis_qty, posting_date)
        else:
            rows = _manufacture_rows(wo, basis_qty, posting_date)

        for row in rows:
            StockEntryItem.objects.create(stock_entry=entry, **row)
        if not rows:
            raise ValidationError(
                _("There is nothing left to transfer or consume for this Work Order.")
            )
    return entry


def _transfer_rows(wo: WorkOrder, basis_qty: Decimal, posting_date) -> list:
    if wo.skip_transfer:
        raise ValidationError(
            _("This Work Order consumes directly from the source warehouse; "
              "no material transfer is needed.")
        )
    if wo.wip_warehouse_id is None:
        raise ValidationError(_("The Work Order has no WIP Warehouse."))

    ratio = Decimal(basis_qty) / Decimal(wo.qty)
    rows = []
    for required in wo.required_items.select_related("item", "source_warehouse"):
        remaining = q3(Decimal(required.required_qty) - Decimal(required.transferred_qty))
        if remaining <= 0:
            continue
        wanted = q3(Decimal(required.required_qty) * ratio)
        if wanted <= 0:
            continue
        qty = min(wanted, remaining)
        source = required.source_warehouse or wo.source_warehouse
        base = {
            "item": required.item,
            "source_warehouse": source,
            "target_warehouse": wo.wip_warehouse,
        }
        rows.extend(_batch_rows(required.item, source, qty, posting_date, base))
    return rows


def _manufacture_rows(wo: WorkOrder, basis_qty: Decimal, posting_date) -> list:
    bom = wo.bom
    ratio = Decimal(basis_qty) / Decimal(wo.qty)
    rows = []

    # Consumption first (post_manufacture posts every out leg before any in leg).
    for required in wo.required_items.select_related("item", "source_warehouse"):
        capacity = consumed_capacity(wo, required)
        if capacity <= 0:
            continue
        wanted = q3(Decimal(required.required_qty) * ratio)
        if wanted <= 0:
            continue
        qty = min(wanted, capacity)
        if wo.skip_transfer:
            source = required.source_warehouse or wo.source_warehouse
        else:
            source = wo.wip_warehouse
        base = {
            "item": required.item,
            "source_warehouse": source,
            "target_warehouse": None,
        }
        rows.extend(_batch_rows(required.item, source, qty, posting_date, base))

    if not rows:
        raise ValidationError(
            _("No transferred material is available to consume for this Work Order.")
        )

    # Scrap / by-products: quantity per good output, before the finished row.
    scrap_rows = list(bom.scrap_items.select_related("item").order_by("idx", "pk"))
    if scrap_rows and wo.scrap_warehouse_id is None:
        raise ValidationError(
            _("The BOM has scrap items but the Work Order has no Scrap Warehouse.")
        )
    good_output = _bom_good_output(bom)
    for scrap in scrap_rows:
        qty = q3(Decimal(scrap.qty) * Decimal(basis_qty) / good_output)
        if qty <= 0:
            continue
        rows.append(
            {
                "item": scrap.item,
                "target_warehouse": wo.scrap_warehouse,
                "qty": qty,
                "valuation_rate": scrap.rate,
                "is_scrap_item": True,
            }
        )

    # A Work Order quantity is the desired good output (design §14.2).
    if basis_qty <= 0:
        raise ValidationError(_("The expected good output is zero."))
    rows.append(
        {
            "item": bom.item,
            "target_warehouse": wo.fg_warehouse,
            "qty": basis_qty,
            "is_finished_item": True,
        }
    )
    return rows
