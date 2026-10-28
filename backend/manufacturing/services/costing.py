"""Derived BOM costing (docs/manufacturing_design.md section 2.4).

Nothing is persisted: every rate and amount is computed on read, from the
company's Bin valuation or the item's standard rate, and multi-level cost rolls
up through ``BOMItem.bom_no``.  A stale BOM cost therefore cannot exist.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from manufacturing.models import BOM, RateBasis
from stock.models import Bin

ZERO = Decimal("0.00")
ZERO_QTY = Decimal("0.000")
ZERO_RATE = Decimal("0.000000")


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q3(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _q6(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _has_valuation(item, company) -> bool:
    agg = Bin.objects.filter(item=item, warehouse__company=company).aggregate(
        qty=Sum("actual_qty"), value=Sum("stock_value")
    )
    return (agg["qty"] or ZERO_QTY) > 0 and (agg["value"] or ZERO) > 0


def company_valuation_rate(item, company) -> Decimal:
    """Company-wide weighted moving-average rate of an item.

    ``Σ Bin.stock_value / Σ Bin.actual_qty`` over the company's warehouses,
    falling back to ``Item.standard_rate`` when there is no positive valuation.
    """
    agg = Bin.objects.filter(item=item, warehouse__company=company).aggregate(
        qty=Sum("actual_qty"), value=Sum("stock_value")
    )
    qty = agg["qty"] or ZERO_QTY
    value = agg["value"] or ZERO
    if qty > 0 and value > 0:
        return _q6(value / qty)
    return _q6(item.standard_rate or 0)


def row_rate(row, bom: BOM, stack=()) -> Decimal:
    """Unit rate of one component row on the BOM's rate basis."""
    if row.bom_no_id:
        return bom_unit_cost(row.bom_no, stack=stack + (bom.pk,))["unit_cost"]
    if bom.rate_basis == RateBasis.STANDARD_RATE:
        return _q6(row.item.standard_rate or 0)
    return company_valuation_rate(row.item, bom.company)


def bom_cost(bom: BOM, stack=()) -> Dict:
    """Components, raw material cost, scrap value and the unit cost of a BOM."""
    if bom.pk in stack:
        raise ValidationError(
            _("The BOM graph for '%(bom)s' contains a cycle.") % {"bom": bom.name}
        )

    component_rows: List[Dict] = []
    raw_material_cost = ZERO
    for row in bom.items.select_related("item", "bom_no").order_by("idx", "pk"):
        rate = row_rate(row, bom, stack)
        amount = _q2(Decimal(row.qty) * rate)
        raw_material_cost += amount
        if row.bom_no_id:
            source = "Sub BOM"
        elif bom.rate_basis == RateBasis.STANDARD_RATE or not _has_valuation(
            row.item, bom.company
        ):
            source = "Standard Rate"
        else:
            source = "Valuation Rate"
        component_rows.append(
            {
                "row": row.pk,
                "item": row.item_id,
                "item_code": row.item.code,
                "item_name": row.item.name,
                "stock_uom": row.item.stock_uom.name,
                "qty": _q3(row.qty),
                "rate": rate,
                "amount": amount,
                "source": source,
                "bom_no": row.bom_no_id,
            }
        )

    scrap_rows: List[Dict] = []
    scrap_value = ZERO
    for row in bom.scrap_items.select_related("item").order_by("idx", "pk"):
        amount = _q2(Decimal(row.qty) * Decimal(row.rate))
        scrap_value += amount
        scrap_rows.append(
            {
                "row": row.pk,
                "item": row.item_id,
                "item_code": row.item.code,
                "item_name": row.item.name,
                "qty": _q3(row.qty),
                "rate": _q6(row.rate),
                "amount": amount,
            }
        )

    loss = Decimal(bom.process_loss_percentage or 0)
    good_qty = Decimal(bom.quantity) * (Decimal(1) - loss / Decimal(100))
    net_cost = raw_material_cost - scrap_value
    unit_cost = _q6(net_cost / good_qty) if good_qty > 0 else ZERO_RATE
    return {
        "bom": bom.pk,
        "name": bom.name,
        "company": bom.company_id,
        "item": bom.item_id,
        "item_code": bom.item.code,
        "item_name": bom.item.name,
        "quantity": _q3(bom.quantity),
        "process_loss_percentage": loss,
        "good_qty": _q3(good_qty),
        "rate_basis": bom.rate_basis,
        "components": component_rows,
        "scrap_items": scrap_rows,
        "raw_material_cost": _q2(raw_material_cost),
        "scrap_value": _q2(scrap_value),
        "net_cost": _q2(net_cost),
        "unit_cost": unit_cost,
    }


def bom_unit_cost(bom: BOM, stack=()) -> Dict:
    return bom_cost(bom, stack=stack)


def explode(bom: BOM, multiplier: Decimal = Decimal(1), level: int = 0, stack=()) -> Dict:
    """Flattened multi-level tree, quantities per the top BOM's basis.

    A row that points at a sub-BOM is kept (``needs_sub_assembly`` true) and is
    followed by its own components scaled by ``row.qty / bom.quantity``.
    """
    if bom.pk in stack:
        raise ValidationError(
            _("The BOM graph for '%(bom)s' contains a cycle.") % {"bom": bom.name}
        )

    rows: List[Dict] = []
    for row in bom.items.select_related("item", "bom_no").order_by("idx", "pk"):
        cumulative = Decimal(multiplier) * Decimal(row.qty)
        rows.append(
            {
                "level": level,
                "item": row.item_id,
                "item_code": row.item.code,
                "item_name": row.item.name,
                "stock_uom": row.item.stock_uom.name,
                "qty": _q3(cumulative),
                "bom_no": row.bom_no_id,
                "bom_no_name": row.bom_no.name if row.bom_no_id else None,
                "needs_sub_assembly": bool(row.bom_no_id),
            }
        )
        if row.bom_no_id:
            factor = Decimal(multiplier) * Decimal(row.qty) / Decimal(bom.quantity)
            child = explode(
                row.bom_no, multiplier=factor, level=level + 1, stack=stack + (bom.pk,)
            )
            rows.extend(child["rows"])

    scrap_rows = [
        {
            "level": level,
            "item": row.item_id,
            "item_code": row.item.code,
            "item_name": row.item.name,
            "qty": _q3(Decimal(multiplier) * Decimal(row.qty)),
            "rate": _q6(row.rate),
        }
        for row in bom.scrap_items.select_related("item").order_by("idx", "pk")
    ]
    return {
        "bom": bom.pk,
        "name": bom.name,
        "item": bom.item_id,
        "item_code": bom.item.code,
        "quantity": _q3(bom.quantity),
        "rows": rows,
        "scrap_rows": scrap_rows,
    }


def last_actual_unit_cost(bom: BOM) -> Optional[Decimal]:
    """Unit cost of the latest submitted Manufacture entry for this BOM."""
    from stock.models import StockEntry, StockEntryItem, StockEntryType

    entry = (
        StockEntry.objects.filter(
            docstatus=1,
            stock_entry_type=StockEntryType.MANUFACTURE,
            bom_id=bom.pk,
        )
        .order_by("-posting_date", "-id")
        .first()
    )
    if entry is None:
        return None
    finished = StockEntryItem.objects.filter(stock_entry=entry, is_finished_item=True)
    qty = sum((r.qty for r in finished), Decimal("0.000"))
    value = sum((_q2(Decimal(r.qty) * Decimal(r.valuation_rate)) for r in finished), ZERO)
    if qty <= 0:
        return None
    return _q6(value / qty)
