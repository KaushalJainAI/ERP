"""Extra charges on a purchase bill: freight, hamali / loading, unloading, cartage.

ERPNext adds such costs to the stock value through *Valuation* / *Valuation and
Total* rows of the taxes table (and later through a Landed Cost Voucher). Our tax
rows live in ``accounts`` and carry GST semantics, so the charges are a separate
buying-side child table, ``PurchaseInvoiceCharge`` (docs/erpnext_divergences.md).

Every charge is **capitalised**: it is spread over the invoice's stock lines and
becomes part of each line's incoming valuation. A charge is either

* **on the bill** (``included_in_bill``): the supplier invoiced it. It is part of
  ``grand_total`` / ``rounded_total`` (ERPNext "Valuation and Total") and is
  debited to its own expense ``account``; or
* **off the bill** (ERPNext "Valuation"): paid to a third party (a transporter,
  the labour gang). It only raises the stock value; with perpetual inventory the
  other side is credited to *Expenses Included In Valuation*, which the later
  payment / journal for the transporter debits. No GST is ever computed on either
  kind: the tax engine only looks at the line amounts.

Spreading is by line amount (default) or by quantity (per-kg hamali). Each charge
is split on its own, in whole paise, rounded **down** line by line with the whole
remainder on the last weighted line, so the shares always add up to the charge
exactly (and no share can go negative).
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Dict, List, Sequence, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

EIIV_NAME = "Expenses Included In Valuation"

#: (account name, template name, included_in_bill, distribute_by)
CHARGE_PRESETS = (
    ("Freight Inward", "Freight Inward", True, "Amount"),
    ("Hamali / Loading Charges", "Hamali / Loading", False, "Qty"),
    ("Unloading Charges", "Unloading", False, "Qty"),
    ("Cartage Inward", "Cartage", False, "Amount"),
)


# ------------------------------------------------------------------ pure maths


def split(total: Decimal, weights: Sequence[Decimal]) -> List[Decimal]:
    """Split ``total`` over ``weights`` in whole paise; the shares sum to ``total``.

    Each share is ``total * weight / sum(weights)`` rounded down to the paisa; the
    remainder goes on the last line with a positive weight.
    """
    total = Decimal(total).quantize(CENT, rounding=ROUND_HALF_UP)
    weights = [Decimal(w) for w in weights]
    weight_sum = sum(weights, Decimal("0"))
    if weight_sum <= 0:
        raise ValueError("Nothing to distribute over.")
    last = max(i for i, w in enumerate(weights) if w > 0)
    shares = [ZERO] * len(weights)
    given = ZERO
    for i, w in enumerate(weights):
        if i == last or w <= 0:
            continue
        shares[i] = (total * w / weight_sum).quantize(CENT, rounding=ROUND_DOWN)
        given += shares[i]
    shares[last] = total - given
    return shares


def allocate(
    charges: Sequence[Tuple[Decimal, str]],
    lines: Sequence[Tuple[object, Decimal, Decimal]],
) -> Dict[object, Decimal]:
    """Landed-cost share of every line.

    ``charges``: ``(amount, distribute_by)`` per charge (``"Amount"`` / ``"Qty"``);
    ``lines``: ``(key, line_amount, abs_qty)`` per stock line, in document order.
    Returns ``{key: total share of all charges}``.
    """
    out = {key: ZERO for key, _a, _q in lines}
    for amount, by in charges:
        weights = [(a if by == "Amount" else q) for _k, a, q in lines]
        for (key, _a, _q), share in zip(lines, split(amount, weights)):
            out[key] += share
    return out


# --------------------------------------------------------------- invoice level


def stock_lines(invoice):
    """The invoice lines that receive a share: stock items, in row order."""
    return [
        line
        for line in invoice.items.select_related("item").order_by("pk")
        if line.item.is_stock_item
    ]


def charge_rows(invoice):
    return list(invoice.charges.select_related("account").order_by("idx", "pk"))


def totals(invoice) -> Tuple[Decimal, Decimal]:
    """``(on_bill, off_bill)`` charge totals; a return carries no charges."""
    if invoice.is_return:
        return ZERO, ZERO
    on_bill = off_bill = ZERO
    for c in invoice.charges.all():
        if c.included_in_bill:
            on_bill += c.amount
        else:
            off_bill += c.amount
    return on_bill, off_bill


def allocation(invoice, rows=None, lines=None) -> Dict[int, Decimal]:
    """``{invoice line pk: landed-cost share}`` for the stock lines (empty if none)."""
    rows = charge_rows(invoice) if rows is None else rows
    lines = stock_lines(invoice) if lines is None else lines
    if not rows or not lines:
        return {}
    if not any(ln.amount > 0 for ln in lines) and any(
        c.distribute_by == "Amount" for c in rows
    ):
        return {}
    return allocate(
        [(c.amount, c.distribute_by) for c in rows],
        [(ln.pk, ln.amount, abs(ln.qty)) for ln in lines],
    )


def eiiv_account(company):
    from accounts.models import Account

    return Account.objects.filter(
        company=company, name=EIIV_NAME, is_group=False
    ).first()


def validate(invoice):
    """Submit gate: refuse charges that cannot be posted."""
    rows = charge_rows(invoice)
    if not rows:
        return
    if invoice.is_return:
        raise ValidationError(_("A return (debit note) cannot carry extra charges."))
    if not invoice.update_stock:
        raise ValidationError(
            _(
                "Extra charges are added to the stock value: turn on 'Update Stock' "
                "or remove the charges."
            )
        )
    lines = stock_lines(invoice)
    if not lines:
        raise ValidationError(
            _("Extra charges need at least one stock item line to be spread over.")
        )
    for n, c in enumerate(rows, start=1):
        if c.amount is None or c.amount <= 0:
            raise ValidationError(
                _("Charge row %(n)s: the amount must be greater than zero.") % {"n": n}
            )
        if c.distribute_by == "Amount" and not any(ln.amount > 0 for ln in lines):
            raise ValidationError(
                _(
                    "Charge row %(n)s: the stock lines have no value to spread an "
                    "amount-based charge over; spread it by quantity."
                )
                % {"n": n}
            )
        if c.included_in_bill:
            account = c.account
            if account is None:
                raise ValidationError(
                    _(
                        "Charge row %(n)s: choose the expense account of a charge "
                        "on the bill."
                    )
                    % {"n": n}
                )
            if account.is_group or account.company_id != invoice.company_id:
                raise ValidationError(
                    _(
                        "Charge row %(n)s: the account must be a ledger of the "
                        "invoice's company."
                    )
                    % {"n": n}
                )
            if account.root_type != "Expense":
                raise ValidationError(
                    _("Charge row %(n)s: the account must be an expense account.")
                    % {"n": n}
                )
    if (
        invoice.company.get_perpetual_accounts()
        and eiiv_account(invoice.company) is None
    ):
        raise ValidationError(
            _(
                "Perpetual inventory needs the '%(name)s' account for company "
                "'%(company)s' (run seed_defaults)."
            )
            % {"name": EIIV_NAME, "company": invoice.company.name}
        )


def apply_bill_charges(invoice):
    """Add the on-bill charges to the header totals the tax engine just wrote.

    ``total_taxes_and_charges`` / ``grand_total`` carry them (ERPNext: "Total"
    rows), and the rupee round-off is re-derived on the new grand total. The engine
    itself never sees them, so no GST is computed on a charge.
    """
    from accounts.services.taxes import whole_rupee

    if invoice.is_return or not invoice.pk:
        return
    on_bill = sum(
        (
            c.amount
            for c in invoice.charges.model.objects.filter(
                purchase_invoice=invoice, included_in_bill=True
            )
        ),
        ZERO,
    )
    if not on_bill:
        return
    invoice.total_taxes_and_charges += on_bill
    invoice.grand_total += on_bill
    if invoice.company.enable_rounded_total:
        invoice.rounded_total = whole_rupee(invoice.grand_total)
    else:
        invoice.rounded_total = invoice.grand_total
    invoice.rounding_adjustment = invoice.rounded_total - invoice.grand_total
    invoice.save(
        update_fields=[
            "total_taxes_and_charges",
            "grand_total",
            "rounding_adjustment",
            "rounded_total",
        ]
    )


def gl_entries(invoice, rows=None):
    """GL lines of the charges (the party and stock lines are the caller's).

    Dr the expense account of each on-bill charge; with perpetual inventory, Cr
    *Expenses Included In Valuation* for the whole capitalised amount (the stock
    leg carries the matching debit), so an on-bill charge nets to a zero P&L effect.
    """
    rows = charge_rows(invoice) if rows is None else rows
    entries = []
    total = ZERO
    for c in rows:
        total += c.amount
        if c.included_in_bill:
            entries.append({"account": c.account, "debit": c.amount, "credit": ZERO})
    if total and invoice.company.get_perpetual_accounts():
        entries.append(
            {"account": eiiv_account(invoice.company), "debit": ZERO, "credit": total}
        )
    return entries


# ----------------------------------------------------------------------- setup


def _root_group(company, root_type):
    from accounts.models import Account

    groups = Account.objects.filter(company=company, root_type=root_type, is_group=True)
    return (
        groups.filter(parent__isnull=True).order_by("pk").first()
        or groups.order_by("pk").first()
    )


@transaction.atomic
def ensure_charge_setup(company):
    """Idempotently create the charge accounts and templates of ``company``."""
    from accounts.models import Account, AccountRootType
    from buying.models import PurchaseChargeTemplate

    def account(name):
        existing = Account.objects.filter(company=company, name=name).first()
        if existing is not None:
            return existing
        return Account.objects.create(
            company=company,
            name=name,
            parent=_root_group(company, AccountRootType.EXPENSE),
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )

    account(EIIV_NAME)
    for account_name, template_name, in_bill, by in CHARGE_PRESETS:
        PurchaseChargeTemplate.objects.get_or_create(
            company=company,
            name=template_name,
            defaults={
                "account": account(account_name),
                "included_in_bill": in_bill,
                "distribute_by": by,
            },
        )
