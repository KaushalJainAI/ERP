"""Quick notes: text, a voice clip or a photo saved to be entered later.

A note is a job, not a document. Saving one writes no ledger row. Its file is checked
here by extension and magic bytes, like core.attachments does for proof, but voice
clips are added, because proof documents never include audio.
"""

import hashlib

from django.utils.translation import gettext as _

from core import attachments as att_rules
from core.attachments import AttachmentError, ValidatedUpload

from .bill_photo import BILL_MAX_BYTES

#: Photos and PDFs go through the proof rules; these are the extra voice formats.
AUDIO_TYPES = {
    "webm": "audio/webm",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
}
NOTE_IMAGE_KINDS = frozenset({"jpeg", "png", "webp", "pdf"})
_SNIFF_BYTES = 8192


def _sniff_audio(head: bytes, ext: str) -> bool:
    """True when the bytes are the container the extension names."""
    if ext == "webm":
        return head.startswith(b"\x1aE\xdf\xa3")
    if ext == "ogg":
        return head.startswith(b"OggS")
    if ext == "wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    return head[4:8] == b"ftyp"  # mp4


def _validate_audio(upload, name: str, ext: str) -> ValidatedUpload:
    size = getattr(upload, "size", None) or 0
    if size <= 0:
        raise AttachmentError("attachment_empty", _("The file is empty."), 400)
    upload.seek(0)
    head = upload.read(_SNIFF_BYTES)
    if not _sniff_audio(head, ext):
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
    return ValidatedUpload(name, "audio", AUDIO_TYPES[ext], size, digest.hexdigest())


def validate_note_upload(upload) -> ValidatedUpload:
    """Validate a note's file: a JPG, PNG, WebP or PDF, or a webm, wav, ogg or mp4 clip.

    Raises AttachmentError with a translated message. The size limit is BILL_MAX_BYTES.
    """
    name = att_rules.safe_filename(getattr(upload, "name", "") or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in AUDIO_TYPES:
        checked = _validate_audio(upload, name, ext)
    else:
        checked = att_rules.validate_upload(upload)
        if checked.kind not in NOTE_IMAGE_KINDS:
            raise AttachmentError(
                "note_type_not_allowed",
                _("Send a photo, a PDF or a voice clip (webm, wav, ogg or mp4)."),
                400,
            )
    if checked.size > BILL_MAX_BYTES:
        raise AttachmentError(
            "note_too_large",
            _("The file is too large. The maximum size is %(mb)s MB.")
            % {"mb": f"{BILL_MAX_BYTES / (1024 * 1024):g}"},
            413,
        )
    return checked
