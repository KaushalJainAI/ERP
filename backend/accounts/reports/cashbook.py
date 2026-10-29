"""Cash Book / Bank Book: the ledger of one Cash or Bank account.

``opening_balance`` is the net debit minus credit of every live row before
``from_date``; each row carries a running balance, the *against* account(s) (the other
lines of the same voucher) and the party; ``closing_balance`` = opening + the period's
net. Same cancellation rule as the General Ledger (``is_cancelled=False``).
Balances are debit minus credit (a cash / bank account is an asset), so a negative
balance is an overdraft.
"""

from rest_framework.exceptions import ValidationError

from django.db.models import Q, Sum
from django.utils.translation import gettext as _

from accounts.models import Account, AccountType, GLEntry
from accounts.reports import (
    ZERO,
    _int_param,
    company_block,
    money,
    net_before,
    page_block,
    period_block,
    q,
)
from accounts.reports.tally import tally_lookup

CASH_BANK = (AccountType.CASH, AccountType.BANK)


def resolve_account(params, company):
    account_id = _int_param(params, "account", required=True)
    account = Account.objects.filter(pk=account_id, company=company).first()
    if account is None:
        raise ValidationError(
            {"account": _("A valid account of this company is required.")}
        )
    if account.is_group or account.account_type not in CASH_BANK:
        raise ValidationError(
            {"account": _("The account must be a Cash or Bank account.")}
        )
    return account


def build(company, account, from_date, to_date, page, size):
    base = GLEntry.objects.filter(company=company, account=account, is_cancelled=False)
    agg = base.filter(posting_date__lt=from_date).aggregate(
        dr=Sum("debit"), cr=Sum("credit")
    )
    opening = q((agg["dr"] or ZERO) - (agg["cr"] or ZERO))

    period = base.filter(
        posting_date__gte=from_date, posting_date__lte=to_date
    ).order_by("posting_date", "id")
    agg = period.aggregate(dr=Sum("debit"), cr=Sum("credit"))
    total_dr = q(agg["dr"])
    total_cr = q(agg["cr"])
    count = period.count()

    start = (page - 1) * size
    running = opening + net_before(period, start)
    entries = list(period.select_related("account")[start : start + size])

    others = {}
    if entries:
        cond = Q()
        for e in entries:
            cond |= Q(voucher_type=e.voucher_type, voucher_no=e.voucher_no)
        for o in (
            GLEntry.objects.filter(company=company, is_cancelled=False)
            .filter(cond)
            .exclude(account=account)
            .select_related("account")
            .order_by("id")
        ):
            others.setdefault((o.voucher_type, o.voucher_no), []).append(o)

    tally = tally_lookup(company, [e.voucher_no for e in entries])
    rows = []
    for e in entries:
        against = others.get((e.voucher_type, e.voucher_no), [])
        names = []
        for o in against:
            if o.account.name not in names:
                names.append(o.account.name)
        running += e.debit - e.credit
        rows.append(
            {
                "id": e.pk,
                "posting_date": e.posting_date.isoformat(),
                "voucher_type": e.voucher_type,
                "voucher_no": e.voucher_no,
                "tally": tally.get(e.voucher_no),
                "against": ", ".join(names),
                "party": e.party or next((o.party for o in against if o.party), None),
                "remarks": e.remarks,
                "debit": money(e.debit),
                "credit": money(e.credit),
                "balance": money(running),
            }
        )

    return {
        "company": company_block(company),
        "account": {
            "id": account.pk,
            "name": account.name,
            "account_type": account.account_type,
        },
        "period": period_block(from_date, to_date),
        "opening_balance": money(opening),
        "rows": rows,
        "total_debit": money(total_dr),
        "total_credit": money(total_cr),
        "closing_balance": money(opening + total_dr - total_cr),
        "pagination": page_block(page, size, count),
    }
