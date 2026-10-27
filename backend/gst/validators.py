"""GSTIN / HSN validation.

Validation is deliberately **not** done in ``Model.save()`` and there is no DB CHECK
on ``gstin``: ORM-level writers (the Tally importer, test fixtures) legitimately
store values that do not pass the checksum. The checks run in the API layer (model
field validators are picked up by DRF automatically, and ``validate_party_gst`` is
called from the party serializers) and again at document submit (Task 2).

This module must not import ``core.models`` at module level -- ``core.models`` and
``selling.models`` import it.
"""

from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.gst_states import STATE_CODES, STATE_NAMES
from gst.constants import (
    GSTIN_CHECKSUM_CHARS,
    GSTIN_LENGTH,
    GSTIN_REGEX,
    HSN_LENGTHS,
)

# Party GST categories (string values of ``core.models.GSTCategory``).
REGISTERED_REGULAR = "Registered Regular"
REGISTERED_COMPOSITION = "Registered Composition"
UNREGISTERED = "Unregistered"
CONSUMER = "Consumer"
SEZ = "SEZ"
OVERSEAS = "Overseas"

#: Categories that must carry a GSTIN, and those that must not.
CATEGORIES_REQUIRING_GSTIN = (REGISTERED_REGULAR, REGISTERED_COMPOSITION, SEZ)
CATEGORIES_FORBIDDING_GSTIN = (UNREGISTERED, CONSUMER, OVERSEAS)


def normalise_gstin(value) -> str:
    """Strip and upper-case a GSTIN ("" for ``None``)."""
    return (value or "").strip().upper()


def gstin_checksum_char(first14: str) -> str:
    """The 15th GSTIN character for the first 14 (base-36 weighted checksum)."""
    total = 0
    for i, ch in enumerate(first14):
        p = GSTIN_CHECKSUM_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return GSTIN_CHECKSUM_CHARS[(36 - total % 36) % 36]


@dataclass(frozen=True)
class GstinCheck:
    valid: bool
    gstin: str
    reason: str = ""
    state_code: Optional[str] = None
    state_name: Optional[str] = None
    pan: Optional[str] = None


def check_gstin(value) -> GstinCheck:
    """Full GSTIN check; never raises. Derivable facts are returned even if invalid."""
    gstin = normalise_gstin(value)
    state_code = gstin[:2] if gstin[:2] in STATE_CODES else None
    state_name = STATE_NAMES.get(state_code) if state_code else None

    def fail(reason):
        return GstinCheck(False, gstin, reason, state_code, state_name, None)

    if not gstin:
        return fail(_("GSTIN is required."))
    if len(gstin) != GSTIN_LENGTH:
        return fail(_("GSTIN must be exactly 15 characters."))
    if not GSTIN_REGEX.match(gstin):
        return fail(_("GSTIN format is invalid."))
    if state_code is None:
        return fail(
            _("GSTIN has an unknown state code (%(code)s).") % {"code": gstin[:2]}
        )
    if gstin_checksum_char(gstin[:14]) != gstin[14]:
        return fail(_("GSTIN checksum is invalid."))
    return GstinCheck(True, gstin, "", state_code, state_name, gstin[2:12])


def validate_gstin_format(value):
    """Field validator: raises ``ValidationError`` unless ``value`` is a valid GSTIN.

    The value is normalised (strip + upper-case) before checking, so ``27aaacr...``
    passes; serializers store the normalised form (see ``gst.serializers``).
    """
    if value in (None, ""):
        return
    result = check_gstin(value)
    if not result.valid:
        raise ValidationError(result.reason, code="invalid_gstin")


def validate_hsn_code(value):
    """Field validator: digits only, length 4, 6 or 8 (SAC for services is 6 digits)."""
    if value in (None, ""):
        return
    if not value.isascii() or not value.isdigit():
        raise ValidationError(
            _("HSN/SAC code must contain digits only."), code="invalid_hsn"
        )
    if len(value) not in HSN_LENGTHS:
        raise ValidationError(
            _("HSN/SAC code must be 4, 6 or 8 digits long."), code="invalid_hsn"
        )


def validate_party_gst(gstin, gst_category, state):
    """Cross-field party rules shared by Customer and Supplier.

    ``state`` is a ``core.State`` instance or ``None``. Returns
    ``(gstin, state)`` where ``gstin`` is normalised (``None`` when blank) and
    ``state`` is auto-filled from the GSTIN prefix when it was omitted (and forced
    to ``None`` for Overseas parties). Raises a field-keyed ``ValidationError``.

    The GSTIN *checksum* is a field-level check (``validate_gstin_format``); this
    function only checks presence, category pairing and the state prefix.
    """
    from core.models import State

    gstin = normalise_gstin(gstin) or None

    if gst_category in CATEGORIES_REQUIRING_GSTIN and not gstin:
        raise ValidationError(
            {
                "gstin": _("A GSTIN is required for a %(category)s party.")
                % {"category": gst_category}
            }
        )
    if gst_category in CATEGORIES_FORBIDDING_GSTIN and gstin:
        raise ValidationError(
            {
                "gstin": _("A %(category)s party must not have a GSTIN.")
                % {"category": gst_category}
            }
        )

    if gst_category == OVERSEAS:
        return gstin, None

    if gstin:
        prefix = gstin[:2]
        if state is None:
            state = State.objects.filter(pk=prefix).first()
        elif state.pk != prefix:
            name = STATE_NAMES.get(prefix)
            msg = (
                _("GSTIN prefix %(code)s is %(name)s.") % {"code": prefix, "name": name}
                if name
                else _("GSTIN prefix %(code)s is not a valid state code.")
                % {"code": prefix}
            )
            raise ValidationError({"state": msg})
    return gstin, state
