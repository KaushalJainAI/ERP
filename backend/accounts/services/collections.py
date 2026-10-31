import datetime
from decimal import Decimal
from typing import Any, Optional, Union

from django.utils.dateparse import parse_date

from buying.models import PurchaseInvoice
from core.models import Company, DocStatus
from selling.models import SalesInvoice

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


def collections(
    company: Any, as_on: Optional[Union[datetime.date, str]] = None
) -> dict:
    """Money to Collect: aggregate open receivables grouped by customer.

    Aggregates submitted open SalesInvoices (docstatus=SUBMITTED, outstanding_amount > 0)
    posted on or before as_on for company.
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

    cust_map = {}
    grand_receivables = ZERO
    grand_overdue = ZERO
    total_bills = 0

    for inv in invoices:
        due = inv.due_date or inv.posting_date
        days_late = max((target_date - due).days, 0)
        is_overdue = days_late > 0
        overdue_amt = inv.outstanding_amount if is_overdue else ZERO

        cid = inv.customer_id
        if cid not in cust_map:
            cust_map[cid] = {
                "customer_id": cid,
                "customer_name": inv.customer.name,
                "customer": inv.customer.name,
                "total_outstanding": ZERO,
                "total_overdue": ZERO,
                "oldest_bill_date": inv.posting_date,
                "days_late": days_late,
                "bills": [],
            }

        cust_data = cust_map[cid]
        cust_data["total_outstanding"] += inv.outstanding_amount
        cust_data["total_overdue"] += overdue_amt
        if inv.posting_date < cust_data["oldest_bill_date"]:
            cust_data["oldest_bill_date"] = inv.posting_date
        if days_late > cust_data["days_late"]:
            cust_data["days_late"] = days_late

        cust_data["bills"].append(
            {
                "invoice": inv.name,
                "name": inv.name,
                "posting_date": str(inv.posting_date),
                "due_date": str(inv.due_date) if inv.due_date else None,
                "rounded_total": _format_money(inv.rounded_total),
                "outstanding_amount": _format_money(inv.outstanding_amount),
                "days_late": days_late,
                "is_overdue": is_overdue,
            }
        )

        grand_receivables += inv.outstanding_amount
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
    posted on or before as_on for company.
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

    grand_payables = ZERO
    grand_overdue = ZERO
    due_week = ZERO
    due_month = ZERO
    gst_warning_count = 0

    all_bills = []
    ft = (filter_type or "").strip().lower()

    for inv in invoices:
        due = inv.due_date or inv.posting_date
        diff_days = (due - target_date).days
        is_overdue = diff_days < 0
        days_since_posting = (target_date - inv.posting_date).days

        # Check if invoice has GST charges
        taxes = list(inv.taxes.all())
        if taxes:
            has_gst = any(t.tax_amount > ZERO for t in taxes)
        else:
            has_gst = getattr(inv, "total_taxes_and_charges", ZERO) > ZERO

        gst_180_warning = has_gst and days_since_posting >= 150

        # Grand totals aggregate across all open bills
        grand_payables += inv.outstanding_amount
        if is_overdue:
            grand_overdue += inv.outstanding_amount
        if 0 <= diff_days <= 7:
            due_week += inv.outstanding_amount
        if 0 <= diff_days <= 30:
            due_month += inv.outstanding_amount
        if gst_180_warning:
            gst_warning_count += 1

        bill_data = {
            "invoice": inv.name,
            "name": inv.name,
            "supplier_id": inv.supplier_id,
            "supplier_name": inv.supplier.name,
            "supplier": inv.supplier.name,
            "bill_no": inv.bill_no,
            "bill_date": str(inv.bill_date) if inv.bill_date else None,
            "posting_date": str(inv.posting_date),
            "due_date": str(inv.due_date) if inv.due_date else None,
            "rounded_total": _format_money(inv.rounded_total),
            "outstanding_amount": _format_money(inv.outstanding_amount),
            "days_left": diff_days,
            "days_since_posting": days_since_posting,
            "is_overdue": is_overdue,
            "gst_180_warning": gst_180_warning,
            "is_expense": False,
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
            "bill_count": len(invoices),
            "filtered_count": len(all_bills),
            "gst_warning_count": gst_warning_count,
        },
    }
