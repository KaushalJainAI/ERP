import datetime
from decimal import Decimal
from typing import Dict, List, Any
from django.core.exceptions import ValidationError
from django.db import transaction
from stock.models import Item, Warehouse, StockLedgerEntry, Bin


def quantize_qty(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.000")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.001"))


def quantize_rate(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.000000")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.000001"))


def quantize_value(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.00")
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(Decimal("0.01"))


def post(
    voucher: Any,
    sle_entries: List[Dict[str, Any]],
    posting_date=None,
    posting_time=None,
) -> List[StockLedgerEntry]:
    """
    Atomically posts StockLedgerEntry rows and updates derived Bin balances.
    """
    if not sle_entries:
        raise ValidationError("Cannot post empty stock ledger entries.")

    date_to_use = posting_date or getattr(voucher, "posting_date", None) or datetime.date.today()
    time_to_use = posting_time or getattr(voucher, "posting_time", None) or datetime.datetime.now().time()

    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))

    created_sles: List[StockLedgerEntry] = []

    with transaction.atomic():
        for item_data in sle_entries:
            item = item_data["item"]
            if isinstance(item, (int, str)):
                item = Item.objects.get(pk=item)

            warehouse = item_data["warehouse"]
            if isinstance(warehouse, (int, str)):
                warehouse = Warehouse.objects.get(pk=warehouse)

            if warehouse.is_group:
                raise ValidationError(f"Stock transaction not allowed on group warehouse '{warehouse.name}'.")

            qty = quantize_qty(item_data["qty"])
            if qty == 0:
                continue

            rate = quantize_rate(item_data.get("valuation_rate", 0))

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

            # Valuation math: Moving Average & FIFO value delta
            new_qty = quantize_qty(bin_obj.actual_qty + qty)

            if qty > 0:
                # Stock In
                incoming_value = quantize_value(qty * rate)
                new_stock_value = quantize_value(bin_obj.stock_value + incoming_value)
                new_valuation_rate = (
                    quantize_rate(new_stock_value / new_qty) if new_qty > 0 else Decimal("0.000000")
                )
                stock_value_diff = incoming_value
            else:
                # Stock Out
                if bin_obj.actual_qty < abs(qty):
                    raise ValidationError(
                        f"Insufficient stock for Item '{item.code}' in Warehouse '{warehouse.name}'. "
                        f"Available: {bin_obj.actual_qty}, Required: {abs(qty)}"
                    )
                outgoing_rate = bin_obj.valuation_rate if bin_obj.valuation_rate > 0 else rate
                outgoing_value = quantize_value(abs(qty) * outgoing_rate)
                new_stock_value = quantize_value(bin_obj.stock_value - outgoing_value)
                new_valuation_rate = (
                    quantize_rate(new_stock_value / new_qty) if new_qty > 0 else Decimal("0.000000")
                )
                stock_value_diff = quantize_value(-outgoing_value)

            # Update derived Bin
            bin_obj.actual_qty = new_qty
            bin_obj.valuation_rate = new_valuation_rate
            bin_obj.stock_value = new_stock_value
            bin_obj.save(update_fields=["actual_qty", "valuation_rate", "stock_value", "modified_at"])

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
            new_value = quantize_value(bin_obj.stock_value + rev_diff)
            new_rate = quantize_rate(new_value / new_qty) if new_qty > 0 else Decimal("0.000000")

            bin_obj.actual_qty = new_qty
            bin_obj.stock_value = new_value
            bin_obj.valuation_rate = new_rate
            bin_obj.save(update_fields=["actual_qty", "valuation_rate", "stock_value", "modified_at"])

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

        sle_qs = StockLedgerEntry.objects.filter(is_cancelled=False).order_by("posting_date", "posting_time", "id")
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
            bin_obj.stock_value = quantize_value(bin_obj.stock_value + sle.stock_value_difference)
            bin_obj.valuation_rate = (
                quantize_rate(bin_obj.stock_value / bin_obj.actual_qty) if bin_obj.actual_qty > 0 else Decimal("0.000000")
            )
            bin_obj.save()

        return True
