"""Tally identity of imported vouchers, shared by the register reports.

Every Tally voucher became a ``TLY-<yy><yy>-NNNNNN`` Journal Entry, a name that means
nothing to the business; they know the voucher by Tally's own type and number
("Receipt 2526/1"). Reports attach that as an additive ``tally`` object on each row
whose voucher is such a Journal Entry (``None`` for every other row). The type and
number are read from the entry's ``user_remark`` ("Tally <type> No. <no> | ..."), with
one bulk, company-scoped query per page of rows -- never per row.
"""

import re

from accounts.models import JournalEntry

TALLY_PREFIX = "TLY-"
_CHUNK = 2000
_REMARK = re.compile(r"^Tally (?P<type>.+?) No\. (?P<no>[^|]*?)\s*(?:\||$)")


def tally_lookup(company, voucher_nos):
    """``{TLY name: {"id", "voucher_type", "voucher_no"}}`` for the TLY-* names given."""
    names = {n for n in voucher_nos if n and n.startswith(TALLY_PREFIX)}
    found = {}
    if not names:
        return found
    ordered = sorted(names)
    for i in range(0, len(ordered), _CHUNK):
        rows = JournalEntry.objects.filter(
            company=company, name__in=ordered[i : i + _CHUNK]
        ).values_list("id", "name", "user_remark")
        for pk, name, remark in rows:
            m = _REMARK.match(remark or "")
            if m:
                found[name] = {
                    "id": pk,
                    "voucher_type": m.group("type"),
                    "voucher_no": m.group("no").strip(),
                }
    return found
