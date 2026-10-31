from datetime import timedelta
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import connection, models
from django.utils import timezone

from core.models import Outbox

logger = logging.getLogger(__name__)

BACKOFF_MINUTES = {
    1: 5,
    2: 15,
    3: 60,
}


def process_outbox():
    """
    Process pending Outbox rows and send emails.
    Returns (sent_count, failed_count).
    """
    now = timezone.now()
    qs = Outbox.objects.filter(
        channel="email",
        status__in=["Pending", "pending"],
    ).filter(
        models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now)
    ).order_by("created_at")

    if connection.features.has_select_for_update_skip_locked:
        qs = qs.select_for_update(skip_locked=True)

    sent_count = 0
    failed_count = 0

    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or getattr(settings, "EMAIL_HOST_USER", None)
        or "noreply@nidhimasala.com"
    )

    # Convert to list if needed or iterate
    for entry in list(qs):
        recipient = (
            entry.recipient
            or (entry.payload.get("recipient") if isinstance(entry.payload, dict) else None)
            or (entry.payload.get("to") if isinstance(entry.payload, dict) else None)
        )
        subject = entry.payload.get("subject", "") if isinstance(entry.payload, dict) else ""
        body = entry.payload.get("body", "") if isinstance(entry.payload, dict) else ""

        try:
            if not recipient:
                raise ValueError("Missing email recipient in Outbox entry")

            send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=[recipient],
                fail_silently=False,
            )
            entry.status = "Sent"
            entry.save(update_fields=["status"])
            sent_count += 1
        except Exception as exc:
            entry.attempts += 1
            entry.last_error = str(exc)
            if entry.attempts >= 4:
                entry.status = "Dead"
                entry.next_attempt_at = None
            else:
                minutes = BACKOFF_MINUTES.get(entry.attempts, 60)
                entry.next_attempt_at = timezone.now() + timedelta(minutes=minutes)
            entry.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])
            failed_count += 1
            logger.warning("Outbox entry %s failed (attempt %s): %s", entry.pk, entry.attempts, exc)

    return sent_count, failed_count


class Command(BaseCommand):
    help = "Dispatch pending emails from the Outbox"

    def handle(self, *args, **options):
        sent, failed = process_outbox()
        self.stdout.write(
            self.style.SUCCESS(f"Outbox run complete: {sent} sent, {failed} failed/retried.")
        )
