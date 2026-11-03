"""GSTR-1 (outward supplies) from submitted Sales Invoices.

Pure aggregation over the per-line GST columns (``cgst_amount`` / ``sgst_amount`` /
``igst_amount`` / ``gst_rate`` / ``gst_treatment`` / ``gst_hsn_code``), so the
figures always agree with what the tax engine stored and what the GL posted.

Sections (docs/gst_design.md section 6.1): ``b2b``, ``b2cl``, ``b2cs``, ``exports``,
``nil_exempt_non_gst``, ``hsn`` (B2B / B2C split), ``docs_issued``, ``warnings``.
Credit notes (a Sales Invoice with ``is_return``, negative amounts) are reported in
``cdnr`` (to registered persons) and ``cdnur`` (exports, and inter-State supplies to
unregistered persons above the B2CL threshold); the rest are netted, negative, into
``b2cs`` (GSTN rule). Every figure keeps its sign, so the summary, ``b2cs``, ``nil_exempt_non_gst``
and ``hsn`` are already net of returns.
Only ``Taxable`` lines appear in the invoice-bearing sections (their tax columns are
what GSTN wants per rate); Nil-Rated / Exempted / Non-GST lines are reported in
``nil_exempt_non_gst`` (and, bar Non-GST, in ``hsn``).
"""

import re
from collections import defaultdict
from decimal import Decimal

from core.models import DocStatus, GSTCategory
from gst.constants import GSTTreatment, b2cl_threshold
from gst.reports import (
    ZERO,
    company_block,
    company_state,
    money,
    period_block,
    rate_str,
)
from selling.models import SalesInvoice

REGISTERED_CATEGORIES = (
    GSTCategory.REGISTERED_REGULAR,
    GSTCategory.REGISTERED_COMPOSITION,
    GSTCategory.SEZ,
)
DEFAULT_UQC = "OTH"
QTY_QUANT = Decimal("0.001")

NIL_BUCKETS = (
    ("inter_registered", "Inter-State supplies to registered persons"),
    ("intra_registered", "Intra-State supplies to registered persons"),
    ("inter_unregistered", "Inter-State supplies to unregistered persons"),
    ("intra_unregistered", "Intra-State supplies to unregistered persons"),
)
NIL_KEYS = {
    GSTTreatment.NIL_RATED: "nil",
    GSTTreatment.EXEMPTED: "exempt",
    GSTTreatment.NON_GST: "non_gst",
}

_SERIES_RE = re.compile(r"^(?P<prefix>.*?)(?P<seq>\d+)$")
_AMENDMENT_SUFFIX_RE = re.compile(r"-\d+$")


class _Tax:
    """Running taxable value / IGST / CGST / SGST."""

    __slots__ = ("taxable", "igst", "cgst", "sgst")

    def __init__(self):
        self.taxable = self.igst = self.cgst = self.sgst = ZERO

    def add(self, line):
        self.taxable += line.amount
        self.igst += line.igst_amount
        self.cgst += line.cgst_amount
        self.sgst += line.sgst_amount

    @property
    def total(self):
        return self.taxable + self.igst + self.cgst + self.sgst

    def as_dict(self):
        return {
            "taxable_value": money(self.taxable),
            "igst": money(self.igst),
            "cgst": money(self.cgst),
            "sgst": money(self.sgst),
        }


def is_inter_state(invoice, state):
    """Inter-State for GST: SEZ / export always; otherwise place of supply != company state."""
    if invoice.gst_category in (GSTCategory.SEZ, GSTCategory.OVERSEAS):
        return True
    return (invoice.place_of_supply_id or state) != state


def _is_registered(invoice):
    return bool(invoice.party_gstin) and invoice.gst_category in REGISTERED_CATEGORIES


def _section(invoice, state):
    if invoice.gst_category == GSTCategory.OVERSEAS:
        return "exports"
    if _is_registered(invoice):
        return "b2b"
    if is_inter_state(invoice, state) and invoice.rounded_total > b2cl_threshold(
        invoice.posting_date
    ):
        return "b2cl"
    return "b2cs"


def _return_section(invoice, state):
    """Where a credit note goes: ``cdnr``, ``cdnur`` or netted into ``b2cs``."""
    if invoice.gst_category == GSTCategory.OVERSEAS:
        return "cdnur"
    if _is_registered(invoice):
        return "cdnr"
    # GSTN: a credit note to an unregistered person is CDNUR only when the
    # original invoice was B2CL; otherwise it is netted in B2CS.
    basis = (
        invoice.return_against.rounded_total
        if invoice.return_against_id
        else (abs(invoice.rounded_total))
    )
    if is_inter_state(invoice, state) and basis > b2cl_threshold(invoice.posting_date):
        return "cdnur"
    return "b2cs"


def _note_row(invoice, taxable_lines, state):
    row = _invoice_row(invoice, taxable_lines, state)
    original = invoice.return_against
    return {
        **row,
        "note_type": "C",
        "note_value": row["invoice_value"],
        "original_invoice": original.name if original else None,
        "original_date": original.posting_date.isoformat() if original else None,
    }


def _invoice_type(invoice):
    if invoice.gst_category == GSTCategory.SEZ:
        return (
            "SEZ supplies with payment"
            if invoice.is_export_with_gst
            else "SEZ supplies without payment"
        )
    return "Regular"


def _invoice_row(invoice, taxable_lines, state):
    by_rate = defaultdict(_Tax)
    for line in taxable_lines:
        by_rate[line.gst_rate].add(line)
    return {
        "name": invoice.name,
        "posting_date": invoice.posting_date.isoformat(),
        "invoice_value": money(invoice.rounded_total),
        "place_of_supply": invoice.place_of_supply_id or state,
        "reverse_charge": "N",
        "invoice_type": _invoice_type(invoice),
        "rates": [
            {"rate": rate_str(rate), **by_rate[rate].as_dict()}
            for rate in sorted(by_rate)
        ],
    }


def _series_key(invoice):
    """``(prefix, seq)`` of a document name; an amendment (``X-0007-1``) belongs to
    the series of its original."""
    name = invoice.name
    if invoice.amended_from_id:
        name = _AMENDMENT_SUFFIX_RE.sub("", name)
    match = _SERIES_RE.match(name)
    if match:
        return match.group("prefix"), int(match.group("seq"))
    return name, 0


def _docs_issued(documents):
    series = defaultdict(list)
    for doc in documents:
        prefix, seq = _series_key(doc)
        series[prefix].append((seq, doc.name, doc.docstatus))
    rows = []
    for prefix in sorted(series):
        entries = sorted(series[prefix])
        rows.append(
            {
                "series": prefix,
                "from": entries[0][1],
                "to": entries[-1][1],
                "total": len(entries),
                "cancelled": sum(1 for e in entries if e[2] == DocStatus.CANCELLED),
            }
        )
    return rows


def _group_key(section, invoice, state):
    if section == "b2b":
        return (invoice.party_gstin, invoice.customer.name)
    if section == "b2cl":
        return invoice.place_of_supply_id or state
    return "WPAY" if invoice.is_export_with_gst else "WOPAY"


def build(company, from_date, to_date):
    state = company_state(company)
    documents = list(
        SalesInvoice.objects.filter(
            company=company,
            posting_date__gte=from_date,
            posting_date__lte=to_date,
            docstatus__in=(DocStatus.SUBMITTED, DocStatus.CANCELLED),
            # Tally-era invoices were filed from Tally, not from the ERP.
            is_historical=False,
        )
        .select_related("customer", "return_against")
        .prefetch_related("items__item__stock_uom")
        .order_by("posting_date", "name")
    )
    # Cancelled invoices only count in "documents issued"; legacy (pre-GST) ones are
    # excluded from every table and surfaced as a warning.
    live = [d for d in documents if d.docstatus == DocStatus.SUBMITTED]
    legacy = [d for d in live if not d.gst_category]
    invoices = [d for d in live if d.gst_category]

    summary = _Tax()
    invoice_value = ZERO
    sections = {"b2b": {}, "b2cl": {}, "exports": {}, "cdnr": {}, "cdnur": {}}
    b2cs = defaultdict(_Tax)
    nil = {key: dict.fromkeys(NIL_KEYS.values(), ZERO) for key, _label in NIL_BUCKETS}
    hsn = {"b2b": {}, "b2c": {}}
    lines_without_hsn = 0

    for inv in invoices:
        invoice_value += inv.rounded_total
        section = _return_section(inv, state) if inv.is_return else _section(inv, state)
        inter = is_inter_state(inv, state)
        registered = _is_registered(inv)
        taxable_lines = []

        for line in inv.items.all():
            if line.gst_treatment == GSTTreatment.TAXABLE:
                taxable_lines.append(line)
                summary.add(line)
            elif inv.gst_category != GSTCategory.OVERSEAS:
                bucket = nil[
                    f"{'inter' if inter else 'intra'}_"
                    f"{'registered' if registered else 'unregistered'}"
                ]
                bucket[NIL_KEYS[line.gst_treatment]] += line.amount

            # HSN summary: every line except Non-GST goods.
            if line.gst_treatment == GSTTreatment.NON_GST:
                continue
            if not line.gst_hsn_code:
                lines_without_hsn += 1
                continue
            uqc = line.item.stock_uom.gst_uqc or DEFAULT_UQC
            key = (line.gst_hsn_code, uqc, line.gst_rate)
            entry = hsn["b2b" if registered else "b2c"].setdefault(
                key, {"qty": Decimal("0"), "tax": _Tax()}
            )
            entry["qty"] += line.qty
            entry["tax"].add(line)

        if not taxable_lines:
            continue
        if section == "b2cs":
            supply_type = "INTER" if inter else "INTRA"
            for line in taxable_lines:
                key = (inv.place_of_supply_id or state, supply_type, line.gst_rate)
                b2cs[key].add(line)
            continue
        if section == "cdnr":
            sections[section].setdefault(
                (inv.party_gstin, inv.customer.name), []
            ).append(_note_row(inv, taxable_lines, state))
        elif section == "cdnur":
            kind = (
                ("EXPWP" if inv.is_export_with_gst else "EXPWOP")
                if inv.gst_category == GSTCategory.OVERSEAS
                else "B2CL"
            )
            sections[section].setdefault(kind, []).append(
                _note_row(inv, taxable_lines, state)
            )
        else:
            sections[section].setdefault(_group_key(section, inv, state), []).append(
                _invoice_row(inv, taxable_lines, state)
            )

    return {
        "company": company_block(company),
        "period": period_block(from_date, to_date),
        "summary": {
            "invoice_count": sum(1 for d in invoices if not d.is_return),
            "credit_note_count": sum(1 for d in invoices if d.is_return),
            "taxable_value": money(summary.taxable),
            "igst": money(summary.igst),
            "cgst": money(summary.cgst),
            "sgst": money(summary.sgst),
            "invoice_value": money(invoice_value),
        },
        "b2b": [
            {"party_gstin": gstin, "party_name": name, "invoices": rows}
            for (gstin, name), rows in sorted(sections["b2b"].items())
        ],
        "b2cl": [
            {"place_of_supply": pos, "invoices": rows}
            for pos, rows in sorted(sections["b2cl"].items())
        ],
        "b2cs": [
            {
                "place_of_supply": pos,
                "supply_type": supply_type,
                "rate": rate_str(rate),
                **tax.as_dict(),
            }
            for (pos, supply_type, rate), tax in sorted(b2cs.items())
        ],
        "exports": [
            {"export_type": kind, "invoices": rows}
            for kind, rows in sorted(sections["exports"].items())
        ],
        "cdnr": [
            {"party_gstin": gstin, "party_name": name, "notes": rows}
            for (gstin, name), rows in sorted(sections["cdnr"].items())
        ],
        "cdnur": [
            {"ur_type": kind, "notes": rows}
            for kind, rows in sorted(sections["cdnur"].items())
        ],
        "nil_exempt_non_gst": [
            {
                "description": description,
                "nil_rated": money(nil[key]["nil"]),
                "exempted": money(nil[key]["exempt"]),
                "non_gst": money(nil[key]["non_gst"]),
            }
            for key, description in NIL_BUCKETS
        ],
        "hsn": {
            kind: [
                {
                    "hsn": code,
                    "uqc": uqc,
                    "total_qty": str(entry["qty"].quantize(QTY_QUANT)),
                    "rate": rate_str(rate),
                    "total_value": money(entry["tax"].total),
                    **entry["tax"].as_dict(),
                }
                for (code, uqc, rate), entry in sorted(hsn[kind].items())
            ]
            for kind in ("b2b", "b2c")
        },
        "docs_issued": _docs_issued(documents),
        "warnings": {
            "legacy_invoices_without_gst_data": len(legacy),
            "lines_without_hsn": lines_without_hsn,
        },
    }
