"""Accounting registers and the party statement (read-only reports).

Sales / Purchase Register, Day Book, Cash / Bank Book and the Statement of Account
(``docs/reports.md``). Every report is a pure read over the immutable ``GLEntry``
ledger and the submitted invoices; nothing here writes.

Shared conventions
------------------
* ``company`` is **required** (every report is scoped to one company's books) and so is
  the period ``from_date`` / ``to_date``; malformed or inverted input is a 400.
* Cancellation follows the General Ledger / tax ledger rule: cancelling a voucher marks
  its original rows *and* its reversal rows ``is_cancelled=True``, so filtering
  ``is_cancelled=False`` leaves exactly the live vouchers (both halves of a cancelled
  pair are hidden, as in ERPNext's default "hide cancelled entries" view).
* Money is a 2dp string. Totals always cover the **whole** filtered range; only the
  rows are paginated (``page`` / ``page_size``, default 500, max 5000).
"""

from decimal import Decimal

from rest_framework.exceptions import ValidationError

from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000


def q(value):
    """Quantize to 2dp (SQL SUM() / Python sums may carry other scales)."""
    return Decimal(value or 0).quantize(CENT)


def money(value):
    """A 2dp money string."""
    return str(q(value))


def _date_param(params, name, required):
    raw = params.get(name)
    if not raw:
        if required:
            raise ValidationError({name: _("This query parameter is required.")})
        return None
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise ValidationError({name: _("Enter a valid date (YYYY-MM-DD).")})
    return parsed


def resolve_period(params):
    """``(from_date, to_date)``, both required, ``from_date <= to_date``."""
    from_date = _date_param(params, "from_date", True)
    to_date = _date_param(params, "to_date", True)
    if from_date > to_date:
        raise ValidationError({"from_date": _("'from_date' is after 'to_date'.")})
    return from_date, to_date


def _int_param(params, name, required=False):
    raw = params.get(name)
    if raw in (None, ""):
        if required:
            raise ValidationError({name: _("This query parameter is required.")})
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: _("Enter a valid id.")})


def resolve_company(params):
    from core.models import Company

    company_id = _int_param(params, "company", required=True)
    company = Company.objects.filter(pk=company_id).first()
    if company is None:
        raise ValidationError({"company": _("A valid company is required.")})
    return company


def resolve_page(params):
    """``(page, page_size)`` from the query string (1-based page)."""
    page = _int_param(params, "page") or 1
    size = _int_param(params, "page_size") or DEFAULT_PAGE_SIZE
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise ValidationError(
            {
                "page_size": _(
                    "'page' must be >= 1 and 'page_size' between 1 and %(max)d."
                )
                % {"max": MAX_PAGE_SIZE}
            }
        )
    return page, size


def page_block(page, size, count):
    return {
        "page": page,
        "page_size": size,
        "count": count,
        "num_pages": max(1, -(-count // size)),
    }


def company_block(company):
    return {"id": company.pk, "name": company.name, "gstin": company.gstin}


def period_block(from_date, to_date):
    return {"from_date": from_date.isoformat(), "to_date": to_date.isoformat()}


def net_before(qs, start):
    """Sum of ``debit - credit`` over the first ``start`` rows of an ordered ledger
    queryset (the running-balance base of a later page)."""
    if start <= 0:
        return ZERO
    net = ZERO
    for debit, credit in qs.values_list("debit", "credit")[:start]:
        net += debit - credit
    return net
