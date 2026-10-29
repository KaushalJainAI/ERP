"""Marketing and Sales KPI and Analytics Engine.

Computes:
1. Sales conversion & velocity (Sales Order -> Sales Invoice conversion time & rate).
2. Revenue & volume metrics by month/fiscal year, growth rate vs previous period.
3. Top selling products / spice blends by quantity and revenue.
4. Customer acquisition, churn, and top customer contributions (concentration analysis).
5. Outstanding receivable ageing summary & average collection period (DSO - Days Sales Outstanding).

Read-only, derived on read from submitted documents and immutable ledgers.
"""

import datetime
from decimal import Decimal
import statistics
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from core.models import DocStatus, FiscalYear
from selling.models import Customer, SalesInvoice, SalesInvoiceItem, SalesOrder, SalesOrderItem

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
ZERO_QTY = Decimal("0.000")

SPICE_KEYWORDS = (
    "spice",
    "blend",
    "masala",
    "powder",
    "seasoning",
    "chilli",
    "chili",
    "turmeric",
    "haldi",
    "coriander",
    "dhaniya",
    "jeera",
    "cumin",
    "pepper",
    "clove",
    "cardamom",
    "elaichi",
    "cinnamon",
    "garam",
    "curry",
    "mustard",
    "fenugreek",
    "methi",
    "hing",
    "asafoetida",
    "amchur",
    "chaat",
    "sambhar",
    "biryani",
)


def q(value: Any) -> Decimal:
    """Quantize to 2dp Decimal."""
    return Decimal(str(value or 0)).quantize(CENT)


def money(value: Any) -> str:
    """Format money as 2dp string."""
    return str(q(value))


def qty_str(value: Any) -> str:
    """Format qty as 3dp string."""
    return str(Decimal(str(value or 0)).quantize(Decimal("0.001")))


def resolve_period(
    params: Dict[str, Any], default_today: Optional[datetime.date] = None
) -> Tuple[datetime.date, datetime.date, str]:
    """Resolve (from_date, to_date, period_name) from query parameters.

    Supports:
    - Custom range: ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    - Named period: ?period={this_month,last_month,this_quarter,last_quarter,this_fy,last_fy,all_time}
    - Default: Current Indian Fiscal Year (Apr 1 to Mar 31) to today
    """
    today = default_today or datetime.date.today()
    period_code = (params.get("period") or "").strip().lower()

    from_str = params.get("from_date")
    to_str = params.get("to_date")

    if from_str and to_str:
        try:
            from_date = parse_date(from_str)
            to_date = parse_date(to_str)
        except (ValueError, TypeError):
            raise ValidationError({"date": _("Enter valid dates (YYYY-MM-DD).")})
        if not from_date or not to_date:
            raise ValidationError({"date": _("Enter valid dates (YYYY-MM-DD).")})
        if from_date > to_date:
            raise ValidationError({"from_date": _("'from_date' cannot be after 'to_date'.")})
        return from_date, to_date, "custom"

    # Named period resolution
    if period_code == "this_month":
        from_date = today.replace(day=1)
        to_date = today
        return from_date, to_date, "this_month"

    if period_code == "last_month":
        first_of_this = today.replace(day=1)
        last_of_prev = first_of_this - datetime.timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return first_of_prev, last_of_prev, "last_month"

    if period_code == "this_quarter":
        # Indian FY Quarters: Q1: Apr-Jun, Q2: Jul-Sep, Q3: Oct-Dec, Q4: Jan-Mar
        month = today.month
        if month in (4, 5, 6):
            q_start = datetime.date(today.year, 4, 1)
        elif month in (7, 8, 9):
            q_start = datetime.date(today.year, 7, 1)
        elif month in (10, 11, 12):
            q_start = datetime.date(today.year, 10, 1)
        else:
            q_start = datetime.date(today.year, 1, 1)
        return q_start, today, "this_quarter"

    if period_code == "last_quarter":
        month = today.month
        if month in (4, 5, 6):
            q_start = datetime.date(today.year, 1, 1)
            q_end = datetime.date(today.year, 3, 31)
        elif month in (7, 8, 9):
            q_start = datetime.date(today.year, 4, 1)
            q_end = datetime.date(today.year, 6, 30)
        elif month in (10, 11, 12):
            q_start = datetime.date(today.year, 7, 1)
            q_end = datetime.date(today.year, 9, 30)
        else:
            q_start = datetime.date(today.year - 1, 10, 1)
            q_end = datetime.date(today.year - 1, 12, 31)
        return q_start, q_end, "last_quarter"

    if period_code == "last_fy":
        # Previous Indian Fiscal Year
        if today.month >= 4:
            fy_start = datetime.date(today.year - 1, 4, 1)
            fy_end = datetime.date(today.year, 3, 31)
        else:
            fy_start = datetime.date(today.year - 2, 4, 1)
            fy_end = datetime.date(today.year - 1, 3, 31)
        return fy_start, fy_end, "last_fy"

    if period_code == "all_time":
        return datetime.date(2000, 1, 1), today, "all_time"

    # Default: Current Indian Fiscal Year
    if today.month >= 4:
        fy_start = datetime.date(today.year, 4, 1)
    else:
        fy_start = datetime.date(today.year - 1, 4, 1)
    return fy_start, today, "this_fy"


def get_comparison_period(
    from_date: datetime.date, to_date: datetime.date, period_code: str = ""
) -> Tuple[datetime.date, datetime.date]:
    """Calculate the equivalent prior period for growth comparisons."""
    if period_code == "this_fy":
        return (
            from_date.replace(year=from_date.year - 1),
            to_date.replace(year=to_date.year - 1),
        )
    if period_code == "this_month":
        last_day_prev = from_date - datetime.timedelta(days=1)
        first_day_prev = last_day_prev.replace(day=1)
        return first_day_prev, last_day_prev

    duration_days = (to_date - from_date).days + 1
    prev_to_date = from_date - datetime.timedelta(days=1)
    prev_from_date = prev_to_date - datetime.timedelta(days=duration_days - 1)
    return prev_from_date, prev_to_date


def calc_growth_pct(current: Decimal, previous: Decimal) -> Optional[float]:
    """Calculate percentage growth: ((current - previous) / previous) * 100."""
    if not previous or previous == Decimal("0.00"):
        return 100.0 if current > Decimal("0.00") else 0.0
    growth = ((current - previous) / abs(previous)) * Decimal("100")
    return round(float(growth), 2)


def is_spice_product(item_name: str, group_name: str) -> bool:
    """Determine whether an item belongs to the spice / masala / blend category."""
    text = f"{item_name} {group_name}".lower()
    return any(keyword in text for keyword in SPICE_KEYWORDS)


def compute_sales_conversion_and_velocity(
    company, from_date: datetime.date, to_date: datetime.date
) -> Dict[str, Any]:
    """Compute Sales Order -> Sales Invoice conversion rate, pipeline funnel, and conversion velocity."""
    orders = SalesOrder.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        transaction_date__gte=from_date,
        transaction_date__lte=to_date,
    )
    total_orders_count = orders.count()
    total_order_value = orders.aggregate(val=Coalesce(Sum("grand_total"), ZERO))["val"]
    total_order_net = orders.aggregate(val=Coalesce(Sum("net_total"), ZERO))["val"]

    # Pipeline stages: Quotes/Draft orders, Submitted orders, Invoiced, Paid
    draft_orders = SalesOrder.objects.filter(
        company=company,
        docstatus=DocStatus.DRAFT,
        transaction_date__gte=from_date,
        transaction_date__lte=to_date,
    )
    draft_count = draft_orders.count()
    draft_value = draft_orders.aggregate(val=Coalesce(Sum("grand_total"), ZERO))["val"]

    order_ids = list(orders.values_list("id", flat=True))
    submitted_invoices = SalesInvoice.objects.filter(
        company=company,
        sales_order_id__in=order_ids,
        docstatus=DocStatus.SUBMITTED,
    ).select_related("sales_order")

    # Map invoices to orders
    invoices_by_order: Dict[int, List[SalesInvoice]] = {}
    for inv in submitted_invoices:
        if inv.sales_order_id:
            invoices_by_order.setdefault(inv.sales_order_id, []).append(inv)

    converted_order_ids = set(invoices_by_order.keys())
    converted_count = len(converted_order_ids)
    unconverted_count = max(0, total_orders_count - converted_count)
    conversion_rate = (
        round((converted_count / total_orders_count) * 100, 2)
        if total_orders_count > 0
        else 0.0
    )

    # Fulfillment breakdown: Fully Invoiced vs Partially Invoiced vs Unbilled
    fully_converted_count = 0
    partially_converted_count = 0

    conversion_days_list: List[int] = []
    bucket_same_day = 0
    bucket_1_to_3 = 0
    bucket_4_to_7 = 0
    bucket_8_to_14 = 0
    bucket_15_plus = 0

    for order in orders:
        linked = invoices_by_order.get(order.id, [])
        if not linked:
            continue
        invoiced_total = sum((inv.grand_total for inv in linked), ZERO)
        if invoiced_total >= order.grand_total:
            fully_converted_count += 1
        else:
            partially_converted_count += 1

        # Velocity in days from order transaction date to earliest invoice posting date
        min_invoice_date = min(inv.posting_date for inv in linked)
        delta_days = (min_invoice_date - order.transaction_date).days
        if delta_days < 0:
            delta_days = 0
        conversion_days_list.append(delta_days)

        if delta_days == 0:
            bucket_same_day += 1
        elif delta_days <= 3:
            bucket_1_to_3 += 1
        elif delta_days <= 7:
            bucket_4_to_7 += 1
        elif delta_days <= 14:
            bucket_8_to_14 += 1
        else:
            bucket_15_plus += 1

    avg_conversion_days = (
        round(sum(conversion_days_list) / len(conversion_days_list), 1)
        if conversion_days_list
        else 0.0
    )
    median_conversion_days = (
        round(statistics.median(conversion_days_list), 1)
        if conversion_days_list
        else 0.0
    )

    # Invoices in period with outstanding == 0 (Paid)
    period_invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    paid_invoices = period_invoices.filter(outstanding_amount=Decimal("0.00"))
    paid_count = paid_invoices.count()
    paid_value = paid_invoices.aggregate(val=Coalesce(Sum("grand_total"), ZERO))["val"]

    funnel = [
        {"stage": "Draft Orders", "count": draft_count, "amount": money(draft_value)},
        {"stage": "Submitted Orders", "count": total_orders_count, "amount": money(total_order_value)},
        {
            "stage": "Converted to Invoices",
            "count": converted_count,
            "amount": money(
                sum(
                    (
                        inv.grand_total
                        for invs in invoices_by_order.values()
                        for inv in invs
                    ),
                    ZERO,
                )
            ),
        },
        {"stage": "Fully Paid Invoices", "count": paid_count, "amount": money(paid_value)},
    ]

    return {
        "total_orders_count": total_orders_count,
        "total_order_value": money(total_order_value),
        "total_order_net": money(total_order_net),
        "converted_orders_count": converted_count,
        "unconverted_orders_count": unconverted_count,
        "conversion_rate_pct": conversion_rate,
        "fully_converted_count": fully_converted_count,
        "partially_converted_count": partially_converted_count,
        "average_conversion_days": avg_conversion_days,
        "median_conversion_days": median_conversion_days,
        "velocity_distribution": {
            "same_day": bucket_same_day,
            "1_to_3_days": bucket_1_to_3,
            "4_to_7_days": bucket_4_to_7,
            "8_to_14_days": bucket_8_to_14,
            "15_plus_days": bucket_15_plus,
        },
        "pipeline_funnel": funnel,
    }


def compute_revenue_and_volume(
    company, from_date: datetime.date, to_date: datetime.date, period_code: str = ""
) -> Dict[str, Any]:
    """Compute revenue (Net, Gross, Tax), volume (units sold), AOV, and growth rates vs previous period."""
    invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    invoices_count = invoices.count()
    net_revenue = invoices.aggregate(s=Coalesce(Sum("net_total"), ZERO))["s"]
    gross_revenue = invoices.aggregate(s=Coalesce(Sum("grand_total"), ZERO))["s"]
    total_tax = invoices.aggregate(s=Coalesce(Sum("total_taxes_and_charges"), ZERO))["s"]

    # Volume (sum of line item quantities)
    volume = (
        SalesInvoiceItem.objects.filter(sales_invoice__in=invoices).aggregate(
            v=Coalesce(Sum("qty"), ZERO_QTY)
        )["v"]
    )
    aov = (
        (net_revenue / Decimal(invoices_count)).quantize(CENT)
        if invoices_count > 0
        else ZERO
    )

    # Comparison period
    prev_from, prev_to = get_comparison_period(from_date, to_date, period_code)
    prev_invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=prev_from,
        posting_date__lte=prev_to,
    )
    prev_count = prev_invoices.count()
    prev_net = prev_invoices.aggregate(s=Coalesce(Sum("net_total"), ZERO))["s"]
    prev_gross = prev_invoices.aggregate(s=Coalesce(Sum("grand_total"), ZERO))["s"]
    prev_volume = (
        SalesInvoiceItem.objects.filter(sales_invoice__in=prev_invoices).aggregate(
            v=Coalesce(Sum("qty"), ZERO_QTY)
        )["v"]
    )
    prev_aov = (
        (prev_net / Decimal(prev_count)).quantize(CENT) if prev_count > 0 else ZERO
    )

    revenue_growth_pct = calc_growth_pct(net_revenue, prev_net)
    gross_growth_pct = calc_growth_pct(gross_revenue, prev_gross)
    volume_growth_pct = calc_growth_pct(volume, prev_volume)
    invoices_growth_pct = calc_growth_pct(Decimal(invoices_count), Decimal(prev_count))
    aov_growth_pct = calc_growth_pct(aov, prev_aov)

    # Monthly breakdown for trends
    monthly_qs = (
        invoices.annotate(month=TruncMonth("posting_date"))
        .values("month")
        .annotate(
            net_rev=Coalesce(Sum("net_total"), ZERO),
            gross_rev=Coalesce(Sum("grand_total"), ZERO),
            tax_rev=Coalesce(Sum("total_taxes_and_charges"), ZERO),
            inv_count=Count("id"),
        )
        .order_by("month")
    )

    monthly_trends = []
    prev_month_net = None
    for row in monthly_qs:
        m_date: datetime.date = row["month"]
        if hasattr(m_date, "date"):
            m_date = m_date.date()
        m_net = row["net_rev"]
        # Calculate monthly volume
        m_volume = (
            SalesInvoiceItem.objects.filter(
                sales_invoice__company=company,
                sales_invoice__docstatus=DocStatus.SUBMITTED,
                sales_invoice__posting_date__year=m_date.year,
                sales_invoice__posting_date__month=m_date.month,
            ).aggregate(v=Coalesce(Sum("qty"), ZERO_QTY))["v"]
        )

        mom_growth = (
            calc_growth_pct(m_net, prev_month_net)
            if prev_month_net is not None
            else None
        )
        prev_month_net = m_net

        monthly_trends.append(
            {
                "period": m_date.strftime("%Y-%m"),
                "label": m_date.strftime("%b %Y"),
                "net_revenue": money(m_net),
                "gross_revenue": money(row["gross_rev"]),
                "tax_amount": money(row["tax_rev"]),
                "volume": qty_str(m_volume),
                "invoices_count": row["inv_count"],
                "growth_pct": mom_growth,
            }
        )

    return {
        "current_period": {
            "from_date": str(from_date),
            "to_date": str(to_date),
            "net_revenue": money(net_revenue),
            "gross_revenue": money(gross_revenue),
            "total_tax": money(total_tax),
            "volume": qty_str(volume),
            "invoices_count": invoices_count,
            "average_order_value": money(aov),
        },
        "previous_period": {
            "from_date": str(prev_from),
            "to_date": str(prev_to),
            "net_revenue": money(prev_net),
            "gross_revenue": money(prev_gross),
            "volume": qty_str(prev_volume),
            "invoices_count": prev_count,
            "average_order_value": money(prev_aov),
        },
        "growth": {
            "revenue_growth_pct": revenue_growth_pct,
            "gross_growth_pct": gross_growth_pct,
            "volume_growth_pct": volume_growth_pct,
            "invoices_growth_pct": invoices_growth_pct,
            "aov_growth_pct": aov_growth_pct,
        },
        "monthly_trends": monthly_trends,
    }


def compute_top_products_and_spices(
    company, from_date: datetime.date, to_date: datetime.date, limit: int = 15
) -> Dict[str, Any]:
    """Identify top selling products and dedicated spice blends by revenue & quantity."""
    items_qs = SalesInvoiceItem.objects.filter(
        sales_invoice__company=company,
        sales_invoice__docstatus=DocStatus.SUBMITTED,
        sales_invoice__posting_date__gte=from_date,
        sales_invoice__posting_date__lte=to_date,
    )

    total_net_rev = items_qs.aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]
    total_qty_sold = items_qs.aggregate(q=Coalesce(Sum("qty"), ZERO_QTY))["q"]

    aggregates = (
        items_qs.values(
            "item_id",
            "item__code",
            "item__name",
            "item__item_group__name",
            "item__stock_uom__name",
        )
        .annotate(
            total_qty=Coalesce(Sum("qty"), ZERO_QTY),
            total_revenue=Coalesce(Sum("amount"), ZERO),
            invoice_count=Count("sales_invoice_id", distinct=True),
        )
        .order_by("-total_revenue")
    )

    all_products = []
    spice_blends = []

    for row in aggregates:
        item_name = row["item__name"] or row["item__code"]
        group_name = row["item__item_group__name"] or ""
        rev = row["total_revenue"]
        qty = row["total_qty"]
        share = (
            round(float((rev / total_net_rev) * Decimal("100")), 2)
            if total_net_rev > 0
            else 0.0
        )
        avg_rate = round(float(rev / qty), 2) if qty > 0 else 0.0
        is_spice = is_spice_product(item_name, group_name)

        item_data = {
            "item_id": row["item_id"],
            "item_code": row["item__code"],
            "item_name": item_name,
            "item_group": group_name,
            "uom": row["item__stock_uom__name"] or "Nos",
            "quantity_sold": qty_str(qty),
            "revenue": money(rev),
            "average_rate": money(avg_rate),
            "invoices_count": row["invoice_count"],
            "contribution_pct": share,
            "is_spice_blend": is_spice,
        }

        all_products.append(item_data)
        if is_spice:
            spice_blends.append(item_data)

    return {
        "total_revenue": money(total_net_rev),
        "total_quantity": qty_str(total_qty_sold),
        "top_products": all_products[:limit],
        "top_spice_blends": spice_blends[:limit],
        "spice_blends_count": len(spice_blends),
        "total_products_count": len(all_products),
    }


def compute_customer_analytics(
    company, from_date: datetime.date, to_date: datetime.date
) -> Dict[str, Any]:
    """Compute customer acquisition, churn, repeat purchase rate, and customer concentration (Pareto & HHI)."""
    period_invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    period_revenue = period_invoices.aggregate(s=Coalesce(Sum("net_total"), ZERO))["s"]

    # Active customer IDs in period
    active_cust_ids = set(period_invoices.values_list("customer_id", flat=True))
    total_active_customers = len(active_cust_ids)

    # Customer acquisition: find earliest invoice date for all customers
    all_time_invoices = SalesInvoice.objects.filter(
        company=company, docstatus=DocStatus.SUBMITTED
    )
    first_purchase_map = {
        row["customer_id"]: row["first_date"]
        for row in all_time_invoices.values("customer_id").annotate(
            first_date=Min("posting_date")
        )
    }

    new_customers = set()
    repeat_customers = set()

    for cust_id in active_cust_ids:
        first_date = first_purchase_map.get(cust_id)
        if first_date and first_date >= from_date:
            new_customers.add(cust_id)
        else:
            repeat_customers.add(cust_id)

    # Churned customers: purchased before `from_date` (or in past), but NO purchase in this period
    past_customer_ids = set(
        SalesInvoice.objects.filter(
            company=company,
            docstatus=DocStatus.SUBMITTED,
            posting_date__lt=from_date,
        ).values_list("customer_id", flat=True)
    )
    churned_customers = past_customer_ids - active_cust_ids
    churned_count = len(churned_customers)

    repeat_rate_pct = (
        round((len(repeat_customers) / total_active_customers) * 100, 2)
        if total_active_customers > 0
        else 0.0
    )

    # Concentration analysis
    cust_aggregates = (
        period_invoices.values(
            "customer_id", "customer__name", "customer__customer_group__name"
        )
        .annotate(
            total_revenue=Coalesce(Sum("net_total"), ZERO),
            invoices_count=Count("id"),
        )
        .order_by("-total_revenue")
    )

    top_customers_list = []
    hhi = 0.0

    for row in cust_aggregates:
        c_rev = row["total_revenue"]
        share = (
            round(float((c_rev / period_revenue) * Decimal("100")), 2)
            if period_revenue > 0
            else 0.0
        )
        hhi += share**2
        top_customers_list.append(
            {
                "customer_id": row["customer_id"],
                "customer_name": row["customer__name"],
                "customer_group": row["customer__customer_group__name"] or "",
                "revenue": money(c_rev),
                "invoices_count": row["invoices_count"],
                "revenue_share_pct": share,
            }
        )

    top_5_share = sum(c["revenue_share_pct"] for c in top_customers_list[:5])
    top_10_share = sum(c["revenue_share_pct"] for c in top_customers_list[:10])

    # Pareto 80/20 metric: revenue share of top 20% customers
    num_cust = len(top_customers_list)
    top_20_pct_count = max(1, int(round(num_cust * 0.2))) if num_cust > 0 else 0
    pareto_share = sum(c["revenue_share_pct"] for c in top_customers_list[:top_20_pct_count])

    concentration_risk = (
        "Low" if hhi < 1500 else ("Moderate" if hhi <= 2500 else "High")
    )

    return {
        "active_customers_count": total_active_customers,
        "new_customers_count": len(new_customers),
        "repeat_customers_count": len(repeat_customers),
        "churned_customers_count": churned_count,
        "repeat_rate_pct": repeat_rate_pct,
        "concentration": {
            "top_5_share_pct": round(top_5_share, 2),
            "top_10_share_pct": round(top_10_share, 2),
            "pareto_20_pct_customers_share": round(pareto_share, 2),
            "hhi_index": round(hhi, 1),
            "concentration_risk": concentration_risk,
        },
        "top_customers": top_customers_list[:15],
    }


def compute_receivables_and_dso(
    company, from_date: datetime.date, to_date: datetime.date
) -> Dict[str, Any]:
    """Compute outstanding receivable ageing summary and Days Sales Outstanding (DSO)."""
    # Open invoices as of to_date
    open_invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        outstanding_amount__gt=Decimal("0.00"),
        posting_date__lte=to_date,
    )

    total_outstanding = open_invoices.aggregate(
        tot=Coalesce(Sum("outstanding_amount"), ZERO)
    )["tot"]

    # Credit Sales in period (Grand Total)
    period_invoices = SalesInvoice.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    period_credit_sales = period_invoices.aggregate(
        tot=Coalesce(Sum("grand_total"), ZERO)
    )["tot"]

    days_in_period = max(1, (to_date - from_date).days + 1)

    # DSO = (Total Accounts Receivable / Total Credit Sales) * Days in Period
    if period_credit_sales > Decimal("0.00"):
        dso_days = round(
            float((total_outstanding / period_credit_sales) * Decimal(days_in_period)),
            1,
        )
    else:
        dso_days = 0.0

    # Ageing buckets as of to_date
    bucket_0_30 = Decimal("0.00")
    bucket_31_60 = Decimal("0.00")
    bucket_61_90 = Decimal("0.00")
    bucket_91_plus = Decimal("0.00")

    count_0_30 = 0
    count_31_60 = 0
    count_61_90 = 0
    count_91_plus = 0

    for inv in open_invoices:
        ref_date = inv.due_date or inv.posting_date
        age = (to_date - ref_date).days
        out = inv.outstanding_amount

        if age <= 30:
            bucket_0_30 += out
            count_0_30 += 1
        elif age <= 60:
            bucket_31_60 += out
            count_31_60 += 1
        elif age <= 90:
            bucket_61_90 += out
            count_61_90 += 1
        else:
            bucket_91_plus += out
            count_91_plus += 1

    calc_share = (
        lambda b: round(float((b / total_outstanding) * Decimal("100")), 2)
        if total_outstanding > 0
        else 0.0
    )

    return {
        "as_of_date": str(to_date),
        "total_outstanding_receivables": money(total_outstanding),
        "period_credit_sales": money(period_credit_sales),
        "days_sales_outstanding": dso_days,
        "ageing_summary": {
            "current_0_30": {
                "amount": money(bucket_0_30),
                "count": count_0_30,
                "share_pct": calc_share(bucket_0_30),
            },
            "overdue_31_60": {
                "amount": money(bucket_31_60),
                "count": count_31_60,
                "share_pct": calc_share(bucket_31_60),
            },
            "overdue_61_90": {
                "amount": money(bucket_61_90),
                "count": count_61_90,
                "share_pct": calc_share(bucket_61_90),
            },
            "overdue_91_plus": {
                "amount": money(bucket_91_plus),
                "count": count_91_plus,
                "share_pct": calc_share(bucket_91_plus),
            },
        },
    }


def build_analytics_summary(
    company, params: Dict[str, Any], default_today: Optional[datetime.date] = None
) -> Dict[str, Any]:
    """Build the complete Marketing & Sales KPI analytics package."""
    from_date, to_date, period_name = resolve_period(params, default_today)

    conversion = compute_sales_conversion_and_velocity(company, from_date, to_date)
    revenue = compute_revenue_and_volume(company, from_date, to_date, period_name)
    products = compute_top_products_and_spices(company, from_date, to_date)
    customers = compute_customer_analytics(company, from_date, to_date)
    receivables = compute_receivables_and_dso(company, from_date, to_date)

    return {
        "company": {
            "id": company.id,
            "name": company.name,
            "abbr": company.abbr,
        },
        "period": {
            "code": period_name,
            "from_date": str(from_date),
            "to_date": str(to_date),
        },
        "summary": {
            "net_revenue": revenue["current_period"]["net_revenue"],
            "revenue_growth_pct": revenue["growth"]["revenue_growth_pct"],
            "volume": revenue["current_period"]["volume"],
            "volume_growth_pct": revenue["growth"]["volume_growth_pct"],
            "invoices_count": revenue["current_period"]["invoices_count"],
            "average_order_value": revenue["current_period"]["average_order_value"],
            "conversion_rate_pct": conversion["conversion_rate_pct"],
            "average_conversion_days": conversion["average_conversion_days"],
            "active_customers": customers["active_customers_count"],
            "new_customers": customers["new_customers_count"],
            "repeat_rate_pct": customers["repeat_rate_pct"],
            "total_receivables": receivables["total_outstanding_receivables"],
            "dso_days": receivables["days_sales_outstanding"],
        },
        "conversion_and_velocity": conversion,
        "revenue_and_volume": revenue,
        "top_products": products,
        "customer_analytics": customers,
        "receivables_and_dso": receivables,
    }
