from django.apps import AppConfig


class PaymentReconciliationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payment_reconciliation"
    verbose_name = "Payment reconciliation"

    def ready(self):
        from payment_reconciliation import signals  # noqa: F401
