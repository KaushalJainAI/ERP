"""Stock Stages Report: inventory quantities by warehouse stage and critical level."""

from decimal import Decimal
from typing import Any, Dict, List

from core.models import Company
from stock.models import Bin, Item


def resolve_company(company: Any) -> Company:
    if isinstance(company, Company):
        return company
    if isinstance(company, int):
        return Company.objects.get(pk=company)
    if isinstance(company, str):
        if company.isdigit():
            return Company.objects.get(pk=int(company))
        return Company.objects.get(name=company)
    raise ValueError(f"Invalid company: {company}")


def stock_stages_report(company: Any) -> List[Dict[str, Any]]:
    """
    Returns per stock item:
    - item id, name, uom, critical_stock
    - unprocessed_qty (from Bin at company.unprocessed_warehouse)
    - in_process_qty (from Bin at company.in_process_warehouse)
    - ready_qty (from Bin at company.ready_warehouse)
    - total_qty (sum across warehouses for the company)
    - is_critical (ready_qty <= critical_stock when critical_stock > 0)
    Sorted with critical items first, then alphabetical by name.
    """
    company_obj = resolve_company(company)
    unprocessed_wh_id = company_obj.unprocessed_warehouse_id
    in_process_wh_id = company_obj.in_process_warehouse_id
    ready_wh_id = company_obj.ready_warehouse_id

    items = (
        Item.objects.filter(is_stock_item=True)
        .select_related("stock_uom")
        .order_by("name")
    )

    # Serve this from Bin only
    bins = Bin.objects.filter(warehouse__company=company_obj).values(
        "item_id", "warehouse_id", "actual_qty"
    )

    bin_map: Dict[tuple, Decimal] = {}
    item_totals: Dict[int, Decimal] = {}

    for b in bins:
        item_id = b["item_id"]
        wh_id = b["warehouse_id"]
        qty = b["actual_qty"] or Decimal("0.000")
        bin_map[(item_id, wh_id)] = bin_map.get((item_id, wh_id), Decimal("0.000")) + qty
        item_totals[item_id] = item_totals.get(item_id, Decimal("0.000")) + qty

    rows: List[Dict[str, Any]] = []
    for item in items:
        unprocessed_qty = (
            bin_map.get((item.id, unprocessed_wh_id), Decimal("0.000"))
            if unprocessed_wh_id
            else Decimal("0.000")
        )
        in_process_qty = (
            bin_map.get((item.id, in_process_wh_id), Decimal("0.000"))
            if in_process_wh_id
            else Decimal("0.000")
        )
        ready_qty = (
            bin_map.get((item.id, ready_wh_id), Decimal("0.000"))
            if ready_wh_id
            else Decimal("0.000")
        )
        total_qty = item_totals.get(item.id, Decimal("0.000"))

        crit_stock = item.critical_stock or Decimal("0.000")
        is_critical = bool(crit_stock > Decimal("0.000") and ready_qty <= crit_stock)

        rows.append(
            {
                "id": item.id,
                "item_id": item.id,
                "item_code": item.code,
                "name": item.name,
                "item_name": item.name,
                "uom": item.stock_uom.name if item.stock_uom else "",
                "critical_stock": crit_stock,
                "unprocessed_qty": unprocessed_qty,
                "in_process_qty": in_process_qty,
                "ready_qty": ready_qty,
                "total_qty": total_qty,
                "is_critical": is_critical,
                "status": "Critical" if is_critical else "Normal",
            }
        )

    # Critical items first, then alphabetical by item name
    rows.sort(key=lambda r: (not r["is_critical"], (r["name"] or "").lower()))
    return rows
