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
    def vouchers(self, cy: str) -> List[dict]:
        """All vouchers of a year (cancelled ones flagged), in (date, id) order."""
        return self.rows(
            "select id, row_key, date, guid, vouchertypename, vouchernumber, "
            "narration, iscancelled "
            "from txn_voucher where company_year=%s order by date, id",
            (cy,),
        )

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

    def not_imported_counts(self) -> Dict[str, Optional[int]]:
        qs = {
            "voucher type definitions (mst_vouchertype rows, all years)": "select count(*) from mst_vouchertype",
            "cost categories (mst_costcategory rows)": "select count(*) from mst_costcategory",
            "cost centres (mst_costcentre rows)": "select count(*) from mst_costcentre",
            "voucher lines with a cost centre (vouchers.costcentrename set)": "select count(*) from txn_voucher where costcentrename is not null and costcentrename<>''",
            "bill-wise allocations (txn_voucher_ledger_entry_bill_alloc)": "select count(*) from txn_voucher_ledger_entry_bill_alloc",
            "batch allocations (txn_voucher_inventory_entry_batch_alloc)": "select count(*) from txn_voucher_inventory_entry_batch_alloc",
            "BRS imported info (tally_full.ledger_brsimportedinfo)": "select count(*) from tally_full.ledger_brsimportedinfo",
            "language names (mst_*_languagename_name: ledger/group/item/unit)": "select (select count(*) from mst_ledger_languagename_name)+(select count(*) from mst_group_languagename_name)+(select count(*) from mst_stockitem_languagename_name)",
            "ledger GST details rows (mst_ledger_gstdetails)": "select count(*) from mst_ledger_gstdetails",
            "stock item GST rate rows (mst_stockitem_gstdetails_statewisedetails_ratedetails)": "select count(*) from mst_stockitem_gstdetails_statewisedetails_ratedetails",
            "stock item HSN rows (tally_full.stockitem_hsndetails)": "select count(*) from tally_full.stockitem_hsndetails",
            "ledger GST registration history (mst_ledger_ledgstregdetails)": "select count(*) from mst_ledger_ledgstregdetails",
            "ledger mailing addresses (mst_ledger_address)": "select count(*) from mst_ledger_address",
            "sales/purchase order vouchers (vouchertypename like '%order%')": "select count(*) from txn_voucher where vouchertypename ilike '%order%'",
            "payroll / attendance vouchers (vouchertypename in payroll, attendance)": "select count(*) from txn_voucher where vouchertypename ilike any (array['%payroll%','%attendance%'])",
            "optional / deleted vouchers (isoptional or isdeleted)": "select count(*) from txn_voucher where isoptional or isdeleted",
            "godown masters (mst_godown rows)": "select count(*) from mst_godown",
            "Tally users / security (no table extracted)": "select null::int",
            "cash counters (no table extracted)": "select null::int",
            "employees (no table extracted)": "select null::int",
        }
        return {k: self.count(v) for k, v in qs.items()}


def chunked(seq: Iterable, n: int):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
