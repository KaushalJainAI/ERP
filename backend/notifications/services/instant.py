from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from core.models import Company, DocStatus
from notifications.services.engine import evaluate_rule


def check_item_ready_low(item, company=None):
    """
    Checks if an item's ready stock has fallen below its critical_stock.
    If so, evaluates rule 'ready_low'.
    """
    if (
        not item.is_stock_item
        or not item.critical_stock
        or item.critical_stock <= Decimal("0.000")
    ):
        return None

    from stock.models import Bin

    bins_qs = Bin.objects.filter(item=item)
    if company:
        bins_qs = bins_qs.filter(warehouse__company=company)
        ready_wh_id = getattr(company, "ready_warehouse_id", None)
        if ready_wh_id and bins_qs.filter(warehouse_id=ready_wh_id).exists():
            bins_qs = bins_qs.filter(warehouse_id=ready_wh_id)
        else:
            bins_qs = bins_qs.exclude(warehouse__name__icontains="wip").exclude(
                warehouse__name__icontains="scrap"
            )
    else:
        bins_qs = bins_qs.exclude(warehouse__name__icontains="wip").exclude(
            warehouse__name__icontains="scrap"
        )

    total_qty = bins_qs.aggregate(total=Sum("actual_qty"))["total"]
    qty = (
        Decimal(str(total_qty)).quantize(Decimal("0.001"))
        if total_qty is not None
        else Decimal("0.000")
    )

    if qty < item.critical_stock:
        today = timezone.localdate().isoformat()
        dedupe_key = f"ready_low_{item.id}_{today}"
        return evaluate_rule(
            rule_key="ready_low",
            message_key="alert.ready_low",
            params={
                "item": item.name,
                "qty": str(qty),
                "critical": str(item.critical_stock),
            },
            link=f"/stock/items/{item.id}",
            dedupe_key=dedupe_key,
        )
    return None


def trigger_ready_low_for_items(items):
    """
    Given a list or queryset of items, run ready_low check for each.
    """
    seen_ids = set()
    for item in items:
        if item and item.id not in seen_ids:
            seen_ids.add(item.id)
            check_item_ready_low(item)


def on_integrity_failed(errors):
    """
    Trigger instant alert when verify_integrity detects errors.
    """
    now_ts = timezone.now().strftime("%Y%m%d%H%M%S")
    error_summary = "\n".join(str(e) for e in errors[:5])
    if len(errors) > 5:
        error_summary += f"\n... and {len(errors) - 5} more error(s)"

    return evaluate_rule(
        rule_key="integrity_failed",
        message_key="alert.integrity_failed",
        params={
            "error_count": str(len(errors)),
            "summary": error_summary,
        },
        link="/health",
        dedupe_key=f"integrity_failed_{now_ts}",
    )


def on_backup_failed(error):
    """Alert the Admin when the nightly database backup fails (core.backup_db).
    One alert per day: a retry the same day does not send a second email."""
    today = timezone.localdate().isoformat()
    return evaluate_rule(
        rule_key="backup_failed",
        message_key="alert.backup_failed",
        params={"error": str(error)[:500]},
        link="/health",
        dedupe_key=f"backup_failed_{today}",
    )


def on_sales_order_submit(order):
    """
    Alert if SalesOrder grand_total > 50,000.
    """
    grand_total = getattr(order, "grand_total", None) or Decimal("0.00")
    if grand_total > Decimal("50000.00"):
        customer_name = ""
        if hasattr(order, "customer") and order.customer:
            customer_name = getattr(order.customer, "name", str(order.customer))

        evaluate_rule(
            rule_key="large_order",
            message_key="alert.large_order",
            params={
                "order": order.name,
                "customer": customer_name,
                "grand_total": str(grand_total),
            },
            link=f"/selling/sales-orders/{order.id}",
            dedupe_key=f"large_order_{order.id}",
        )


def on_payment_entry_submit(payment):
    """
    Alert if PaymentEntry amount > 50,000.
    """
    paid = getattr(payment, "paid_amount", Decimal("0.00")) or Decimal("0.00")
    received = getattr(payment, "received_amount", Decimal("0.00")) or Decimal("0.00")
    amount = max(paid, received)
    if amount > Decimal("50000.00"):
        party = getattr(payment, "party_name", "") or ""
        evaluate_rule(
            rule_key="large_payment",
            message_key="alert.large_payment",
            params={
                "payment": payment.name,
                "party": party,
                "amount": str(amount),
            },
            link=f"/accounts/payment-entries/{payment.id}",
            dedupe_key=f"large_payment_{payment.id}",
        )


def check_payment_delay(company=None, today=None):
    """
    Alert for SalesInvoice reaching 90 days unpaid.
    """
    from selling.models import SalesInvoice

    today_date = today or timezone.localdate()
    cutoff_date = today_date - timedelta(days=90)

    qs = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        outstanding_amount__gt=Decimal("0.00"),
        posting_date__lte=cutoff_date,
    )
    if company is not None:
        qs = qs.filter(company=company)

    alerts = []
    for inv in qs:
        days_unpaid = (today_date - inv.posting_date).days
        customer_name = ""
        if hasattr(inv, "customer") and inv.customer:
            customer_name = getattr(inv.customer, "name", str(inv.customer))
        res = evaluate_rule(
            rule_key="payment_delay",
            message_key="alert.payment_delay",
            params={
                "invoice": inv.name,
                "customer": customer_name,
                "outstanding": str(inv.outstanding_amount),
                "days_unpaid": str(days_unpaid),
            },
            link=f"/selling/sales-invoices/{inv.id}",
            dedupe_key=f"payment_delay_{inv.id}_{today_date.isoformat()}",
        )
        alerts.append((inv, res))
    return alerts


def check_expense_unpaid(company=None, today=None):
    """
    Alert for Expense PurchaseInvoice reaching 30 days unpaid.
    """
    from buying.models import PurchaseInvoice

    today_date = today or timezone.localdate()
    cutoff_date = today_date - timedelta(days=30)

    qs = PurchaseInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        is_expense=True,
        outstanding_amount__gt=Decimal("0.00"),
        posting_date__lte=cutoff_date,
    )
    if company is not None:
        qs = qs.filter(company=company)

    alerts = []
    for inv in qs:
        days_unpaid = (today_date - inv.posting_date).days
        supplier_name = ""
        if hasattr(inv, "supplier") and inv.supplier:
            supplier_name = getattr(inv.supplier, "name", str(inv.supplier))
        res = evaluate_rule(
            rule_key="expense_unpaid",
            message_key="alert.expense_unpaid",
            params={
                "invoice": inv.name,
                "supplier": supplier_name,
                "outstanding": str(inv.outstanding_amount),
                "days_unpaid": str(days_unpaid),
            },
            link=f"/buying/purchase-invoices/{inv.id}",
            dedupe_key=f"expense_unpaid_{inv.id}_{today_date.isoformat()}",
        )
        alerts.append((inv, res))
    return alerts


def check_gst_credit_at_risk(company=None, today=None):
    """
    Alert for PurchaseInvoice with GST reaching 150 days unpaid.
    """
    from accounts.services.gst_position import gst_credit_at_risk

    today_date = today or timezone.localdate()
    companies = [company] if company is not None else list(Company.objects.all())

    alerts = []
    for comp in companies:
        rows = gst_credit_at_risk(comp, as_on=today_date)
        for row in rows:
            inv_id = row.get("invoice_id")
            inv_name = row.get("invoice_name")
            outstanding = row.get("outstanding")
            total_gst = row.get("total_gst")
            days_unpaid = row.get("days_unpaid")
            res = evaluate_rule(
                rule_key="gst_credit_at_risk",
                message_key="alert.gst_credit_at_risk",
                params={
                    "invoice": inv_name,
                    "outstanding": str(outstanding),
                    "gst_amount": str(total_gst),
                    "days_unpaid": str(days_unpaid),
                },
                link=f"/buying/purchase-invoices/{inv_id}",
                dedupe_key=f"gst_credit_at_risk_{inv_id}_{today_date.isoformat()}",
            )
            alerts.append((row, res))
    return alerts
