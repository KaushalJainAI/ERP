"""GST Position service.

Calculates output tax, input credit, net payable, paid amount, status, and due date.
"""

from __future__ import annotations

import calendar
import datetime
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import TaxPayment
from buying.models import PurchaseInvoiceItem
from core.models import Company, DocStatus
from selling.models import SalesInvoiceItem

ZERO = Decimal("0.00")


def _money(val: Decimal | None) -> str:
    if val is None:
        val = ZERO
    return f"{Decimal(val):.2f}"


def resolve_company(company: Any) -> Company:
    if isinstance(company, Company):
        return company
    try:
        return Company.objects.get(pk=int(company))
    except (ValueError, TypeError, Company.DoesNotExist):
        raise ValidationError(_("Company does not exist."))


def parse_month(month: Any) -> tuple[datetime.date, datetime.date, str]:
    if isinstance(month, (datetime.date, datetime.datetime)):
        start = datetime.date(month.year, month.month, 1)
        month_str = start.strftime("%Y-%m")
    elif isinstance(month, str):
        month = month.strip()
        parts = month.split("-")
        if len(parts) < 2:
            raise ValidationError(_("Invalid month format. Expected YYYY-MM."))
        try:
            year = int(parts[0])
            mon = int(parts[1])
            start = datetime.date(year, mon, 1)
            month_str = f"{year:04d}-{mon:02d}"
        except (ValueError, TypeError):
            raise ValidationError(_("Invalid month format. Expected YYYY-MM."))
    else:
        raise ValidationError(_("Invalid month format."))

    _, last_day = calendar.monthrange(start.year, start.month)
    end = datetime.date(start.year, start.month, last_day)
    return start, end, month_str


def gst_position(company: Any, month: Any) -> dict[str, Any]:
    company_obj = resolve_company(company)
    start_date, end_date, month_str = parse_month(month)

    # 1. Output tax from submitted SalesInvoices in the month
    sales_qs = SalesInvoiceItem.objects.filter(
        sales_invoice__company=company_obj,
        sales_invoice__posting_date__gte=start_date,
        sales_invoice__posting_date__lte=end_date,
        sales_invoice__docstatus=DocStatus.SUBMITTED,
    )
    sales_agg = sales_qs.aggregate(
        igst=Sum("igst_amount"),
        cgst=Sum("cgst_amount"),
        sgst=Sum("sgst_amount"),
    )
    out_igst = sales_agg["igst"] or ZERO
    out_cgst = sales_agg["cgst"] or ZERO
    out_sgst = sales_agg["sgst"] or ZERO
    output_tax = max(ZERO, out_igst + out_cgst + out_sgst)

    # 2. Input credit from submitted PurchaseInvoices (itc_eligible=True) in the month
    purchases_qs = PurchaseInvoiceItem.objects.filter(
        purchase_invoice__company=company_obj,
        purchase_invoice__posting_date__gte=start_date,
        purchase_invoice__posting_date__lte=end_date,
        purchase_invoice__docstatus=DocStatus.SUBMITTED,
        purchase_invoice__itc_eligible=True,
    )
    purchases_agg = purchases_qs.aggregate(
        igst=Sum("igst_amount"),
        cgst=Sum("cgst_amount"),
        sgst=Sum("sgst_amount"),
    )
    in_igst = purchases_agg["igst"] or ZERO
    in_cgst = purchases_agg["cgst"] or ZERO
    in_sgst = purchases_agg["sgst"] or ZERO
    input_credit = max(ZERO, in_igst + in_cgst + in_sgst)

    # 3. Net payable: max(0, output_tax - input_credit)
    net_payable = max(ZERO, output_tax - input_credit)

    # 4. Paid: sum of GST TaxPayments for this month/period
    payments = TaxPayment.objects.filter(
        company=company_obj,
        tax_type="GST",
        period=start_date,
        docstatus=DocStatus.SUBMITTED,
    )
    paid_cash = sum(
        (tp.igst_cash + tp.cgst_cash + tp.sgst_cash for tp in payments),
        ZERO,
    )
    paid_credit = sum(
        (tp.igst_credit_used + tp.cgst_credit_used + tp.sgst_credit_used for tp in payments),
        ZERO,
    )
    paid = paid_cash + paid_credit

    # 5. Status: "Paid" if paid >= net_payable (or net_payable == 0) else "Due"
    status = "Paid" if (paid >= net_payable or net_payable == ZERO) else "Due"

    # 6. Due date: 20th of the following month
    due_day = getattr(company_obj, "gst_due_day", 20) or 20
    if start_date.month == 12:
        due_date = datetime.date(start_date.year + 1, 1, due_day)
    else:
        due_date = datetime.date(start_date.year, start_date.month + 1, due_day)

    return {
        "company": company_obj.id,
        "company_name": company_obj.name,
        "month": month_str,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "output_tax": _money(output_tax),
        "output_igst": _money(out_igst),
        "output_cgst": _money(out_cgst),
        "output_sgst": _money(out_sgst),
        "input_credit": _money(input_credit),
        "input_igst": _money(in_igst),
        "input_cgst": _money(in_cgst),
        "input_sgst": _money(in_sgst),
        "net_payable": _money(net_payable),
        "paid": _money(paid),
        "paid_cash": _money(paid_cash),
        "paid_credit": _money(paid_credit),
        "status": status,
        "due_date": due_date.isoformat(),
        "payments": [
            {
                "id": p.id,
                "name": p.name,
                "challan_no": p.challan_no,
                "posting_date": p.posting_date.isoformat(),
                "cash": _money(p.igst_cash + p.cgst_cash + p.sgst_cash),
                "credit_used": _money(
                    p.igst_credit_used + p.cgst_credit_used + p.sgst_credit_used
                ),
            }
            for p in payments
        ],
    }
