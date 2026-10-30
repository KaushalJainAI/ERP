import datetime
from collections import deque
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.db import transaction
from core.models import FiscalYear
from stock.models import Batch, Item, Warehouse, StockLedgerEntry, Bin
from stock.services import batches as batch_service
from stock.services import variants as variant_service


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
      ``mirror_outgoing`` bool. Stock-in only (a transfer's incoming leg): carry
                         the batch(es) of the outgoing leg(s) just posted.

    A stock-out of a batch-tracked item that names no batch is allocated here,
    FIFO (oldest manufacturing date first), across as many batches as needed: the
    leg is replaced by one SLE per batch. The allocation runs under the Bin lock
    taken for that item + warehouse, so concurrent postings cannot double-allocate.
    """
    if not sle_entries:
        raise ValidationError(_("Cannot post empty stock ledger entries."))

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
    # The (batch, qty, rate, value) legs of the latest stock-out line (one line may
    # have been split over several FIFO batches); a transfer's incoming leg mirrors it.
    last_out_group: List[Dict[str, Any]] = []

    with transaction.atomic():
        queue = deque(sle_entries)
        while queue:
            item_data = queue.popleft()
            item = item_data["item"]
            if isinstance(item, (int, str)):
                item = Item.objects.get(pk=item)

            warehouse = item_data["warehouse"]
            if isinstance(warehouse, (int, str)):
                warehouse = Warehouse.objects.get(pk=warehouse)

            if warehouse.is_group:
                raise ValidationError(
                    _("Stock transaction not allowed on group warehouse '%(name)s'.")
                    % {"name": warehouse.name}
                )
            variant_service.assert_not_template(item)

            qty = quantize_qty(item_data["qty"])
            if qty == 0:
                continue

            rate = quantize_rate(item_data.get("valuation_rate", 0))

            batch = item_data.get("batch")
            if isinstance(batch, (int, str)):
                batch = Batch.objects.get(pk=batch)

            # Fetch or initialize Bin
            bin_obj, _created = Bin.objects.select_for_update().get_or_create(
                item=item,
                warehouse=warehouse,
                defaults={
                    "actual_qty": Decimal("0.000"),
                    "valuation_rate": Decimal("0.000000"),
                    "stock_value": Decimal("0.00"),
                },
            )

            if item.has_batch_no and batch is None:
                if qty < 0:
                    # FIFO auto-pick, under the Bin lock taken above: replace this
                    # leg by one explicit leg per allocated batch (processed next,
                    # in order, so each sees the balances left by the previous).
                    allocations = batch_service.allocate_fifo(
                        item,
                        warehouse,
                        abs(qty),
                        date_to_use,
                        allow_expired=bool(item_data.get("allow_expired")),
                    )
                    legs = [
                        {
                            **item_data,
                            "batch": picked,
                            "qty": -picked_qty,
                            "_group_continues": index > 0,
                        }
                        for index, (picked, picked_qty) in enumerate(allocations)
                    ]
                    queue.extendleft(reversed(legs))
                    continue
                if item_data.get("mirror_outgoing") and last_out_group:
                    if sum((g["qty"] for g in last_out_group), Decimal("0")) != qty:
                        raise ValidationError(
                            _(
                                "Transfer of '%(code)s': incoming quantity does not match the quantity taken out."
                            )
                            % {"code": item.code}
                        )
                    legs = [
                        {
                            **item_data,
                            "batch": g["batch"],
                            "qty": g["qty"],
                            "incoming_value": g["value"],
                            "mirror_outgoing": False,
                            "use_outgoing_rate": False,
                        }
                        for g in last_out_group
                    ]
                    queue.extendleft(reversed(legs))
                    continue

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
                        _(
                            "Insufficient stock for Item '%(code)s' in Warehouse '%(warehouse)s'. "
                            "Available: %(available)s, Required: %(required)s"
                        )
                        % {
                            "code": item.code,
                            "warehouse": warehouse.name,
                            "available": bin_obj.actual_qty,
                            "required": abs(qty),
                        }
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
                leg = {
                    "batch": batch,
                    "qty": abs(qty),
                    "value": abs(stock_value_diff),
                }
                if item_data.get("_group_continues"):
                    last_out_group.append(leg)
                else:
                    last_out_group[:] = [leg]

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


def resolve_reconciliation_target(
    cur_qty: Decimal, cur_value: Decimal, qty: Any, rate: Any
) -> Dict[str, Decimal]:
    """Resolve what one reconciliation row asks for against the current position.

    ``qty`` / ``rate`` are the counted values; ``None`` keeps the current one (a
    rate-only correction, or a quantity-only count that keeps the rate). Returns
    ``cur_*``, the resolved target ``qty`` / ``rate`` / ``value`` and the deltas
    ``dq`` / ``dv`` the posted SLE carries (0 and 0 = nothing to reconcile).
    """
    cur_qty = quantize_qty(cur_qty)
    cur_value = quantize_value(cur_value)
    cur_rate = quantize_rate(cur_value / cur_qty) if cur_qty > 0 else quantize_rate(0)

    if qty is None:
        if cur_qty < 0:
            raise ValidationError(
                _("Current stock is negative; enter the counted quantity.")
            )
        t_qty = cur_qty
    else:
        t_qty = quantize_qty(qty)
        if t_qty < 0:
            raise ValidationError(_("Negative Quantity is not allowed."))
    if rate is not None and quantize_rate(rate) < 0:
        raise ValidationError(_("Negative Valuation Rate is not allowed."))
    t_rate = cur_rate if rate is None else quantize_rate(rate)

    if t_qty == 0:
        t_value = Decimal("0.00")
    else:
        if t_rate <= 0:
            raise ValidationError(
                _(
                    "A valuation rate is required to count stock into a position "
                    "that has no current rate."
                )
            )
        if cur_qty > 0 and t_qty == cur_qty and t_rate == cur_rate:
            t_value = cur_value  # unchanged: do not invent a rounding difference
        else:
            t_value = quantize_value(t_qty * t_rate)
    return {
        "cur_qty": cur_qty,
        "cur_value": cur_value,
        "cur_rate": cur_rate,
        "qty": t_qty,
        "rate": t_rate,
        "value": t_value,
        "dq": quantize_qty(t_qty - cur_qty),
        "dv": quantize_value(t_value - cur_value),
    }


def position(item: Any, warehouse: Any, batch: Any = None) -> tuple:
    """Current ``(qty, stock_value)`` of an item in a warehouse, or of one batch
    of it, read from the ledger-derived Bin / batch balance."""
    if batch is not None:
        return batch_service.batch_position(item, warehouse, batch)
    bin_obj = Bin.objects.filter(item=item, warehouse=warehouse).first()
    if bin_obj is None:
        return Decimal("0.000"), Decimal("0.00")
    return bin_obj.actual_qty, bin_obj.stock_value


def post_reconciliation(
    voucher: Any,
    rows: List[Dict[str, Any]],
    posting_date=None,
    posting_time=None,
) -> List[StockLedgerEntry]:
    """
    Atomically posts the SLEs of a Stock Reconciliation.

    Each row states the *absolute* target (``qty`` and/or ``valuation_rate``,
    ``None`` = keep current; ``batch`` for batch-tracked items). Under the Bin
    lock the row is resolved against the live position and one SLE is written
    carrying the **delta** (``qty`` and ``stock_value_difference`` may be zero
    but not both). Because the stored facts are deltas, ``repost`` / the
    integrity checks (plain sums over non-cancelled SLEs) replay them exactly
    and ``reverse`` undoes them like any other voucher.

    There is no later-entry repost in this ledger, so a reconciliation is
    refused when the item+warehouse already has a live SLE after its
    date/time (see docs/erpnext_divergences.md).
    """
    date_to_use = (
        posting_date or getattr(voucher, "posting_date", None) or datetime.date.today()
    )
    time_to_use = (
        posting_time
        or getattr(voucher, "posting_time", None)
        or datetime.datetime.now().time()
    )
    FiscalYear.for_date(date_to_use)

    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))
    created: List[StockLedgerEntry] = []

    with transaction.atomic():
        for row in rows:
            item = row["item"]
            if isinstance(item, (int, str)):
                item = Item.objects.get(pk=item)
            warehouse = row["warehouse"]
            if isinstance(warehouse, (int, str)):
                warehouse = Warehouse.objects.get(pk=warehouse)
            if warehouse.is_group:
                raise ValidationError(
                    _("Stock transaction not allowed on group warehouse '%(name)s'.")
                    % {"name": warehouse.name}
                )
            batch = row.get("batch")
            if isinstance(batch, (int, str)):
                batch = Batch.objects.get(pk=batch)
            if item.has_batch_no and batch is None:
                raise ValidationError(
                    _("Item '%(code)s' is batch-tracked: enter the batch counted.")
                    % {"code": item.code}
                )
            if not item.has_batch_no and batch is not None:
                raise ValidationError(
                    _("Item '%(code)s' is not batch-tracked but a batch was given.")
                    % {"code": item.code}
                )
            if batch is not None and batch.item_id != item.pk:
                raise ValidationError(
                    _(
                        "Batch '%(batch_no)s' belongs to a different item than '%(code)s'."
                    )
                    % {"batch_no": batch.batch_no, "code": item.code}
                )

            bin_obj, _created = Bin.objects.select_for_update().get_or_create(
                item=item,
                warehouse=warehouse,
                defaults={
                    "actual_qty": Decimal("0.000"),
                    "valuation_rate": Decimal("0.000000"),
                    "stock_value": Decimal("0.00"),
                },
            )

            latest = (
                StockLedgerEntry.objects.filter(
                    item=item, warehouse=warehouse, is_cancelled=False
                )
                .order_by("-posting_date", "-posting_time", "-id")
                .first()
            )
            if latest is not None and (latest.posting_date, latest.posting_time) > (
                date_to_use,
                time_to_use,
            ):
                raise ValidationError(
                    _(
                        "Cannot reconcile '%(code)s' in '%(warehouse)s' as of "
                        "%(date)s %(time)s: stock moved "
                        "later (%(latest_date)s "
                        "%(latest_time)s). "
                        "Reconcile at or after the latest movement."
                    )
                    % {
                        "code": item.code,
                        "warehouse": warehouse.name,
                        "date": date_to_use,
                        "time": time_to_use.strftime("%H:%M:%S"),
                        "latest_date": latest.posting_date,
                        "latest_time": latest.posting_time.strftime("%H:%M:%S"),
                    }
                )

            cur_qty, cur_value = position(item, warehouse, batch)
            target = resolve_reconciliation_target(
                cur_qty, cur_value, row.get("qty"), row.get("valuation_rate")
            )
            if target["dq"] == 0 and target["dv"] == 0:
                continue

            new_qty = quantize_qty(bin_obj.actual_qty + target["dq"])
            new_value = quantize_value(bin_obj.stock_value + target["dv"])
            bin_obj.actual_qty = new_qty
            bin_obj.stock_value = new_value
            bin_obj.valuation_rate = (
                quantize_rate(new_value / new_qty)
                if new_qty > 0
                else Decimal("0.000000")
            )
            bin_obj.save(
                update_fields=[
                    "actual_qty",
                    "valuation_rate",
                    "stock_value",
                    "modified_at",
                ]
            )
            created.append(
                StockLedgerEntry.objects.create(
                    posting_date=date_to_use,
                    posting_time=time_to_use,
                    item=item,
                    warehouse=warehouse,
                    qty=target["dq"],
                    valuation_rate=target["rate"],
                    stock_value_difference=target["dv"],
                    voucher_type=voucher_type,
                    voucher_no=voucher_no,
                    is_cancelled=False,
                    batch=batch,
                )
            )
    return created


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

            bin_obj, _created = Bin.objects.select_for_update().get_or_create(
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
                    _(
                        "Cannot cancel: reversing this entry would leave negative stock for "
                        "Item '%(code)s' in Warehouse '%(warehouse)s' "
                        "(available %(available)s, reversal %(reversal)s). "
                        "Cancel the later transactions that consumed this stock first."
                    )
                    % {
                        "code": orig.item.code,
                        "warehouse": orig.warehouse.name,
                        "available": bin_obj.actual_qty,
                        "reversal": rev_qty,
                    }
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
                        _(
                            "Cannot cancel: reversing this entry would leave negative stock "
                            "in batch '%(batch_no)s' of Item '%(code)s' "
                            "in Warehouse '%(warehouse)s' (short by %(short)s). "
                            "Cancel the later transactions that consumed this batch first."
                        )
                        % {
                            "batch_no": orig.batch.batch_no,
                            "code": orig.item.code,
                            "warehouse": orig.warehouse.name,
                            "short": -left,
                        }
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
            bin_obj, _created = Bin.objects.select_for_update().get_or_create(
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
