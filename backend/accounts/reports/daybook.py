"""Day Book: every live GL voucher in a period, in voucher order, with daily totals.

Vouchers are grouped by ``(posting_date, voucher_type, voucher_no)`` and ordered by
date, then by the id of their first ledger row (= creation order). Cancelled vouchers
(original and reversal rows are both ``is_cancelled=True``) are not shown -- the same
convention as the General Ledger. Pagination is by voucher; the daily and grand
totals cover the whole period.
"""

from django.db.models import Min, Q, Sum

from accounts.models import GLEntry
from accounts.reports import ZERO, company_block, money, page_block, period_block


def build(company, from_date, to_date, voucher_type, page, size):
    live = GLEntry.objects.filter(
        company=company,
        is_cancelled=False,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    if voucher_type:
        live = live.filter(voucher_type=voucher_type)

    groups = list(
        live.values("posting_date", "voucher_type", "voucher_no")
        .annotate(first_id=Min("id"), dr=Sum("debit"), cr=Sum("credit"))
        .order_by("posting_date", "first_id")
    )

    daily = {}
    total_dr = total_cr = ZERO
    for g in groups:
        day = daily.setdefault(g["posting_date"], {"dr": ZERO, "cr": ZERO, "n": 0})
        day["dr"] += g["dr"] or ZERO
        day["cr"] += g["cr"] or ZERO
        day["n"] += 1
        total_dr += g["dr"] or ZERO
        total_cr += g["cr"] or ZERO

    start = (page - 1) * size
    chosen = groups[start : start + size]
    lines = {}
    if chosen:
        cond = Q()
        for g in chosen:
            cond |= Q(
                posting_date=g["posting_date"],
                voucher_type=g["voucher_type"],
                voucher_no=g["voucher_no"],
            )
        for e in live.filter(cond).select_related("account").order_by("id"):
            lines.setdefault((e.posting_date, e.voucher_type, e.voucher_no), []).append(
                e
            )

    vouchers = []
    for g in chosen:
        entries = lines.get((g["posting_date"], g["voucher_type"], g["voucher_no"]), [])
        party = next((e.party for e in entries if e.party), None)
        remarks = next((e.remarks for e in entries if e.remarks), None)
        vouchers.append(
            {
                "posting_date": g["posting_date"].isoformat(),
                "voucher_type": g["voucher_type"],
                "voucher_no": g["voucher_no"],
                "party": party,
                "remarks": remarks,
                "total_debit": money(g["dr"]),
                "total_credit": money(g["cr"]),
                "lines": [
                    {
                        "account_id": e.account_id,
                        "account": e.account.name,
                        "party": e.party,
                        "debit": money(e.debit),
                        "credit": money(e.credit),
                        "dr_cr": "Dr" if e.debit > 0 else "Cr",
                    }
                    for e in entries
                ],
            }
        )

    return {
        "company": company_block(company),
        "period": period_block(from_date, to_date),
        "vouchers": vouchers,
        "daily_totals": [
            {
                "date": day.isoformat(),
                "vouchers": v["n"],
                "debit": money(v["dr"]),
                "credit": money(v["cr"]),
            }
            for day, v in sorted(daily.items())
        ],
        "totals": {
            "vouchers": len(groups),
            "debit": money(total_dr),
            "credit": money(total_cr),
        },
        "pagination": page_block(page, size, len(groups)),
    }
