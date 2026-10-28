"""GST reports (GSTR-1, GSTR-3B, tax ledger).

Shared helpers: query-parameter parsing (company, period), money formatting and the
response ``company`` / ``period`` blocks. See docs/gst_design.md section 6.

Every report is a pure read over *submitted* documents (``docstatus = 1``) and
non-cancelled GL rows; nothing here writes. Documents with a blank ``gst_category``
are legacy (pre-GST) and are excluded, with a warning count in the response.
"""

import calendar
import datetime
import re
from decimal import Decimal

from rest_framework.exceptions import ValidationError

from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
RATE_QUANT = Decimal("0.001")

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def q(value):
    """Quantize to 2dp (SQL SUM() / Python sums may carry other scales)."""
    return Decimal(value or 0).quantize(CENT)


def money(value):
    """A 2dp money string."""
    return str(q(value))


def rate_str(value):
    """A GST rate as a 3dp string (the column is DecimalField(7, 3))."""
    return str(Decimal(value or 0).quantize(RATE_QUANT))


def _date_param(params, name):
    raw = params.get(name)
    if not raw:
        return None
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise ValidationError({name: _("Enter a valid date (YYYY-MM-DD).")})
    return parsed


def resolve_period(params, required=True):
    """``(from_date, to_date)`` from ``month=YYYY-MM`` or ``from_date`` / ``to_date``.

    ``month`` and the explicit dates are mutually exclusive. Malformed values and
    ``from_date > to_date`` are 400. When ``required`` and nothing is given, 400;
    otherwise the missing side is ``None`` (open-ended).
    """
    month = params.get("month")
    if month:
        if params.get("from_date") or params.get("to_date"):
            raise ValidationError(
                {"month": _("Use either 'month' or 'from_date'/'to_date', not both.")}
            )
        match = _MONTH_RE.match(month.strip())
        if not match:
            raise ValidationError({"month": _("Enter a valid month (YYYY-MM).")})
        year, mon = int(match.group(1)), int(match.group(2))
        last = calendar.monthrange(year, mon)[1]
        return datetime.date(year, mon, 1), datetime.date(year, mon, last)

    from_date = _date_param(params, "from_date")
    to_date = _date_param(params, "to_date")
    if required and (from_date is None or to_date is None):
        raise ValidationError(
            {"month": _("Give a 'month' (YYYY-MM) or both 'from_date' and 'to_date'.")}
        )
    if from_date and to_date and from_date > to_date:
        raise ValidationError({"from_date": _("'from_date' is after 'to_date'.")})
    return from_date, to_date


def resolve_company(params):
    """The ``company`` query parameter as a GST-registered ``Company`` (else 400)."""
    from core.models import Company

    raw = params.get("company")
    if not raw:
        raise ValidationError({"company": _("This query parameter is required.")})
    try:
        company = Company.objects.filter(pk=int(raw)).first()
    except (TypeError, ValueError):
        company = None
    if company is None:
        raise ValidationError({"company": _("A valid company is required.")})
    if not (company.gstin or "").strip():
        raise ValidationError(
            {"company": _("The company has no GSTIN; GST reports are unavailable.")}
        )
    return company


def company_state(company):
    """The company's GST state code (the GSTIN prefix when the master has none)."""
    return company.state_id or (company.gstin or "")[:2]


def company_block(company):
    return {
        "id": company.pk,
        "name": company.name,
        "gstin": company.gstin,
        "state": company_state(company),
    }


def period_block(from_date, to_date):
    return {
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
    }
