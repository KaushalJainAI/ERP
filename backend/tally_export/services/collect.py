"""Which vouchers exist, and which of them are still to be exported.

Everything is derived from the immutable ledgers (``GLEntry``; ``StockEntry``
documents for the quantity-only Stock Journals) and the append-only export log.
"""

import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from django.apps import apps
from django.db.models import Case, Count, IntegerField, Min, When

from accounts.models import GLEntry
from tally_export.constants import NEVER_EXPORTED_DOCTYPES, OPENING_DOCTYPES
from tally_export.models import TallyExportedVoucher

Key = Tuple[str, str]  # (doctype "app.Model", voucher number)


@dataclass
class VoucherRef:
    doctype: str
    name: str
    posting_date: datetime.date
    active: bool  # live postings exist (the document is submitted)
    prev: Optional[TallyExportedVoucher] = None  # latest export record

    @property
    def key(self) -> Key:
        return (self.doctype, self.name)

    @property
    def action(self) -> Optional[str]:
        """What the next export must send: Create, Cancel, or None (nothing)."""
        if self.active:
            return "Create"
        if self.prev is not None and self.prev.action == "Create":
            return "Cancel"
        return None

    @property
    def pending(self) -> bool:
        """New (never exported) or changed since the last export (cancelled)."""
        if self.active:
            return self.prev is None or self.prev.action != "Create"
        return self.action == "Cancel"


def last_exports(company) -> Dict[Key, TallyExportedVoucher]:
    """Latest export record of every voucher of the company."""
    out: Dict[Key, TallyExportedVoucher] = {}
    qs = TallyExportedVoucher.objects.filter(batch__company=company).order_by("id")
    for rec in qs:
        out[(rec.doctype, rec.name)] = rec
    return out


def _excluded(name: str, prefixes) -> bool:
    return any(p and name.startswith(p) for p in prefixes or [])


def list_refs(
    company,
    settings,
    from_date=None,
    to_date=None,
    only_pending=True,
) -> Tuple[List[VoucherRef], List[dict]]:
    """``(refs, skipped)`` for the company in the date range.

    ``only_pending`` drops submitted vouchers that were already exported (they
    can be re-exported on demand with ``only_pending=False``). A cancelled
    voucher is listed only if an earlier export put it in Tally.
    """
    prefixes = settings.excluded_prefixes or []
    gl = GLEntry.objects.filter(company=company)
    if from_date:
        gl = gl.filter(posting_date__gte=from_date)
    if to_date:
        gl = gl.filter(posting_date__lte=to_date)
    for p in prefixes:
        if p:
            gl = gl.exclude(voucher_no__startswith=p)
    rows = gl.values("voucher_type", "voucher_no").annotate(
        live=Count(Case(When(is_cancelled=False, then=1), output_field=IntegerField())),
        first_date=Min("posting_date"),
    )
    found: Dict[Key, VoucherRef] = {}
    for r in rows:
        key = (r["voucher_type"], r["voucher_no"])
        found[key] = VoucherRef(key[0], key[1], r["first_date"], r["live"] > 0)

    # Quantity-only vouchers (Stock Journals) carry no GL: pick them up from the
    # documents themselves.
    from stock.models import StockEntry

    se = StockEntry.objects.filter(company=company, docstatus__in=(1, 2))
    if from_date:
        se = se.filter(posting_date__gte=from_date)
    if to_date:
        se = se.filter(posting_date__lte=to_date)
    for doc in se.only("name", "posting_date", "docstatus"):
        if _excluded(doc.name, prefixes):
            continue
        key = ("stock.StockEntry", doc.name)
        if key not in found:
            found[key] = VoucherRef(
                key[0], key[1], doc.posting_date, doc.docstatus == 1
            )

    skipped: List[dict] = []
    # Opening entries set the books' opening position; Tally has its own.
    openings = set(
        apps.get_model("accounts.JournalEntry")
        .objects.filter(
            name__in=[k[1] for k in found if k[0] == "accounts.JournalEntry"],
            voucher_type="Opening Entry",
        )
        .values_list("name", flat=True)
    )
    # Opening Stock counts (planning 09) are the stock Tally already holds.
    from stock.models import ReconciliationPurpose

    opening_stock = set(
        apps.get_model("stock.StockReconciliation")
        .objects.filter(
            company=company,
            purpose=ReconciliationPurpose.OPENING_STOCK,
            name__in=[k[1] for k in found if k[0] == "stock.StockReconciliation"],
        )
        .values_list("name", flat=True)
    )
    prev = last_exports(company)
    refs: List[VoucherRef] = []
    for key in sorted(found, key=lambda k: (found[k].posting_date, k)):
        ref = found[key]
        ref.prev = prev.get(key)
        if key[0] == "accounts.JournalEntry" and key[1] in openings:
            skipped.append({**_skip(ref), "reason": "opening_entry"})
            continue
        if key[0] == "stock.StockReconciliation" and key[1] in opening_stock:
            skipped.append({**_skip(ref), "reason": "opening_stock"})
            continue
        if key[0] in OPENING_DOCTYPES:
            skipped.append({**_skip(ref), "reason": "opening_bill"})
            continue
        if key[0] in NEVER_EXPORTED_DOCTYPES:
            skipped.append({**_skip(ref), "reason": "period_closing"})
            continue
        if ref.action is None:
            continue  # cancelled and never in Tally: nothing to tell it
        if only_pending and not ref.pending:
            continue
        refs.append(ref)
    return refs, skipped


def _skip(ref: VoucherRef) -> dict:
    return {
        "doctype": ref.doctype,
        "name": ref.name,
        "posting_date": ref.posting_date.isoformat(),
    }


def pending_counts(company, settings, from_date=None, to_date=None) -> dict:
    refs, skipped = list_refs(company, settings, from_date, to_date, only_pending=True)
    return {
        "new": sum(1 for r in refs if r.action == "Create"),
        "cancelled": sum(1 for r in refs if r.action == "Cancel"),
        "total": len(refs),
        "skipped": len(skipped),
    }
