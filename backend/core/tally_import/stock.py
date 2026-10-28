"""Stock masters (UOM, item groups, items, warehouse) and the FY 2026-27 stock
ledger (opening receipt + one Material Receipt / Issue per Tally voucher).

Only 2026-27 is posted to the stock ledger: Tally merged items in 2019-20 and
the older history stays in the analytics DB. ``StockLedgerEntry`` / ``Bin`` are
written only by ``StockEntry.submit()`` -> ``stock.services.ledger.post``.
"""

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List

from core.tally_import.masters import (
    clean_name,
    hsn_from_xml,
    item_gst_flags,
    normalise_hsn,
    normalise_uom,
)

WAREHOUSE_GROUP = "All Warehouses"
WAREHOUSE_NAME = "Main Location"
STOCK_FY = "2026-27"

_Q3 = Decimal("0.001")
_Q6 = Decimal("0.000001")
_Z3 = Decimal("0.000")


def q3(v) -> Decimal:
    return Decimal(str(v)).quantize(_Q3, rounding=ROUND_HALF_UP)


def q6(v) -> Decimal:
    return Decimal(str(v)).quantize(_Q6, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Pure: negative-stock simulation
# --------------------------------------------------------------------------


def simulate_negative_items(openings: Dict[str, Decimal], events: List[tuple]):
    """Find items whose running balance ever goes negative.

    ``openings``: item -> opening qty (may be negative).
    ``events``: ordered ``[(item, signed_qty)]`` exactly in posting order.
    Returns ``{item: min_balance}`` for items that go negative (opening included).
    """
    bal = {k: q3(v) for k, v in openings.items()}
    low = {k: v for k, v in bal.items() if v < 0}
    for item, qty in events:
        b = bal.get(item, _Z3) + q3(qty)
        bal[item] = b
        if b < 0 and b < low.get(item, _Z3):
            low[item] = b
    return low


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------


class StockMasters:
    def __init__(self):
        self.uom_map: Dict[str, str] = {}  # tally unit -> ERP UOM
        self.items: Dict[str, object] = {}  # tally item name -> Item
        self.item_groups: Dict[str, object] = {}
        self.warehouse = None
        self.notes: Dict[str, object] = {}


def build_stock_masters(src, years, company, log=print) -> StockMasters:
    from core.models import FiscalYear  # noqa: F401  (ensures apps loaded)
    from stock.models import UOM, Item, ItemGroup, ValuationMethod, Warehouse

    sm = StockMasters()
    latest_cy = years[-1].cy
    fy_of = {y.cy: y.fy for y in years}

    # ---- UOMs ----
    unit_names = {u["name"] for u in src.units()}
    for y in years:
        for it in src.stock_items(y.cy):
            unit_names.add(it["baseunits"] or "")
    for u in sorted(unit_names):
        sm.uom_map[u] = normalise_uom(u)
    uoms = {}
    for erp_name in sorted(set(sm.uom_map.values())):
        uoms[erp_name] = UOM.objects.get_or_create(name=erp_name)[0]

    # ---- item groups ----
    latest_sg: Dict[str, str] = {}
    for y in years:
        for g in src.stock_groups(y.cy):
            latest_sg[g["name"]] = g["parent"]
    root = ItemGroup.objects.get_or_create(
        name="All Item Groups", defaults={"is_group": True}
    )[0]
    has_children = {p for p in latest_sg.values() if p in latest_sg}

    def depth(n):
        d, seen = 0, set()
        while latest_sg.get(n) in latest_sg and n not in seen:
            seen.add(n)
            n = latest_sg[n]
            d += 1
        return d

    for g in sorted(latest_sg, key=lambda x: (depth(x), x)):
        parent = sm.item_groups.get(latest_sg[g], root)
        name = clean_name(g)
        obj = ItemGroup.objects.filter(name=name, parent=parent).first()
        if obj is None:
            obj = ItemGroup.objects.create(
                name=name, parent=parent, is_group=g in has_children
            )
        sm.item_groups[g] = obj

    # ---- GST info (HSN from XML text, taxability) ----
    hsn_by_item: Dict[str, str] = {}
    hsn_bad: List[tuple] = []
    rows = src.stock_hsn_xml()
    rows.sort(key=lambda r: fy_of.get(r["company_year"], ""))
    for r in rows:
        raw = hsn_from_xml(r["raw_xml"])
        if raw is None:
            continue
        norm = normalise_hsn(raw)
        if norm is None:
            hsn_bad.append((r["name"], raw))
            continue
        hsn_by_item[r["name"]] = norm  # later years overwrite earlier
    tax_by_item: Dict[str, tuple] = {}
    trows = src.stock_taxability()
    trows.sort(key=lambda r: fy_of.get(r["company_year"], ""))
    for r in trows:
        tax_by_item[r["name"]] = (r["taxability"], r["isnongstgoods"])

    # ---- items (union of all years, latest year's attributes) ----
    latest_row: Dict[str, dict] = {}
    for y in years:
        for it in src.stock_items(y.cy):
            latest_row[it["name"]] = it
    in_latest = {it["name"] for it in src.stock_items(latest_cy)}
    truncated, collisions = [], []
    seen_codes = set()
    for name in sorted(latest_row):
        row = latest_row[name]
        code = name.strip()
        if len(code) > 140:
            truncated.append(name)
            code = code[:140]
        if code in seen_codes:
            collisions.append(name)
            continue
        seen_codes.add(code)
        parent = row["parent"]
        group = sm.item_groups.get(parent, root) if parent != "Primary" else root
        uom = uoms[sm.uom_map[row["baseunits"] or ""]]
        nil_exempt, non_gst = item_gst_flags(*tax_by_item.get(name, (None, None)))
        item, _ = Item.objects.get_or_create(
            code=code,
            defaults={
                "name": clean_name(name),
                "item_group": group,
                "stock_uom": uom,
                "is_stock_item": True,
                "valuation_method": ValuationMethod.MOVING_AVERAGE,
                "is_active": name in in_latest,
                "gst_hsn_code": hsn_by_item.get(name, ""),
                "is_nil_exempt": nil_exempt,
                "is_non_gst": non_gst,
            },
        )
        sm.items[name] = item
    sm.notes.update(
        truncated=truncated,
        collisions=collisions,
        hsn_set=sum(1 for n in sm.items if n in hsn_by_item),
        hsn_invalid=hsn_bad,
        inactive=sum(1 for n in latest_row if n not in in_latest),
        nil_exempt=sum(
            1 for n in sm.items if item_gst_flags(*tax_by_item.get(n, (None, None)))[0]
        ),
    )

    # ---- warehouse ----
    wg = Warehouse.objects.get_or_create(
        company=company, name=WAREHOUSE_GROUP, defaults={"is_group": True}
    )[0]
    sm.warehouse = Warehouse.objects.get_or_create(
        company=company, name=WAREHOUSE_NAME, defaults={"parent": wg, "is_group": False}
    )[0]
    return sm


# --------------------------------------------------------------------------
# FY 2026-27 stock ledger
# --------------------------------------------------------------------------


def _post_entry(company, name, etype, date, time, wh, lines):
    """lines: [(Item, qty, rate)] -> submitted StockEntry."""
    from django.db.models import Prefetch, prefetch_related_objects

    from stock.models import StockEntry, StockEntryItem, StockEntryType

    se = StockEntry(
        name=name,
        stock_entry_type=etype,
        posting_date=date,
        posting_time=time,
        company=company,
    )
    se.save()
    is_receipt = etype == StockEntryType.MATERIAL_RECEIPT
    StockEntryItem.objects.bulk_create(
        [
            StockEntryItem(
                stock_entry=se,
                item=item,
                target_warehouse=wh if is_receipt else None,
                source_warehouse=None if is_receipt else wh,
                qty=qty,
                valuation_rate=rate,
            )
            for item, qty, rate in lines
        ]
    )
    prefetch_related_objects(
        [se],
        Prefetch(
            "items",
            queryset=StockEntryItem.objects.select_related(
                "item", "source_warehouse", "target_warehouse"
            ).order_by("pk"),
        ),
    )
    se.submit()
    return se


def import_stock_year(src, y, sm: StockMasters, company, log=print) -> dict:
    """Post the opening receipt and every inventory voucher of one year."""
    from stock.models import Bin, StockEntryType

    cy = y.cy
    prefix = f"TLY-{y.fy[2:4]}{y.fy[-2:]}"  # 2026-27 -> TLY-2627
    items_rows = {r["name"]: r for r in src.stock_items(cy)}
    openings: Dict[str, Decimal] = {}
    for n, r in items_rows.items():
        if r["openingbalance_num"] is not None:
            openings[n] = q3(r["openingbalance_num"])

    vouchers = [v for v in src.vouchers(cy) if not v["iscancelled"]]
    vorder = {v["row_key"]: i for i, v in enumerate(vouchers)}
    vinfo = {v["row_key"]: v for v in vouchers}
    by_voucher: Dict[str, List[dict]] = defaultdict(list)
    skipped_lines = 0
    for ln in src.stock_lines(cy):
        if ln["voucher_key"] not in vinfo:
            continue  # line of a cancelled voucher
        qty = ln["actualqty_num"]
        if qty is None or Decimal(qty) == 0:
            skipped_lines += 1
            continue
        by_voucher[ln["voucher_key"]].append(ln)

    # posting plan: per date, receipts (by voucher order) then issues
    plan: List[tuple] = []  # (date, kind, voucher_key, [lines])
    dates = sorted({vinfo[k]["date"] for k in by_voucher})
    per_date: Dict[datetime.date, List[str]] = defaultdict(list)
    for k in by_voucher:
        per_date[vinfo[k]["date"]].append(k)
    for d in dates:
        keys = sorted(per_date[d], key=lambda k: vorder[k])
        for kind, flag in (("receipt", True), ("issue", False)):
            for k in keys:
                ls = [
                    ln_
                    for ln_ in by_voucher[k]
                    if bool(ln_["isdeemedpositive"]) is flag
                ]
                if ls:
                    plan.append((d, kind, k, ls))

    events = []
    for d, kind, k, ls in plan:
        for ln_ in ls:
            q = q3(ln_["actualqty_num"])
            events.append((ln_["stockitemname"], q if kind == "receipt" else -q))
    negative = simulate_negative_items(openings, events)

    # report excluded items
    excluded = []
    move_count = defaultdict(int)
    for item, _q in events:
        move_count[item] += 1
    for name, low in sorted(negative.items()):
        r = items_rows.get(name, {})
        excluded.append(
            {
                "item": name,
                "opening_qty": str(openings.get(name, _Z3)),
                "min_balance": str(low),
                "movements": move_count.get(name, 0),
                "tally_closing_qty": str(r.get("closingbalance_num")),
            }
        )

    wh = sm.warehouse
    stats = {
        "fy": y.fy,
        "voucher_count_with_stock": len(by_voucher),
        "lines_without_qty": skipped_lines,
        "excluded_items": excluded,
        "entries": 0,
        "receipts": 0,
        "issues": 0,
        "lines_posted": 0,
        "lines_excluded": 0,
        "opening_items": 0,
    }
    midnight = datetime.time(0, 0, 0)
    morning = datetime.time(6, 0, 0)
    evening = datetime.time(18, 0, 0)

    # opening
    op_lines = []
    for n in sorted(openings):
        qty = openings[n]
        if qty <= 0 or n in negative or n not in sm.items:
            continue
        r = items_rows[n]
        rate = r["openingrate_num"]
        if rate is None or Decimal(rate) <= 0:
            val = abs(Decimal(r["openingvalue"] or 0))
            rate = val / qty
        op_lines.append((sm.items[n], qty, q6(rate)))
    if op_lines:
        _post_entry(
            company,
            f"{prefix}-STKOPEN",
            StockEntryType.MATERIAL_RECEIPT,
            datetime.date(int(y.fy[:4]), 4, 1),
            midnight,
            wh,
            op_lines,
        )
        stats["opening_items"] = len(op_lines)
        stats["entries"] += 1

    seq = 0
    for d, kind, k, ls in plan:
        lines = []
        for ln_ in ls:
            n = ln_["stockitemname"]
            if n in negative or n not in sm.items:
                stats["lines_excluded"] += 1
                continue
            qty = q3(ln_["actualqty_num"])
            amount = abs(Decimal(ln_["amount"] or 0))
            rate = q6(amount / qty) if qty else Decimal("0")
            lines.append((sm.items[n], qty, rate))
        if not lines:
            continue
        seq += 1
        is_receipt = kind == "receipt"
        _post_entry(
            company,
            f"{prefix}-STK-{seq:06d}",
            (
                StockEntryType.MATERIAL_RECEIPT
                if is_receipt
                else StockEntryType.MATERIAL_ISSUE
            ),
            d,
            morning if is_receipt else evening,
            wh,
            lines,
        )
        stats["entries"] += 1
        stats["receipts" if is_receipt else "issues"] += 1
        stats["lines_posted"] += len(lines)
        if seq % 500 == 0:
            log(f"  stock entries: {seq}")

    # ERP vs Tally closing qty
    bins = {
        b.item_id: b for b in Bin.objects.filter(warehouse=wh).select_related("item")
    }
    cmp_rows = []
    for n, r in sorted(items_rows.items()):
        item = sm.items.get(n)
        b = bins.get(item.pk) if item else None
        cmp_rows.append(
            {
                "item": n,
                "erp_qty": str(b.actual_qty) if b else "0.000",
                "tally_qty": (
                    str(r["closingbalance_num"])
                    if r["closingbalance_num"] is not None
                    else None
                ),
                "excluded": n in negative,
            }
        )
    stats["bin_compare"] = cmp_rows
    return stats
