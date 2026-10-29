"""Drive upload validation.

The browser's ``Content-Type`` is never trusted. The extension must agree with
the type detected from the bytes, so an executable renamed to ``.pdf`` is
rejected. OOXML files (DOCX/XLSX/PPTX) are ZIP containers; the internal package
structure identifies which one it is.
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.utils.translation import gettext as _

from core.attachments import safe_filename

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

KINDS = {
    "pdf": ("application/pdf", {"pdf"}),
    "jpeg": ("image/jpeg", {"jpg", "jpeg"}),
    "png": ("image/png", {"png"}),
    "webp": ("image/webp", {"webp"}),
    "heic": ("image/heic", {"heic", "heif"}),
    "docx": (DOCX, {"docx"}),
    "xlsx": (XLSX, {"xlsx"}),
    "pptx": (PPTX, {"pptx"}),
    "csv": ("text/csv", {"csv"}),
    "txt": ("text/plain", {"txt"}),
}
_EXT_TO_KIND = {ext: kind for kind, (_ct, exts) in KINDS.items() for ext in exts}
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


class DriveUploadError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class ValidatedDriveUpload:
    filename: str
    kind: str
    content_type: str
    size: int
    sha256: str


def allowed_extensions() -> list[str]:
    return sorted(_EXT_TO_KIND)


def max_bytes() -> int:
    return int(getattr(settings, "DRIVE_MAX_BYTES", 25 * 1024 * 1024))


def _is_text(head: bytes) -> bool:
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    return not re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", head)


def _ooxml_kind(stream) -> Optional[str]:
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return None


def sniff_kind(stream) -> Optional[str]:
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
        return _ooxml_kind(stream)
    if _is_text(head):
        return "text"
    return None


def validate_upload(upload) -> ValidatedDriveUpload:
    if upload is None:
        raise DriveUploadError("drive_missing_file", _("No file was uploaded."), 400)

    filename = safe_filename(getattr(upload, "name", ""))
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    allowed = ", ".join(e.upper() for e in allowed_extensions())
    if not filename or ext not in _EXT_TO_KIND:
        raise DriveUploadError(
            "drive_type_not_allowed",
            _("This file type is not allowed. Allowed: %(types)s.")
            % {"types": allowed},
            400,
        )

    size = getattr(upload, "size", None) or 0
    if size <= 0:
        raise DriveUploadError("drive_empty_file", _("The file is empty."), 400)
    limit = max_bytes()
    if size > limit:
        raise DriveUploadError(
            "drive_too_large",
            _("The file is too large. The maximum size is %(mb)s MB.")
            % {"mb": f"{limit / (1024 * 1024):g}"},
            413,
        )

    kind = sniff_kind(upload)
    if kind == "text":
        if ext not in ("csv", "txt"):
            raise DriveUploadError(
                "drive_content_mismatch",
                _("The file content does not match its type, so it was rejected."),
                400,
            )
        kind = ext
    elif kind is None or _EXT_TO_KIND[ext] != kind:
        raise DriveUploadError(
            "drive_content_mismatch",
            _("The file content does not match its type, so it was rejected."),
            400,
        )

    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    upload.name = filename
    return ValidatedDriveUpload(
        filename=filename,
        kind=kind,
        content_type=KINDS[kind][0],
        size=size,
        sha256=digest.hexdigest(),
    )
