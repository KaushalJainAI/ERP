import datetime
from decimal import Decimal
from typing import Any, Optional, Union

from django.utils.dateparse import parse_date

from core.models import Company, DocStatus
from selling.models import SalesInvoice

ZERO = Decimal("0.00")


def _format_money(val: Decimal) -> str:
    return f"{(val or ZERO):.2f}"


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
    if isinstance(company, Company):
        comp = company
    elif isinstance(company, (int, str)) and str(company).isdigit():
        comp = Company.objects.get(pk=int(company))
    else:
        comp = Company.objects.get(name=str(company))

    if as_on is None:
        target_date = datetime.date.today()
    elif isinstance(as_on, datetime.date):
        target_date = as_on
    elif isinstance(as_on, str):
        target_date = parse_date(as_on) or datetime.date.fromisoformat(as_on)
    else:
        raise ValueError(f"Invalid as_on date: {as_on!r}")

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
