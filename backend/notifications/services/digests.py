from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from core.models import DocStatus
from notifications.services.engine import evaluate_rule


def send_overdue_bills_digest(company=None):
    """
    Finds sales bills past 90 days unpaid, builds formatted summary,
    and queues email and notifications for 'overdue_bills_digest' rule.
    Dedupe key: overdue_digest_YYYY-MM-DD
    """
    from selling.models import SalesInvoice

    today = timezone.localdate()
    cutoff_date = today - timedelta(days=90)

    qs = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        outstanding_amount__gt=Decimal("0.00"),
        posting_date__lte=cutoff_date,
    )
    if company is not None:
        qs = qs.filter(company=company)

    count = qs.count()
    total_agg = qs.aggregate(Sum("outstanding_amount"))["outstanding_amount__sum"]
    total_amount = (
        Decimal(str(total_agg)).quantize(Decimal("0.01"))
        if total_agg is not None
        else Decimal("0.00")
    )

    oldest = qs.order_by("posting_date").first()
    oldest_desc = ""
    if oldest:
        days_late = (today - oldest.posting_date).days
        cust_name = getattr(oldest.customer, "name", str(oldest.customer)) if oldest.customer else ""
        oldest_desc = f"{cust_name}, ₹{oldest.outstanding_amount}, {days_late} days late"

    params = {
        "date": today.isoformat(),
        "count": str(count),
        "total_amount": str(total_amount),
        "oldest_summary": oldest_desc,
    }

    dedupe_key = f"overdue_digest_{today.isoformat()}"
    return evaluate_rule(
        rule_key="overdue_bills_digest",
        message_key="digest.overdue_bills",
        params=params,
        link="/selling/sales-invoices?overdue=1",
        dedupe_key=dedupe_key,
    )


def _ready_stock_short_count(company=None):
    """Items whose stock (outside WIP and scrap) is below their critical level."""
    from stock.models import Bin, Item

    crit_items = Item.objects.filter(
        is_stock_item=True, critical_stock__gt=Decimal("0.000")
    )
    short_count = 0
    for item in crit_items:
        bins = Bin.objects.filter(item=item)
        if company is not None:
            bins = bins.filter(warehouse__company=company)
        bins = bins.exclude(warehouse__name__icontains="wip").exclude(
            warehouse__name__icontains="scrap"
        )
        total_qty = bins.aggregate(total=Sum("actual_qty"))["total"] or Decimal("0.000")
        if total_qty < item.critical_stock:
            short_count += 1
    return short_count


def _money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def send_day_summary(company):
    """
    Evening summary for one company's day: sold, collected, spent, still to collect,
    still to pay and low stock. Goes only to the rule's recipients who belong to
    the company (a company's figures never reach a user of another company).
    Dedupe key: day_summary_<company id>_YYYY-MM-DD, so each company gets one a day.
    Does nothing when the day_summary rule is off or missing.
    """
    from accounts.models import PaymentEntry, PaymentType
    from buying.models import PurchaseInvoice
    from notifications.models import AlertRule
    from selling.models import SalesInvoice

    rule = AlertRule.objects.filter(key="day_summary", is_active=True).first()
    if rule is None:
        return []
    recipients = list(
        rule.recipients.filter(
            company_memberships__company=company,
            company_memberships__is_active=True,
        ).distinct()
    )
    if not recipients:
        return []

    today = timezone.localdate()
    paid_out = PaymentEntry.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        company=company,
        payment_type=PaymentType.PAY,
        posting_date=today,
    ).aggregate(total=Sum("paid_amount"))["total"]
    collected = PaymentEntry.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        company=company,
        payment_type=PaymentType.RECEIVE,
        posting_date=today,
    ).aggregate(total=Sum("paid_amount"))["total"]
    sold = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED, company=company, posting_date=today
    ).aggregate(total=Sum("grand_total"))["total"]
    to_collect = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        company=company,
        outstanding_amount__gt=Decimal("0.00"),
    ).aggregate(total=Sum("outstanding_amount"))["total"]
    to_pay = PurchaseInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        company=company,
        outstanding_amount__gt=Decimal("0.00"),
    ).aggregate(total=Sum("outstanding_amount"))["total"]

    params = {
        "date": today.isoformat(),
        "sold": str(_money(sold)),
        "collected": str(_money(collected)),
        # Money that left today. An expense paid at once already has its Pay entry,
        # and one still due is not spent yet, so expense bills are not added again.
        "spent": str(_money(paid_out)),
        "to_collect": str(_money(to_collect)),
        "to_pay": str(_money(to_pay)),
        "low_stock": str(_ready_stock_short_count(company)),
    }

    return evaluate_rule(
        rule_key="day_summary",
        message_key="digest.day_summary",
        params=params,
        link="/dashboard",
        dedupe_key=f"day_summary_{company.pk}_{today.isoformat()}",
        recipients=recipients,
    )


def send_day_summaries():
    """The evening day summary for every company."""
    from core.models import Company

    sent = []
    for company in Company.objects.all():
        sent.extend(send_day_summary(company))
    return sent


def send_daily_summary(company=None):
    """
    Money in, bills issued, pending dispatches, ready stock short.
    Dedupe key: daily_summary_YYYY-MM-DD
    """
    from accounts.models import PaymentEntry, PaymentType
    from selling.models import SalesInvoice, SalesOrder, DispatchStatus

    today = timezone.localdate()

    # 1. Money in today
    pay_qs = PaymentEntry.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        payment_type=PaymentType.RECEIVE,
        posting_date=today,
    )
    if company is not None:
        pay_qs = pay_qs.filter(company=company)
    in_agg = pay_qs.aggregate(Sum("received_amount"))["received_amount__sum"]
    money_in = (
        Decimal(str(in_agg)).quantize(Decimal("0.01"))
        if in_agg is not None
        else Decimal("0.00")
    )

    # 2. Bills issued today
    inv_qs = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        posting_date=today,
    )
    if company is not None:
        inv_qs = inv_qs.filter(company=company)
    bills_count = inv_qs.count()
    bills_agg = inv_qs.aggregate(Sum("grand_total"))["grand_total__sum"]
    bills_total = (
        Decimal(str(bills_agg)).quantize(Decimal("0.01"))
        if bills_agg is not None
        else Decimal("0.00")
    )

    # 3. Pending dispatches
    order_qs = SalesOrder.objects.filter(
        docstatus=DocStatus.SUBMITTED,
    ).exclude(dispatch_status=DispatchStatus.DISPATCHED)
    if company is not None:
        order_qs = order_qs.filter(company=company)
    pending_dispatches = order_qs.count()

    # 4. Ready stock short
    short_count = _ready_stock_short_count(company)

    params = {
        "date": today.isoformat(),
        "money_in": str(money_in),
        "bills_count": str(bills_count),
        "bills_total": str(bills_total),
        "pending_dispatches": str(pending_dispatches),
        "ready_stock_short": str(short_count),
    }

    dedupe_key = f"daily_summary_{today.isoformat()}"
    return evaluate_rule(
        rule_key="daily_summary",
        message_key="digest.daily_summary",
        params=params,
        link="/dashboard",
        dedupe_key=dedupe_key,
    )


def send_weekly_digest(company=None):
    """
    Collections needed this week, bills due this week, GST/TDS due dates.
    Dedupe key: weekly_digest_YYYY-MM-DD
    """
    from selling.models import SalesInvoice
    from buying.models import PurchaseInvoice

    today = timezone.localdate()
    week_end = today + timedelta(days=7)

    # 1. Collections needed this week
    cust_qs = SalesInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        outstanding_amount__gt=Decimal("0.00"),
        due_date__gte=today,
        due_date__lte=week_end,
    )
    if company is not None:
        cust_qs = cust_qs.filter(company=company)
    cust_agg = cust_qs.aggregate(Sum("outstanding_amount"))["outstanding_amount__sum"]
    collections_needed = (
        Decimal(str(cust_agg)).quantize(Decimal("0.01"))
        if cust_agg is not None
        else Decimal("0.00")
    )

    # 2. Bills due this week
    supp_qs = PurchaseInvoice.objects.filter(
        docstatus=DocStatus.SUBMITTED,
        outstanding_amount__gt=Decimal("0.00"),
        due_date__gte=today,
        due_date__lte=week_end,
    )
    if company is not None:
        supp_qs = supp_qs.filter(company=company)
    supp_agg = supp_qs.aggregate(Sum("outstanding_amount"))["outstanding_amount__sum"]
    bills_due = (
        Decimal(str(supp_agg)).quantize(Decimal("0.01"))
        if supp_agg is not None
        else Decimal("0.00")
    )

    # 3. GST/TDS info
    tax_info = "GST due 20th, TDS due 7th"

    params = {
        "date": today.isoformat(),
        "collections_needed": str(collections_needed),
        "bills_due": str(bills_due),
        "tax_due_info": tax_info,
    }

    dedupe_key = f"weekly_digest_{today.isoformat()}"
    return evaluate_rule(
        rule_key="weekly_digest",
        message_key="digest.weekly_digest",
        params=params,
        link="/dashboard",
        dedupe_key=dedupe_key,
    )
