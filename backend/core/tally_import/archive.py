"""Lossless archive of the Tally books, item-wise history and party addresses.

``archive_masters`` / ``archive_year`` write one ``legacy.TallyRecord`` per Tally
master and voucher of every year (the XML exactly as Tally exported it, plus a
link to the ERP document it became), so nothing the importer does not map is
lost. ``archive_year`` also writes ``legacy.TallyItemLine`` for every inventory
line (item, qty, rate, party, ledger), the history the GL-only import of the
old years cannot show. ``import_addresses`` turns Tally mailing details into
``contacts.Address``.

None of this touches a ledger: rows are inserted, never updated.
"""

from decimal import Decimal
from typing import Dict, List, Optional

from core.tally_import.masters import clean_name, state_code_from_name

#: tally_full master table -> TallyRecord.kind
MASTER_TABLES = (
    ("group", "group"),
    ("ledger", "ledger"),
    ("stockgroup", "stock_group"),
    ("stockitem", "stock_item"),
    ("unit", "unit"),
    ("godown", "godown"),
    ("vouchertype", "voucher_type"),
    ("costcategory", "cost_category"),
    ("currency", "currency"),
)

BATCH = 1000


def _cut(value, n: int) -> str:
    return (str(value) if value is not None else "").strip()[:n]


def _link(doctype: str, name: Optional[str]) -> List[dict]:
    return [{"doctype": doctype, "name": name}] if name else []


def ledger_links(chart, fy: str, ledger: str) -> List[dict]:
    kind = chart.kind.get(fy, {}).get(ledger)
    if kind == "debtor":
        return _link("selling.Customer", chart.customers.get(ledger))
    if kind == "creditor":
        return _link("buying.Supplier", chart.suppliers.get(ledger))
    acct = chart.accounts.get(ledger) or chart.group_accounts.get(ledger)
    return _link("accounts.Account", acct.name if acct else None)


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------


def archive_masters(src, chart, sm, years, log=print) -> Dict[str, int]:
    """One TallyRecord per master per year. Skips a (year, kind) already archived."""
    from legacy.models import TallyRecord

    company = chart.company
    counts: Dict[str, int] = {}
    for y in years:
        done = set(
            TallyRecord.objects.filter(company=company, company_year=y.cy)
            .exclude(kind="voucher")
            .values_list("kind", flat=True)
            .distinct()
        )
        rows: List[TallyRecord] = []
        if "company" not in done:
            for r in src.company_raw(y.cy):
                rows.append(
                    TallyRecord(
                        company=company,
                        kind="company",
                        company_year=y.cy,
                        fiscal_year=y.fy,
                        guid=y.cy,
                        name=company.name,
                        erp_links=_link("core.Company", company.name),
                        erp_name=_cut(company.name, 60),
                        raw=r["raw_xml"] or "",
                    )
                )
        for table, kind in MASTER_TABLES:
            if kind in done:
                continue
            seen = set()
            for r in src.master_raw(table, y.cy):
                guid = _cut(r["guid"] or r["name"], 100)
                if guid in seen:
                    continue
                seen.add(guid)
                name = r["name"] or ""
                if kind in ("ledger", "group"):
                    links = ledger_links(chart, y.fy, name)
                elif kind == "stock_item":
                    item = sm.items.get(name)
                    links = _link("stock.Item", item.code if item else None)
                else:
                    links = []
                rows.append(
                    TallyRecord(
                        company=company,
                        kind=kind,
                        company_year=y.cy,
                        fiscal_year=y.fy,
                        guid=guid,
                        name=_cut(name, 200),
                        erp_links=links,
                        erp_name=_cut(links[0]["name"] if links else "", 60),
                        raw=r["raw_xml"] or "",
                    )
                )
        TallyRecord.objects.bulk_create(rows, batch_size=BATCH)
        for r in rows:
            counts[r.kind] = counts.get(r.kind, 0) + 1
    log(f"  archived masters: {sum(counts.values())}")
    return counts


# --------------------------------------------------------------------------
# Vouchers + item lines
# --------------------------------------------------------------------------


def item_line_values(ln: dict, uom_map: Dict[str, str]) -> dict:
    """Plain TallyItemLine values of one Tally inventory line (pure; see tests).
    Tally signs amounts (negative = debit); the direction is ``isdeemedpositive``."""

    def pos(v, places):
        if v is None:
            return None
        return abs(Decimal(v)).quantize(Decimal(1).scaleb(-places))

    unit = ln.get("actualqty_unit") or ""
    return {
        "item_name": _cut(ln["stockitemname"], 200),
        "is_inward": bool(ln["isdeemedpositive"]),
        "qty": pos(ln.get("actualqty_num"), 3),
        "billed_qty": pos(ln.get("billedqty_num"), 3),
        "uom": _cut(uom_map.get(unit, unit), 20),
        "rate": pos(ln.get("rate_num"), 6),
        "discount_percent": pos(ln.get("discount") or None, 4),
        "amount": pos(ln.get("amount") or 0, 2),
        "ledger": _cut(ln.get("ledgername"), 200),
    }


def archive_year(
    src,
    chart,
    sm,
    y,
    je_links: Dict[str, str],
    stock_links: Dict[str, List[str]],
    log=print,
) -> Dict[str, int]:
    """TallyRecord for every voucher of the year (cancelled included) and
    TallyItemLine for every inventory line of a non-cancelled voucher."""
    from buying.models import Supplier
    from legacy.models import TallyItemLine, TallyRecord
    from selling.models import Customer

    company = chart.company
    vouchers = src.vouchers(y.cy)
    raw = {r["row_key"]: r["raw_xml"] for r in src.voucher_raw(y.cy)}
    rows = []
    for v in vouchers:
        links = _link("accounts.JournalEntry", je_links.get(v["row_key"]))
        links += [
            {"doctype": "stock.StockEntry", "name": n}
            for n in stock_links.get(v["row_key"], [])
        ]
        rows.append(
            TallyRecord(
                company=company,
                kind="voucher",
                company_year=y.cy,
                fiscal_year=y.fy,
                guid=_cut(v["guid"], 100),
                voucher_type=_cut(v["vouchertypename"], 100),
                base_voucher_type=_cut(v["base_type"], 40),
                voucher_no=_cut(v["vouchernumber"], 100),
                posting_date=v["date"],
                party=_cut(v["partyledgername"], 200),
                amount=Decimal(v["amount"]) if v["amount"] is not None else None,
                reference=_cut(v["reference"], 200),
                narration=v["narration"] or "",
                is_cancelled=bool(v["iscancelled"]),
                erp_links=links,
                erp_name=links[0]["name"] if links else "",
                raw=raw.get(v["row_key"]) or "",
            )
        )
    TallyRecord.objects.bulk_create(rows, batch_size=BATCH)
    rec_pk = dict(
        TallyRecord.objects.filter(
            company=company, company_year=y.cy, kind="voucher"
        ).values_list("guid", "pk")
    )

    customers = dict(Customer.objects.filter(company=company).values_list("name", "pk"))
    suppliers = dict(Supplier.objects.filter(company=company).values_list("name", "pk"))
    kind = chart.kind.get(y.fy, {})
    vinfo = {v["row_key"]: v for v in vouchers}
    lines = []
    for ln in src.item_lines(y.cy):
        v = vinfo.get(ln["voucher_key"])
        if v is None or v["iscancelled"]:
            continue
        party = v["partyledgername"] or ""
        pk = kind.get(party)
        cust = customers.get(chart.customers.get(party)) if pk == "debtor" else None
        supp = suppliers.get(chart.suppliers.get(party)) if pk == "creditor" else None
        item = sm.items.get(ln["stockitemname"])
        lines.append(
            TallyItemLine(
                company=company,
                record_id=rec_pk[_cut(v["guid"], 100)],
                fiscal_year=y.fy,
                posting_date=v["date"],
                voucher_type=_cut(v["base_type"], 40),
                voucher_no=_cut(v["vouchernumber"], 100),
                party=_cut(party, 200),
                customer_id=cust,
                supplier_id=supp,
                item=item,
                place_of_supply=_cut(v["placeofsupply"], 60),
                **item_line_values(ln, sm.uom_map),
            )
        )
    TallyItemLine.objects.bulk_create(lines, batch_size=BATCH)
    log(f"  archived {len(rows)} vouchers, {len(lines)} item lines")
    return {"vouchers": len(rows), "item_lines": len(lines)}


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------


def address_values(ledger: str, data: dict) -> Optional[dict]:
    """contacts.Address values from a Tally mailing record (pure; see tests).
    None when Tally holds no address line. ``city`` is left blank: Tally's
    free-text lines do not say which one is the town."""
    lines = [clean_name(x) for x in data.get("lines") or [] if clean_name(x)]
    if not lines:
        return None
    pin = data.get("pincode")
    pin = str(int(pin)) if isinstance(pin, (int, Decimal)) else _cut(pin, 10)
    return {
        "address_title": _cut(clean_name(data.get("mailingname")) or ledger, 200),
        "address_line1": _cut(lines[0], 240),
        "address_line2": _cut(", ".join(lines[1:]), 240),
        "pincode": pin if len(pin) == 6 and pin.isdigit() and pin[0] != "0" else "",
        "pincode_raw": pin,
        "state_id": state_code_from_name(data.get("state")),
        "country": _cut(clean_name(data.get("country")) or "India", 60),
    }


def import_addresses(src, chart, log=print) -> dict:
    """Primary billing Address for every Customer / Supplier ledger that has
    a Tally mailing address (latest year). Parties that already have one are
    left alone."""
    from buying.models import Supplier
    from contacts.models import Address
    from selling.models import Customer

    company = chart.company
    stats = {"created": 0, "no_lines": 0, "not_a_party": 0, "bad_pincode": []}
    customers = {c.name: c for c in Customer.objects.filter(company=company)}
    suppliers = {s.name: s for s in Supplier.objects.filter(company=company)}
    for ledger, data in sorted(src.ledger_addresses().items()):
        parties = []
        if ledger in chart.customers:
            parties.append(("customer", customers.get(chart.customers[ledger])))
        if ledger in chart.suppliers:
            parties.append(("supplier", suppliers.get(chart.suppliers[ledger])))
        parties = [(f, p) for f, p in parties if p is not None]
        if not parties:
            stats["not_a_party"] += 1
            continue
        vals = address_values(ledger, data)
        if vals is None:
            stats["no_lines"] += 1
            continue
        raw_pin = vals.pop("pincode_raw")
        if raw_pin and not vals["pincode"]:
            stats["bad_pincode"].append((ledger, raw_pin))
        for field, party in parties:
            if Address.objects.filter(**{field: party}).exists():
                continue
            Address.objects.create(
                **vals,
                gstin=party.gstin or None,
                is_primary_address=True,
                **{field: party},
            )
            stats["created"] += 1
    log(f"  addresses: {stats['created']} created")
    return stats
