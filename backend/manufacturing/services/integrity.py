"""Manufacturing integrity checks for ``verify_integrity``."""

from decimal import Decimal

from django.db.models import Sum

from core.models import DocStatus
from manufacturing.models import WorkOrder
from manufacturing.services.work_orders import compute_progress
from stock.models import StockEntry, StockEntryItem, StockEntryType, StockLedgerEntry

MANUFACTURING_TYPES = (
    StockEntryType.MANUFACTURE,
    StockEntryType.REPACK,
)


def check():
    """Return ``(errors, warnings)`` for manufacturing invariants."""
    errors = []
    warnings = []

    for entry in (
        StockEntry.objects.filter(docstatus=DocStatus.SUBMITTED)
        .filter(stock_entry_type__in=MANUFACTURING_TYPES)
        .select_related("work_order", "bom")
    ):
        # Quantize to the field's 2 dp, as the Bin check does: SQLite sums
        # DECIMAL columns as floats, which leaves ~1e-14 noise on a true zero.
        total = (
            StockLedgerEntry.objects.filter(
                voucher_type="stock.StockEntry",
                voucher_no=entry.name,
                is_cancelled=False,
            ).aggregate(total=Sum("stock_value_difference"))["total"]
            or Decimal("0.00")
        ).quantize(Decimal("0.01"))
        if total != Decimal("0.00"):
            errors.append(
                f"Manufacturing value drift in {entry.name}: ledger value "
                f"difference is {total}, not zero."
            )
        finished = StockEntryItem.objects.filter(
            stock_entry=entry, is_finished_item=True
        )
        finished_qty = sum((row.qty for row in finished), Decimal("0.000"))
        if entry.fg_completed_qty != finished_qty:
            errors.append(
                f"{entry.name}: completed quantity {entry.fg_completed_qty} does not "
                f"match finished rows {finished_qty}."
            )
        if finished.count() == 0:
            errors.append(f"{entry.name}: a manufacturing entry has no finished row.")

    for wo in WorkOrder.objects.exclude(docstatus=DocStatus.DRAFT):
        if wo.produced_qty > wo.qty:
            errors.append(
                f"Work Order {wo.name}: produced {wo.produced_qty} exceeds planned "
                f"{wo.qty}."
            )
        progress = compute_progress(wo)
        if (
            wo.produced_qty != progress["produced_qty"]
            or wo.process_loss_qty != progress["process_loss_qty"]
            or wo.status != progress["status"]
        ):
            errors.append(
                f"Work Order {wo.name}: stored progress "
                f"({wo.produced_qty}, {wo.process_loss_qty}, {wo.status}) does not "
                f"match the ledger ({progress['produced_qty']}, "
                f"{progress['process_loss_qty']}, {progress['status']}); run "
                f"repost_work_orders."
            )
        for row in wo.required_items.all():
            expected_transferred = progress["transferred"].get(
                row.item_id, Decimal("0.000")
            )
            expected_consumed = progress["consumed"].get(row.item_id, Decimal("0.000"))
            if (
                row.transferred_qty != expected_transferred
                or row.consumed_qty != expected_consumed
            ):
                errors.append(
                    f"Work Order {wo.name}: row '{row.item.code}' progress "
                    f"({row.transferred_qty}, {row.consumed_qty}) does not match the "
                    f"ledger ({expected_transferred}, {expected_consumed}); run "
                    f"repost_work_orders."
                )
        if wo.skip_transfer and progress["transferred"]:
            warnings.append(
                f"Work Order {wo.name} skips transfer but has transferred quantities."
            )

    return errors, warnings
