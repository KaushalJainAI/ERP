from notifications.models import AlertRule

DEFAULT_ALERT_RULES = [
    "overdue_bills_digest",
    "daily_summary",
    "weekly_digest",
    "raw_material_shortage",
    "large_order",
    "large_payment",
    "payment_delay",
    "expense_unpaid",
    "gst_credit_at_risk",
]


def seed_alert_rules():
    """Idempotently seed default AlertRule rows."""
    created_count = 0
    for key in DEFAULT_ALERT_RULES:
        rule, created = AlertRule.objects.get_or_create(
            key=key, defaults={"is_active": True}
        )
        if created:
            created_count += 1
    return created_count
