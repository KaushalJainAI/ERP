"""Sales Register / Purchase Register (ERPNext ``sales_register`` / ``purchase_register``).

One row per **submitted** invoice in the period, with each GST tax head as its own
column. Cancelled and draft invoices are excluded. Credit / debit notes (``is_return``)
are included with every amount negative. The returns feature already stores a note's
figures negative (CHECK constraints); amounts are still forced to ``-abs(value)`` so a
legacy / imported note can never show positive. The rounding is derived as ``rounded_total - grand_total`` so
the identity holds on the signed figures.

Each row is an invoice-level snapshot (``net_total`` / tax rows / ``rounded_total``);
the optional ``item_group`` filter only *selects* the invoices that carry an item of
that group (or of a descendant group), it does not re-cut the amounts.

Purchase rows also carry the supplier's ``bill_no`` / ``bill_date`` (the Tally /
GST input-credit reference). Extra charges on the bill (freight, hamali) are part of
``grand_total`` and are shown in the ``OTHER`` head.

Reverse-charge purchase invoices carry their tax in memo rows that are not part of
``grand_total``; those go to the ``reverse_charge_tax`` column and not to the heads, so
``net_total + heads == grand_total`` always holds.
"""

from decimal import Decimal

from rest_framework.exceptions import ValidationError

from django.utils.translation import gettext as _

from accounts.reports import (
    ZERO,
    company_block,
    money,
    page_block,
    period_block,
)
from core.models import DocStatus

HEADS = ("CGST", "SGST", "IGST", "CESS", "OTHER")
CHUNK = 1000
DEDUCT = "Deduct"
SUMMED = (
    "net_total",
    "reverse_charge_tax",
    "grand_total",
    "rounding_adjustment",
    "rounded_total",
    "outstanding",
)


def register_models():
    from buying.models import PurchaseInvoice
    from selling.models import SalesInvoice

    return {"sales": SalesInvoice, "purchase": PurchaseInvoice}


def _descendant_group_ids(group_id):
    from stock.models import ItemGroup

    seen = {group_id}
    frontier = [group_id]
    while frontier:
        frontier = [
            pk
            for pk in ItemGroup.objects.filter(parent_id__in=frontier).values_list(
                "pk", flat=True
            )
            if pk not in seen
        ]
        seen.update(frontier)
    return seen


def _party_of(model, company, party_id):
    field = model._meta.get_field(model.party_field)
    party = field.related_model.objects.filter(pk=party_id).first()
    if party is None or party.company_id != company.pk:
        raise ValidationError(
            {"party": _("A valid party of this company is required.")}
        )
    return party


def _signed(value, is_return):
    value = Decimal(value or 0)
    return -abs(value) if is_return else value


def _rows(model, invoices):
    """Decimal row dicts for a list of invoice instances (one query for their taxes)."""
    if not invoices:
        return []
    fk = model.tax_fk_name()
    taxes = {}
    for parent_id, component, amount, add_deduct in (
        model.tax_model()
        .objects.filter(**{f"{fk}__in": [inv.pk for inv in invoices]})
        .values_list(
            f"{fk}_id", "account__gst_component", "tax_amount", "add_deduct_tax"
        )
    ):
        amount = Decimal(amount or 0)
        if add_deduct == DEDUCT:
            amount = -amount
        head = component if component in HEADS else "OTHER"
        bucket = taxes.setdefault(parent_id, {})
        bucket[head] = bucket.get(head, ZERO) + amount

    bill_charges = {}
    if hasattr(model, "charges_on_bill"):
        for parent_id, amount in (
            model._meta.get_field("charges")
            .related_model.objects.filter(
                purchase_invoice__in=[inv.pk for inv in invoices],
                included_in_bill=True,
            )
            .values_list("purchase_invoice_id", "amount")
        ):
            bill_charges[parent_id] = bill_charges.get(parent_id, ZERO) + Decimal(
                amount
            )

    rows = []
    for inv in invoices:
        is_return = bool(inv.is_return)
        party = inv.party
        heads = {h: ZERO for h in HEADS}
        rcm_tax = ZERO
        if inv.is_reverse_charge:
            rcm_tax = sum(taxes.get(inv.pk, {}).values(), ZERO)
        else:
            heads.update(taxes.get(inv.pk, {}))
        if bill_charges.get(inv.pk) and not is_return:
            heads["OTHER"] += bill_charges[inv.pk]
        grand = _signed(inv.grand_total, is_return)
        rounded = _signed(inv.rounded_total, is_return)
        against = (
            inv.return_against.name if is_return and inv.return_against_id else None
        )
        rows.append(
            {
                "id": inv.pk,
                "name": inv.name,
                "bill_no": getattr(inv, "bill_no", None),
                "bill_date": getattr(inv, "bill_date", None),
                "posting_date": inv.posting_date,
                "party_id": party.pk,
                "party": party.name,
                "party_gstin": inv.party_gstin or party.gstin or "",
                "place_of_supply": inv.place_of_supply_id,
                "is_reverse_charge": inv.is_reverse_charge,
                "is_return": is_return,
                "return_against": against,
                "status": inv.status,
                "net_total": _signed(inv.net_total, is_return),
                "heads": {h: _signed(v, is_return) for h, v in heads.items()},
                "reverse_charge_tax": _signed(rcm_tax, is_return),
                "grand_total": grand,
                "rounding_adjustment": rounded - grand,
                "rounded_total": rounded,
                "outstanding": _signed(inv.outstanding_amount, is_return),
            }
        )
    return rows


def _out(row):
    out = {
        "id": row["id"],
        "name": row["name"],
        "posting_date": row["posting_date"].isoformat(),
        "party_id": row["party_id"],
        "party": row["party"],
        "party_gstin": row["party_gstin"],
        "place_of_supply": row["place_of_supply"],
        "is_reverse_charge": row["is_reverse_charge"],
        "is_return": row["is_return"],
        "return_against": row["return_against"],
        "status": row["status"],
        "net_total": money(row["net_total"]),
        "taxes": {h: money(row["heads"][h]) for h in HEADS},
        "reverse_charge_tax": money(row["reverse_charge_tax"]),
        "grand_total": money(row["grand_total"]),
        "rounding_adjustment": money(row["rounding_adjustment"]),
        "rounded_total": money(row["rounded_total"]),
        "outstanding": money(row["outstanding"]),
    }
    if row["bill_no"] is not None:  # purchase register only
        out["bill_no"] = row["bill_no"]
        out["bill_date"] = row["bill_date"].isoformat() if row["bill_date"] else None
    return out


def build(kind, company, from_date, to_date, party_id, item_group_id, page, size):
    model = register_models()[kind]
    qs = model.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    if party_id is not None:
        party = _party_of(model, company, party_id)
        qs = qs.filter(**{model.party_field: party})
    if item_group_id is not None:
        from stock.models import ItemGroup

        if not ItemGroup.objects.filter(pk=item_group_id).exists():
            raise ValidationError({"item_group": _("A valid item group is required.")})
        qs = qs.filter(
            pk__in=model.item_model()
            .objects.filter(
                item__item_group_id__in=_descendant_group_ids(item_group_id)
            )
            .values(model.item_fk_name())
        )
    qs = qs.select_related(model.party_field, "return_against").order_by(
        "posting_date", "pk"
    )

    # Totals over the whole range (streamed in chunks), rows for the requested page.
    totals = {k: ZERO for k in SUMMED}
    head_totals = {h: ZERO for h in HEADS}
    count = 0

    def absorb(batch):
        for row in _rows(model, batch):
            for key in SUMMED:
                totals[key] += row[key]
            for h in HEADS:
                head_totals[h] += row["heads"][h]

    chunk = []
    for inv in qs.iterator(chunk_size=CHUNK):
        count += 1
        chunk.append(inv)
        if len(chunk) >= CHUNK:
            absorb(chunk)
            chunk = []
    absorb(chunk)

    start = (page - 1) * size
    rows = [_out(r) for r in _rows(model, list(qs[start : start + size]))]
    return {
        "kind": kind,
        "company": company_block(company),
        "period": period_block(from_date, to_date),
        "heads": list(HEADS),
        "rows": rows,
        "totals": {
            **{k: money(v) for k, v in totals.items()},
            "taxes": {h: money(v) for h, v in head_totals.items()},
        },
        "pagination": page_block(page, size, count),
    }
