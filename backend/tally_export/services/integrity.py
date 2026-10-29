"""verify_integrity hook: the export log still matches what was handed over."""

import hashlib
from typing import List

from django.db.models import Count, Q

from tally_export.models import TallyExportBatch


def _sha(field) -> str:
    h = hashlib.sha256()
    with field.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check() -> List[str]:
    """Errors: a stored export file missing or altered, or counts that disagree
    with the logged voucher rows."""
    errors: List[str] = []
    batches = TallyExportBatch.objects.annotate(
        n_create=Count("vouchers", filter=Q(vouchers__action="Create")),
        n_cancel=Count("vouchers", filter=Q(vouchers__action="Cancel")),
    )
    for b in batches:
        label = f"Tally export batch #{b.pk}"
        for field, expected, what in (
            (b.file, b.file_sha256, "voucher file"),
            (b.masters_file, b.masters_sha256, "masters file"),
        ):
            if not field:
                continue
            try:
                if _sha(field) != expected:
                    errors.append(f"{label}: {what} no longer matches its SHA-256.")
            except (OSError, ValueError):
                errors.append(f"{label}: {what} is missing from storage.")
        if (b.n_create, b.n_cancel) != (b.voucher_count, b.cancel_count):
            errors.append(
                f"{label}: counts ({b.voucher_count} create / {b.cancel_count} cancel) "
                f"disagree with the logged vouchers ({b.n_create} / {b.n_cancel})."
            )
    return errors
