"""GSTR-3B (summary return) from submitted Sales and Purchase Invoices.

Aggregates the per-line GST columns, never the document totals: a reverse-charge
purchase invoice carries its tax in memo rows (``total_taxes_and_charges`` is 0 and
``grand_total == net_total``) while the lines still hold the CGST / SGST / IGST.

Tables (docs/gst_design.md section 6.2): 3.1(a)-(e), 3.2, 4, 5 and the informational
6.1 (output minus ITC per head; no cash / credit ledger offset order). Sections the
v1 data model cannot produce (import of goods / services, ISD, ITC reversals,
ineligible ITC, cess) return zeros.
"""

from collections import defaultdict

from buying.models import PurchaseInvoice
from core.models import DocStatus, GSTCategory
from gst.constants import GSTTreatment
from gst.reports import ZERO, company_block, company_state, money, period_block
from selling.models import SalesInvoice

HEADS = ("igst", "cgst", "sgst")
EXPORT_LIKE = (GSTCategory.SEZ, GSTCategory.OVERSEAS)
#: Customer categories listed in table 3.2 (inter-State supplies to non-registered).
TABLE_3_2_CATEGORIES = {
    GSTCategory.UNREGISTERED: "Unregistered",
    GSTCategory.CONSUMER: "Unregistered",
    GSTCategory.REGISTERED_COMPOSITION: "Composition",
}
NON_TAXABLE = (
    GSTTreatment.NIL_RATED,
    GSTTreatment.EXEMPTED,
    GSTTreatment.NON_GST,
)


class _Tax:
    """Running taxable value and tax per head."""

    def __init__(self):
        self.taxable = ZERO
        self.igst = ZERO
        self.cgst = ZERO
        self.sgst = ZERO

    def add(self, line):
        self.taxable += line.amount
        self.igst += line.igst_amount
        self.cgst += line.cgst_amount
        self.sgst += line.sgst_amount

    def tax(self, head):
        return getattr(self, head)

    def full(self):
        return {
            "taxable_value": money(self.taxable),
            "igst": money(self.igst),
            "cgst": money(self.cgst),
            "sgst": money(self.sgst),
            "cess": money(ZERO),
        }

    def tax_only(self):
        """The ITC shape: tax per head, no taxable value."""
        return {
            "igst": money(self.igst),
            "cgst": money(self.cgst),
            "sgst": money(self.sgst),
            "cess": money(ZERO),
        }


def _zero_tax():
    return _Tax().tax_only()


def _purchase_is_inter(invoice, company_state_code):
    """Supplier State (GSTIN prefix, else the master) != place of supply."""
    supplier_state = (invoice.party_gstin or "")[:2] or invoice.supplier.state_id
    if not supplier_state:
        return False
    return supplier_state != (invoice.place_of_supply_id or company_state_code)


def _table_5_line(invoice, line):
    """Is this purchase line an exempt / nil / non-GST / composition inward supply?"""
    if line.gst_treatment in NON_TAXABLE:
        return True
    if invoice.is_reverse_charge:
        return False
    return invoice.gst_category in (
        GSTCategory.REGISTERED_COMPOSITION,
        GSTCategory.UNREGISTERED,
        GSTCategory.CONSUMER,
    )


def build(company, from_date, to_date):
    state = company_state(company)
    in_period = {
        "company": company,
        "posting_date__gte": from_date,
        "posting_date__lte": to_date,
        "docstatus": DocStatus.SUBMITTED,
        # Tally-era invoices were filed from Tally, not from the ERP.
        "is_historical": False,
    }
    sales = SalesInvoice.objects.filter(**in_period).prefetch_related("items")
    purchases = (
        PurchaseInvoice.objects.filter(**in_period)
        .select_related("supplier")
        .prefetch_related("items")
    )

    outward_taxable = _Tax()  # 3.1(a)
    zero_rated = _Tax()  # 3.1(b)
    nil_exempt = _Tax()  # 3.1(c)
    non_gst = _Tax()  # 3.1(e)
    inward_rcm = _Tax()  # 3.1(d) / 4(A)(3)
    other_itc = _Tax()  # 4(A)(5)
    ineligible_itc = _Tax()  # 4(D)
    table_3_2 = defaultdict(_Tax)
    table_5 = {"inter_state": ZERO, "intra_state": ZERO}
    legacy_sales = legacy_purchases = 0

    for inv in sales:
        if not inv.gst_category:
            legacy_sales += 1
            continue
        pos = inv.place_of_supply_id or state
        for line in inv.items.all():
            treatment = line.gst_treatment
            if treatment == GSTTreatment.TAXABLE:
                if inv.gst_category in EXPORT_LIKE:
                    zero_rated.add(line)
                    continue
                outward_taxable.add(line)
                label = TABLE_3_2_CATEGORIES.get(inv.gst_category)
                if label and pos != state:
                    table_3_2[(pos, label)].add(line)
            elif treatment == GSTTreatment.NON_GST:
                non_gst.add(line)
            else:
                nil_exempt.add(line)

    for inv in purchases:
        if not inv.gst_category:
            legacy_purchases += 1
            continue
        inter = _purchase_is_inter(inv, state)
        for line in inv.items.all():
            if _table_5_line(inv, line):
                table_5["inter_state" if inter else "intra_state"] += line.amount
            elif not getattr(inv, "itc_eligible", True):
                ineligible_itc.add(line)
            elif line.gst_treatment == GSTTreatment.TAXABLE and inv.is_reverse_charge:
                inward_rcm.add(line)
            else:
                other_itc.add(line)

    itc = _Tax()
    for source in (inward_rcm, other_itc):
        itc.igst += source.igst
        itc.cgst += source.cgst
        itc.sgst += source.sgst

    # 6.1 (informational): tax payable on outward supplies (incl. reverse charge)
    # less input tax credit, per head.
    payment = {}
    for head in HEADS:
        output = outward_taxable.tax(head) + zero_rated.tax(head) + inward_rcm.tax(head)
        payment[head] = {
            "output": money(output),
            "itc": money(itc.tax(head)),
            "net_payable": money(output - itc.tax(head)),
        }

    return {
        "company": company_block(company),
        "period": period_block(from_date, to_date),
        "3.1": {
            "a_outward_taxable": outward_taxable.full(),
            "b_outward_zero_rated": {
                "taxable_value": money(zero_rated.taxable),
                "igst": money(zero_rated.igst),
            },
            "c_other_nil_exempt": {"taxable_value": money(nil_exempt.taxable)},
            "d_inward_rcm": {
                "taxable_value": money(inward_rcm.taxable),
                "igst": money(inward_rcm.igst),
                "cgst": money(inward_rcm.cgst),
                "sgst": money(inward_rcm.sgst),
            },
            "e_non_gst_outward": {"taxable_value": money(non_gst.taxable)},
        },
        "3.2": [
            {
                "place_of_supply": pos,
                "category": category,
                "taxable_value": money(tax.taxable),
                "igst": money(tax.igst),
            }
            for (pos, category), tax in sorted(table_3_2.items())
        ],
        "4": {
            "A": {
                "import_goods": _zero_tax(),
                "import_services": _zero_tax(),
                "inward_rcm": inward_rcm.tax_only(),
                "isd": _zero_tax(),
                "all_other_itc": other_itc.tax_only(),
            },
            "B": {"reversed": _zero_tax()},
            "C_net_itc": itc.tax_only(),
            "D_ineligible": ineligible_itc.tax_only(),
        },
        "5": {
            "inter_state": money(table_5["inter_state"]),
            "intra_state": money(table_5["intra_state"]),
        },
        "6.1": payment,
        "warnings": {
            "legacy_sales_invoices_without_gst_data": legacy_sales,
            "legacy_purchase_invoices_without_gst_data": legacy_purchases,
        },
    }
