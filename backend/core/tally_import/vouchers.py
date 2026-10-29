"""Opening adjustment, voucher -> JournalEntry, closing stock and period closing.

Every JournalEntry is created as a draft with its lines and then ``.submit()``ed,
so ``GLEntry`` is only ever written by ``accounts.services.gl.post``. Balances
(``Balances``) are an in-memory mirror of the GL used to compute the opening
adjustment and the period-closing entry; they are seeded from the GL at start.
"""

import copy
import time
from collections import defaultdict, namedtuple
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from core.tally_import.masters import (
    ZERO,
    drcr,
    fiscal_year_dates,
    je_name,
    money,
    signed_debit,
)

Key = Tuple[int, Optional[str], Optional[str]]  # (account_id, party_type, party)
Line = namedtuple("Line", "account party_type party debit credit")

BS_ROOTS = ("Asset", "Liability", "Equity")
PL_ROOTS = ("Income", "Expense")


class Balances:
    """Debit-positive running balance per (account, party_type, party)."""

    def __init__(self):
        self.d: Dict[Key, Decimal] = defaultdict(lambda: ZERO)

    @classmethod
    def from_gl(cls) -> "Balances":
        from django.db.models import Sum

        from accounts.models import GLEntry

        b = cls()
        qs = (
            GLEntry.objects.filter(is_cancelled=False)
            .values("account_id", "party_type", "party")
            .annotate(dr=Sum("debit"), cr=Sum("credit"))
        )
        for r in qs:
            b.d[(r["account_id"], r["party_type"], r["party"])] += r["dr"] - r["cr"]
        return b

    def apply(self, lines: List[Line]):
        for ln in lines:
            self.d[(ln.account.pk, ln.party_type, ln.party)] += ln.debit - ln.credit

    def get(self, key: Key) -> Decimal:
        return self.d.get(key, ZERO)

    def snapshot(self):
        return copy.copy(self.d)

    def restore(self, snap):
        self.d = defaultdict(lambda: ZERO, snap)


class JournalSkipped(Exception):
    pass


def post_journal(company, name, posting_date, remark, lines: List[Line], **header):
    """create draft -> lines -> recompute_totals -> submit. Raises on imbalance.
    ``header``: extra JournalEntry fields (voucher_type, cheque_no, bill_no ...)."""
    from django.db.models import Prefetch, prefetch_related_objects

    from accounts.models import JournalEntry, JournalEntryLine

    dr = sum((ln_.debit for ln_ in lines), ZERO)
    cr = sum((ln_.credit for ln_ in lines), ZERO)
    if dr != cr:
        raise JournalSkipped(f"unbalanced: Dr {dr} != Cr {cr}")
    je = JournalEntry(
        name=name,
        posting_date=posting_date,
        company=company,
        user_remark=remark,
        **header,
    )
    je.save()
    JournalEntryLine.objects.bulk_create(
        [
            JournalEntryLine(
                journal_entry=je,
                account=ln_.account,
                debit=ln_.debit,
                credit=ln_.credit,
                party_type=ln_.party_type,
                party=ln_.party,
            )
            for ln_ in lines
        ]
    )
    prefetch_related_objects(
        [je],
        Prefetch(
            "lines",
            queryset=JournalEntryLine.objects.select_related(
                "account__company"
            ).order_by("pk"),
        ),
    )
    je.recompute_totals()
    je.submit()
    return je


def _line_from_debit(account, party_type, party, signed: Decimal) -> Optional[Line]:
    """signed = debit-positive amount."""
    if signed == 0:
        return None
    if signed > 0:
        return Line(account, party_type, party, signed, ZERO)
    return Line(account, party_type, party, ZERO, -signed)


# --------------------------------------------------------------------------
# Line mapping
# --------------------------------------------------------------------------


class LineMapper:
    def __init__(self, chart, fy):
        self.chart = chart
        self.kind = chart.kind[fy]

    def map(self, ledger_name: str, amount) -> Optional[Line]:
        debit, credit = drcr(amount)
        if debit == 0 and credit == 0:
            return None
        kind = self.kind.get(ledger_name)
        c = self.chart
        if kind is None:
            raise JournalSkipped(f"unknown ledger '{ledger_name}'")
        if kind == "debtor":
            return Line(c.debtors, "Customer", c.customers[ledger_name], debit, credit)
        if kind == "creditor":
            return Line(
                c.creditors, "Supplier", c.suppliers[ledger_name], debit, credit
            )
        acct = c.accounts.get(ledger_name)
        if acct is None:
            raise JournalSkipped(f"no account for ledger '{ledger_name}'")
        return Line(acct, None, None, debit, credit)


#: Tally base voucher type -> ERPNext Journal Entry ``voucher_type``.
_JE_TYPE = {
    "Contra": "Contra Entry",
    "Credit Note": "Credit Note",
    "Debit Note": "Debit Note",
}
#: Tally types whose ``reference`` is the supplier's bill number.
_BILL_TYPES = ("Purchase", "Debit Note")


def je_header(base_type: str, reference: Optional[str], account_types) -> dict:
    """JournalEntry header fields for a Tally voucher (pure; see tests)."""
    if base_type in ("Receipt", "Payment"):
        types = set(account_types)
        if "Bank" in types:
            vtype = "Bank Entry"
        elif "Cash" in types:
            vtype = "Cash Entry"
        else:
            vtype = "Journal Entry"
    else:
        vtype = _JE_TYPE.get(base_type, "Journal Entry")
    header = {"voucher_type": vtype}
    ref = (reference or "").strip()[:140]
    if ref:
        header["bill_no" if base_type in _BILL_TYPES else "cheque_no"] = ref
    return header


def voucher_remark(v: dict) -> str:
    parts = [f"Tally {v['vouchertypename']} No. {v['vouchernumber'] or ''}".rstrip()]
    narr = (v.get("narration") or "").strip()
    if narr:
        parts.append(narr)
    parts.append(f"guid {v['guid']}")
    return " | ".join(parts)


# --------------------------------------------------------------------------
# Opening adjustment (6a)
# --------------------------------------------------------------------------


def stock_values(src, cy) -> Tuple[Decimal, Decimal]:
    """(opening, closing) stock value as debit-positive Σ over mst_stockitem."""
    o = c = Decimal(0)
    for r in src.stock_items(cy):
        o += Decimal(r["openingvalue"] or 0)
        c += Decimal(r["closingvalue"] or 0)
    return money(-o), money(-c)


def opening_targets(chart, fy, ledger_rows, stock_open_debit) -> Dict[Key, Decimal]:
    kind = chart.kind[fy]
    t: Dict[Key, Decimal] = defaultdict(lambda: ZERO)
    for row in ledger_rows:
        name = row["name"]
        ob = signed_debit(row["openingbalance"] or 0)
        k = kind[name]
        if k == "debtor":
            t[(chart.debtors.pk, "Customer", chart.customers[name])] += ob
        elif k == "creditor":
            t[(chart.creditors.pk, "Supplier", chart.suppliers[name])] += ob
        else:
            if chart.account_root[name] not in BS_ROOTS:
                continue
            t[(chart.accounts[name].pk, None, None)] += ob
    t[(chart.stock_in_hand.pk, None, None)] += stock_open_debit
    return t


def opening_lines(chart, bal: Balances, targets: Dict[Key, Decimal]):
    """Lines moving the ERP balance of every key to its Tally target, plus the
    balancing line to 'Difference in Opening Balances'. Returns (lines, plug)."""
    accounts = chart.all_account_ids()
    diff_pk = chart.diff_opening.pk
    keys = set(targets) | {k for k, v in bal.d.items() if v != 0}
    keys.discard((diff_pk, None, None))
    lines: List[Line] = []
    net = ZERO
    for key in sorted(keys, key=lambda k: (k[0], k[1] or "", k[2] or "")):
        delta = targets.get(key, ZERO) - bal.get(key)
        ln = _line_from_debit(accounts[key[0]], key[1], key[2], delta)
        if ln:
            lines.append(ln)
            net += delta
    plug = -net  # debit-positive amount for the Difference account
    ln = _line_from_debit(chart.diff_opening, None, None, plug)
    if ln:
        lines.append(ln)
    return lines, plug


# --------------------------------------------------------------------------
# Year import
# --------------------------------------------------------------------------


class Progress:
    def __init__(self, log, every=1000):
        self.log, self.every, self.n, self.t0 = log, every, 0, time.time()

    def tick(self, label):
        self.n += 1
        if self.n % self.every == 0:
            self.log(
                f"  [{time.time() - self.t0:7.0f}s] {self.n} journal entries ({label})"
            )


def import_year(
    src,
    chart,
    y,
    bal: Balances,
    progress: Progress,
    completed: bool,
    log=print,
    links: Optional[Dict[str, str]] = None,
) -> dict:
    """Import one Tally year inside the caller's transaction.atomic().
    ``links`` (if given) receives voucher row_key -> Journal Entry name."""
    from core.models import SystemHealthCheck

    company = chart.company
    fy = y.fy
    start, end = fiscal_year_dates(fy)
    stats = {
        "fy": fy,
        "cy": y.cy,
        "tally_vouchers": 0,
        "cancelled": 0,
        "no_lines": 0,
        "journals": 0,
        "journal_lines": 0,
        "skipped_unbalanced": 0,
        "skipped_error": 0,
        "skipped_examples": [],
        "zero_lines_dropped": 0,
        "opening_lines": 0,
        "opening_plug_debit": "0.00",
        "diff_balance_after_open": "0.00",
        "stock_open_debit": "0.00",
        "stock_close_debit": "0.00",
        "clstk_amount": "0.00",
        "pcv_profit": None,
        "pcv_lines": 0,
        "completed": completed,
    }

    # ---- 6a: opening adjustment ----
    rows = chart.notes["ledgers_by_fy"][fy]
    s_open, s_close = stock_values(src, y.cy)
    targets = opening_targets(chart, fy, rows, s_open)
    lines, plug = opening_lines(chart, bal, targets)
    stats["stock_open_debit"] = str(s_open)
    stats["stock_close_debit"] = str(s_close)
    if lines:
        post_journal(
            company,
            je_name(fy, "OPEN"),
            start,
            f"Tally import: opening balances of FY {fy} as per Tally (1-Apr-{start.year}); "
            f"difference to 'Difference in Opening Balances'",
            lines,
            voucher_type="Opening Entry",
        )
        bal.apply(lines)
        stats["opening_lines"] = len(lines)
    stats["opening_plug_debit"] = str(plug)
    stats["diff_balance_after_open"] = str(bal.get((chart.diff_opening.pk, None, None)))

    # ---- 6b: vouchers ----
    vouchers = src.vouchers(y.cy)
    vlines = src.voucher_lines(y.cy)
    mapper = LineMapper(chart, fy)
    stats["tally_vouchers"] = len(vouchers)
    seq = 0
    for v in vouchers:
        if v["iscancelled"]:
            stats["cancelled"] += 1
            continue
        seq += 1
        raw = vlines.get(v["row_key"], [])
        if not raw:
            stats["no_lines"] += 1
            continue
        name = je_name(fy, seq)
        try:
            jl: List[Line] = []
            for led, amt in raw:
                ln = mapper.map(led, amt)
                if ln is None:
                    stats["zero_lines_dropped"] += 1
                    continue
                jl.append(ln)
            if not jl:
                stats["no_lines"] += 1
                continue
            dr = sum((ln_.debit for ln_ in jl), ZERO)
            cr = sum((ln_.credit for ln_ in jl), ZERO)
            if dr != cr:
                stats["skipped_unbalanced"] += 1
                if len(stats["skipped_examples"]) < 20:
                    stats["skipped_examples"].append(
                        f"{name} {v['vouchertypename']} {v['vouchernumber']} {v['date']}: Dr {dr} != Cr {cr}"
                    )
                continue
            from django.core.exceptions import ValidationError
            from django.db import transaction

            try:
                with transaction.atomic():
                    header = je_header(
                        v["base_type"],
                        v["reference"],
                        (ln_.account.account_type for ln_ in jl),
                    )
                    post_journal(
                        company, name, v["date"], voucher_remark(v), jl, **header
                    )
            except ValidationError as e:
                stats["skipped_error"] += 1
                if len(stats["skipped_examples"]) < 20:
                    stats["skipped_examples"].append(f"{name}: {e}")
                continue
            bal.apply(jl)
            if links is not None:
                links[v["row_key"]] = name
            stats["journals"] += 1
            stats["journal_lines"] += len(jl)
            progress.tick(fy)
        except JournalSkipped as e:
            stats["skipped_error"] += 1
            if len(stats["skipped_examples"]) < 20:
                stats["skipped_examples"].append(f"{name}: {e}")

    # ---- 6c: closing stock + period closing (completed years only) ----
    if completed:
        delta = s_close - s_open  # increase of stock (debit-positive)
        if delta != 0:
            lines = [
                _line_from_debit(chart.stock_in_hand, None, None, delta),
                _line_from_debit(chart.change_in_stock, None, None, -delta),
            ]
            post_journal(
                company,
                je_name(fy, "CLSTK"),
                end,
                f"Tally import: closing stock FY {fy} (Tally closing {s_close} - opening {s_open})",
                lines,
            )
            bal.apply(lines)
        stats["clstk_amount"] = str(delta)

        accounts = chart.all_account_ids()
        pl_lines: List[Line] = []
        net = ZERO
        for key, v in sorted(bal.d.items(), key=lambda kv: kv[0][0]):
            if v == 0:
                continue
            acct = accounts.get(key[0])
            if acct is None or acct.root_type not in PL_ROOTS:
                continue
            ln = _line_from_debit(acct, key[1], key[2], -v)
            pl_lines.append(ln)
            net += v
        if pl_lines:
            # net (debit-positive) of P&L accounts: negative = profit (credit)
            ln = _line_from_debit(chart.pnl, None, None, net)
            if ln:
                pl_lines.append(ln)
            post_journal(
                company,
                je_name(fy, "PCV"),
                end,
                f"Tally import: period closing voucher FY {fy} (P&L to Profit & Loss A/c)",
                pl_lines,
            )
            bal.apply(pl_lines)
            stats["pcv_lines"] = len(pl_lines)
            stats["pcv_profit"] = str(-net)

    SystemHealthCheck.objects.create(
        check_name=f"tally_import:{fy}", status="ok", details=stats
    )
    return stats
