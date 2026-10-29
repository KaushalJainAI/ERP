"""Stock Reconciliation: validation, snapshots, posting and the "fetch items" helper.

The ledger arithmetic lives in ``stock.services.ledger`` (``post_reconciliation``
/ ``resolve_reconciliation_target``); this module owns the document rules
(ERPNext ``StockReconciliation.validate`` / ``validate_expense_account`` /
``remove_items_with_no_change``) and the perpetual-inventory GL.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from stock.models import (
    Bin,
    Item,
    ReconciliationPurpose,
    StockLedgerEntry,
    Warehouse,
)
from stock.services import ledger

ZERO = Decimal("0.00")


def _row(line, message: str) -> str:
    return _("Row #%(row)s: %(msg)s") % {"row": line._row_no, "msg": message}


def _lines(doc) -> List[Any]:
    lines = list(doc.items.select_related("item", "warehouse", "batch").order_by("pk"))
    for index, line in enumerate(lines, start=1):
        line._row_no = index
    return lines


# --------------------------------------------------------------------------- #
# Difference account
# --------------------------------------------------------------------------- #


def is_opening(company, purpose: str) -> bool:
    """An Opening Stock reconciliation, or the company's very first stock entry
    (ERPNext treats both as an opening entry)."""
    if purpose == ReconciliationPurpose.OPENING_STOCK:
        return True
    return not StockLedgerEntry.objects.filter(
        warehouse__company=company, is_cancelled=False
    ).exists()


def check_difference_account(company, purpose: str, account) -> None:
    """ERPNext ``validate_expense_account``: only enforced under perpetual
    inventory; an opening entry needs a balance-sheet account (the opening
    balance is the other side of the stock value, not an expense)."""
    if not company.enable_perpetual_inventory:
        return
    if account is None:
        raise ValidationError(
            {"expense_account": _("Please enter the Difference (Expense) Account.")}
        )
    if account.company_id != company.pk:
        raise ValidationError(
            {
                "expense_account": _(
                    "Account '%(account)s' belongs to a different company."
                )
                % {"account": account.name}
            }
        )
    if account.is_group:
        raise ValidationError(
            {
                "expense_account": _(
                    "Account '%(account)s' must not be a group account."
                )
                % {"account": account.name}
            }
        )
    if is_opening(company, purpose) and account.root_type in ("Income", "Expense"):
        raise ValidationError(
            {
                "expense_account": _(
                    "The Difference Account must be an Asset / Liability / Equity "
                    "account (e.g. Temporary Opening), since this Stock "
                    "Reconciliation is an opening entry."
                )
            }
        )


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #


def line_state(line, *, strict: bool = False) -> Dict[str, Decimal]:
    """Current position and resolved target of one line.

    ``strict=False`` (draft save) never raises: a line that cannot be resolved
    yet (rate not entered) is shown as "no change". ``strict=True`` (submit)
    raises the ledger's ValidationError.
    """
    cur_qty, cur_value = ledger.position(line.item, line.warehouse, line.batch)
    try:
        return ledger.resolve_reconciliation_target(
            cur_qty, cur_value, line.qty, line.valuation_rate
        )
    except ValidationError:
        if strict:
            raise
        # Half-entered row (e.g. no rate yet): show it as "no change" for now.
        cur_qty = ledger.quantize_qty(cur_qty)
        cur_value = ledger.quantize_value(cur_value)
        cur_rate = (
            ledger.quantize_rate(cur_value / cur_qty)
            if cur_qty > 0
            else ledger.quantize_rate(0)
        )
        return {
            "cur_qty": cur_qty,
            "cur_value": cur_value,
            "cur_rate": cur_rate,
            "qty": cur_qty,
            "rate": cur_rate,
            "value": cur_value,
            "dq": ledger.quantize_qty(0),
            "dv": ZERO,
        }


def _apply_state(line, state: Dict[str, Decimal], *, fill_inputs: bool) -> None:
    line.current_qty = state["cur_qty"]
    line.current_valuation_rate = state["cur_rate"]
    line.current_amount = state["cur_value"]
    line.quantity_difference = state["dq"]
    line.amount = state["value"]
    line.amount_difference = state["dv"]
    if fill_inputs:
        line.qty = state["qty"]
        line.valuation_rate = state["rate"]
    line.save(
        update_fields=[
            "qty",
            "valuation_rate",
            "current_qty",
            "current_valuation_rate",
            "current_amount",
            "quantity_difference",
            "amount",
            "amount_difference",
        ]
    )


def refresh_draft(doc) -> None:
    """Recompute the display snapshots of a draft (tolerant of half-entered rows)."""
    total = ZERO
    for line in _lines(doc):
        state = line_state(line)
        _apply_state(line, state, fill_inputs=False)
        total += state["dv"]
    doc.difference_amount = total
    doc.save(update_fields=["difference_amount"])


# --------------------------------------------------------------------------- #
# Submit gate
# --------------------------------------------------------------------------- #


def validate_lines(doc, lines: List[Any]) -> None:
    """Document-level rules, all messages reported together (like ERPNext)."""
    messages: List[str] = []
    seen = set()
    for line in lines:
        item, warehouse, batch = line.item, line.warehouse, line.batch
        key = (item.pk, warehouse.pk, line.batch_id)
        if key in seen:
            messages.append(
                _row(line, _("Same item and warehouse combination already entered."))
            )
        seen.add(key)
        if not item.is_stock_item:
            messages.append(
                _row(
                    line,
                    _("Item '%(code)s' is not a stock item.") % {"code": item.code},
                )
            )
        if not item.is_active:
            messages.append(
                _row(line, _("Item '%(code)s' is inactive.") % {"code": item.code})
            )
        if warehouse.is_group:
            messages.append(
                _row(
                    line,
                    _("Warehouse '%(name)s' is a group warehouse.")
                    % {"name": warehouse.name},
                )
            )
        if warehouse.company_id != doc.company_id:
            messages.append(
                _row(
                    line,
                    _("Warehouse '%(name)s' belongs to a different company.")
                    % {"name": warehouse.name},
                )
            )
        if item.has_batch_no and batch is None:
            messages.append(
                _row(
                    line,
                    _("Item '%(code)s' is batch-tracked: enter the batch counted.")
                    % {"code": item.code},
                )
            )
        if batch is not None and not item.has_batch_no:
            messages.append(
                _row(
                    line,
                    _("Item '%(code)s' is not batch-tracked.") % {"code": item.code},
                )
            )
        if batch is not None and batch.item_id != item.pk:
            messages.append(_row(line, _("Batch does not belong to this item.")))
        if line.qty is None and line.valuation_rate is None:
            messages.append(
                _row(
                    line, _("Please specify either Quantity or Valuation Rate or both.")
                )
            )
        if (line.qty is not None and line.qty < 0) or (
            line.valuation_rate is not None and line.valuation_rate < 0
        ):
            messages.append(
                _row(line, _("Negative Quantity or Valuation Rate is not allowed."))
            )
    if messages:
        raise ValidationError(messages)


def prepare_for_submit(doc) -> None:
    """``validate_before_submit`` of the document (runs inside the submit atomic
    block, docstatus still draft): validate, refresh snapshots against the live
    ledger, drop rows with no change (ERPNext), total the difference."""
    lines = _lines(doc)
    if not lines:
        raise ValidationError(_("Stock Reconciliation must have at least one item."))
    doc.company.get_perpetual_accounts()  # raises when perpetual is misconfigured
    check_difference_account(doc.company, doc.purpose, doc.expense_account)
    validate_lines(doc, lines)

    messages: List[str] = []
    states = []
    for line in lines:
        try:
            states.append((line, line_state(line, strict=True)))
        except ValidationError as exc:
            messages.extend(_row(line, m) for m in exc.messages)
    if messages:
        raise ValidationError(messages)

    kept = 0
    total = ZERO
    for line, state in states:
        if state["dq"] == 0 and state["dv"] == 0:
            line.delete()  # nothing to reconcile: removed like ERPNext does
            continue
        _apply_state(line, state, fill_inputs=True)
        kept += 1
        total += state["dv"]
    if not kept:
        raise ValidationError(
            _("Nothing to reconcile: no row changes the quantity or valuation rate.")
        )
    doc.difference_amount = total
    doc.save(update_fields=["difference_amount"])
    doc._prefetched_objects_cache = {}


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #


def post(doc) -> None:
    """Post the SLEs (through the ledger service) and the perpetual-inventory GL."""
    rows = [
        {
            "item": line.item,
            "warehouse": line.warehouse,
            "batch": line.batch,
            "qty": line.qty,
            "valuation_rate": line.valuation_rate,
        }
        for line in doc.items.select_related("item", "warehouse", "batch").order_by(
            "pk"
        )
    ]
    sles = ledger.post_reconciliation(
        doc, rows, posting_date=doc.posting_date, posting_time=doc.posting_time
    )
    if not sles:
        raise ValidationError(
            _("Nothing to reconcile: no row changes the quantity or valuation rate.")
        )
    _post_gl(doc, sles)


def _post_gl(doc, sles) -> None:
    """Each warehouse account gains/loses the stock value difference; the
    opposite side is the difference account (Dr stock / Cr difference for a
    count-up, the reverse for a count-down)."""
    from accounts.services import gl as gl_service

    perpetual = doc.company.get_perpetual_accounts()
    if not perpetual:
        return
    inventory = perpetual["default_inventory_account"]
    balances: Dict[Any, Decimal] = {}
    for sle in sles:
        account = sle.warehouse.account or inventory
        balances[account] = balances.get(account, ZERO) + sle.stock_value_difference

    entries = []
    for account, value in balances.items():
        value = value.quantize(Decimal("0.01"))
        if value > 0:
            entries.append({"account": account, "debit": value, "credit": ZERO})
        elif value < 0:
            entries.append({"account": account, "debit": ZERO, "credit": -value})
    net = sum(balances.values(), ZERO).quantize(Decimal("0.01"))
    if net > 0:
        entries.append({"account": doc.expense_account, "debit": ZERO, "credit": net})
    elif net < 0:
        entries.append({"account": doc.expense_account, "debit": -net, "credit": ZERO})
    if entries:
        gl_service.post(doc, entries, posting_date=doc.posting_date)


# --------------------------------------------------------------------------- #
# Fetch items from warehouse
# --------------------------------------------------------------------------- #


def _descendants(model, root_id: int) -> List[int]:
    ids, frontier = [root_id], [root_id]
    while frontier:
        frontier = list(
            model.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
        )
        ids.extend(frontier)
    return ids


def fetch_items(
    company,
    warehouse,
    *,
    item: Optional[Item] = None,
    item_group=None,
    include_zero: bool = False,
) -> List[Dict[str, Any]]:
    """Rows to prefill a count sheet (ERPNext "Fetch Items from Warehouse"): the
    current qty / rate of every stock item in ``warehouse`` (a group warehouse
    expands to its leaf descendants); a batch-tracked item yields one row per
    batch holding stock. Negative balances are included (they need a count)."""
    wh_ids = _descendants(Warehouse, warehouse.pk)
    leaf_ids = list(
        Warehouse.objects.filter(
            pk__in=wh_ids, company=company, is_group=False
        ).values_list("pk", flat=True)
    )
    bins = Bin.objects.filter(
        warehouse_id__in=leaf_ids, item__is_stock_item=True, item__is_active=True
    ).select_related("item", "warehouse", "item__stock_uom")
    if item is not None:
        bins = bins.filter(item=item)
    if item_group is not None:
        from stock.models import ItemGroup

        bins = bins.filter(
            item__item_group_id__in=_descendants(ItemGroup, item_group.pk)
        )
    bins = bins.order_by("warehouse__name", "item__code")

    rows: List[Dict[str, Any]] = []
    for b in bins:
        if b.item.has_batch_no:
            per_batch = (
                StockLedgerEntry.objects.filter(
                    item=b.item,
                    warehouse=b.warehouse,
                    is_cancelled=False,
                    batch__isnull=False,
                )
                .values("batch_id", "batch__batch_no")
                .annotate(q=Sum("qty"), v=Sum("stock_value_difference"))
                .order_by("batch__batch_no")
            )
            for r in per_batch:
                q, v = r["q"] or Decimal("0"), r["v"] or ZERO
                if q == 0 and not include_zero:
                    continue
                rows.append(_fetch_row(b, r["batch_id"], r["batch__batch_no"], q, v))
        else:
            if b.actual_qty == 0 and not include_zero:
                continue
            rows.append(_fetch_row(b, None, None, b.actual_qty, b.stock_value))
    return rows


def _fetch_row(b, batch_id, batch_no, qty, value) -> Dict[str, Any]:
    qty = ledger.quantize_qty(qty)
    rate = ledger.quantize_rate(value / qty) if qty > 0 else ledger.quantize_rate(0)
    return {
        "item": b.item_id,
        "item_code": b.item.code,
        "item_name": b.item.name,
        "stock_uom": b.item.stock_uom.name,
        "warehouse": b.warehouse_id,
        "warehouse_name": b.warehouse.name,
        "batch": batch_id,
        "batch_no": batch_no,
        "current_qty": qty,
        "current_valuation_rate": rate,
        "qty": None,
        "valuation_rate": None,
    }


# --------------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------------- #


def integrity_errors() -> List[str]:
    """Reconciliation facts must tie back to their documents: every submitted
    document's live SLEs sum to its ``difference_amount`` and no live
    reconciliation SLE is left behind by a document that is not submitted."""
    from core.models import DocStatus
    from stock.models import StockReconciliation

    errors: List[str] = []
    voucher_type = "stock.StockReconciliation"
    sums = {
        r["voucher_no"]: r["v"] or ZERO
        for r in StockLedgerEntry.objects.filter(
            voucher_type=voucher_type, is_cancelled=False
        )
        .values("voucher_no")
        .annotate(v=Sum("stock_value_difference"))
    }
    submitted = {
        d.name: d
        for d in StockReconciliation.objects.filter(docstatus=DocStatus.SUBMITTED)
    }
    for name, doc in submitted.items():
        total = sums.get(name, ZERO)
        if total != doc.difference_amount:
            errors.append(
                f"Stock Reconciliation {name}: SLE value difference ({total}) != "
                f"document difference ({doc.difference_amount})"
            )
    for name in sums:
        if name not in submitted:
            errors.append(
                f"Stock Reconciliation {name}: live SLEs exist but the document "
                "is not submitted"
            )
    return errors
