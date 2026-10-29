"""Tally identity of imported vouchers, shared by the register reports.

Every Tally voucher became a ``TLY-<yy><yy>-NNNNNN`` Journal Entry, a name that means
nothing to the business; they know the voucher by Tally's own type and number
("Receipt 2526/1"). Reports attach that as an additive ``tally`` object on each row
whose voucher is such a Journal Entry (``None`` for every other row), resolved with one
bulk, company-scoped query per page of rows -- never per row.
"""

from legacy.models import TallyRecord, TallyRecordKind

TALLY_PREFIX = "TLY-"
_CHUNK = 2000


def tally_lookup(company, voucher_nos):
    """``{TLY name: {"id", "voucher_type", "voucher_no"}}`` for the TLY-* names given."""
    names = {n for n in voucher_nos if n and n.startswith(TALLY_PREFIX)}
    found = {}
    if not names:
        return found
    ordered = sorted(names)
    for i in range(0, len(ordered), _CHUNK):
        records = (
            TallyRecord.objects.filter(
                company=company,
                kind=TallyRecordKind.VOUCHER,
                erp_name__in=ordered[i : i + _CHUNK],
            )
            .only("id", "erp_name", "voucher_type", "voucher_no")
            .order_by("id")
        )
        for r in records:
            found.setdefault(
                r.erp_name,
                {
                    "id": r.pk,
                    "voucher_type": r.voucher_type,
                    "voucher_no": r.voucher_no,
                },
            )
    return found
