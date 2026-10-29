"""Read-only SQL access to the Tally Postgres database.

The connection is opened with ``default_transaction_read_only=on`` so a bug here
can never write to the source. Every method returns plain Python structures
(``dict`` rows, ``Decimal`` numerics) so the rest of the importer never touches
a cursor.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "postgresql://postgres:tally@127.0.0.1:5432/tally"


@dataclass(frozen=True)
class YearInfo:
    cy: str  # Tally company_year folder id, e.g. "010010"
    fy: str  # "2024-25"
    first_voucher: Optional[datetime.date]
    last_voucher: Optional[datetime.date]


class TallySource:
    def __init__(self, dsn: str = DEFAULT_DSN):
        self.conn = psycopg.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        )

    def close(self):
        self.conn.close()

    # -- generic -----------------------------------------------------------
    def rows(self, sql: str, params=None) -> List[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())

    def scalar(self, sql: str, params=None):
        r = self.rows(sql, params)
        if not r:
            return None
        return next(iter(r[0].values()))

    # -- years -------------------------------------------------------------
    def years(self) -> List[YearInfo]:
        rows = self.rows(
            "select company_year, fy, first_voucher, last_voucher "
            "from company order by fy"
        )
        return [
            YearInfo(r["company_year"], r["fy"], r["first_voucher"], r["last_voucher"])
            for r in rows
        ]

    # -- accounting masters ------------------------------------------------
    def groups(self, cy: str) -> List[dict]:
        return self.rows(
            "select name, reservedname, parent, isrevenue, isdeemedpositive, "
            "issubledger, affectsgrossprofit "
            "from mst_group where company_year=%s order by id",
            (cy,),
        )

    def ledgers(self, cy: str) -> List[dict]:
        return self.rows(
            "select name, reservedname, parent, isrevenue, partygstin, "
            "incometaxnumber, openingbalance, closingbalance, gstregistrationtype, "
            "ledstatename "
            "from mst_ledger where company_year=%s order by id",
            (cy,),
        )

    # -- stock masters -----------------------------------------------------
    def units(self) -> List[dict]:
        return self.rows(
            "select distinct name, originalname from mst_unit order by name"
        )

    def stock_groups(self, cy: str) -> List[dict]:
        return self.rows(
            "select name, parent from mst_stockgroup where company_year=%s order by id",
            (cy,),
        )

    def stock_items(self, cy: str) -> List[dict]:
        return self.rows(
            "select name, parent, baseunits, openingbalance_num, openingrate_num, "
            "openingvalue, closingbalance_num, closingvalue, closingrate_num "
            "from mst_stockitem where company_year=%s order by id",
            (cy,),
        )

    def stock_hsn_xml(self) -> List[dict]:
        """(company_year, name, raw_xml) of tally_full.stockitem. HSN lives in the
        XML as text; tally_full.stockitem_hsndetails.hsncode is numeric and has
        lost its leading zeros."""
        return self.rows("select company_year, name, raw_xml from tally_full.stockitem")

    def stock_taxability(self) -> List[dict]:
        """Per (company_year, item): GST taxability of the latest-applicable row."""
        return self.rows(
            "select distinct on (s.company_year, s.name) s.company_year, s.name, "
            "g.taxability, g.isnongstgoods "
            "from mst_stockitem s join mst_stockitem_gstdetails g "
            "on g.parent_key = s.row_key "
            "order by s.company_year, s.name, g.applicablefrom desc nulls last, g.id desc"
        )

    # -- vouchers ----------------------------------------------------------
    def voucher_type_bases(self, cy: str) -> Dict[str, str]:
        """voucher type name -> Tally base type (Sales, Receipt, ...): the
        reserved name reached by following ``parent`` up the chain."""
        rows = self.rows(
            "select name, parent, reservedname from mst_vouchertype "
            "where company_year=%s",
            (cy,),
        )
        parent = {r["name"]: r["parent"] for r in rows}
        reserved = {r["name"] for r in rows if r["reservedname"]}
        out: Dict[str, str] = {}
        for name in parent:
            cur, seen = name, set()
            while cur not in reserved and parent.get(cur) and cur not in seen:
                seen.add(cur)
                cur = parent[cur]
            out[name] = cur
        return out

    def vouchers(self, cy: str) -> List[dict]:
        """All vouchers of a year (cancelled ones flagged), in (date, id) order,
        with ``base_type`` = the Tally base voucher type."""
        rows = self.rows(
            "select id, row_key, date, guid, vouchertypename, vouchernumber, "
            "narration, iscancelled, reference, partyledgername, amount, "
            "placeofsupply "
            "from txn_voucher where company_year=%s order by date, id",
            (cy,),
        )
        bases = self.voucher_type_bases(cy)
        for r in rows:
            r["base_type"] = bases.get(r["vouchertypename"], r["vouchertypename"])
        return rows

    def stream(self, sql: str, params=None, size: int = 2000):
        """Yield rows of a large query through a server-side cursor."""
        with self.conn.transaction():
            with self.conn.cursor(name="tally_stream") as cur:
                cur.itersize = size
                cur.execute(sql, params or ())
                yield from cur

    def master_raw(self, table: str, cy: str):
        """(guid, name, raw_xml) of one tally_full master table for a year."""
        return self.stream(
            f'select guid, name, raw_xml from tally_full."{table}" '
            "where company_year=%s order by id",
            (cy,),
        )

    def company_raw(self, cy: str) -> List[dict]:
        return self.rows(
            "select raw_xml from tally_full.company_ref where company_year=%s", (cy,)
        )

    def voucher_raw(self, cy: str):
        """(row_key, raw_xml) of every voucher of a year."""
        return self.stream(
            "select row_key, raw_xml from txn_voucher where company_year=%s order by id",
            (cy,),
        )

    def item_lines(self, cy: str) -> List[dict]:
        """Inventory lines of a year with the ledger each was booked to."""
        return self.rows(
            "select e.id, e.voucher_key, e.stockitemname, e.isdeemedpositive, "
            "e.amount, e.actualqty_num, e.billedqty_num, e.actualqty_unit, "
            "e.rate_num, e.discount, a.ledgername "
            "from txn_voucher_inventory_entry e "
            "left join txn_voucher_inventory_entry_accounting_alloc a "
            "on a.parent_key = e.row_key and a.company_year = e.company_year "
            "where e.company_year=%s order by e.id",
            (cy,),
        )

    def ledger_addresses(self) -> Dict[str, dict]:
        """Ledger name -> latest year's mailing data: ``lines`` (address rows in
        order), ``pincode``, ``state``, ``country``, ``mailingname``."""
        rows = self.rows(
            "select l.company_year, c.fy, l.name, l.pincode, l.ledstatename, "
            "l.countryname, l.mailingname, a.address "
            "from mst_ledger l join company c on c.company_year = l.company_year "
            "join mst_ledger_address a on a.master_key = l.row_key "
            "and a.company_year = l.company_year "
            "order by c.fy, l.name, a.id"
        )
        out: Dict[str, dict] = {}
        for r in rows:
            cur = out.get(r["name"])
            if cur is None or cur["fy"] != r["fy"]:
                cur = out[r["name"]] = {
                    "fy": r["fy"],
                    "lines": [],
                    "pincode": r["pincode"],
                    "state": r["ledstatename"],
                    "country": r["countryname"],
                    "mailingname": r["mailingname"],
                }
            if (r["address"] or "").strip():
                cur["lines"].append(r["address"].strip())
        return out

    def item_gst_rates(self) -> Dict[str, Decimal]:
        """Stock item -> IGST rate of its latest-applicable GST detail row
        (latest year first)."""
        rows = self.rows(
            "select distinct on (s.name) s.name, r.gstrate "
            "from mst_stockitem s join company c on c.company_year = s.company_year "
            "join mst_stockitem_gstdetails g on g.parent_key = s.row_key "
            "and g.company_year = s.company_year "
            "join mst_stockitem_gstdetails_statewisedetails w "
            "on w.parent_key = g.row_key and w.company_year = g.company_year "
            "join mst_stockitem_gstdetails_statewisedetails_ratedetails r "
            "on r.parent_key = w.row_key and r.company_year = w.company_year "
            "where r.gstratedutyhead = 'IGST' "
            "order by s.name, c.fy desc, g.applicablefrom desc nulls last, g.id desc"
        )
        return {r["name"]: Decimal(r["gstrate"] or 0) for r in rows}

    def voucher_lines(self, cy: str) -> Dict[str, List[tuple]]:
        """voucher row_key -> [(ledgername, amount)] = ledger entries followed
        by inventory accounting allocations (together they net to zero)."""
        out: Dict[str, List[tuple]] = defaultdict(list)
        with self.conn.cursor() as cur:
            cur.execute(
                "select voucher_key, ledgername, amount from ("
                " select voucher_key, ledgername, amount, 0 as src, id "
                "   from txn_voucher_ledger_entry where company_year=%s"
                " union all"
                " select voucher_key, ledgername, amount, 1 as src, id "
                "   from txn_voucher_inventory_entry_accounting_alloc "
                "   where company_year=%s"
                ") t order by voucher_key, src, id",
                (cy, cy),
            )
            for r in cur:
                out[r["voucher_key"]].append((r["ledgername"], r["amount"]))
        return out

    def stock_lines(self, cy: str) -> List[dict]:
        return self.rows(
            "select e.id, e.voucher_key, e.stockitemname, e.isdeemedpositive, "
            "e.amount, e.actualqty_num, e.rate_num "
            "from txn_voucher_inventory_entry e where e.company_year=%s "
            "order by e.id",
            (cy,),
        )

    # -- reconciliation inputs ----------------------------------------------
    def ledger_balances(self, cy: str) -> List[dict]:
        """v_ledger_balance rows (opening, computed closing, Tally's own closing)."""
        return self.rows(
            "select ledger, ledger_group, opening, closing, tally_closing "
            "from v_ledger_balance where company_year=%s",
            (cy,),
        )

    def revenue_movement(self, cy: str) -> Decimal:
        """Sum of posting amounts (credit +) on revenue ledgers for a year,
        cancelled vouchers excluded (they carry no lines anyway)."""
        v = self.scalar(
            "select coalesce(sum(p.amount),0) from v_posting p "
            "join mst_ledger l on l.company_year=p.company_year and l.name=p.ledgername "
            "join txn_voucher t on t.row_key=p.voucher_key "
            "where p.company_year=%s and l.isrevenue and not t.iscancelled",
            (cy,),
        )
        return Decimal(v or 0)

    def count(self, sql: str) -> Optional[int]:
        try:
            return int(self.scalar(sql))
        except Exception:  # missing table / column: report as "not present"
            return None

    def archive_source_counts(self) -> Dict[str, int]:
        """Rows in Tally per TallyRecord kind (+ ``item_line``), the denominator
        of the archive-completeness check."""
        from core.tally_import.archive import MASTER_TABLES

        out = {
            "company": self.scalar("select count(*) from tally_full.company_ref"),
            "voucher": self.scalar("select count(*) from txn_voucher"),
            "item_line": self.scalar(
                "select count(*) from txn_voucher_inventory_entry e "
                "join txn_voucher v on v.row_key = e.voucher_key "
                "and v.company_year = e.company_year where not v.iscancelled"
            ),
        }
        for table, kind in MASTER_TABLES:
            out[kind] = self.scalar(
                "select count(distinct (company_year, coalesce(guid, name))) "
                f'from tally_full."{table}"'
            )
        return {k: int(v or 0) for k, v in out.items()}


def chunked(seq: Iterable, n: int):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
