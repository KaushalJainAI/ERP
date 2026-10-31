import datetime
from decimal import Decimal
from typing import Any, Optional, Union

from django.utils.dateparse import parse_date

from accounts.models import OpeningBill
from buying.models import PurchaseInvoice, Supplier
from core.models import Company, DocStatus
from selling.models import Customer, SalesInvoice

ZERO = Decimal("0.00")


def _format_money(val: Decimal) -> str:
    return f"{(val or ZERO):.2f}"


def _resolve_company(company: Any) -> Company:
    if isinstance(company, Company):
        return company
    elif isinstance(company, (int, str)) and str(company).isdigit():
        return Company.objects.get(pk=int(company))
    return Company.objects.get(name=str(company))


def _resolve_date(as_on: Optional[Union[datetime.date, str]]) -> datetime.date:
    if as_on is None:
        return datetime.date.today()
    elif isinstance(as_on, datetime.date):
        return as_on
    elif isinstance(as_on, str):
        parsed = parse_date(as_on)
        return parsed or datetime.date.fromisoformat(as_on)
    raise ValueError(f"Invalid as_on date: {as_on!r}")


def _opening_bills(comp: Company, target_date: datetime.date, party_type: str):
    """Submitted opening bills (planning 09) still open on ``target_date``."""
    return OpeningBill.objects.filter(
        company=comp,
        docstatus=DocStatus.SUBMITTED,
        party_type=party_type,
        outstanding_amount__gt=ZERO,
        bill_date__lte=target_date,
    ).order_by("party", "bill_date", "name")


def collections(
    company: Any, as_on: Optional[Union[datetime.date, str]] = None
) -> dict:
    """Money to Collect: aggregate open receivables grouped by customer.

    Aggregates submitted open SalesInvoices (docstatus=SUBMITTED, outstanding_amount > 0)
    and open opening bills (planning 09) posted on or before as_on for company.
    Groups by Customer: total outstanding, total overdue (due_date < as_on),
    oldest bill date, days late, and list of unpaid bills.
    Calculates grand totals: total receivables, total overdue.
    """
    comp = _resolve_company(company)
    target_date = _resolve_date(as_on)

    invoices = (
        SalesInvoice.objects.filter(
            company=comp,
            docstatus=DocStatus.SUBMITTED,
            outstanding_amount__gt=ZERO,
            posting_date__lte=target_date,
            is_return=False,
        )
        .select_related("customer")
        .order_by("customer__name", "posting_date", "name")
    )
    customer_ids = dict(Customer.objects.filter(company=comp).values_list("name", "pk"))

    items = [
        {
            "customer_name": inv.customer.name,
            "customer_id": inv.customer_id,
            "invoice": inv.name,
            "posting_date": inv.posting_date,
            "due_date": inv.due_date,
            "rounded_total": inv.rounded_total,
            "outstanding": inv.outstanding_amount,
            "is_opening": False,
        }
        for inv in invoices
    ]
    for bill in _opening_bills(comp, target_date, "Customer"):
        items.append(
            {
                "customer_name": bill.party,
                "customer_id": customer_ids.get(bill.party),
                "invoice": bill.name,
                "posting_date": bill.bill_date,
                "due_date": bill.due_date,
                "rounded_total": bill.amount,
                "outstanding": bill.outstanding_amount,
                "is_opening": True,
            }
        )
    items.sort(key=lambda i: (i["customer_name"], i["posting_date"], i["invoice"]))

    cust_map = {}
    grand_receivables = ZERO
    grand_overdue = ZERO
    total_bills = 0

    for item in items:
        due = item["due_date"] or item["posting_date"]
        days_late = max((target_date - due).days, 0)
        is_overdue = days_late > 0
        overdue_amt = item["outstanding"] if is_overdue else ZERO

        # Keyed by name: the ledger and opening bills both identify a party by name.
        cname = item["customer_name"]
        if cname not in cust_map:
            cust_map[cname] = {
                "customer_id": item["customer_id"],
                "customer_name": cname,
                "customer": cname,
                "total_outstanding": ZERO,
                "total_overdue": ZERO,
                "oldest_bill_date": item["posting_date"],
                "days_late": days_late,
                "bills": [],
            }

        cust_data = cust_map[cname]
        cust_data["total_outstanding"] += item["outstanding"]
        cust_data["total_overdue"] += overdue_amt
        if item["posting_date"] < cust_data["oldest_bill_date"]:
            cust_data["oldest_bill_date"] = item["posting_date"]
        if days_late > cust_data["days_late"]:
            cust_data["days_late"] = days_late

        cust_data["bills"].append(
            {
                "invoice": item["invoice"],
                "name": item["invoice"],
                "posting_date": str(item["posting_date"]),
                "due_date": str(item["due_date"]) if item["due_date"] else None,
                "rounded_total": _format_money(item["rounded_total"]),
                "outstanding_amount": _format_money(item["outstanding"]),
                "days_late": days_late,
                "is_overdue": is_overdue,
                "is_opening": item["is_opening"],
            }
        )

        grand_receivables += item["outstanding"]
        grand_overdue += overdue_amt
        total_bills += 1

    # Sort customers by highest overdue, then most days late, then name
    sorted_customers = sorted(
        cust_map.values(),
        key=lambda c: (-c["total_overdue"], -c["days_late"], c["customer_name"]),
    )

    # Format customer totals for JSON consumption
    formatted_customers = [
        {
            "customer_id": c["customer_id"],
            "customer_name": c["customer_name"],
            "customer": c["customer"],
            "total_outstanding": _format_money(c["total_outstanding"]),
            "total_overdue": _format_money(c["total_overdue"]),
            "oldest_bill_date": (
                str(c["oldest_bill_date"]) if c["oldest_bill_date"] else None
            ),
            "days_late": c["days_late"],
            "bills": c["bills"],
        }
        for c in sorted_customers
    ]

    return {
        "customers": formatted_customers,
        "totals": {
            "total_receivables": _format_money(grand_receivables),
            "total_overdue": _format_money(grand_overdue),
            "customer_count": len(formatted_customers),
            "invoice_count": total_bills,
        },
    }


def bills_to_pay(
    company: Any,
    as_on: Optional[Union[datetime.date, str]] = None,
    filter_type: Optional[str] = None,
) -> dict:
    """Bills to Pay: aggregate open payables with due dates and GST warnings.

    Aggregates submitted open PurchaseInvoices (docstatus=SUBMITTED, outstanding_amount > 0)
    and open supplier opening bills (planning 09) posted on or before as_on for company.
    Includes days_left (due_date - as_on), is_overdue, and warning flag if invoice has
    GST and is approaching 180 days unpaid (days_since_posting >= 150).
    Filters: filter_type in ('overdue', 'week', 'month') or None (all).
    Calculates grand totals: total payables, overdue, due this week, due this month.
    """
    comp = _resolve_company(company)
    target_date = _resolve_date(as_on)

    invoices = (
        PurchaseInvoice.objects.filter(
            company=comp,
            docstatus=DocStatus.SUBMITTED,
            outstanding_amount__gt=ZERO,
            posting_date__lte=target_date,
            is_return=False,
        )
        .select_related("supplier")
        .prefetch_related("taxes")
        .order_by("due_date", "posting_date", "name")
    )
    supplier_ids = dict(Supplier.objects.filter(company=comp).values_list("name", "pk"))

    items = []
    for inv in invoices:
        # Check if invoice has GST charges
        taxes = list(inv.taxes.all())
        if taxes:
            has_gst = any(t.tax_amount > ZERO for t in taxes)
        else:
            has_gst = getattr(inv, "total_taxes_and_charges", ZERO) > ZERO
        items.append(
            {
                "name": inv.name,
                "supplier_id": inv.supplier_id,
                "supplier_name": inv.supplier.name,
                "bill_no": inv.bill_no,
                "bill_date": inv.bill_date,
                "posting_date": inv.posting_date,
                "due_date": inv.due_date,
                "rounded_total": inv.rounded_total,
                "outstanding": inv.outstanding_amount,
                "has_gst": has_gst,
                "is_opening": False,
            }
        )
    for bill in _opening_bills(comp, target_date, "Supplier"):
        items.append(
            {
                "name": bill.name,
                "supplier_id": supplier_ids.get(bill.party),
                "supplier_name": bill.party,
                "bill_no": bill.reference,
                "bill_date": bill.bill_date,
                "posting_date": bill.bill_date,
                "due_date": bill.due_date,
                "rounded_total": bill.amount,
                "outstanding": bill.outstanding_amount,
                "has_gst": False,
                "is_opening": True,
            }
        )
    items.sort(
        key=lambda i: (i["due_date"] or i["posting_date"], i["posting_date"], i["name"])
    )

    grand_payables = ZERO
    grand_overdue = ZERO
    due_week = ZERO
    due_month = ZERO
    gst_warning_count = 0

    all_bills = []
    ft = (filter_type or "").strip().lower()

    for item in items:
        due = item["due_date"] or item["posting_date"]
        diff_days = (due - target_date).days
        is_overdue = diff_days < 0
        days_since_posting = (target_date - item["posting_date"]).days

        gst_180_warning = item["has_gst"] and days_since_posting >= 150

        # Grand totals aggregate across all open bills
        outstanding = item["outstanding"]
        grand_payables += outstanding
        if is_overdue:
            grand_overdue += outstanding
        if 0 <= diff_days <= 7:
            due_week += outstanding
        if 0 <= diff_days <= 30:
            due_month += outstanding
        if gst_180_warning:
            gst_warning_count += 1

        bill_data = {
            "invoice": item["name"],
            "name": item["name"],
            "supplier_id": item["supplier_id"],
            "supplier_name": item["supplier_name"],
            "supplier": item["supplier_name"],
            "bill_no": item["bill_no"],
            "bill_date": str(item["bill_date"]) if item["bill_date"] else None,
            "posting_date": str(item["posting_date"]),
            "due_date": str(item["due_date"]) if item["due_date"] else None,
            "rounded_total": _format_money(item["rounded_total"]),
            "outstanding_amount": _format_money(outstanding),
            "days_left": diff_days,
            "days_since_posting": days_since_posting,
            "is_overdue": is_overdue,
            "gst_180_warning": gst_180_warning,
            "is_expense": False,
            "is_opening": item["is_opening"],
        }

        # Apply filter
        if ft in ("overdue",):
            if is_overdue:
                all_bills.append(bill_data)
        elif ft in ("week", "this_week"):
            if 0 <= diff_days <= 7:
                all_bills.append(bill_data)
        elif ft in ("month", "this_month"):
            if 0 <= diff_days <= 30:
                all_bills.append(bill_data)
        else:
            all_bills.append(bill_data)

    return {
        "bills": all_bills,
        "totals": {
            "total_payables": _format_money(grand_payables),
            "total_overdue": _format_money(grand_overdue),
            "due_this_week": _format_money(due_week),
            "due_this_month": _format_money(due_month),
            "bill_count": len(items),
            "filtered_count": len(all_bills),
            "gst_warning_count": gst_warning_count,
        },
    }
