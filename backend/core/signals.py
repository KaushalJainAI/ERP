"""Signal handlers for ``core``."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.mixins import record_audit_log


@receiver(
    post_save,
    sender=settings.AUTH_USER_MODEL,
    dispatch_uid="core.usercompany.single_company_on_create",
)
def assign_single_company_on_user_create(sender, instance, created, **kwargs):
    """A new user joins the only active company automatically.

    In a single-company deployment this is the sane onboarding default; in a
    multi-company deployment membership stays explicit. Superusers see every
    company and need no row.
    """
    if not created or instance.is_superuser:
        return
    from core.models import Company, UserCompany

    companies = list(Company.objects.filter(is_active=True)[:2])
    if len(companies) != 1:
        return
    UserCompany.objects.get_or_create(
        user=instance,
        company=companies[0],
        defaults={"is_default": True, "is_active": True},
    )


def delete_attachments_of_deleted_parent(sender, instance, **kwargs):
    """A deleted draft / master takes its attachments with it (like ERPNext's File).

    Submitted and cancelled documents cannot be deleted at all, so this never
    removes proof; it only stops orphaned files piling up behind deleted drafts.
    """
    from core import attachments
    from core.models import Attachment

    label = getattr(getattr(sender, "_meta", None), "label", None)
    if label not in attachments.attachable_models():
        return
    for att in Attachment.objects.filter(model_label=label, object_id=str(instance.pk)):
        changes = {
            "attachment_id": att.pk,
            "filename": att.original_filename,
            "sha256": att.sha256,
            "cascade": True,
        }
        att.delete()
        record_audit_log(None, "attachment_delete", instance, changes=changes)
