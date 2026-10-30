from django.conf import settings
from django.db import models


class AlertRule(models.Model):
    key = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="alert_rules"
    )
    days = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.key


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    rule_key = models.CharField(max_length=64)
    message_key = models.CharField(max_length=128)
    params = models.JSONField(default=dict, blank=True)
    link = models.CharField(max_length=255, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    dedupe_key = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                name="unique_user_notification_dedupe",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.rule_key} ({self.dedupe_key})"
