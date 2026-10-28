"""GL lines for the tax / rounding part of a Sales or Purchase Invoice.

``SalesInvoice.on_submit`` and ``PurchaseInvoice.on_submit`` call these helpers and
hand the result to ``accounts.services.gl.post`` in the same voucher, so the tax
lines are posted (and, on cancel, reversed) atomically with the rest of the
invoice (docs/gst_design.md section 4).

With ``N = net_total``, ``T_i`` = the tax-row amounts, ``A = rounding_adjustment``
and ``R = rounded_total = N + sum(T_i) + A``:

Sales Invoice
    Dr party ``R`` / Cr income ``N`` / Cr each Add row ``T_i`` (Dr a Deduct row) /
    round-off ``A`` (Cr when ``A > 0``, Dr when ``A < 0``).
Purchase Invoice
    Dr expense / inventory ``N`` (tax excluded) / Dr each Add row ``T_i`` (Cr a
    Deduct row) / Cr party ``R`` / round-off ``A`` (Dr when ``A > 0``, Cr when
    ``A < 0``). Reverse-charge GST rows are memo rows: they are not part of ``R``
    and post as ``Dr Input X`` / ``Cr RCM Payable X`` instead.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from accounts.models import Account
from gst.constants import AddDeduct, GSTDirection

ZERO = Decimal("0.00")


def tax_rows(doc):
    """The document's tax rows in table order, with their accounts loaded."""
    return list(doc.taxes.select_related("account").order_by("idx", "pk"))


def is_memo_row(doc, row) -> bool:
    """A reverse-charge GST row: computed and shown, excluded from the totals."""
    return bool(doc.is_reverse_charge and row.account.gst_direction)


def _entry(account, debit=ZERO, credit=ZERO, **extra):
    return {"account": account, "debit": debit, "credit": credit, **extra}


def _round_off_entry(doc, *, debit_when_positive: bool):
    adjustment = doc.rounding_adjustment
    if not adjustment:
        return []
    account = doc.company.round_off_account
    amount = abs(adjustment)
    if (adjustment > 0) == debit_when_positive:
        return [_entry(account, debit=amount)]
    return [_entry(account, credit=amount)]


def rcm_payable_accounts(doc, rows=None):
    """``{component: RCM Payable account}`` for the memo rows of ``doc``."""
    rows = tax_rows(doc) if rows is None else rows
    components = {row.account.gst_component for row in rows if is_memo_row(doc, row)}
    found = {
        a.gst_component: a
        for a in Account.objects.filter(
            company_id=doc.company_id,
            gst_direction=GSTDirection.REVERSE_CHARGE,
            gst_component__in=components,
        )
    }
    missing = sorted(components - set(found))
    if missing:
        raise ValidationError(
            _(
                "No 'Reverse Charge' GST account is set up for %(components)s in "
                "company '%(company)s'."
            )
            % {"components": ", ".join(missing), "company": doc.company.name}
        )
    return found


def validate_posting(doc):
    """Fail early (before any ledger is touched) if the GL lines cannot be built."""
    if doc.rounding_adjustment:
        account = doc.company.round_off_account
        if account is None:
            raise ValidationError(
                _(
                    "The invoice has a rounding adjustment but company '%(company)s' "
                    "has no round-off account."
                )
                % {"company": doc.company.name}
            )
        if account.company_id != doc.company_id or account.is_group:
            raise ValidationError(
                _("The company's round-off account must be a ledger of that company.")
            )
    if doc.GST_DIRECTION == "Purchase" and doc.is_reverse_charge:
        rcm_payable_accounts(doc)


def sales_tax_entries(doc):
    """Tax rows and round-off of a Sales Invoice (the party and income lines are the
    caller's)."""
    entries = []
    for row in tax_rows(doc):
        amount = row.tax_amount
        if not amount:
            continue
        if row.add_deduct_tax == AddDeduct.DEDUCT:
            entries.append(_entry(row.account, debit=amount))
        else:
            entries.append(_entry(row.account, credit=amount))
    entries.extend(_round_off_entry(doc, debit_when_positive=False))
    return entries


def purchase_tax_entries(doc):
    """Tax rows, reverse-charge pairs and round-off of a Purchase Invoice (the
    expense / inventory and party lines are the caller's)."""
    rows = tax_rows(doc)
    rcm = rcm_payable_accounts(doc, rows) if doc.is_reverse_charge else {}
    entries = []
    for row in rows:
        amount = row.tax_amount
        if not amount:
            continue
        if is_memo_row(doc, row):
            entries.append(_entry(row.account, debit=amount))
            entries.append(_entry(rcm[row.account.gst_component], credit=amount))
        elif row.add_deduct_tax == AddDeduct.DEDUCT:
            entries.append(_entry(row.account, credit=amount))
        else:
            entries.append(_entry(row.account, debit=amount))
    entries.extend(_round_off_entry(doc, debit_when_positive=True))
    return entries
