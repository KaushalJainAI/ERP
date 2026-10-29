"""Organisation drive: shared folders, files and per-file sharing.

This is deliberately *not* ``core.Attachment``. An attachment is immutable proof
bound to one parent record; a drive file is a mutable collaboration object that
can be renamed, moved, made private, shared with colleagues and deleted. Keeping
the two models separate preserves the proof-document guarantees.
"""

import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import Company, TimeStampedModel


def drive_upload_to(instance, filename):
    """Store uploads under an opaque path; never trust the client's filename."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".heif",
        ".docx",
        ".xlsx",
        ".pptx",
        ".csv",
        ".txt",
    }:
        ext = ""
    return f"drive/{instance.company_id}/{uuid.uuid4().hex}{ext}"


class Folder(TimeStampedModel):
    """A company-scoped folder. Folders are shared with every company member."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="drive_folders"
    )
    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    #: Marks the one system-owned folder (company documents). System folders
    #: cannot be renamed, moved or deleted.
    system_key = models.CharField(max_length=40, blank=True, default="")
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "parent", "name"],
                condition=Q(parent__isnull=False),
                name="unique_drive_subfolder_name",
            ),
            models.UniqueConstraint(
                fields=["company", "name"],
                condition=Q(parent__isnull=True),
                name="unique_drive_root_folder_name",
            ),
            models.UniqueConstraint(
                fields=["company", "system_key"],
                condition=~Q(system_key=""),
                name="unique_drive_system_folder",
            ),
            models.CheckConstraint(
                check=Q(is_system=False) | Q(system_key__gt=""),
                name="drive_system_folder_has_key",
            ),
        ]

    def clean(self):
        if self.parent_id and self.parent.company_id != self.company_id:
            raise ValidationError(_("A folder cannot be moved across companies."))
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError(_("A folder cannot be its own parent."))
        if self.is_system and not self.system_key:
            raise ValidationError(_("A system folder requires a system key."))
        ancestor = self.parent
        seen = set()
        while ancestor is not None:
            if ancestor.pk == self.pk:
                raise ValidationError(_("A folder cannot be inside itself."))
            if ancestor.pk in seen:
                break
            seen.add(ancestor.pk)
            ancestor = ancestor.parent

    def __str__(self):
        return self.name


class DriveFile(TimeStampedModel):
    """A mutable file in the organisation drive."""

    class Visibility(models.TextChoices):
        COMPANY = "company", "Everyone in the company"
        PRIVATE = "private", "Only people it is shared with"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="drive_files"
    )
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="files",
    )
    file = models.FileField(upload_to=drive_upload_to, max_length=255)
    name = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    description = models.CharField(max_length=500, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="drive_files",
    )
    visibility = models.CharField(
        max_length=12,
        choices=Visibility.choices,
        default=Visibility.COMPANY,
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "folder"]),
            models.Index(fields=["company", "visibility"]),
        ]

    def clean(self):
        if self.folder_id and self.folder.company_id != self.company_id:
            raise ValidationError(_("The folder belongs to another company."))
        if self.visibility not in self.Visibility.values:
            raise ValidationError(_("Invalid visibility."))

    def delete(self, *args, **kwargs):
        """Remove the row and, after commit, the stored bytes."""
        storage = self.file.storage
        name = self.file.name
        result = super().delete(*args, **kwargs)
        if name:
            transaction.on_commit(lambda: storage.delete(name))
        return result

    def __str__(self):
        return self.name


class DriveShare(models.Model):
    """Explicit access to a private file for one company member."""

    file = models.ForeignKey(
        DriveFile, on_delete=models.CASCADE, related_name="shares"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="drive_shares",
    )
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    can_write = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["file", "user"], name="unique_drive_file_share"
            )
        ]

    def __str__(self):
        return f"{self.file} -> {self.user}"
