from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.db.models.signals import post_delete

        from core import signals

        post_delete.connect(
            signals.delete_attachments_of_deleted_parent,
            dispatch_uid="core.attachments.cascade_with_parent",
        )
