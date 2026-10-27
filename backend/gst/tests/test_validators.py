"""GSTIN / HSN validators and the party cross-field rules (no HTTP)."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from core.models import State
from gst.constants import b2cl_threshold
from gst.validators import (
    check_gstin,
    gstin_checksum_char,
    normalise_gstin,
    validate_gstin_format,
    validate_hsn_code,
    validate_party_gst,
)


def make_gstin(state="27", pan="AAACR5055K", entity="1"):
    """Build a GSTIN with a correct checksum (test helper)."""
    body = f"{state}{pan}{entity}Z"
    return body + gstin_checksum_char(body)


class GstinValidationTests(SimpleTestCase):
    def test_known_valid_gstins_pass(self):
        for gstin in ("27AAACR5055K1Z7", "29AAACH7409R1ZX"):
            result = check_gstin(gstin)
            self.assertTrue(result.valid, gstin)
            validate_gstin_format(gstin)  # does not raise

    def test_valid_result_carries_state_and_pan(self):
        result = check_gstin("27AAACR5055K1Z7")
        self.assertEqual(result.state_code, "27")
        self.assertEqual(result.state_name, "Maharashtra")
        self.assertEqual(result.pan, "AAACR5055K")

    def test_bad_checksum_fails(self):
        result = check_gstin("27AAACR5055K1Z8")
        self.assertFalse(result.valid)
        self.assertIn("checksum", result.reason.lower())
        # state is still derivable for inline feedback
        self.assertEqual(result.state_code, "27")
        self.assertIsNone(result.pan)
        with self.assertRaises(ValidationError):
            validate_gstin_format("27AAACR5055K1Z8")

    def test_bad_length_fails(self):
        for bad in ("27AAACR5055K1Z", "27AAACR5055K1Z77", "1"):
            self.assertFalse(check_gstin(bad).valid, bad)
            with self.assertRaises(ValidationError):
                validate_gstin_format(bad)

    def test_bad_format_fails(self):
        # 15 chars but the PAN part contains digits where letters are required
        self.assertFalse(check_gstin("27A1ACR5055K1Z7").valid)
        # 14th char must be Z
        self.assertFalse(check_gstin("27AAACR5055K1X7").valid)

    def test_lowercase_and_whitespace_are_normalised(self):
        self.assertEqual(normalise_gstin("  27aaacr5055k1z7 "), "27AAACR5055K1Z7")
        self.assertTrue(check_gstin("27aaacr5055k1z7").valid)
        validate_gstin_format(" 29aaach7409r1zx")

    def test_unknown_state_code_fails(self):
        gstin = make_gstin(state="99")
        result = check_gstin(gstin)
        self.assertFalse(result.valid)
        self.assertIn("state", result.reason.lower())

    def test_retired_state_codes_25_and_28_are_accepted(self):
        for code in ("25", "28"):
            self.assertTrue(check_gstin(make_gstin(state=code)).valid, code)

    def test_blank_is_skipped_by_field_validator(self):
        validate_gstin_format("")
        validate_gstin_format(None)
        self.assertFalse(check_gstin("").valid)


class HsnValidationTests(SimpleTestCase):
    def test_valid_lengths(self):
        for code in ("1006", "100610", "10061000", ""):
            validate_hsn_code(code)

    def test_invalid(self):
        for bad in ("100", "10061", "1006100", "100610000", "10a6", "१००६"):
            with self.assertRaises(ValidationError, msg=bad):
                validate_hsn_code(bad)


class ConstantsTests(SimpleTestCase):
    def test_b2cl_threshold_changes_on_2024_08_01(self):
        import datetime
        from decimal import Decimal

        self.assertEqual(b2cl_threshold(datetime.date(2024, 7, 31)), Decimal("250000"))
        self.assertEqual(b2cl_threshold(datetime.date(2024, 8, 1)), Decimal("100000"))
        self.assertEqual(b2cl_threshold(datetime.date(2026, 10, 27)), Decimal("100000"))


class PartyRulesTests(TestCase):
    def setUp(self):
        self.mh = State.objects.get(pk="27")
        self.gstin = make_gstin()

    def test_registered_requires_gstin(self):
        for category in ("Registered Regular", "Registered Composition", "SEZ"):
            with self.assertRaises(ValidationError) as cm:
                validate_party_gst(None, category, None)
            self.assertIn("gstin", cm.exception.message_dict)

    def test_unregistered_consumer_overseas_must_not_have_gstin(self):
        for category in ("Unregistered", "Consumer", "Overseas"):
            with self.assertRaises(ValidationError) as cm:
                validate_party_gst(self.gstin, category, None)
            self.assertIn("gstin", cm.exception.message_dict)

    def test_unregistered_without_gstin_is_fine(self):
        self.assertEqual(
            validate_party_gst("", "Unregistered", self.mh), (None, self.mh)
        )

    def test_state_is_autofilled_from_gstin(self):
        gstin, state = validate_party_gst(
            self.gstin.lower(), "Registered Regular", None
        )
        self.assertEqual(gstin, self.gstin)
        self.assertEqual(state, self.mh)

    def test_prefix_state_mismatch_is_reported_on_state(self):
        delhi = State.objects.get(pk="07")
        with self.assertRaises(ValidationError) as cm:
            validate_party_gst(self.gstin, "Registered Regular", delhi)
        self.assertEqual(
            cm.exception.message_dict["state"], ["GSTIN prefix 27 is Maharashtra."]
        )

    def test_overseas_state_is_forced_to_none(self):
        self.assertEqual(validate_party_gst(None, "Overseas", self.mh), (None, None))
