"""GST tax ledger: the GL on the accounts tagged with ``Account.gst_direction``.

Same balance convention as the general ledger report (debit minus credit, so Output
and Reverse Charge accounts run negative) and the same cancellation rule: a cancelled
voucher's original rows *and* its reversal rows are both ``is_cancelled=True``, so
filtering ``is_cancelled=False`` leaves exactly the live vouchers.
"""

from rest_framework.exceptions import ValidationError

from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import Account, GLEntry
from gst.constants import GSTComponent, GSTDirection
from gst.reports import ZERO, company_block, money, period_block, q

COMPONENT_ORDER = (
    GSTComponent.CGST,
    GSTComponent.SGST,
    GSTComponent.IGST,
    GSTComponent.CESS,
)
DIRECTION_ORDER = {
    GSTDirection.OUTPUT: 0,
    GSTDirection.INPUT: 1,
    GSTDirection.REVERSE_CHARGE: 2,
}


def _choice_param(params, name, choices):
    raw = params.get(name)
    if not raw:
        return None
    for value in choices:
        if value.lower() == raw.strip().lower():
            return value
    raise ValidationError(
        {name: _("Must be one of: %(values)s.") % {"values": ", ".join(choices)}}
    )


def _voucher_parties(live, period):
    """Party of each voucher in ``period``: tax rows carry no party themselves, the
    receivable / payable row of the same voucher does."""
    vouchers = {(e.voucher_type, e.voucher_no) for e in period}
    if not vouchers:
        return {}
    rows = live.filter(
        voucher_no__in={no for _t, no in vouchers}, party__isnull=False
    ).values_list("voucher_type", "voucher_no", "party")
    return {(t, no): party for t, no, party in rows if (t, no) in vouchers}


def _bill_nos(movements):
    """``{voucher_no: supplier bill no}`` of the Purchase Invoices among ``movements``
    (the reference the accountant claims input credit against)."""
    from buying.models import PurchaseInvoice

    names = {
        e.voucher_no for e in movements if e.voucher_type == "buying.PurchaseInvoice"
    }
    if not names:
        return {}
    return dict(
        PurchaseInvoice.objects.filter(name__in=names)
        .exclude(bill_no="")
        .values_list("name", "bill_no")
    )


def build(company, from_date, to_date, params):
    component = _choice_param(params, "component", GSTComponent.values)
    direction = _choice_param(params, "direction", GSTDirection.values)
    summary = params.get("summary", "").strip().lower() in ("1", "true", "yes")

    accounts = Account.objects.filter(company=company, gst_direction__isnull=False)
    if component:
        accounts = accounts.filter(gst_component=component)
    if direction:
        accounts = accounts.filter(gst_direction=direction)
    accounts = sorted(
        accounts,
        key=lambda a: (
            COMPONENT_ORDER.index(a.gst_component),
            DIRECTION_ORDER[a.gst_direction],
            a.name,
        ),
    )

    live = GLEntry.objects.filter(company=company, is_cancelled=False)
    net = {}  # (component, direction) -> net credit over the period
    rows = []
    for account in accounts:
        base = live.filter(account=account)
        opening = ZERO
        if from_date:
            agg = base.filter(posting_date__lt=from_date).aggregate(
                dr=Sum("debit"), cr=Sum("credit")
            )
            opening = q((agg["dr"] or ZERO) - (agg["cr"] or ZERO))
        period = base.filter(posting_date__lte=to_date) if to_date else base
        if from_date:
            period = period.filter(posting_date__gte=from_date)

        movements = list(period.order_by("posting_date", "id"))
        parties = {} if summary else _voucher_parties(live, movements)
        bill_nos = {} if summary else _bill_nos(movements)
        running = opening
        total_debit = total_credit = ZERO
        entries = []
        for entry in movements:
            running += entry.debit - entry.credit
            total_debit += entry.debit
            total_credit += entry.credit
            if not summary:
                entries.append(
                    {
                        "posting_date": entry.posting_date.isoformat(),
                        "voucher_type": entry.voucher_type,
                        "voucher_no": entry.voucher_no,
                        "supplier_bill_no": bill_nos.get(entry.voucher_no),
                        "party": entry.party
                        or parties.get((entry.voucher_type, entry.voucher_no)),
                        "debit": money(entry.debit),
                        "credit": money(entry.credit),
                        "balance": money(running),
                    }
                )
        key = (account.gst_component, account.gst_direction)
        net[key] = net.get(key, ZERO) + (total_credit - total_debit)
        row = {
            "account_id": account.pk,
            "account_name": account.name,
            "direction": account.gst_direction,
            "component": account.gst_component,
            "opening_balance": money(opening),
            "total_debit": money(total_debit),
            "total_credit": money(total_credit),
            "closing_balance": money(running),
        }
        if not summary:
            row["entries"] = entries
        rows.append(row)

    by_component = {}
    for comp in COMPONENT_ORDER:
        if not any(a.gst_component == comp for a in accounts):
            continue
        output = net.get((comp, GSTDirection.OUTPUT), ZERO)
        rcm = net.get((comp, GSTDirection.REVERSE_CHARGE), ZERO)
        # Input accounts are debit-normal: ITC is the net debit.
        itc = -net.get((comp, GSTDirection.INPUT), ZERO)
        by_component[comp.value] = {
            "output": money(output),
            "input": money(itc),
            "rcm": money(rcm),
            "net_payable": money(output + rcm - itc),
        }

    return {
        "company": company_block(company),
        "period": period_block(from_date, to_date),
        "accounts": rows,
        "net_by_component": by_component,
    }
