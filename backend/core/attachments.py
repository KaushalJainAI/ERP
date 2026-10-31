"""Attachments (proof documents): validation, parent resolution and RBAC rules.

ERPNext's ``File`` doctype attaches a file to any document through
``attached_to_doctype`` / ``attached_to_name``. We keep that shape
(``Attachment.model_label`` + ``object_id``) and add what a books-of-account
system needs on top: proof is immutable and, once its document is submitted or
cancelled, can never be removed.

This module is deliberately free of HTTP concerns; ``core.views`` calls into it.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.urls import Resolver404, resolve
from django.utils.translation import gettext as _

from users.permissions import resolve_model_label, user_has_model_permission

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class AttachmentError(Exception):
    """A refused attachment operation.

    ``code`` is stable and machine-readable so the UI can show the message in
    the operator's own language; ``message`` is already gettext-translated.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# Which models can carry attachments
# --------------------------------------------------------------------------- #

#: Masters that may carry proof documents, besides every ``SubmittableDocument``.
MASTER_LABELS = frozenset(
    {
        "selling.Customer",
        "buying.Supplier",
        "stock.Item",
        "stock.Batch",
        "stock.Warehouse",
        "accounts.Account",
        "manufacturing.BOM",
        "contacts.Address",
    }
)

#: Parents that exist only to hold an assistant's bill photo until the purchase
#: invoice it proposes is saved. The photo is copied onto that invoice then. The
#: generic attachment API never names these parents (see ``resolve_parent_label``).
STAGING_LABELS = frozenset({"assistant.AssistantMessage"})

_attachable_cache: Optional[dict] = None


def attachable_models() -> dict:
    """``{"app.Model": model_class}`` for every model that may carry attachments."""
    global _attachable_cache
    if _attachable_cache is None:
        from core.models import SubmittableDocument

        found = {}
        for model in apps.get_models():
            label = model._meta.label
            if (
                issubclass(model, SubmittableDocument)
                or label in MASTER_LABELS
                or label in STAGING_LABELS
            ):
                found[label] = model
        _attachable_cache = found
    return _attachable_cache


def resolve_parent_label(model: str = "", endpoint: str = "") -> str:
    """Turn the ``model`` label or the frontend's list ``endpoint`` into a label.

    The generic form knows its DRF list route (``/selling/sales-invoices/``), not
    the Django model, so the endpoint is resolved through the URL conf to the
    viewset's model -- the same resolution RBAC itself uses.
    """
    model = (model or "").strip()
    endpoint = (endpoint or "").strip()
    label = ""
    if model:
        label = model
    elif endpoint:
        path = "/api/" + endpoint.strip("/") + "/"
        try:
            match = resolve(path)
        except Resolver404:
            match = None
        view_cls = getattr(match.func, "cls", None) if match else None
        if view_cls is not None:
            label = (
                resolve_model_label(view_cls(**getattr(match.func, "initkwargs", {})))
                or ""
            )
    if label not in attachable_models() or label in STAGING_LABELS:
        raise AttachmentError(
            "attachment_parent_invalid",
            _("Files cannot be attached to this kind of record."),
            400,
        )
    return label


def get_parent(model_label: str, object_id: str):
    """The parent row, or ``None`` when it does not exist (or the id is malformed)."""
    model = attachable_models().get(model_label)
    if model is None:
        return None
    try:
        return model._default_manager.filter(pk=object_id).first()
    except (ValueError, TypeError):
        return None


def parent_docstatus(parent) -> Optional[int]:
    """The parent's ``docstatus``, or ``None`` for masters (which have none)."""
    return getattr(parent, "docstatus", None)


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #

#: Any one of these flags on the parent's model lets a user attach proof to it.
UPLOAD_FLAGS = ("can_write", "can_submit", "can_create")


def can_read(user, model_label: str) -> bool:
    return user_has_model_permission(user, model_label, "can_read")


def can_upload(user, model_label: str) -> bool:
    return any(user_has_model_permission(user, model_label, f) for f in UPLOAD_FLAGS)


def can_manage(user, model_label: str) -> bool:
    """Manager-type authority: may delete the parent record itself (``can_delete``)."""
    return user_has_model_permission(user, model_label, "can_delete")


def delete_verdict(user, attachment, parent) -> Optional[AttachmentError]:
    """Why ``user`` may NOT delete ``attachment`` (``None`` when they may).

    Rules, in order: the user must be able to read the parent; proof on a
    submitted or cancelled document is permanent (for everyone, superusers
    included); otherwise only the uploader (while they can still write to the
    parent) or a manager may delete.
    """
    label = attachment.model_label
    if not can_read(user, label):
        return AttachmentError(
            "attachment_forbidden", _("You do not have permission to do that."), 403
        )
    if parent_docstatus(parent) in (1, 2):
        return AttachmentError(
            "attachment_locked",
            _("Proof attached to a submitted or cancelled document cannot be deleted."),
            409,
        )
    if user.is_superuser or can_manage(user, label):
        return None
    if attachment.uploaded_by_id == user.id and can_upload(user, label):
        return None
    return AttachmentError(
        "attachment_delete_forbidden",
        _("Only the uploader or a manager can delete this attachment."),
        403,
    )


# --------------------------------------------------------------------------- #
# Content validation: extension AND magic bytes, never the client content type
# --------------------------------------------------------------------------- #

#: kind -> (canonical content type, allowed extensions)
KINDS = {
    "pdf": ("application/pdf", {"pdf"}),
    "jpeg": ("image/jpeg", {"jpg", "jpeg"}),
    "png": ("image/png", {"png"}),
    "webp": ("image/webp", {"webp"}),
    "heic": ("image/heic", {"heic", "heif"}),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        {"xlsx"},
    ),
    "csv": ("text/csv", {"csv"}),
}
_EXT_TO_KIND = {ext: kind for kind, (_ct, exts) in KINDS.items() for ext in exts}
#: Kinds a browser can safely render inline from the download endpoint.
INLINE_KINDS = frozenset({"pdf", "jpeg", "png", "webp"})
INLINE_CONTENT_TYPES = frozenset(KINDS[k][0] for k in INLINE_KINDS)

_HEIC_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
}
_SNIFF_BYTES = 8192


def allowed_extensions() -> list:
    return sorted(_EXT_TO_KIND)


def _is_xlsx(data: bytes, stream) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return "[Content_Types].xml" in names and "xl/workbook.xml" in names


def _is_csv(head: bytes) -> bool:
    """Plain text: no NUL / control bytes, so executables and archives are out."""
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 CSV exports
        return True
    return not re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", head)


def sniff_kind(stream) -> Optional[str]:
    """Identify the real file type from its bytes (``None`` if not allowed)."""
    stream.seek(0)
    head = stream.read(_SNIFF_BYTES)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx" if _is_xlsx(head, stream) else None
    if _is_csv(head):
        return "csv"
    return None


def safe_filename(name: str) -> str:
    """The client's filename reduced to a harmless display name (no path, no control chars)."""
    name = unicodedata.normalize("NFC", name or "")
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[-200:] if len(name) > 200 else name


@dataclass
class ValidatedUpload:
    filename: str
    kind: str
    content_type: str
    size: int
    sha256: str


def max_bytes() -> int:
    return int(getattr(settings, "ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024))


def validate_upload(upload) -> ValidatedUpload:
    """Validate an uploaded file; raise ``AttachmentError`` with a translated message."""
    if upload is None:
        raise AttachmentError("attachment_missing", _("No file was uploaded."), 400)

    name = safe_filename(getattr(upload, "name", ""))
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    allowed = ", ".join(e.upper() for e in allowed_extensions())
    if not name or ext not in _EXT_TO_KIND:
        raise AttachmentError(
            "attachment_type_not_allowed",
            _("This file type is not allowed. Allowed: %(types)s.")
            % {"types": allowed},
            400,
        )

    size = getattr(upload, "size", None) or 0
    if size <= 0:
        raise AttachmentError("attachment_empty", _("The file is empty."), 400)
    limit = max_bytes()
    if size > limit:
        raise AttachmentError(
            "attachment_too_large",
            _("The file is too large. The maximum size is %(mb)s MB.")
            % {"mb": f"{limit / (1024 * 1024):g}"},
            413,
        )

    kind = sniff_kind(upload)
    if kind is None or _EXT_TO_KIND[ext] != kind:
        raise AttachmentError(
            "attachment_content_mismatch",
            _("The file content does not match its type, so it was rejected."),
            400,
        )

    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    return ValidatedUpload(name, kind, KINDS[kind][0], size, digest.hexdigest())


# --------------------------------------------------------------------------- #
# Self-healing: detect drift between rows and files
# --------------------------------------------------------------------------- #


def check_integrity() -> list:
    """Errors for ``verify_integrity``: missing/altered files and orphaned rows."""
    from core.models import Attachment

    errors = []
    for att in Attachment.objects.all().iterator():
        ref = f"Attachment #{att.pk} ({att.model_label} {att.object_id})"
        if get_parent(att.model_label, att.object_id) is None:
            errors.append(f"{ref}: parent record no longer exists.")
        try:
            with att.file.open("rb") as fh:
                digest = hashlib.sha256()
                total = 0
                for chunk in iter(lambda: fh.read(io.DEFAULT_BUFFER_SIZE * 64), b""):
                    digest.update(chunk)
                    total += len(chunk)
        except (OSError, ValueError):
            errors.append(f"{ref}: stored file is missing.")
            continue
        if total != att.size or digest.hexdigest() != att.sha256:
            errors.append(
                f"{ref}: stored file does not match its recorded size/SHA-256."
            )
    return errors


def has_proof(doc) -> bool:
    """Check if document has proof (an Attachment or non-empty reference_no / proof)."""
    if doc is None:
        return False
    if getattr(doc, "reference_no", None) and str(doc.reference_no).strip():
        return True
    if getattr(doc, "proof", None) and str(doc.proof).strip():
        return True
    if not getattr(doc, "pk", None):
        return False
    from core.models import Attachment

    return Attachment.objects.filter(
        model_label=doc._meta.label, object_id=str(doc.pk)
    ).exists()
