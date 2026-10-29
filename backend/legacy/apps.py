from django.apps import AppConfig


class LegacyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "legacy"
    verbose_name = "Legacy (Tally) history"
