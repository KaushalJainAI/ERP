import uuid
import logging
from django.db import IntegrityError, transaction

from notifications.models import AlertRule, Notification
from notifications.services.mail import queue_email

logger = logging.getLogger(__name__)


def evaluate_rule(
    rule_key, message_key, params=None, link="", dedupe_key=None, recipients=None
):
    """
    Evaluate an alert rule:
    - Checks if AlertRule with key=rule_key is active. If inactive (or not found), does nothing.
    - Creates Notification for each recipient user (unique on user, dedupe_key).
    - Queues email via queue_email for recipients with non-empty user.email.
    """
    params = params or {}
    try:
        rule = AlertRule.objects.get(key=rule_key)
    except AlertRule.DoesNotExist:
        logger.info("AlertRule %s does not exist; skipping.", rule_key)
        return []

    if not rule.is_active:
        return []

    if recipients is None:
        target_recipients = list(rule.recipients.all())
    else:
        target_recipients = list(recipients)

    if not target_recipients:
        return []

    created_notifications = []
    base_dedupe = dedupe_key or uuid.uuid4().hex

    for user in target_recipients:
        # Create Notification
        notif = None
        try:
            with transaction.atomic():
                notif = Notification.objects.create(
                    user=user,
                    rule_key=rule_key,
                    message_key=message_key,
                    params=params,
                    link=link,
                    dedupe_key=base_dedupe,
                )
                created_notifications.append(notif)
        except IntegrityError:
            # Notification with this user and dedupe_key already exists
            notif = Notification.objects.filter(
                user=user, dedupe_key=base_dedupe
            ).first()

        # Queue email if user has an email
        user_email = getattr(user, "email", None)
        if user_email:
            from notifications.messages import render_email

            subject, body = render_email(
                rule_key, params, link, language=getattr(user, "language", None)
            )

            email_dedupe_key = f"email_{user.pk}_{base_dedupe}"
            queue_email(
                to=user_email,
                subject=subject,
                body=body,
                dedupe_key=email_dedupe_key,
            )

    return created_notifications


def check_raw_material_shortage(company=None):
    """
    Checks all stock items where critical_stock > 0.
    For each, gets ready/unprocessed Bin qty. If qty < critical_stock:
    Calls evaluate_rule(
        "raw_material_shortage",
        message_key="alert.raw_material_shortage",
        params={"item": item.name, "qty": str(qty), "critical": str(item.critical_stock)},
        dedupe_key=f"shortage_{item.id}_{today}",
    )
    """
    from decimal import Decimal
    from django.db import models
    from django.db.models import Sum, Q
    from django.utils import timezone
    from stock.models import Bin, Item

    today = timezone.localdate().isoformat()
    items = Item.objects.filter(is_stock_item=True, critical_stock__gt=Decimal("0.000"))

    triggered_alerts = []

    for item in items:
        bins_qs = Bin.objects.filter(item=item)
        if company is not None:
            bins_qs = bins_qs.filter(warehouse__company=company)
            wh_candidates = [
                getattr(company, "unprocessed_warehouse_id", None),
                getattr(company, "ready_warehouse_id", None),
            ]
            wh_candidates = [w for w in wh_candidates if w]
            if (
                wh_candidates
                and bins_qs.filter(warehouse_id__in=wh_candidates).exists()
            ):
                bins_qs = bins_qs.filter(warehouse_id__in=wh_candidates)
        else:
            stage_wh_bins = bins_qs.filter(
                Q(warehouse__company__unprocessed_warehouse=models.F("warehouse"))
                | Q(warehouse__company__ready_warehouse=models.F("warehouse"))
            )
            if stage_wh_bins.exists():
                bins_qs = stage_wh_bins

        total_qty = bins_qs.aggregate(total=Sum("actual_qty"))["total"]
        if total_qty is None:
            qty = Decimal("0.000")
        else:
            qty = Decimal(str(total_qty)).quantize(Decimal("0.001"))

        if qty < item.critical_stock:
            res = evaluate_rule(
                rule_key="raw_material_shortage",
                message_key="alert.raw_material_shortage",
                params={
                    "item": item.name,
                    "qty": str(qty),
                    "critical": str(item.critical_stock),
                },
                dedupe_key=f"shortage_{item.id}_{today}",
            )
            triggered_alerts.append((item, qty, res))

    return triggered_alerts
