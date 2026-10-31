"""Validation tests for the 11 draft PO catalogs (Card L-6).

Target languages:
as bn gu kn ml mr or pa ta te ur
(Assamese, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, Urdu)

Verifies:
1. Catalog existence: backend/locale/<lang>/LC_MESSAGES/django.po exists for all 11 languages.
2. UTF-8 encoding: all files are decodable as valid UTF-8.
3. Fuzzy flag: every non-header translation entry is marked with `#, fuzzy`
   (ensuring Django safely falls back to English at runtime until human review).
4. Placeholders preservation: named placeholders (e.g. %(name)s) match between msgid and msgstr.
5. Non-empty drafts: every msgid has a corresponding draft msgstr.
6. Syntax verification: files pass msgfmt format and syntax validation if available.
7. Runtime fallback: with translation.override(lang), fuzzy strings fall back to English.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple
from unittest import TestCase

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

from core.test_i18n import _PLACEHOLDER_RE, parse_po_entries

TARGET_LANGUAGES: Tuple[str, ...] = (
    "as",
    "bn",
    "gu",
    "kn",
    "ml",
    "mr",
    "or",
    "pa",
    "ta",
    "te",
    "ur",
)


def get_locale_po_path(lang: str) -> Path:
    """Return path to backend/locale/<lang>/LC_MESSAGES/django.po."""
    return Path(settings.BASE_DIR) / "locale" / lang / "LC_MESSAGES" / "django.po"


def parse_entries_with_flags(po_path: Path | str) -> List[Dict[str, any]]:
    """Parse a .po file and return entry dicts with msgid, msgstr, flags, and line number."""
    with open(po_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = content.split("\n\n")
    entries = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()

        flags: Set[str] = set()
        comments: List[str] = []
        msgid_parts: List[str] = []
        msgstr_parts: List[str] = []
        mode = "start"

        for line in lines:
            line_str = line.strip()
            if line_str.startswith("#,"):
                for flag in line_str[2:].split(","):
                    flags.add(flag.strip())
            elif line_str.startswith("#"):
                comments.append(line_str)
            elif line_str.startswith("msgid "):
                mode = "msgid"
                content = line_str[6:].strip()
                if content.startswith('"') and content.endswith('"'):
                    msgid_parts.append(
                        content[1:-1].encode("utf-8").decode("unicode_escape")
                    )
            elif line_str.startswith("msgstr "):
                mode = "msgstr"
                content = line_str[7:].strip()
                if content.startswith('"') and content.endswith('"'):
                    msgstr_parts.append(
                        content[1:-1].encode("utf-8").decode("unicode_escape")
                    )
            elif line_str.startswith('"') and line_str.endswith('"'):
                unescaped = line_str[1:-1].encode("utf-8").decode("unicode_escape")
                if mode == "msgid":
                    msgid_parts.append(unescaped)
                elif mode == "msgstr":
                    msgstr_parts.append(unescaped)

        msgid = "".join(msgid_parts)
        msgstr = "".join(msgstr_parts)
        entries.append(
            {
                "msgid": msgid,
                "msgstr": msgstr,
                "flags": flags,
                "comments": comments,
            }
        )

    return entries


class OtherLanguagesLocaleTests(TestCase):
    """Test suite ensuring all 11 non-Hindi Indian language catalogs meet Card L-6 criteria."""

    def test_all_11_locale_files_exist(self):
        """Verify that backend/locale/<lang>/LC_MESSAGES/django.po exists for all 11 target languages."""
        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            self.assertTrue(
                po_path.exists(),
                f"Missing django.po catalog for language '{lang}' at {po_path}",
            )

    def test_all_locale_files_are_valid_utf8(self):
        """Verify that each catalog file decodes as valid UTF-8."""
        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            try:
                content = po_path.read_text(encoding="utf-8")
                self.assertGreater(
                    len(content),
                    100,
                    f"Catalog for '{lang}' is unexpectedly short.",
                )
            except UnicodeDecodeError as exc:
                self.fail(f"Catalog for '{lang}' has invalid UTF-8 encoding: {exc}")

    def test_all_entries_marked_fuzzy(self):
        """Verify that every translation entry in all 11 catalogs has the fuzzy flag.

        Django ignores fuzzy entries at runtime, ensuring safe fallback to English
        until reviewed by native speakers.
        """
        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            entries = parse_entries_with_flags(po_path)

            # Skip the header (where msgid == "")
            non_header_entries = [e for e in entries if e["msgid"] != ""]
            self.assertGreater(
                len(non_header_entries),
                500,
                f"Language '{lang}' has fewer entries than expected ({len(non_header_entries)}).",
            )

            non_fuzzy = [
                e["msgid"] for e in non_header_entries if "fuzzy" not in e["flags"]
            ]
            self.assertEqual(
                non_fuzzy,
                [],
                f"Found {len(non_fuzzy)} entries in '{lang}' missing the 'fuzzy' flag:\n"
                f"{non_fuzzy[:5]}",
            )

    def test_named_placeholders_preserved_in_all_11_languages(self):
        """Verify that all named placeholders (e.g. %(name)s) match between msgid and msgstr."""
        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            entries = parse_po_entries(po_path)

            mismatches = []
            for msgid, msgstr, line in entries:
                if not msgid or not msgstr:
                    continue
                id_ph = set(_PLACEHOLDER_RE.findall(msgid))
                str_ph = set(_PLACEHOLDER_RE.findall(msgstr))
                if id_ph != str_ph:
                    mismatches.append((line, msgid, msgstr, id_ph, str_ph))

            if mismatches:
                formatted = "\n".join(
                    f"  line {line}: msgid has {id_ph}, but msgstr has {str_ph}\n    msgid: {msgid!r}\n    msgstr: {msgstr!r}"
                    for line, msgid, msgstr, id_ph, str_ph in mismatches[:10]
                )
                self.fail(
                    f"Found {len(mismatches)} placeholder mismatches in '{lang}' catalog {po_path}:\n{formatted}"
                )

    def test_all_msgids_have_non_empty_draft_msgstr(self):
        """Verify that every msgid entry has a non-empty draft msgstr."""
        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            entries = parse_po_entries(po_path)
            non_header_entries = [
                (msgid, msgstr, line) for msgid, msgstr, line in entries if msgid != ""
            ]
            empty_msgstrs = [
                (line, msgid)
                for msgid, msgstr, line in non_header_entries
                if not msgstr.strip()
            ]
            self.assertEqual(
                empty_msgstrs,
                [],
                f"Found {len(empty_msgstrs)} empty msgstr in '{lang}': {empty_msgstrs[:5]}",
            )

    def test_runtime_fuzzy_entries_fall_back_to_english(self):
        """Verify that at runtime, Django ignores fuzzy entries and returns English text."""
        for lang in TARGET_LANGUAGES:
            with translation.override(lang):
                result = _("Only draft documents can be submitted.")
                self.assertEqual(
                    result,
                    "Only draft documents can be submitted.",
                    f"Expected English fallback for '{lang}', but got '{result}'",
                )

    def test_msgfmt_validation_passes_for_all_catalogs(self):
        """Verify that GNU msgfmt -c validates syntax, headers, and format strings for all 11 catalogs."""
        msgfmt_cmd = shutil.which("msgfmt")
        if not msgfmt_cmd:
            self.skipTest("msgfmt not installed in system PATH")

        for lang in TARGET_LANGUAGES:
            po_path = get_locale_po_path(lang)
            res = subprocess.run(
                [msgfmt_cmd, "-c", "--statistics", "-o", os.devnull, str(po_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"msgfmt -c failed for {lang}:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}",
            )
            # Stderr reports '0 translated messages, N fuzzy translations'
            self.assertIn(
                "fuzzy translations",
                res.stderr,
                f"Expected fuzzy translations reported by msgfmt for {lang}, got: {res.stderr}",
            )
