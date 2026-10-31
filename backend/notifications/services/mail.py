import uuid
from django.db import IntegrityError, transaction
from core.models import Outbox


def queue_email(to, subject, body, dedupe_key=None):
    """
    Queue an email in the Outbox table for background dispatch.
    If dedupe_key already exists, the existing record is returned without duplicating.
    """
    key = dedupe_key or f"email:{uuid.uuid4().hex}"
    payload = {
        "subject": subject,
        "body": body,
        "recipient": to,
        "to": to,
    }
    try:
        with transaction.atomic():
            return Outbox.objects.create(
                channel="email",
                recipient=to,
                payload=payload,
                dedupe_key=key,
                status="Pending",
            )
    except IntegrityError:
        return Outbox.objects.filter(dedupe_key=key).first()
