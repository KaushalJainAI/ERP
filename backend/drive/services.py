"""Drive domain helpers: the company-documents folder and access predicates."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from core.company_context import companies_for
from drive.models import DriveFile, DriveShare, Folder

COMPANY_DOCUMENTS_KEY = "company_documents"
COMPANY_DOCUMENTS_NAME = "Company documents"


def company_documents_folder(company):
    """Return (create once) the system folder that backs the company page."""
    existing = Folder.objects.filter(
        company=company, system_key=COMPANY_DOCUMENTS_KEY
    ).first()
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            # Adopt a user-created folder with the same display name instead of
            # failing on the root-name uniqueness constraint.
            folder = Folder.objects.filter(
                company=company, parent__isnull=True, name=COMPANY_DOCUMENTS_NAME
            ).first()
            if folder is not None:
                folder.is_system = True
                folder.system_key = COMPANY_DOCUMENTS_KEY
                folder.save(update_fields=["is_system", "system_key", "modified_at"])
                return folder
            return Folder.objects.create(
                company=company,
                name=COMPANY_DOCUMENTS_NAME,
                is_system=True,
                system_key=COMPANY_DOCUMENTS_KEY,
            )
    except IntegrityError:
        # Another request won the race; return its row.
        return Folder.objects.get(
            company=company, system_key=COMPANY_DOCUMENTS_KEY
        )


def is_company_member(user, company) -> bool:
    return companies_for(user).filter(pk=company.pk).exists()


def can_read_file(user, drive_file: DriveFile) -> bool:
    if user.is_superuser or drive_file.uploaded_by_id == user.id:
        return True
    if drive_file.visibility == DriveFile.Visibility.COMPANY:
        return True
    return drive_file.shares.filter(user=user).exists()


def can_edit_file(user, drive_file: DriveFile) -> bool:
    if user.is_superuser or drive_file.uploaded_by_id == user.id:
        return True
    return drive_file.shares.filter(user=user, can_write=True).exists()


def can_delete_file(user, drive_file: DriveFile) -> bool:
    return user.is_superuser or drive_file.uploaded_by_id == user.id


def can_manage_folder(user, folder: Folder) -> bool:
    if user.is_superuser:
        return True
    if folder.is_system:
        return False
    return folder.created_by_id == user.id


def visible_file_queryset(user, company):
    """Files in ``company`` the caller may read (company-wide or explicitly shared)."""
    queryset = DriveFile.objects.filter(company=company)
    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(visibility=DriveFile.Visibility.COMPANY)
        | Q(uploaded_by=user)
        | Q(shares__user=user)
    ).distinct()


def validate_share_target(user, company, target_user):
    """A file may only be shared with an active member of the same company."""
    if target_user is None or not target_user.is_active:
        raise ValueError(_("That user is not active."))
    if not is_company_member(target_user, company):
        raise ValueError(_("That user is not a member of this company."))
    if target_user.pk == user.pk:
        raise ValueError(_("You already have access to your own file."))
    return target_user


def share_file(*, drive_file, target_user, shared_by, can_write=False):
    share, _created = DriveShare.objects.update_or_create(
        file=drive_file,
        user=target_user,
        defaults={"shared_by": shared_by, "can_write": can_write},
    )
    return share
