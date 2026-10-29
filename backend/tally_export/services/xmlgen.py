"""Tally Prime import XML (ENVELOPE) from voucher drafts and master records.

Conventions (as Tally exports them): a debit is ``ISDEEMEDPOSITIVE Yes`` with a
*negative* AMOUNT, a credit is ``No`` with a positive AMOUNT; a voucher's
ledger entries plus the accounting allocations of its inventory entries sum to
zero. The output is UTF-8 and every text node is XML-escaped (and stripped of
characters XML cannot carry).
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Iterable, List

from tally_export.services.build import VoucherDraft

XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def clean(text) -> str:
    return _BAD.sub("", "" if text is None else str(text))


def sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: clean(v) for k, v in attrs.items()})
    if text is not None:
        el.text = clean(text)
    return el


def amt(value: Decimal) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01')):f}"


def qty_text(qty: Decimal, unit: str) -> str:
    q = Decimal(qty).quantize(Decimal("0.001")).normalize()
    return f"{q:f} {unit}".strip()


def rate_text(rate: Decimal, unit: str) -> str:
    r = Decimal(rate).quantize(Decimal("0.01"))
    return f"{r:f}/{unit}" if unit else f"{r:f}"


def tally_date(d) -> str:
    return d.strftime("%Y%m%d")


def _yn(flag: bool) -> str:
    return "Yes" if flag else "No"


def signed_for_tally(debit_positive: Decimal) -> Decimal:
    """Debit-positive -> Tally AMOUNT (debit negative)."""
    return -Decimal(debit_positive)


def _ledger_entry(parent, tag, ledger, debit_positive, party=False, bills=()):
    el = sub(parent, tag)
    sub(el, "LEDGERNAME", ledger)
    sub(el, "ISDEEMEDPOSITIVE", _yn(debit_positive > 0))
    if party:
        sub(el, "ISPARTYLEDGER", "Yes")
    sub(el, "AMOUNT", amt(signed_for_tally(debit_positive)))
    for b in bills:
        ba = sub(el, "BILLALLOCATIONS.LIST")
        if b.name:
            sub(ba, "NAME", b.name)
        sub(ba, "BILLTYPE", b.kind)
        sub(ba, "AMOUNT", amt(signed_for_tally(b.amount)))
    return el


def _batch(parent, s, signed_amount: Decimal):
    ba = sub(parent, "BATCHALLOCATIONS.LIST")
    sub(ba, "GODOWNNAME", s.godown)
    sub(ba, "BATCHNAME", s.batch or "Primary Batch")
    sub(ba, "AMOUNT", amt(signed_amount))
    sub(ba, "ACTUALQTY", qty_text(s.qty, s.unit))
    sub(ba, "BILLEDQTY", qty_text(s.qty, s.unit))


def voucher_element(d: VoucherDraft) -> ET.Element:
    v = ET.Element(
        "VOUCHER",
        {
            "REMOTEID": d.remote_id,
            "VCHKEY": "",
            "VCHTYPE": clean(d.tally_type),
            "ACTION": d.action,
        },
    )
    if d.action == "Cancel":
        sub(v, "DATE", tally_date(d.posting_date))
        sub(v, "GUID", d.remote_id)
        sub(v, "VOUCHERTYPENAME", d.tally_type)
        sub(v, "VOUCHERNUMBER", d.name)
        return v

    v.set(
        "OBJVIEW", "Invoice Voucher View" if d.is_invoice else "Accounting Voucher View"
    )
    sub(v, "DATE", tally_date(d.posting_date))
    sub(v, "GUID", d.remote_id)
    sub(v, "VOUCHERTYPENAME", d.tally_type)
    sub(v, "VOUCHERNUMBER", d.name)
    if d.reference:
        sub(v, "REFERENCE", d.reference)
        if d.reference_date:
            sub(v, "REFERENCEDATE", tally_date(d.reference_date))
        elif d.is_invoice:
            sub(v, "REFERENCEDATE", tally_date(d.posting_date))
    if d.narration:
        sub(v, "NARRATION", d.narration)
    if d.party_ledger:
        sub(v, "PARTYLEDGERNAME", d.party_ledger)
        sub(v, "BASICBUYERNAME", d.party_ledger)
    if d.party_gstin:
        sub(v, "PARTYGSTIN", d.party_gstin)
    if d.place_of_supply:
        sub(v, "PLACEOFSUPPLY", d.place_of_supply)
    if d.is_invoice:
        sub(v, "PERSISTEDVIEW", "Invoice Voucher View")
        sub(v, "ISINVOICE", "Yes")

    if d.is_stock_journal:
        for s in d.stock:
            tag = "INVENTORYENTRIESIN.LIST" if s.inward else "INVENTORYENTRIESOUT.LIST"
            el = sub(v, tag)
            sub(el, "STOCKITEMNAME", s.item_name)
            sub(el, "ISDEEMEDPOSITIVE", _yn(not s.inward))
            sub(el, "RATE", rate_text(s.rate, s.unit))
            signed = s.amount if s.inward else -s.amount
            sub(el, "AMOUNT", amt(signed))
            sub(el, "ACTUALQTY", qty_text(s.qty, s.unit))
            sub(el, "BILLEDQTY", qty_text(s.qty, s.unit))
            _batch(el, s, signed)
        return v

    for e in d.entries:
        _ledger_entry(
            v, "ALLLEDGERENTRIES.LIST", e.ledger, e.amount, e.is_party, e.bills
        )
    for s in d.stock:
        el = sub(v, "ALLINVENTORYENTRIES.LIST")
        sub(el, "STOCKITEMNAME", s.item_name)
        sub(el, "ISDEEMEDPOSITIVE", _yn(s.inward))
        sub(el, "RATE", rate_text(s.rate, s.unit))
        signed = -s.amount if s.inward else s.amount
        sub(el, "AMOUNT", amt(signed))
        sub(el, "ACTUALQTY", qty_text(s.qty, s.unit))
        sub(el, "BILLEDQTY", qty_text(s.qty, s.unit))
        _batch(el, s, signed)
        acc = sub(el, "ACCOUNTINGALLOCATIONS.LIST")
        sub(acc, "LEDGERNAME", s.ledger)
        sub(acc, "ISDEEMEDPOSITIVE", _yn(s.inward))
        sub(acc, "AMOUNT", amt(signed))
    return v


def _text(el: ET.Element) -> str:
    return ET.tostring(el, encoding="unicode")


def voucher_xml(d: VoucherDraft) -> str:
    """Canonical (unindented) XML of one voucher: what the hash covers."""
    return _text(voucher_element(d))


def voucher_hash(d: VoucherDraft) -> str:
    return hashlib.sha256(voucher_xml(d).encode("utf-8")).hexdigest()


def envelope(report: str, company_name: str, messages: Iterable[ET.Element]) -> bytes:
    """``<ENVELOPE>`` for ``REPORTNAME`` = ``Vouchers`` or ``All Masters``."""
    env = ET.Element("ENVELOPE")
    head = sub(env, "HEADER")
    sub(head, "TALLYREQUEST", "Import Data")
    body = sub(env, "BODY")
    imp = sub(body, "IMPORTDATA")
    desc = sub(imp, "REQUESTDESC")
    sub(desc, "REPORTNAME", report)
    sv = sub(desc, "STATICVARIABLES")
    sub(sv, "SVCURRENTCOMPANY", company_name)
    data = sub(imp, "REQUESTDATA")
    for m in messages:
        tm = sub(data, "TALLYMESSAGE", **{"xmlns:UDF": "TallyUDF"})
        tm.append(m)
    ET.indent(env, space=" ")
    return (XML_HEAD + _text(env) + "\n").encode("utf-8")


def vouchers_file(company_name: str, drafts: List[VoucherDraft]) -> bytes:
    return envelope("Vouchers", company_name, [voucher_element(d) for d in drafts])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------


def ledger_master(
    name: str,
    parent: str,
    *,
    billwise=False,
    gstin="",
    state="",
    address=(),
    pincode="",
    reg_type="",
    country="India",
) -> ET.Element:
    el = ET.Element("LEDGER", {"NAME": clean(name), "ACTION": "Create"})
    nl = sub(el, "NAME.LIST")
    sub(nl, "NAME", name)
    sub(el, "PARENT", parent)
    if billwise:
        sub(el, "ISBILLWISEON", "Yes")
    if address or pincode:
        md = sub(el, "LEDMAILINGDETAILS.LIST")
        if address:
            al = sub(md, "ADDRESS.LIST")
            for line in address:
                sub(al, "ADDRESS", line)
        if pincode:
            sub(md, "PINCODE", pincode)
        sub(md, "MAILINGNAME", name)
        if state:
            sub(md, "STATE", state)
        sub(md, "COUNTRY", country)
    if state:
        sub(el, "LEDSTATENAME", state)
    sub(el, "COUNTRYNAME", country)
    if reg_type:
        sub(el, "GSTREGISTRATIONTYPE", reg_type)
    if gstin:
        sub(el, "PARTYGSTIN", gstin)
    return el


def unit_master(name: str) -> ET.Element:
    el = ET.Element("UNIT", {"NAME": clean(name), "ACTION": "Create"})
    nl = sub(el, "NAME.LIST")
    sub(nl, "NAME", name)
    sub(el, "ISSIMPLEUNIT", "Yes")
    return el


def stock_item_master(name: str, unit: str, hsn="", gst_rate=None, taxable=True):
    el = ET.Element("STOCKITEM", {"NAME": clean(name), "ACTION": "Create"})
    nl = sub(el, "NAME.LIST")
    sub(nl, "NAME", name)
    sub(el, "BASEUNITS", unit)
    if hsn or gst_rate is not None:
        gd = sub(el, "GSTDETAILS.LIST")
        sub(gd, "APPLICABLEFROM", "20170701")
        if hsn:
            sub(gd, "HSNCODE", hsn)
        sub(gd, "TAXABILITY", "Taxable" if taxable else "Exempt")
        if gst_rate is not None and taxable:
            sw = sub(gd, "STATEWISEDETAILS.LIST")
            sub(sw, "STATENAME", "Any")
            rd = sub(sw, "RATEDETAILS.LIST")
            sub(rd, "GSTRATEDUTYHEAD", "IGST")
            sub(rd, "GSTRATE", f"{Decimal(gst_rate).normalize():f}")
    return el


def masters_file(company_name: str, masters: List[ET.Element]) -> bytes:
    return envelope("All Masters", company_name, masters)
