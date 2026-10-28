"""Batch services: balance (derived from the ledger), valuation rate, FEFO,
trace, report rows, the ledger-leg guard and the batch integrity checks.

There is deliberately no ``BatchBin``: a batch balance is always
``SUM(qty)`` over the batch's non-cancelled ``StockLedgerEntry`` rows
(see docs/manufacturing_design.md section 1.3). ``batch_balance`` /
``batch_position`` are the single read seam; if it is ever too slow, a
projection can replace them without touching callers.

Concurrency: ``ledger.post()`` takes ``select_for_update`` on the (item,
warehouse) ``Bin`` before it calls ``validate_leg``, so every posting that
touches a batch in that warehouse is serialised and the balance check here is
race-free.
"""

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from core.models import NamingSeries
from stock.models import Batch, Bin, Item, StockLedgerEntry

ZERO_QTY = Decimal("0.000")
ZERO_VALUE = Decimal("0.00")
#: ``NamingSeries.prefix`` is a ``CharField(max_length=40)``.
_SERIES_PREFIX_MAX = 40


def _pk(obj: Any):
    return obj.pk if hasattr(obj, "pk") else obj


def _q3(val: Decimal) -> Decimal:
    return Decimal(val).quantize(Decimal("0.001"))


def _q2(val: Decimal) -> Decimal:
    return Decimal(val).quantize(Decimal("0.01"))


def _q6(val: Decimal) -> Decimal:
    return Decimal(val).quantize(Decimal("0.000001"))


# --------------------------------------------------------------------------- #
# Balance and valuation (derived from the ledger)
# --------------------------------------------------------------------------- #


def batch_position(
    item, warehouse, batch, as_of: Optional[datetime.date] = None
) -> Tuple[Decimal, Decimal]:
    """``(qty, stock_value)`` of one batch in one warehouse (non-cancelled SLE)."""
    qs = StockLedgerEntry.objects.filter(
        item_id=_pk(item),
        warehouse_id=_pk(warehouse),
        batch_id=_pk(batch),
        is_cancelled=False,
    )
    if as_of is not None:
        qs = qs.filter(posting_date__lte=as_of)
    agg = qs.aggregate(q=Sum("qty"), v=Sum("stock_value_difference"))
    return (agg["q"] or ZERO_QTY), (agg["v"] or ZERO_VALUE)


def batch_balance(
    item, warehouse, batch, as_of: Optional[datetime.date] = None
) -> Decimal:
    """Quantity of ``batch`` in ``warehouse`` (optionally as of a date)."""
    return batch_position(item, warehouse, batch, as_of)[0]


def batch_rate(item, warehouse, batch) -> Optional[Decimal]:
    """The batch's own moving average in this warehouse (``value/qty``, 6 dp),
    or ``None`` when the batch has no positive quantity or no positive value (the
    caller then falls back to the Bin rate / the passed rate)."""
    qty, value = batch_position(item, warehouse, batch)
    if qty > 0 and value > 0:
        return _q6(value / qty)
    return None


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #


def resolve_dates(
    item: Item,
    manufacturing_date: Optional[datetime.date],
    expiry_date: Optional[datetime.date],
    default_manufacturing_date: Optional[datetime.date] = None,
) -> Tuple[Optional[datetime.date], Optional[datetime.date]]:
    """Fill the manufacturing / expiry dates of a new batch (ERPNext's
    ``set_expiry_date``): expiry = manufacturing + ``shelf_life_in_days`` when the
    item tracks expiry and none is given. Raises when an expiry-tracked batch
    would have no expiry, or the dates are inconsistent."""
    manufacturing_date = manufacturing_date or default_manufacturing_date
    if expiry_date and not item.has_expiry_date:
        raise ValidationError(
            _("Item '%(code)s' does not track expiry; remove the expiry date.")
            % {"code": item.code}
        )
    if item.has_expiry_date and not expiry_date:
        if manufacturing_date is None or not item.shelf_life_in_days:
            raise ValidationError(
                _(
                    "Item '%(code)s' tracks expiry: enter an expiry date, or a "
                    "manufacturing date and set the item's shelf life."
                )
                % {"code": item.code}
            )
        expiry_date = manufacturing_date + datetime.timedelta(
            days=item.shelf_life_in_days
        )
    if expiry_date and manufacturing_date and expiry_date < manufacturing_date:
        raise ValidationError(_("Expiry date cannot be before the manufacturing date."))
    return manufacturing_date, expiry_date


def next_batch_no(item: Item, on_date: Optional[datetime.date] = None) -> str:
    """Auto number ``ITEMCODE-YYMMDD-NN`` (skipping any manually taken number)."""
    on_date = on_date or datetime.date.today()
    prefix = f"{item.code}-{on_date:%y%m%d}-"
    if len(prefix) > _SERIES_PREFIX_MAX:
        raise ValidationError(
            _(
                "Item code '%(code)s' is too long for automatic batch numbering; "
                "enter the batch number manually."
            )
            % {"code": item.code}
        )
    while True:
        candidate = NamingSeries.next(prefix, padding=2)
        if len(candidate) > Batch._meta.get_field("batch_no").max_length:
            raise ValidationError(
                _("Generated batch number '%(no)s' is too long.") % {"no": candidate}
            )
        if not Batch.objects.filter(batch_no=candidate).exists():
            return candidate


def create_batch(
    item: Item,
    *,
    batch_no: Optional[str] = None,
    manufacturing_date: Optional[datetime.date] = None,
    expiry_date: Optional[datetime.date] = None,
    supplier_batch_no: str = "",
    default_manufacturing_date: Optional[datetime.date] = None,
    numbering_date: Optional[datetime.date] = None,
    reference_doctype: str = "",
    reference_name: str = "",
    description: str = "",
) -> Batch:
    """Create a batch (manual ``batch_no`` allowed, else auto-numbered)."""
    if not item.has_batch_no:
        raise ValidationError(
            _("Item '%(code)s' is not batch-tracked.") % {"code": item.code}
        )
    manufacturing_date, expiry_date = resolve_dates(
        item, manufacturing_date, expiry_date, default_manufacturing_date
    )
    if batch_no:
        if Batch.objects.filter(batch_no=batch_no).exists():
            raise ValidationError(
                _("Batch '%(no)s' already exists.") % {"no": batch_no}
            )
    else:
        batch_no = next_batch_no(item, numbering_date)
    return Batch.objects.create(
        batch_no=batch_no,
        item=item,
        manufacturing_date=manufacturing_date,
        expiry_date=expiry_date,
        supplier_batch_no=supplier_batch_no or "",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        description=description,
    )


# --------------------------------------------------------------------------- #
# FEFO
# --------------------------------------------------------------------------- #


def annotate_available(qs, warehouse=None):
    """Annotate a ``Batch`` queryset with ``available_qty`` (sum of non-cancelled
    SLE qty, in one warehouse or across all)."""
    sle = StockLedgerEntry.objects.filter(batch=OuterRef("pk"), is_cancelled=False)
    if warehouse is not None:
        sle = sle.filter(warehouse_id=_pk(warehouse))
    total = sle.order_by().values("batch").annotate(s=Sum("qty")).values("s")
    return qs.annotate(
        available_qty=Coalesce(
            Subquery(total, output_field=DecimalField(max_digits=18, decimal_places=3)),
            Decimal("0.000"),
            output_field=DecimalField(max_digits=18, decimal_places=3),
        )
    )


def fefo_order(qs):
    """FEFO ordering: earliest expiry first, no-expiry last, then older
    manufacturing date, then id."""
    return qs.order_by(
        F("expiry_date").asc(nulls_last=True),
        F("manufacturing_date").asc(nulls_last=True),
        "id",
    )


def fefo_candidates(item, warehouse, posting_date: Optional[datetime.date] = None):
    """Usable batches of ``item`` in ``warehouse`` in FEFO order, with
    ``available_qty``. Excludes disabled, expired (before ``posting_date``) and
    empty batches."""
    posting_date = posting_date or datetime.date.today()
    qs = Batch.objects.filter(item_id=_pk(item), is_disabled=False).filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gte=posting_date)
    )
    qs = annotate_available(qs, warehouse).filter(available_qty__gt=0)
    return fefo_order(qs)


def fefo(
    item, warehouse, qty, posting_date: Optional[datetime.date] = None
) -> Dict[str, Any]:
    """Suggest which batches to issue ``qty`` from (read-only; nothing is reserved)."""
    qty = _q3(Decimal(str(qty)))
    posting_date = posting_date or datetime.date.today()
    remaining = qty
    allocations: List[Dict[str, Any]] = []
    for b in fefo_candidates(item, warehouse, posting_date):
        if remaining <= 0:
            break
        available = _q3(b.available_qty)
        take = min(remaining, available)
        allocations.append(
            {
                "batch": b.pk,
                "batch_no": b.batch_no,
                "expiry_date": b.expiry_date,
                "available_qty": available,
                "allocate_qty": take,
            }
        )
        remaining -= take
    allocated = qty - remaining
    return {
        "item": _pk(item),
        "warehouse": _pk(warehouse),
        "requested_qty": qty,
        "allocated_qty": allocated,
        "shortfall": remaining,
        "allocations": allocations,
    }


def batch_required_message(item, warehouse, qty, posting_date) -> str:
    """'Batch required for Item X; FEFO suggests B1 (400.000), ...'."""
    msg = _("Batch required for Item '%(code)s'.") % {"code": item.code}
    if warehouse is not None and qty:
        plan = fefo(item, warehouse, abs(Decimal(str(qty))), posting_date)
        if plan["allocations"]:
            picks = ", ".join(
                f"{a['batch_no']} ({a['allocate_qty']})" for a in plan["allocations"]
            )
            msg += " " + _("FEFO suggests %(picks)s.") % {"picks": picks}
    return msg


# --------------------------------------------------------------------------- #
# Ledger-leg guard
# --------------------------------------------------------------------------- #


def validate_leg(
    item: Item,
    batch: Optional[Batch],
    qty: Decimal,
    warehouse,
    posting_date: datetime.date,
    allow_expired: bool = False,
) -> Optional[Tuple[Decimal, Decimal]]:
    """Assert the batch invariants of one SLE leg (called by ``ledger.post`` after
    the Bin lock). For a stock-out leg returns the batch's current
    ``(qty, value)`` so the caller can cost the leg without a second query."""
    if item.has_batch_no and batch is None:
        raise ValidationError(
            batch_required_message(
                item, warehouse, qty if qty < 0 else None, posting_date
            )
        )
    if not item.has_batch_no and batch is not None:
        raise ValidationError(
            _("Item '%(code)s' is not batch-tracked but a batch was given.")
            % {"code": item.code}
        )
    if batch is None:
        return None
    if batch.item_id != item.pk:
        raise ValidationError(
            _("Batch '%(batch)s' belongs to a different item than '%(code)s'.")
            % {"batch": batch.batch_no, "code": item.code}
        )
    if batch.is_disabled:
        raise ValidationError(
            _("Batch '%(batch)s' is disabled.") % {"batch": batch.batch_no}
        )
    if qty >= 0:
        return None
    if batch.expiry_date and batch.expiry_date < posting_date and not allow_expired:
        raise ValidationError(
            _("Batch '%(batch)s' expired on %(date)s.")
            % {"batch": batch.batch_no, "date": batch.expiry_date.isoformat()}
        )
    bal_qty, bal_value = batch_position(item, warehouse, batch)
    if bal_qty < abs(qty):
        raise ValidationError(
            _(
                "Insufficient stock in batch '%(batch)s' of item '%(code)s': "
                "available %(avail)s, required %(req)s."
            )
            % {
                "batch": batch.batch_no,
                "code": item.code,
                "avail": bal_qty,
                "req": abs(qty),
            }
        )
    return bal_qty, bal_value


# --------------------------------------------------------------------------- #
# Balances per warehouse, trace
# --------------------------------------------------------------------------- #


def balances(batch: Batch) -> Dict[str, Any]:
    """Per-warehouse balance of one batch."""
    rows = (
        StockLedgerEntry.objects.filter(batch=batch, is_cancelled=False)
        .values("warehouse_id", "warehouse__name")
        .annotate(q=Sum("qty"), v=Sum("stock_value_difference"))
        .order_by("warehouse__name")
    )
    out = []
    total_q, total_v = ZERO_QTY, ZERO_VALUE
    for r in rows:
        q, v = _q3(r["q"] or ZERO_QTY), r["v"] or ZERO_VALUE
        total_q += q
        total_v += v
        out.append(
            {
                "warehouse": r["warehouse_id"],
                "warehouse_name": r["warehouse__name"],
                "qty": q,
                "stock_value": _q2(v),
                "valuation_rate": _q6(v / q) if q > 0 else Decimal("0.000000"),
            }
        )
    return {
        "batch": batch.pk,
        "batch_no": batch.batch_no,
        "warehouses": out,
        "total_qty": _q3(total_q),
        "total_stock_value": _q2(total_v),
    }


def trace(batch: Batch) -> Dict[str, Any]:
    """Every SLE of the batch plus, per voucher, the *other* batches moved in the
    same voucher (what a Manufacture / Repack consumed and produced): a recall
    report."""
    entries = list(
        StockLedgerEntry.objects.filter(batch=batch)
        .select_related("warehouse")
        .order_by("posting_date", "posting_time", "id")
    )
    live_vouchers = {
        (e.voucher_type, e.voucher_no) for e in entries if not e.is_cancelled
    }
    siblings: List[Dict[str, Any]] = []
    if live_vouchers:
        others = (
            StockLedgerEntry.objects.filter(
                voucher_no__in={v[1] for v in live_vouchers},
                batch__isnull=False,
                is_cancelled=False,
            )
            .exclude(batch=batch)
            .select_related("batch", "item", "warehouse")
            .order_by("id")
        )
        for o in others:
            if (o.voucher_type, o.voucher_no) not in live_vouchers:
                continue
            siblings.append(
                {
                    "voucher_type": o.voucher_type,
                    "voucher_no": o.voucher_no,
                    "batch": o.batch_id,
                    "batch_no": o.batch.batch_no,
                    "item_code": o.item.code,
                    "warehouse_name": o.warehouse.name,
                    "qty": o.qty,
                    "direction": "in" if o.qty > 0 else "out",
                }
            )
    return {
        "batch": batch.pk,
        "batch_no": batch.batch_no,
        "item": batch.item_id,
        "item_code": batch.item.code,
        "entries": [
            {
                "id": e.pk,
                "posting_date": e.posting_date,
                "posting_time": e.posting_time,
                "warehouse": e.warehouse_id,
                "warehouse_name": e.warehouse.name,
                "qty": e.qty,
                "valuation_rate": e.valuation_rate,
                "stock_value_difference": e.stock_value_difference,
                "voucher_type": e.voucher_type,
                "voucher_no": e.voucher_no,
                "is_cancelled": e.is_cancelled,
            }
            for e in entries
        ],
        "siblings": siblings,
    }


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def _days_to_expiry(expiry, today) -> Optional[int]:
    return (expiry - today).days if expiry else None


def balance_rows(
    *,
    item=None,
    warehouse=None,
    batch=None,
    company=None,
    as_of: Optional[datetime.date] = None,
    include_zero: bool = False,
) -> List[Dict[str, Any]]:
    """One row per (item, warehouse, batch) with its balance and value."""
    today = as_of or datetime.date.today()
    qs = StockLedgerEntry.objects.filter(is_cancelled=False, batch__isnull=False)
    if as_of is not None:
        qs = qs.filter(posting_date__lte=as_of)
    if item:
        qs = qs.filter(item_id=item)
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    if batch:
        qs = qs.filter(batch_id=batch)
    if company:
        qs = qs.filter(warehouse__company_id=company)
    grouped = (
        qs.values(
            "item__code",
            "item__name",
            "warehouse__name",
            "batch_id",
            "batch__batch_no",
            "batch__manufacturing_date",
            "batch__expiry_date",
        )
        .annotate(q=Sum("qty"), v=Sum("stock_value_difference"))
        .order_by("item__code", "warehouse__name", "batch__batch_no")
    )
    rows = []
    for g in grouped:
        q, v = g["q"] or ZERO_QTY, g["v"] or ZERO_VALUE
        if q == 0 and not include_zero:
            continue
        rows.append(
            {
                "batch": g["batch_id"],
                "item_code": g["item__code"],
                "item_name": g["item__name"],
                "warehouse_name": g["warehouse__name"],
                "batch_no": g["batch__batch_no"],
                "manufacturing_date": g["batch__manufacturing_date"],
                "expiry_date": g["batch__expiry_date"],
                "days_to_expiry": _days_to_expiry(g["batch__expiry_date"], today),
                "balance_qty": _q3(q),
                "valuation_rate": _q6(v / q) if q > 0 else Decimal("0.000000"),
                "stock_value": _q2(v),
            }
        )
    return rows


def expiry_rows(
    *,
    company=None,
    warehouse=None,
    within_days: int = 60,
    include_expired: bool = True,
    today: Optional[datetime.date] = None,
) -> List[Dict[str, Any]]:
    """Batches holding stock whose expiry is within ``within_days`` (or already
    past, if ``include_expired``), soonest first."""
    today = today or datetime.date.today()
    horizon = today + datetime.timedelta(days=within_days)
    rows = []
    for r in balance_rows(company=company, warehouse=warehouse):
        expiry = r["expiry_date"]
        if expiry is None or r["balance_qty"] <= 0 or expiry > horizon:
            continue
        expired = expiry < today
        if expired and not include_expired:
            continue
        r = dict(r)
        r["days_to_expiry"] = (expiry - today).days
        r["status"] = "Expired" if expired else "Expiring"
        rows.append(r)
    rows.sort(key=lambda r: (r["expiry_date"], r["item_code"], r["batch_no"]))
    return rows


# --------------------------------------------------------------------------- #
# Integrity (wired into ``verify_integrity`` by the caller)
# --------------------------------------------------------------------------- #


def integrity_errors() -> List[str]:
    """Batch invariants that must always hold; each violation is one string."""
    errors: List[str] = []

    for sle in StockLedgerEntry.objects.filter(
        item__has_batch_no=True, batch__isnull=True
    ).select_related("item", "warehouse"):
        errors.append(
            f"Batch missing on SLE #{sle.pk} ({sle.voucher_type}:{sle.voucher_no}) "
            f"for batch-tracked item {sle.item.code} @ {sle.warehouse.name}"
        )
    for sle in StockLedgerEntry.objects.filter(
        item__has_batch_no=False, batch__isnull=False
    ).select_related("item"):
        errors.append(
            f"SLE #{sle.pk} ({sle.voucher_type}:{sle.voucher_no}) carries batch "
            f"{sle.batch.batch_no} but item {sle.item.code} is not batch-tracked"
        )
    for sle in (
        StockLedgerEntry.objects.filter(batch__isnull=False)
        .exclude(batch__item_id=F("item_id"))
        .select_related("item", "batch")
    ):
        errors.append(
            f"SLE #{sle.pk} ({sle.voucher_type}:{sle.voucher_no}) item "
            f"{sle.item.code} != batch {sle.batch.batch_no} item"
        )

    live = StockLedgerEntry.objects.filter(is_cancelled=False, batch__isnull=False)
    for r in (
        live.values("batch__batch_no", "item__code", "warehouse__name")
        .annotate(q=Sum("qty"))
        .filter(q__lt=0)
    ):
        errors.append(
            f"Negative batch balance {r['q']} for batch {r['batch__batch_no']} "
            f"({r['item__code']}) @ {r['warehouse__name']}"
        )

    # Sum of batches == Bin, for batch-tracked items.
    sums = {
        (r["item_id"], r["warehouse_id"]): (r["q"] or ZERO_QTY, r["v"] or ZERO_VALUE)
        for r in live.values("item_id", "warehouse_id").annotate(
            q=Sum("qty"), v=Sum("stock_value_difference")
        )
    }
    seen = set()
    for b in Bin.objects.filter(item__has_batch_no=True).select_related(
        "item", "warehouse"
    ):
        key = (b.item_id, b.warehouse_id)
        seen.add(key)
        q, v = sums.get(key, (ZERO_QTY, ZERO_VALUE))
        if b.actual_qty != _q3(q) or b.stock_value != _q2(v):
            errors.append(
                f"Batch totals for {b.item.code} @ {b.warehouse.name} "
                f"(qty {_q3(q)}, value {_q2(v)}) != Bin "
                f"(qty {b.actual_qty}, value {b.stock_value})"
            )
    for key, (q, v) in sums.items():
        if key not in seen and (q != 0 or v != 0):
            errors.append(
                f"Batch stock exists without a Bin for item #{key[0]} "
                f"warehouse #{key[1]} (qty {_q3(q)})"
            )

    for b in Batch.objects.filter(
        item__has_expiry_date=True, expiry_date__isnull=True
    ).select_related("item"):
        errors.append(
            f"Batch {b.batch_no} of expiry-tracked item {b.item.code} has no expiry date"
        )
    return errors


def integrity_warnings(today: Optional[datetime.date] = None) -> List[str]:
    """Non-fatal findings: stock still on hand in an expired batch."""
    expired = expiry_rows(within_days=0, include_expired=True, today=today)
    expired = [r for r in expired if r["status"] == "Expired"]
    if not expired:
        return []
    return [
        f"{len(expired)} expired batch/warehouse position(s) still hold stock "
        "(write off with a Material Issue)"
    ]
