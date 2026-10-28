import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any
from django.core.exceptions import ValidationError
from django.db import transaction
from core.models import FiscalYear
from stock.models import Batch, Item, Warehouse, StockLedgerEntry, Bin
from stock.services import batches as batch_service


def quantize_qty(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.000")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def quantize_rate(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.000000")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def quantize_value(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.00")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def post(
    voucher: Any,
    sle_entries: List[Dict[str, Any]],
    posting_date=None,
    posting_time=None,
) -> List[StockLedgerEntry]:
    """
    Atomically posts StockLedgerEntry rows and updates derived Bin balances.

    Optional keys per SLE dict (all default to the pre-batch behaviour):
      ``batch``          Batch | pk. Required iff the item is batch-tracked.
      ``incoming_value`` Decimal. Stock-in only: the exact value of the leg
                         (rate becomes value/qty), instead of qty * rate.
      ``allow_expired``  bool. Stock-out only: permit issuing an expired batch
                         (Material Issue, the write-off path).
    """
    if not sle_entries:
        raise ValidationError("Cannot post empty stock ledger entries.")

    date_to_use = (
        posting_date or getattr(voucher, "posting_date", None) or datetime.date.today()
    )
    time_to_use = (
        posting_time
        or getattr(voucher, "posting_time", None)
        or datetime.datetime.now().time()
    )

    # Posting into a missing or closed fiscal year is refused (ERPNext parity).
    FiscalYear.for_date(date_to_use)

    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))

    created_sles: List[StockLedgerEntry] = []
    # Rate computed on the most recent stock-out leg in this call. A transfer's
    # incoming leg reuses it so an internal move neither creates nor destroys value.
    last_outgoing_rate: Any = None

    with transaction.atomic():
        for item_data in sle_entries:
            item = item_data["item"]
            if isinstance(item, (int, str)):
                item = Item.objects.get(pk=item)

            warehouse = item_data["warehouse"]
            if isinstance(warehouse, (int, str)):
                warehouse = Warehouse.objects.get(pk=warehouse)

            if warehouse.is_group:
                raise ValidationError(
                    f"Stock transaction not allowed on group warehouse '{warehouse.name}'."
                )

            qty = quantize_qty(item_data["qty"])
            if qty == 0:
                continue

            rate = quantize_rate(item_data.get("valuation_rate", 0))

            batch = item_data.get("batch")
            if isinstance(batch, (int, str)):
                batch = Batch.objects.get(pk=batch)

            # Fetch or initialize Bin
            bin_obj, _ = Bin.objects.select_for_update().get_or_create(
                item=item,
                warehouse=warehouse,
                defaults={
                    "actual_qty": Decimal("0.000"),
                    "valuation_rate": Decimal("0.000000"),
                    "stock_value": Decimal("0.00"),
                },
            )

            # Batch invariants, checked under the Bin lock so concurrent postings
            # to the same item+warehouse cannot both pass the balance check.
            batch_position = batch_service.validate_leg(
                item,
                batch,
                qty,
                warehouse,
                date_to_use,
                allow_expired=bool(item_data.get("allow_expired")),
            )

            # Valuation math: Moving Average. FIFO is not yet implemented — see
            # docs/erpnext_divergences.md G1; FIFO-flagged items use this path too.
            new_qty = quantize_qty(bin_obj.actual_qty + qty)

            if qty > 0:
                # Stock In. For a transfer's incoming leg, value it at the rate the
                # matching outgoing leg left with so total stock value is conserved.
                if (
                    item_data.get("use_outgoing_rate")
                    and last_outgoing_rate is not None
                ):
                    rate = quantize_rate(last_outgoing_rate)
                if item_data.get("incoming_value") is not None:
                    incoming_value = quantize_value(item_data["incoming_value"])
                    rate = quantize_rate(incoming_value / qty)
                else:
                    incoming_value = quantize_value(qty * rate)
                new_stock_value = quantize_value(bin_obj.stock_value + incoming_value)
                new_valuation_rate = (
                    quantize_rate(new_stock_value / new_qty)
                    if new_qty > 0
                    else Decimal("0.000000")
                )
                stock_value_diff = incoming_value
            else:
                # Stock Out
                if bin_obj.actual_qty < abs(qty):
                    raise ValidationError(
                        f"Insufficient stock for Item '{item.code}' in Warehouse '{warehouse.name}'. "
                        f"Available: {bin_obj.actual_qty}, Required: {abs(qty)}"
                    )
                # A batch-tracked item is costed at its batch's own average (ERPNext's
                # batch-wise valuation); otherwise (and when the batch carries no
                # cost) at the Bin average, else the passed rate.
                outgoing_rate = None
                batch_cost = None
                if batch_position is not None:
                    b_qty, b_value = batch_position
                    if b_qty > 0 and b_value > 0:
                        outgoing_rate = quantize_rate(b_value / b_qty)
                        batch_cost = b_value
                if not outgoing_rate:
                    outgoing_rate = (
                        bin_obj.valuation_rate if bin_obj.valuation_rate > 0 else rate
                    )
                outgoing_value = quantize_value(abs(qty) * outgoing_rate)
                # Issuing a batch's whole balance releases exactly its value, so no
                # paisa of rounding is stranded on an empty batch.
                if batch_cost is not None and batch_position[0] == abs(qty):
                    outgoing_value = quantize_value(batch_cost)
                new_stock_value = quantize_value(bin_obj.stock_value - outgoing_value)
                new_valuation_rate = (
                    quantize_rate(new_stock_value / new_qty)
                    if new_qty > 0
                    else Decimal("0.000000")
                )
                stock_value_diff = quantize_value(-outgoing_value)
                last_outgoing_rate = outgoing_rate

            # Update derived Bin
            bin_obj.actual_qty = new_qty
            bin_obj.valuation_rate = new_valuation_rate
            bin_obj.stock_value = new_stock_value
            bin_obj.save(
                update_fields=[
                    "actual_qty",
                    "valuation_rate",
                    "stock_value",
                    "modified_at",
                ]
            )

            # Create immutable SLE
            sle = StockLedgerEntry.objects.create(
                posting_date=date_to_use,
                posting_time=time_to_use,
                item=item,
                warehouse=warehouse,
                qty=qty,
                valuation_rate=rate if qty > 0 else outgoing_rate,
                stock_value_difference=stock_value_diff,
                voucher_type=voucher_type,
                voucher_no=voucher_no,
                is_cancelled=False,
                batch=batch,
            )
            created_sles.append(sle)

        return created_sles


def reverse(voucher: Any) -> List[StockLedgerEntry]:
    """
    Atomically posts reversing StockLedgerEntry rows and updates Bin balances.
    """
    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))

    with transaction.atomic():
        original_sles = StockLedgerEntry.objects.filter(
            voucher_type=voucher_type,
            voucher_no=voucher_no,
            is_cancelled=False,
        )

        # Cancelling a document dated in a closed fiscal year is refused.
        for posting_date in sorted({s.posting_date for s in original_sles}):
            FiscalYear.for_date(posting_date)

        reversal_sles: List[StockLedgerEntry] = []
        for orig in original_sles:
            orig._allow_internal_cancellation = True
            orig.is_cancelled = True
            orig.save(update_fields=["is_cancelled"])

            # Reversing entry negates qty & value diff
            rev_qty = quantize_qty(-orig.qty)
            rev_diff = quantize_value(-orig.stock_value_difference)

            bin_obj, _ = Bin.objects.select_for_update().get_or_create(
                item=orig.item,
                warehouse=orig.warehouse,
            )
            new_qty = quantize_qty(bin_obj.actual_qty + rev_qty)
            # Guard the No-Negative-Stock invariant: a reversal must never drive a
            # Bin below zero. This happens when a later voucher already consumed the
            # stock this entry brought in (e.g. cancelling a receipt whose units were
            # since issued/sold/transferred out). Like ERPNext, block the cancel and
            # tell the user to reverse the dependent transactions first.
            if new_qty < 0:
                raise ValidationError(
                    f"Cannot cancel: reversing this entry would leave negative stock for "
                    f"Item '{orig.item.code}' in Warehouse '{orig.warehouse.name}' "
                    f"(available {bin_obj.actual_qty}, reversal {rev_qty}). "
                    f"Cancel the later transactions that consumed this stock first."
                )
            # Same guard per batch: cancelling an inward leg must not leave its
            # batch negative (the original is already flagged cancelled, so the
            # derived balance excludes it).
            if orig.batch_id and orig.qty > 0:
                left = batch_service.batch_balance(
                    orig.item_id, orig.warehouse_id, orig.batch_id
                )
                if left < 0:
                    raise ValidationError(
                        f"Cannot cancel: reversing this entry would leave negative stock "
                        f"in batch '{orig.batch.batch_no}' of Item '{orig.item.code}' "
                        f"in Warehouse '{orig.warehouse.name}' (short by {-left}). "
                        f"Cancel the later transactions that consumed this batch first."
                    )
            new_value = quantize_value(bin_obj.stock_value + rev_diff)
            new_rate = (
                quantize_rate(new_value / new_qty)
                if new_qty > 0
                else Decimal("0.000000")
            )

            bin_obj.actual_qty = new_qty
            bin_obj.stock_value = new_value
            bin_obj.valuation_rate = new_rate
            bin_obj.save(
                update_fields=[
                    "actual_qty",
                    "valuation_rate",
                    "stock_value",
                    "modified_at",
                ]
            )

            rev_sle = StockLedgerEntry.objects.create(
                posting_date=orig.posting_date,
                posting_time=orig.posting_time,
                item=orig.item,
                warehouse=orig.warehouse,
                qty=rev_qty,
                valuation_rate=orig.valuation_rate,
                stock_value_difference=rev_diff,
                voucher_type=orig.voucher_type,
                voucher_no=orig.voucher_no,
                is_cancelled=True,
                batch_id=orig.batch_id,
            )
            reversal_sles.append(rev_sle)

        return reversal_sles


def repost(item_id=None, warehouse_id=None) -> bool:
    """
    Self-healing tool: completely deletes derived Bin records in scope
    and rebuilds actual_qty, valuation_rate, and stock_value by replaying
    all non-cancelled StockLedgerEntry logs in chronological order.
    """
    with transaction.atomic():
        bin_qs = Bin.objects.all()
        if item_id:
            bin_qs = bin_qs.filter(item_id=item_id)
        if warehouse_id:
            bin_qs = bin_qs.filter(warehouse_id=warehouse_id)

        # Clear derived Bins in scope
        bin_qs.delete()

        sle_qs = StockLedgerEntry.objects.filter(is_cancelled=False).order_by(
            "posting_date", "posting_time", "id"
        )
        if item_id:
            sle_qs = sle_qs.filter(item_id=item_id)
        if warehouse_id:
            sle_qs = sle_qs.filter(warehouse_id=warehouse_id)

        for sle in sle_qs:
            bin_obj, _ = Bin.objects.select_for_update().get_or_create(
                item=sle.item,
                warehouse=sle.warehouse,
                defaults={
                    "actual_qty": Decimal("0.000"),
                    "valuation_rate": Decimal("0.000000"),
                    "stock_value": Decimal("0.00"),
                },
            )
            bin_obj.actual_qty = quantize_qty(bin_obj.actual_qty + sle.qty)
            bin_obj.stock_value = quantize_value(
                bin_obj.stock_value + sle.stock_value_difference
            )
            bin_obj.valuation_rate = (
                quantize_rate(bin_obj.stock_value / bin_obj.actual_qty)
                if bin_obj.actual_qty > 0
                else Decimal("0.000000")
            )
            bin_obj.save()

        return True
