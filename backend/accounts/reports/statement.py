"""Party Statement (Statement of Account / Customer Ledger).

Built from the live GL rows on the party's control accounts (Receivable for a
customer, Payable for a supplier) -- exactly the rows
``outstanding.party_balance`` sums -- so the closing balance reconciles to the party
balance in the ledger. Balances are in the party's own sense: a positive customer
balance is owed *by* the customer, a positive supplier balance is owed *to* the
supplier; ``dr_cr`` always names the real ledger side. Invoices, payments, credit /
debit notes and journals all appear because they all post to the control account.

The ageing summary reuses ``outstanding.ageing_report`` as on ``to_date`` (current
outstanding of the open invoices, as everywhere else).
"""

from rest_framework.exceptions import ValidationError

from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import AccountType, GLEntry
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
from accounts.services import outstanding as outstanding_service

PARTY_TYPES = ("Customer", "Supplier")


def party_model(party_type):
    from buying.models import Supplier
    from selling.models import Customer

    return {"Customer": Customer, "Supplier": Supplier}[party_type]


def model_label(party_type):
    return {"Customer": "selling.Customer", "Supplier": "buying.Supplier"}[party_type]


def resolve_party(params, company):
    party_type = params.get("party_type")
    if party_type not in PARTY_TYPES:
        raise ValidationError(
            {"party_type": _("party_type must be 'Customer' or 'Supplier'.")}
        )
    party_id = _int_param(params, "party", required=True)
    party = party_model(party_type).objects.filter(pk=party_id).first()
    if party is None or party.company_id != company.pk:
        raise ValidationError(
            {"party": _("A valid party of this company is required.")}
        )
    return party_type, party


def _side(natural, receivable):
    """Real ledger side of a balance given in the party's own sense."""
    if natural == 0:
        return ""
    debit_side = natural > 0 if receivable else natural < 0
    return "Dr" if debit_side else "Cr"


def build(company, party_type, party, from_date, to_date, page, size):
    receivable = party_type == "Customer"
    sign = 1 if receivable else -1
    base = GLEntry.objects.filter(
        company=company,
        party_type=party_type,
        party=party.name,
        account__account_type=(
            AccountType.RECEIVABLE if receivable else AccountType.PAYABLE
        ),
        is_cancelled=False,
    )
    agg = base.filter(posting_date__lt=from_date).aggregate(
        dr=Sum("debit"), cr=Sum("credit")
    )
    opening = q(((agg["dr"] or ZERO) - (agg["cr"] or ZERO)) * sign)

    period = base.filter(
        posting_date__gte=from_date, posting_date__lte=to_date
    ).order_by("posting_date", "id")
    agg = period.aggregate(dr=Sum("debit"), cr=Sum("credit"))
    total_dr = q(agg["dr"])
    total_cr = q(agg["cr"])
    count = period.count()
    closing = opening + (total_dr - total_cr) * sign

    start = (page - 1) * size
    running = opening + net_before(period, start) * sign
    page_rows = list(period[start : start + size])
    tally = tally_lookup(company, [e.voucher_no for e in page_rows])
    rows = []
    for e in page_rows:
        running += (e.debit - e.credit) * sign
        rows.append(
            {
                "id": e.pk,
                "posting_date": e.posting_date.isoformat(),
                "voucher_type": e.voucher_type,
                "voucher_no": e.voucher_no,
                "tally": tally.get(e.voucher_no),
                "against_voucher_type": e.against_voucher_type,
                "against_voucher_no": e.against_voucher_no,
                "remarks": e.remarks,
                "debit": money(e.debit),
                "credit": money(e.credit),
                "balance": money(running),
                "dr_cr": _side(running, receivable),
            }
        )

    ledger_balance = outstanding_service.party_balance(
        party_type, party.name, company.pk
    )
    return {
        "company": company_block(company),
        "party_type": party_type,
        "party": {
            "id": party.pk,
            "name": party.name,
            "gstin": party.gstin,
            "phone": party.phone,
            "email": party.email,
        },
        "period": period_block(from_date, to_date),
        "opening_balance": money(opening),
        "opening_dr_cr": _side(opening, receivable),
        "rows": rows,
        "total_debit": money(total_dr),
        "total_credit": money(total_cr),
        "closing_balance": money(closing),
        "closing_dr_cr": _side(closing, receivable),
        # The party's balance over *all* dates; differs from the closing balance only
        # by entries posted after ``to_date``.
        "ledger_balance": money(ledger_balance),
        "ageing": outstanding_service.ageing_report(
            party_type, as_on=to_date, company_id=company.pk, party=party.name
        ),
        "pagination": page_block(page, size, count),
    }
