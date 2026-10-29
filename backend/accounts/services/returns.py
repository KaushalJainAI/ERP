"""Sales Return (credit note) / Purchase Return (debit note) rules.

ERPNext v15 (``controllers/sales_and_purchase_return.py``): a return is a normal
Sales / Purchase Invoice with ``is_return`` set, **negative** quantities and
totals, and an optional ``return_against`` link to the original. This module holds
the parts shared by both invoice types:

* :func:`validate_return` -- the submit gate (original submitted, same company /
  party / party account, items exist on the original, cumulative returned quantity
  per original line within the original, batch of the original, ...);
* :func:`returnable_lines` -- what is still returnable (used by the "make return"
  mapping);
* :func:`sales_return_incoming_value` -- the value a returned quantity re-enters
  stock at (the original outgoing valuation);
* :func:`check_integrity` -- the verify_integrity checks for returns.

Nothing here writes a ledger: the invoices' ``on_submit`` / ``on_cancel`` do that
through ``accounts.services.gl`` and ``stock.services.ledger``.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from core.models import DocStatus

ZERO = Decimal("0.000")
CENT = Decimal("0.01")

#: invoice model label -> name of the line's FK to the original line
LINK_FIELDS = {
    "selling.SalesInvoice": "sales_invoice_item",
    "buying.PurchaseInvoice": "purchase_invoice_item",
}


def link_field(doc_or_model):
    """Name of the line field pointing at the original invoice line."""
    return LINK_FIELDS[doc_or_model.voucher_label()]


def returned_quantities(original, exclude=None):
    """Quantities already returned against ``original`` by **submitted** returns.

    Returns ``(linked, unlinked)``: ``linked`` maps an original line pk to the
    absolute quantity returned against that exact line, ``unlinked`` maps an item pk
    to the quantity returned on lines that name only the item.
    """
    model = type(original)
    line_model = model.item_model()
    parent = model.item_fk_name()
    link = link_field(model)
    qs = line_model.objects.filter(
        **{
            f"{parent}__return_against": original,
            f"{parent}__docstatus": DocStatus.SUBMITTED,
            f"{parent}__is_return": True,
        }
    )
    if exclude is not None and exclude.pk:
        qs = qs.exclude(**{parent: exclude})
    linked, unlinked = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for row in qs.values(link, "item_id").annotate(q=Sum("qty")):
        qty = abs(row["q"] or ZERO)
        if row[link]:
            linked[row[link]] += qty
        else:
            unlinked[row["item_id"]] += qty
    return linked, unlinked


def returnable_lines(original):
    """``[(original line, qty still returnable)]`` for a submitted ``original``."""
    linked, unlinked = returned_quantities(original)
    pool = dict(unlinked)
    out = []
    for line in original.items.select_related("item", "warehouse", "batch").order_by(
        "pk"
    ):
        remaining = line.qty - linked.get(line.pk, ZERO)
        take = min(max(remaining, ZERO), pool.get(line.item_id, ZERO))
        if take:
            pool[line.item_id] -= take
            remaining -= take
        if remaining > 0:
            out.append((line, remaining.quantize(Decimal("0.001"))))
    return out


# ------------------------------------------------------------------ validation


def validate_return(doc):
    """The submit gate of a return. Raises ``ValidationError``; may flip
    ``update_outstanding_for_self`` (and say so in ``doc.notices``)."""
    lines = list(doc.items.select_related("item", "batch").order_by("pk"))
    errors = []
    for position, line in enumerate(lines, start=1):
        if line.qty >= 0:
            errors.append(
                _("Row %(n)s: quantity must be negative in a return.") % {"n": position}
            )
    if errors:
        raise ValidationError(errors)
    if doc.return_against_id is None:
        return  # a standalone return has no original to check against

    model = type(doc)
    # Lock the original: two concurrent returns must not both pass the
    # cumulative-quantity check below.
    original = model.objects.select_for_update().get(pk=doc.return_against_id)
    _validate_against_original(doc, original)
    _validate_lines(doc, original, lines)
    _settle_outstanding_mode(doc, original)


def _validate_against_original(doc, original):
    label = original.name
    if original.pk == doc.pk:
        raise ValidationError(_("A document cannot be returned against itself."))
    if original.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            _("'%(name)s' is not submitted; only a submitted document can be returned.")
            % {"name": label}
        )
    if original.is_return:
        raise ValidationError(
            _("'%(name)s' is itself a return and cannot be returned.") % {"name": label}
        )
    if original.company_id != doc.company_id:
        raise ValidationError(
            _("'%(name)s' belongs to a different company.") % {"name": label}
        )
    if original.party != doc.party:
        raise ValidationError(
            _("The party does not match '%(name)s' (%(party)s).")
            % {"name": label, "party": original.party.name}
        )
    if original.party_account.pk != doc.party_account.pk:
        raise ValidationError(
            _("The party account must be the same as in '%(name)s' (%(account)s).")
            % {"name": label, "account": original.party_account.name}
        )
    if doc.posting_date < original.posting_date:
        raise ValidationError(
            _("The posting date cannot be before that of '%(name)s' (%(date)s).")
            % {"name": label, "date": original.posting_date.isoformat()}
        )
    # ERPNext: stock cannot come back through a sales return when the invoice
    # never delivered it.
    if doc.GST_DIRECTION == "Sales" and doc.update_stock and not original.update_stock:
        raise ValidationError(
            _(
                "'Update Stock' cannot be set: the items were not delivered through "
                "'%(name)s'."
            )
            % {"name": label}
        )


def _validate_lines(doc, original, lines):
    link = link_field(doc)
    orig_lines = {
        ln.pk: ln
        for ln in original.items.select_related("item", "batch").order_by("pk")
    }
    prior_linked, prior_unlinked = returned_quantities(original, exclude=doc)

    this_linked, this_unlinked = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    item_orig_qty, item_max_rate, item_batches = (
        defaultdict(lambda: ZERO),
        defaultdict(lambda: ZERO),
        defaultdict(set),
    )
    for ln in orig_lines.values():
        item_orig_qty[ln.item_id] += ln.qty
        item_max_rate[ln.item_id] = max(item_max_rate[ln.item_id], ln.rate)
        if ln.batch_id:
            item_batches[ln.item_id].add(ln.batch_id)

    errors = []
    for position, line in enumerate(lines, start=1):
        row = {"n": position, "code": line.item.code, "name": original.name}
        qty = abs(line.qty)
        linked_to = getattr(line, f"{link}_id")
        if linked_to is not None:
            orig = orig_lines.get(linked_to)
            if orig is None or orig.item_id != line.item_id:
                errors.append(
                    _(
                        "Row %(n)s: item %(code)s is not on the row it returns in %(name)s."
                    )
                    % row
                )
                continue
            this_linked[linked_to] += qty
            max_rate = orig.rate
            allowed_batches = {orig.batch_id}
        else:
            if line.item_id not in item_orig_qty:
                errors.append(
                    _("Row %(n)s: item %(code)s does not exist in %(name)s.") % row
                )
                continue
            this_unlinked[line.item_id] += qty
            max_rate = item_max_rate[line.item_id]
            allowed_batches = item_batches[line.item_id]
        if line.rate > max_rate:
            errors.append(
                _(
                    "Row %(n)s: the rate cannot be greater than the rate used in "
                    "%(name)s (%(rate)s)."
                )
                % {**row, "rate": max_rate}
            )
        if (
            doc.update_stock
            and line.item.is_stock_item
            and line.item.has_batch_no
            and line.batch_id not in allowed_batches
        ):
            errors.append(
                _(
                    "Row %(n)s: a batch-tracked item must be returned from the batch "
                    "of %(name)s."
                )
                % row
            )

    for pk, qty in this_linked.items():
        cap = orig_lines[pk].qty
        if prior_linked.get(pk, ZERO) + qty > cap:
            errors.append(
                _(
                    "Cannot return more than %(max)s of item %(code)s from this row "
                    "of %(name)s."
                )
                % {
                    "max": cap - prior_linked.get(pk, ZERO),
                    "code": orig_lines[pk].item.code,
                    "name": original.name,
                }
            )
    items = set(this_unlinked) | {orig_lines[pk].item_id for pk in this_linked}
    for item_id in items:
        prior = prior_unlinked.get(item_id, ZERO) + sum(
            (
                prior_linked.get(pk, ZERO)
                for pk, ln in orig_lines.items()
                if ln.item_id == item_id
            ),
            ZERO,
        )
        now = this_unlinked.get(item_id, ZERO) + sum(
            (
                this_linked.get(pk, ZERO)
                for pk, ln in orig_lines.items()
                if ln.item_id == item_id
            ),
            ZERO,
        )
        if prior + now > item_orig_qty[item_id]:
            code = next(
                ln.item.code for ln in orig_lines.values() if ln.item_id == item_id
            )
            errors.append(
                _("Cannot return more than %(max)s of item %(code)s from %(name)s.")
                % {
                    "max": item_orig_qty[item_id] - prior,
                    "code": code,
                    "name": original.name,
                }
            )
    if errors:
        raise ValidationError(errors)


def _settle_outstanding_mode(doc, original):
    """ERPNext: a return that updates the *original's* outstanding but is larger than
    it falls back to updating its own, and tells the user."""
    if doc.update_outstanding_for_self:
        return
    from accounts.services import outstanding as outstanding_service

    open_amount = outstanding_service.current_outstanding(original)
    if abs(doc.rounded_total) > open_amount:
        doc.update_outstanding_for_self = True
        doc.save(update_fields=["update_outstanding_for_self"])
        doc.notices = [
            *getattr(doc, "notices", []),
            _(
                "The outstanding amount %(open)s in %(name)s is less than %(total)s: "
                "this return keeps its own outstanding amount instead."
            )
            % {
                "open": open_amount,
                "name": original.name,
                "total": abs(doc.rounded_total),
            },
        ]


# ------------------------------------------------------------------ stock value


def sales_return_incoming_value(doc, line):
    """Value (2 dp) at which a returned sales line re-enters stock.

    ERPNext values a sales return at the **original outgoing** rate. The value is
    taken from the original voucher's non-cancelled outgoing stock ledger rows for
    the item (and batch, when the line has one), pro rata to the returned quantity,
    so a full return restores exactly what the sale took out. A standalone return
    (or one whose original left no matching row) uses the bin's current valuation
    rate, else the item's standard rate.
    """
    from stock.models import Bin, StockLedgerEntry

    qty = abs(line.qty)
    if doc.return_against_id:
        original = doc.return_against
        rows = StockLedgerEntry.objects.filter(
            voucher_type=original.voucher_label(),
            voucher_no=original.name,
            item_id=line.item_id,
            is_cancelled=False,
            qty__lt=0,
        )
        if line.batch_id:
            rows = rows.filter(batch_id=line.batch_id)
        agg = rows.aggregate(q=Sum("qty"), v=Sum("stock_value_difference"))
        if agg["q"]:
            return (abs(agg["v"]) * qty / abs(agg["q"])).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
    bin_rate = (
        Bin.objects.filter(item_id=line.item_id, warehouse_id=line.warehouse_id)
        .values_list("valuation_rate", flat=True)
        .first()
    )
    rate = bin_rate if bin_rate and bin_rate > 0 else line.item.standard_rate
    return (qty * (rate or Decimal("0"))).quantize(CENT, rounding=ROUND_HALF_UP)


# -------------------------------------------------------------------- integrity


def check_integrity():
    """verify_integrity checks of returns; returns a list of error strings."""
    from accounts.models import GLEntry
    from accounts.services.outstanding import invoice_models

    errors = []
    for model in invoice_models():
        label = model.voucher_label()
        returns = model.objects.filter(
            is_return=True, docstatus=DocStatus.SUBMITTED
        ).select_related("return_against")
        originals = set()
        for ret in returns:
            if ret.items.filter(qty__gte=0).exists():
                errors.append(f"Return {label}:{ret.name} has a non-negative quantity")
            if ret.return_against_id:
                originals.add(ret.return_against_id)
            want_type, want_no = ret.against_voucher
            bad = (
                GLEntry.objects.filter(
                    voucher_type=label,
                    voucher_no=ret.name,
                    account_id=ret.party_account.pk,
                    is_cancelled=False,
                )
                .exclude(against_voucher_type=want_type, against_voucher_no=want_no)
                .exists()
            )
            if bad:
                errors.append(
                    f"Return {label}:{ret.name} party line is not tagged against "
                    f"{want_no} (update_outstanding_for_self="
                    f"{ret.update_outstanding_for_self})"
                )
        for original in model.objects.filter(pk__in=originals):
            linked, unlinked = returned_quantities(original)
            per_item = defaultdict(lambda: ZERO)
            for ln in original.items.all():
                per_item[ln.item_id] += ln.qty
                if linked.get(ln.pk, ZERO) > ln.qty:
                    errors.append(
                        f"Over-returned: {label}:{original.name} line #{ln.pk} "
                        f"({ln.item.code}) returned {linked[ln.pk]} of {ln.qty}"
                    )
            returned_by_item = defaultdict(lambda: ZERO)
            for ln in original.items.all():
                returned_by_item[ln.item_id] += linked.get(ln.pk, ZERO)
            for item_id, qty in unlinked.items():
                returned_by_item[item_id] += qty
            for item_id, qty in returned_by_item.items():
                if qty > per_item.get(item_id, ZERO):
                    errors.append(
                        f"Over-returned: {label}:{original.name} item #{item_id} "
                        f"returned {qty} of {per_item.get(item_id, ZERO)}"
                    )
    return errors
