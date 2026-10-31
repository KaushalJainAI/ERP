import uuid
import logging
from django.db import IntegrityError, transaction

from notifications.models import AlertRule, Notification
from notifications.services.mail import queue_email

logger = logging.getLogger(__name__)


def evaluate_rule(rule_key, message_key, params=None, link="", dedupe_key=None, recipients=None):
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
            notif = Notification.objects.filter(user=user, dedupe_key=base_dedupe).first()

        # Queue email if user has an email
        user_email = getattr(user, "email", None)
        if user_email:
            subject = f"ERP Alert: {message_key}"
            body_parts = [f"Rule: {rule_key}"]
            for k, v in params.items():
                body_parts.append(f"{k}: {v}")
            if link:
                body_parts.append(f"\nLink: {link}")
            body = "\n".join(body_parts)

            email_dedupe_key = f"email_{user.pk}_{base_dedupe}"
            queue_email(
                to=user_email,
                subject=subject,
                body=body,
                dedupe_key=email_dedupe_key,
            )

    return created_notifications
