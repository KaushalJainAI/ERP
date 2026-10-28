"""Manufacturing stock-entry validation and posting (design §3.3, §3.4, §4).

``Material Transfer for Manufacture`` is a two-leg transfer into WIP under the
Work Order's per-item cap.  ``Manufacture`` and ``Repack`` consume stock, value
the consumed material from the ledger, subtract scrap, allocate the result over
the finished rows and post the finished goods with explicit ``incoming_value``
so the voucher conserves value to the paisa.

Process loss follows the user decision in design §14.2: a Work Order quantity is
the **desired good output**, so the raw requirement is grossed up by the BOM loss
and ``fg_completed_qty`` is the good output of one entry.  ``process_loss_qty``
is server-owned: the gross input equivalent consumed minus that good output.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from core.models import DocStatus
from manufacturing.models import BOM, WorkOrder
from stock.models import StockEntry, StockEntryItem, StockEntryType
from stock.services import batches as batch_service
from stock.services import ledger as stock_service
from manufacturing.services import work_orders as wo_service

ZERO = Decimal("0.00")
ZERO_QTY = Decimal("0.000")


def q3(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def q2(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fail(message, **params):
    raise ValidationError(_(message) % params if params else _(message))


def _validate_warehouse(warehouse, company, field):
    if warehouse is None:
        _fail("%(field)s is required.", field=field)
    if warehouse.is_group:
        _fail(
            "%(field)s '%(name)s' is a group warehouse.",
            field=field,
            name=warehouse.name,
        )
    if warehouse.company_id != company.pk:
        _fail(
            "%(field)s '%(name)s' belongs to a different company.",
            field=field,
            name=warehouse.name,
        )


def _consumption_ratio(bom: BOM) -> Decimal:
    """Good output produced by one BOM basis (``quantity × (1 − loss/100)``)."""
    loss = Decimal(bom.process_loss_percentage or 0)
    good = Decimal(bom.quantity) * (Decimal(1) - loss / Decimal(100))
    if good <= 0:
        _fail("BOM '%(bom)s' has no positive good output.", bom=bom.name)
    return good


# --------------------------------------------------------------------------- #
# Validation (the submit gate)
# --------------------------------------------------------------------------- #


def validate_entry(se: StockEntry, items: List[StockEntryItem]):
    entry_type = se.stock_entry_type
    if entry_type == StockEntryType.REPACK:
        _validate_repack(se, items)
    elif entry_type == StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE:
        _validate_transfer_for_manufacture(se, items)
    else:
        _validate_manufacture(se, items)


def _lock_work_order(se: StockEntry) -> WorkOrder:
    try:
        wo = WorkOrder.objects.select_for_update().get(pk=se.work_order_id)
    except WorkOrder.DoesNotExist:
        _fail("The Work Order does not exist.")
    if wo.docstatus != DocStatus.SUBMITTED:
        _fail("Work Order '%(wo)s' is not submitted.", wo=wo.name)
    if wo.stopped:
        _fail("Work Order '%(wo)s' is stopped.", wo=wo.name)
    if wo.company_id != se.company_id:
        _fail("Work Order '%(wo)s' belongs to a different company.", wo=wo.name)
    if se.bom_id and se.bom_id != wo.bom_id:
        _fail("The entry's BOM is not the Work Order's BOM.")
    return wo


def _validate_transfer_for_manufacture(se: StockEntry, items):
    if not se.work_order_id:
        _fail("Material Transfer for Manufacture requires a Work Order.")
    wo = _lock_work_order(se)
    if wo.skip_transfer:
        _fail(
            "Work Order '%(wo)s' consumes directly from the source warehouse; "
            "no material transfer is needed.",
            wo=wo.name,
        )
    if wo.wip_warehouse_id is None:
        _fail("Work Order '%(wo)s' has no WIP Warehouse.", wo=wo.name)

    required = {row.item_id: row for row in wo.required_items.all()}
    for line in items:
        if line.is_finished_item or line.is_scrap_item:
            _fail("A Material Transfer for Manufacture cannot have finished or scrap rows.")
        _validate_warehouse(line.source_warehouse, se.company, _("Source Warehouse"))
        _validate_warehouse(line.target_warehouse, se.company, _("Target Warehouse"))
        if line.target_warehouse_id != wo.wip_warehouse_id:
            _fail(
                "Material Transfer for Manufacture must move into the Work Order's "
                "WIP Warehouse."
            )
        row = required.get(line.item_id)
        if row is None:
            _fail(
                "Item '%(code)s' is not a required item of Work Order '%(wo)s'.",
                code=line.item.code,
                wo=wo.name,
            )
        remaining = q3(Decimal(row.required_qty) - wo_service.transferred_for(wo, row.item_id))
        if line.qty > remaining:
            _fail(
                "Item '%(code)s': only %(qty)s remains to be transferred "
                "(requested %(requested)s).",
                code=line.item.code,
                qty=remaining,
                requested=line.qty,
            )
        se._validate_batch_line(line)


def _validate_manufacture(se: StockEntry, items):
    if se.work_order_id:
        wo = _lock_work_order(se)
        if se.bom_id is None:
            se.bom = wo.bom
        bom = wo.bom
    else:
        wo = None
        if se.bom_id is None:
            _fail("A Manufacture entry needs a Work Order or a BOM.")
        bom = se.bom
    if bom.company_id != se.company_id:
        _fail("BOM '%(bom)s' belongs to a different company.", bom=bom.name)

    consumption = [i for i in items if not i.is_finished_item and not i.is_scrap_item]
    finished = [i for i in items if i.is_finished_item]
    scrap = [i for i in items if i.is_scrap_item]
    if not consumption:
        _fail("A Manufacture entry needs at least one consumption row.")
    if len(finished) != 1:
        _fail("A Manufacture entry must have exactly one finished row.")
    if finished[0].item_id != bom.item_id:
        _fail(
            "The finished item must be the BOM's product '%(item)s'.",
            item=bom.item.code,
        )

    for line in consumption:
        _validate_warehouse(line.source_warehouse, se.company, _("Source Warehouse"))
        se._validate_batch_line(line)
    for line in finished + scrap:
        _validate_warehouse(line.target_warehouse, se.company, _("Target Warehouse"))
        se._validate_batch_line(line)

    _assert_finished_qty(se, finished[0])
    _assert_consumption_matches_bom(se, bom, consumption)
    _assert_consumption_capacity(se, wo, consumption)
    _assert_process_loss(se, bom, consumption, finished[0])


def _validate_repack(se: StockEntry, items):
    if se.work_order_id or se.bom_id:
        _fail("A Repack entry has no Work Order and no BOM.")
    consumption = [i for i in items if not i.is_finished_item and not i.is_scrap_item]
    finished = [i for i in items if i.is_finished_item]
    scrap = [i for i in items if i.is_scrap_item]
    if not consumption:
        _fail("A Repack entry needs at least one consumption row.")
    if not finished:
        _fail("A Repack entry needs at least one finished row.")
    for line in consumption:
        _validate_warehouse(line.source_warehouse, se.company, _("Source Warehouse"))
        se._validate_batch_line(line)
    for line in finished + scrap:
        _validate_warehouse(line.target_warehouse, se.company, _("Target Warehouse"))
        se._validate_batch_line(line)

    percentages = [line.cost_allocation_per for line in finished]
    if len(finished) == 1:
        if percentages[0] is not None and q3(percentages[0]) != Decimal("100.000"):
            _fail("A single finished row must allocate 100%%.")
    else:
        if any(p is None for p in percentages):
            _fail("Every finished row needs a Cost Allocation % for a Repack.")
        total = sum((Decimal(p) for p in percentages), Decimal("0"))
        if q3(total) != Decimal("100.000"):
            _fail("Cost Allocation %% over the finished rows must total 100.")


def _assert_finished_qty(se: StockEntry, finished: StockEntryItem):
    if finished.qty <= 0:
        _fail("The finished quantity must be greater than zero.")
    if se.fg_completed_qty and q3(se.fg_completed_qty) != q3(finished.qty):
        _fail(
            "Completed quantity %(given)s does not match the finished row quantity "
            "%(row)s.",
            given=q3(se.fg_completed_qty),
            row=q3(finished.qty),
        )


def _assert_consumption_capacity(se, wo: Optional[WorkOrder], consumption):
    if wo is None:
        return
    per_item: Dict[int, Decimal] = {}
    for line in consumption:
        per_item[line.item_id] = per_item.get(line.item_id, ZERO_QTY) + Decimal(line.qty)
    for item_id, qty in per_item.items():
        row = wo.required_items.filter(item_id=item_id).first()
        if row is None:
            _fail(
                "Item id %(item)s is not a required item of Work Order '%(wo)s'.",
                item=item_id,
                wo=wo.name,
            )
        capacity = wo_service.consumed_capacity(wo, row)
        if qty > capacity:
            _fail(
                "Item '%(code)s': only %(qty)s can still be consumed "
                "(requested %(requested)s).",
                code=row.item.code,
                qty=capacity,
                requested=qty,
            )


def _assert_consumption_matches_bom(se, bom: BOM, consumption):
    allowed = set(bom.items.values_list("item_id", flat=True))
    for line in consumption:
        if line.item_id not in allowed:
            _fail(
                "Item '%(code)s' is not a component of BOM '%(bom)s'.",
                code=line.item.code,
                bom=bom.name,
            )


def _basis_consumed(bom: BOM, consumption) -> Decimal:
    """The BOM basis quantity that the consumed rows represent.

    Each component's consumed quantity implies a basis
    ``line.qty × bom.quantity / row.qty``.  A correctly consumed batch gives the
    same value for every component; the smallest implies how many whole BOM bases
    were actually consumed, which is the honest gross input when one component is
    short.
    """
    rows = {row.item_id: row for row in bom.items.all()}
    implied = []
    for line in consumption:
        row = rows.get(line.item_id)
        if row is None or row.qty == 0:
            continue
        implied.append(
            Decimal(line.qty) * Decimal(bom.quantity) / Decimal(row.qty)
        )
    if not implied:
        return ZERO_QTY
    return q3(min(implied))


def _assert_process_loss(se: StockEntry, bom: BOM, consumption, finished):
    gross = _basis_consumed(bom, consumption)
    output = q3(finished.qty)
    if gross < output:
        _fail(
            "The consumed materials represent %(gross)s of BOM basis but %(out)s of "
            "good output is booked; a Manufacture cannot produce more than it consumes.",
            gross=gross,
            out=output,
        )


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #


def _create_inward_batch(se: StockEntry, line: StockEntryItem):
    if not line.item.has_batch_no or line.batch_id:
        return line.batch
    batch = batch_service.create_batch(
        line.item,
        manufacturing_date=line.manufacturing_date,
        expiry_date=line.expiry_date,
        supplier_batch_no=line.supplier_batch_no,
        default_manufacturing_date=se.posting_date,
        numbering_date=se.posting_date,
        reference_doctype=f"{se._meta.app_label}.{se._meta.object_name}",
        reference_name=se.name,
    )
    line.batch = batch
    line.save(update_fields=["batch"])
    return batch


def _out_leg(line: StockEntryItem) -> Dict:
    return {
        "item": line.item,
        "warehouse": line.source_warehouse,
        "qty": -abs(line.qty),
        "valuation_rate": line.valuation_rate,
        "batch": line.batch,
    }


def post_transfer_for_manufacture(se: StockEntry) -> List:
    """Move the Work Order's materials to WIP and refresh its progress."""
    legs = []
    for line in se.items.select_related("item", "batch", "source_warehouse"):
        batch = line.batch
        legs.append(_out_leg(line))
        legs.append(
            {
                "item": line.item,
                "warehouse": line.target_warehouse,
                "qty": abs(line.qty),
                "valuation_rate": line.valuation_rate,
                "batch": batch,
                "use_outgoing_rate": True,
            }
        )
    sles = stock_service.post(
        se, legs, posting_date=se.posting_date, posting_time=se.posting_time
    )
    refresh_bound_work_order(se)
    return sles


def _allocate(fg_total: Decimal, finished: List[StockEntryItem]) -> List[Decimal]:
    if len(finished) == 1:
        return [fg_total]
    values = [
        q2(fg_total * Decimal(line.cost_allocation_per) / Decimal(100))
        for line in finished
    ]
    residue = q2(fg_total - sum(values, ZERO))
    values[-1] = q2(values[-1] + residue)
    return values


def post_manufacture(se: StockEntry) -> List:
    """Post consumption, scrap and finished goods for Manufacture / Repack."""
    lines = list(
        se.items.select_related(
            "item", "batch", "source_warehouse", "target_warehouse"
        )
    )
    consumption = [
        line for line in lines if not line.is_finished_item and not line.is_scrap_item
    ]
    finished = [line for line in lines if line.is_finished_item]
    scrap = [line for line in lines if line.is_scrap_item]

    out_sles = stock_service.post(
        se,
        [_out_leg(line) for line in consumption],
        posting_date=se.posting_date,
        posting_time=se.posting_time,
    )
    consumed_value = q2(-sum((s.stock_value_difference for s in out_sles), ZERO))
    scrap_value = q2(
        sum((q2(Decimal(line.qty) * Decimal(line.valuation_rate)) for line in scrap), ZERO)
    )
    fg_total = q2(consumed_value - scrap_value)
    if fg_total < 0:
        _fail(
            "The scrap value (%(scrap)s) exceeds the consumed material value "
            "(%(consumed)s).",
            scrap=scrap_value,
            consumed=consumed_value,
        )

    in_legs = []
    incoming_values: List[Tuple[StockEntryItem, Decimal]] = []
    for line in scrap:
        value = q2(Decimal(line.qty) * Decimal(line.valuation_rate))
        batch = _create_inward_batch(se, line)
        in_legs.append(
            {
                "item": line.item,
                "warehouse": line.target_warehouse,
                "qty": abs(line.qty),
                "batch": batch,
                "incoming_value": value,
            }
        )
        incoming_values.append((line, value))
    for line, value in zip(finished, _allocate(fg_total, finished)):
        batch = _create_inward_batch(se, line)
        in_legs.append(
            {
                "item": line.item,
                "warehouse": line.target_warehouse,
                "qty": abs(line.qty),
                "batch": batch,
                "incoming_value": value,
            }
        )
        incoming_values.append((line, value))

    in_sles = stock_service.post(
        se, in_legs, posting_date=se.posting_date, posting_time=se.posting_time
    )

    # Display the exact rate each inward row was valued at.
    for line, value in incoming_values:
        if line.qty:
            line.valuation_rate = stock_service.quantize_rate(value / Decimal(line.qty))
            line.save(update_fields=["valuation_rate"])

    total = sum(
        (s.stock_value_difference for s in out_sles + in_sles), ZERO
    ).quantize(Decimal("0.01"))
    if total != ZERO:
        _fail(
            "Manufacturing value is not conserved (%(total)s); refusing to post the "
            "difference to the stock adjustment account.",
            total=total,
        )

    fg_completed = q3(sum((line.qty for line in finished), ZERO_QTY))
    process_loss = ZERO_QTY
    if se.bom_id:
        process_loss = q3(_basis_consumed(se.bom, consumption) - fg_completed)
        if process_loss < 0:
            process_loss = ZERO_QTY
    StockEntry.objects.filter(pk=se.pk).update(
        fg_completed_qty=fg_completed, process_loss_qty=process_loss
    )
    se.fg_completed_qty = fg_completed
    se.process_loss_qty = process_loss

    refresh_bound_work_order(se)
    return out_sles + in_sles


def refresh_bound_work_order(se: StockEntry):
    """Rebuild the bound Work Order's derived progress (submit and cancel)."""
    if not se.work_order_id:
        return
    try:
        wo = WorkOrder.objects.get(pk=se.work_order_id)
    except WorkOrder.DoesNotExist:  # pragma: no cover - PROTECT prevents this
        return
    with transaction.atomic():
        wo_service.refresh_progress(wo)
