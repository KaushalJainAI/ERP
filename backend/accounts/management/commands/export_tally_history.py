"""Export the Tally vouchers, with their item lines, to a portable history file.

Reads the external ``tally-pg`` Postgres extract (read-only) and writes
``tally_history.jsonl.gz``: one JSON object per Tally voucher with its type,
number, date, party, header amount, ledger lines, item lines and the ``TLY-*``
Journal Entry it became in the ERP. The file is what goes to the server, so
``import_tally_history`` never needs ``tally-pg`` there.

Matching a voucher to its journal. The Tally import wrote each voucher as a
``TLY-<yy><yy>-NNNNNN`` Journal Entry whose ``user_remark`` starts with
``Tally <type> No. <number>`` (see ``accounts/reports/tally.py``). A voucher matches
the journal with the same financial year (name prefix), voucher type, number and
date. When that key is not unique the journal whose total equals the voucher's
total is taken, if exactly one does; otherwise (and when a ``guid`` written in the
remark identifies it) the voucher is left unmatched with the reason in
``match_error``. Nothing is guessed: an unmatched voucher is reported and later
skipped by the import.

Needs the ERP database the journals live in (a local restore of production): the
journal names written here are the names on the server.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from accounts.models import JournalEntry
from accounts.reports.tally import _REMARK, TALLY_PREFIX
from core.models import Company, DocStatus

_GUID = re.compile(r"\bguid\s+([^\s|]+)")
CENT = Decimal("0.01")


def fy_code(fy: str) -> str:
    """``"2024-25"`` -> ``"2425"`` (the year part of a ``TLY-2425-...`` name)."""
    m = re.fullmatch(r"\s*(\d{2})?(\d{2})\s*-\s*(\d{2})?(\d{2})\s*", fy or "")
    if not m:
        return ""
    return m.group(2) + m.group(4)


def norm_no(no: str | None) -> str:
    return (no or "").strip()


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


class JournalIndex:
    """TLY journals of one company, keyed the way a Tally voucher identifies them."""

    def __init__(self, company: Company):
        self.by_key: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        self.by_guid: dict[str, dict[str, Any]] = {}
        self.unparsed = 0
        rows = JournalEntry.objects.filter(
            company=company,
            name__startswith=TALLY_PREFIX,
            docstatus=DocStatus.SUBMITTED,
        ).values_list("name", "posting_date", "user_remark", "total_debit")
        for name, posting_date, remark, total in rows.iterator(chunk_size=5000):
            m = _REMARK.match(remark or "")
            if not m:
                self.unparsed += 1
                continue
            info = {
                "name": name,
                "date": str(posting_date),
                "type": m.group("type").strip(),
                "no": norm_no(m.group("no")),
                "total": _dec(total).quantize(CENT),
            }
            year = name[len(TALLY_PREFIX) : len(TALLY_PREFIX) + 4]
            self.by_key[(year, info["type"], info["no"], info["date"])].append(info)
            g = _GUID.search(remark or "")
            if g:
                self.by_guid[g.group(1)] = info
        self.count = sum(len(v) for v in self.by_key.values())

    def match(self, *, fy, vchtype, number, date, total, guid):
        """``(journal_name, None)`` or ``(None, reason)``."""
        if guid and guid in self.by_guid:
            return self.by_guid[guid]["name"], None
        key = (fy_code(fy), vchtype.strip(), norm_no(number), date)
        candidates = self.by_key.get(key, [])
        if not candidates:
            return None, "no TLY journal with this type, number and date"
        if len(candidates) == 1:
            return candidates[0]["name"], None
        same_total = [c for c in candidates if c["total"] == total]
        if len(same_total) == 1:
            return same_total[0]["name"], None
        return None, (
            f"{len(candidates)} TLY journals share this type, number and date "
            f"({', '.join(c['name'] for c in candidates[:5])})"
        )


class Command(BaseCommand):
    help = (
        "Export Tally vouchers with item lines and their TLY journal names to "
        "tally_history.jsonl.gz (reads tally-pg, read-only)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tally-url",
            default=os.environ.get(
                "TALLY_DATABASE_URL", "postgresql://postgres:tally@127.0.0.1:5432/tally"
            ),
            help="Connection URL of the tally-pg extract (read-only).",
        )
        parser.add_argument("--output", default="tally_history.jsonl.gz")
        parser.add_argument(
            "--company", help="ERP company name (default: the only one)."
        )
        parser.add_argument(
            "--year", help="Only this Tally financial year, e.g. 2024-25."
        )

    def handle(self, *args, **options):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise CommandError("psycopg is required to read tally-pg.") from exc

        company = resolve_company(options.get("company"))
        index = JournalIndex(company)
        self.stdout.write(
            f"ERP: {index.count} TLY journals indexed for {company.name}"
            + (
                f" ({index.unparsed} without a 'Tally <type> No.' remark)"
                if index.unparsed
                else ""
            )
        )

        try:
            conn = psycopg.connect(options["tally_url"])
        except Exception as exc:
            raise CommandError(f"Could not connect to tally-pg: {exc}") from exc
        conn.read_only = True
        with conn:
            records = list(read_tally_pg(conn, options.get("year")))

        stats = write_history(records, index, options["output"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {stats['total']} vouchers to {options['output']}"
            )
        )
        self.stdout.write(f"  matched to a TLY journal: {stats['matched']}")
        self.stdout.write(f"  unmatched:                {stats['unmatched']}")
        for reason, n in stats["reasons"].most_common():
            self.stdout.write(f"    {n:6}  {reason}")
        self.stdout.write("Vouchers by Tally type:")
        for vt, n in stats["types"].most_common():
            self.stdout.write(f"  {n:6}  {vt}")


def resolve_company(name: str | None) -> Company:
    if name:
        try:
            return Company.objects.get(name=name)
        except Company.DoesNotExist as exc:
            raise CommandError(f"Company '{name}' not found.") from exc
    companies = list(Company.objects.all()[:2])
    if len(companies) != 1:
        raise CommandError("Pass --company: the database has more than one company.")
    return companies[0]


def read_tally_pg(conn, fy_filter: str | None):
    """Yield one dict per non-cancelled voucher of the tally-pg extract."""
    cur = conn.cursor()
    sql = """
        SELECT v.row_key, v.guid, c.fy, v.vchtype, v.vouchernumber, v.reference,
               v.date, v.partyledgername, v.narration, v.amount
        FROM txn_voucher v JOIN company c USING (company_year)
        WHERE NOT v.iscancelled
    """
    params: list[Any] = []
    if fy_filter:
        sql += " AND c.fy = %s"
        params.append(fy_filter)
    sql += " ORDER BY v.date, v.row_key"
    cur.execute(sql, params)
    vouchers = cur.fetchall()

    items: dict[Any, list[dict[str, str]]] = defaultdict(list)
    cur.execute("""
        SELECT voucher_key, stockitemname, actualqty_num, billedqty_num,
               rate_num, rate_unit, amount
        FROM txn_voucher_inventory_entry ORDER BY row_key
        """)
    for key, name, act, billed, rate, unit, amt in cur.fetchall():
        items[key].append(
            {
                "stockitemname": (name or "").strip(),
                "actualqty": "" if act is None else str(act),
                "billedqty": "" if billed is None else str(billed),
                "rate": "" if rate is None else str(rate),
                "rate_unit": (unit or "").strip(),
                "amount": "" if amt is None else str(amt),
            }
        )

    ledgers: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    cur.execute("""
        SELECT voucher_key, ledgername, amount, isdeemedpositive
        FROM txn_voucher_ledger_entry ORDER BY row_key
        """)
    for key, name, amt, positive in cur.fetchall():
        ledgers[key].append(
            {
                "ledgername": (name or "").strip(),
                "amount": "" if amt is None else str(amt),
                "isdeemedpositive": bool(positive),
            }
        )

    for (
        row_key,
        guid,
        fy,
        vchtype,
        vno,
        ref,
        vdate,
        party,
        narration,
        amount,
    ) in vouchers:
        yield {
            "guid": (guid or "").strip(),
            "voucher_key": row_key,
            "fy": fy,
            "vchtype": (vchtype or "").strip(),
            "vouchernumber": norm_no(vno),
            "reference": (ref or "").strip(),
            "date": str(vdate),
            "party": (party or "").strip(),
            "narration": (narration or "").strip(),
            "header_amount": "" if amount is None else str(amount),
            "inventory_entries": items.get(row_key, []),
            "ledger_entries": ledgers.get(row_key, []),
        }


def voucher_total(rec: dict[str, Any]) -> Decimal:
    """Debit side of the voucher: half the sum of its absolute ledger amounts."""
    total = sum(
        (abs(_dec(le["amount"] or 0)) for le in rec["ledger_entries"]), Decimal(0)
    )
    return (total / 2).quantize(CENT)


def party_amount(rec: dict[str, Any]) -> str | None:
    """Absolute amount of the voucher's party ledger line(s), or None."""
    lines = [
        le
        for le in rec["ledger_entries"]
        if le["ledgername"] and le["ledgername"] == rec["party"]
    ]
    if not lines:
        return None
    return str(
        abs(sum((_dec(le["amount"] or 0) for le in lines), Decimal(0))).quantize(CENT)
    )


def write_history(records, index: JournalIndex, output: str) -> dict[str, Any]:
    """Match each record to its journal and write the JSONL.GZ file."""
    stats: dict[str, Any] = {
        "total": 0,
        "matched": 0,
        "unmatched": 0,
        "reasons": Counter(),
        "types": Counter(),
    }
    used: dict[str, str] = {}
    with gzip.open(output, "wt", encoding="utf-8") as out:
        for rec in records:
            name, reason = index.match(
                fy=rec["fy"],
                vchtype=rec["vchtype"],
                number=rec["vouchernumber"],
                date=rec["date"],
                total=voucher_total(rec),
                guid=rec.get("guid"),
            )
            if name and name in used:
                reason = f"journal {name} already matched to voucher {used[name]}"
                name = None
            if name:
                used[name] = str(rec.get("voucher_key"))
                stats["matched"] += 1
            else:
                stats["unmatched"] += 1
                stats["reasons"][reason] += 1
            stats["total"] += 1
            stats["types"][rec["vchtype"]] += 1
            rec = {
                **rec,
                "journal_name": name,
                "match_error": reason,
                "party_amount": party_amount(rec),
            }
            out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return stats
