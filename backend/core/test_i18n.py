"""Translation guards and internationalization tests.

Verifies:
1. AST guard: raises of ValidationError, DRF ValidationError, or AttachmentError
   across backend code do not pass raw string literals without translation markers.
2. Hindi catalog: backend/locale/hi/LC_MESSAGES/django.po has non-empty msgstr
   for every defined msgid (excluding the po header).
3. Placeholders: named placeholders (e.g. %(name)s) match between msgid and msgstr.
4. Guard validation: positive proof that the AST guard catches bad literals / f-strings.
"""

import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple
from unittest import TestCase

from django.conf import settings

# --------------------------------------------------------------------------- #
# PO Parser Helper
# --------------------------------------------------------------------------- #

_STRING_LINE_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*$')
_PLACEHOLDER_RE = re.compile(r"%\(([a-zA-Z0-9_]+)\)[a-zA-Z]")


def _unescape_po_str(s: str) -> str:
    """Unescape standard PO escaped characters."""
    return s.encode("utf-8").decode("unicode_escape")


def parse_po_entries(po_path: str | Path) -> List[Tuple[str, str, int]]:
    """Parse a gettext .po file.

    Returns a list of (msgid, msgstr, line_number) tuples.
    Multi-line string continuations are concatenated.
    """
    entries: List[Tuple[str, str, int]] = []
    with open(po_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    current_msgid: List[str] | None = None
    current_msgstr: List[str] | None = None
    target_buffer: List[str] | None = None
    entry_line = 0

    def flush_entry():
        nonlocal current_msgid, current_msgstr, target_buffer, entry_line
        if current_msgid is not None and current_msgstr is not None:
            msgid = "".join(current_msgid)
            msgstr = "".join(current_msgstr)
            entries.append((msgid, msgstr, entry_line))
        current_msgid = None
        current_msgstr = None
        target_buffer = None

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("msgid "):
            flush_entry()
            entry_line = idx
            content = line[6:].strip()
            current_msgid = []
            target_buffer = current_msgid
            m = _STRING_LINE_RE.match(content)
            if m:
                target_buffer.append(_unescape_po_str(m.group(1)))
        elif line.startswith("msgstr "):
            content = line[7:].strip()
            current_msgstr = []
            target_buffer = current_msgstr
            m = _STRING_LINE_RE.match(content)
            if m:
                target_buffer.append(_unescape_po_str(m.group(1)))
        elif line.startswith('"') and target_buffer is not None:
            m = _STRING_LINE_RE.match(line)
            if m:
                target_buffer.append(_unescape_po_str(m.group(1)))

    flush_entry()
    return entries


# --------------------------------------------------------------------------- #
# AST Inspection Helpers
# --------------------------------------------------------------------------- #

_TRANSLATION_FUNCS = frozenset(
    {"_", "gettext", "ngettext", "lazy_gettext", "gettext_lazy", "_l"}
)

_VALIDATION_ERROR_NAMES = frozenset(
    {"ValidationError", "DRFValidationError", "DjangoValidationError"}
)


def is_translation_marker(node: ast.AST) -> bool:
    """True if node is a gettext translation marker or a %(...)s formatted gettext call."""
    if isinstance(node, ast.Call):
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in _TRANSLATION_FUNCS:
            return True
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return is_translation_marker(node.left)
    return False


def extract_raw_string_violations(expr: ast.AST) -> List[str]:
    """Return raw string literals or f-strings inside an expression (nested in dicts/lists)."""
    violations: List[str] = []
    if isinstance(expr, ast.JoinedStr):
        violations.append(ast.unparse(expr))
    elif isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        violations.append(expr.value)
    elif isinstance(expr, ast.Dict):
        for val in expr.values:
            if not is_translation_marker(val):
                violations.extend(extract_raw_string_violations(val))
    elif isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        for elt in expr.elts:
            if not is_translation_marker(elt):
                violations.extend(extract_raw_string_violations(elt))
    return violations


def scan_source_for_raw_raises(
    source: str, filename: str = "<string>"
) -> List[Tuple[int, str, List[str]]]:
    """Scan Python source code and return violations as (line_number, exc_name, raw_strings)."""
    try:
        tree = ast.parse(source, filename)
    except SyntaxError:
        return []

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc and isinstance(node.exc, ast.Call):
            exc = node.exc
            func_name = ""
            if isinstance(exc.func, ast.Name):
                func_name = exc.func.id
            elif isinstance(exc.func, ast.Attribute):
                func_name = exc.func.attr

            if func_name in _VALIDATION_ERROR_NAMES or func_name.endswith(
                "ValidationError"
            ):
                for arg in exc.args:
                    if not is_translation_marker(arg):
                        raws = extract_raw_string_violations(arg)
                        if raws:
                            violations.append((node.lineno, func_name, raws))
            elif func_name == "AttachmentError":
                # AttachmentError(code, message, status=400): code is machine code, arg[1] is message
                if len(exc.args) > 1 and not is_translation_marker(exc.args[1]):
                    raws = extract_raw_string_violations(exc.args[1])
                    if raws:
                        violations.append((node.lineno, func_name, raws))
    return violations


def scan_codebase_for_raw_raises(
    root_dir: str | Path | None = None,
) -> Dict[str, List[Tuple[int, str, List[str]]]]:
    """Scan backend python files excluding migrations, venv, tests, scripts."""
    if root_dir is None:
        root_dir = Path(settings.BASE_DIR)
    else:
        root_dir = Path(root_dir)

    excluded_dirs = {
        "migrations",
        "venv",
        "tests",
        "scripts",
        "__pycache__",
        ".git",
        "scratch",
        "test_data",
    }
    all_violations: Dict[str, List[Tuple[int, str, List[str]]]] = {}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in excluded_dirs]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if (
                fname.startswith("test")
                or fname.endswith("_test.py")
                or fname == "tests.py"
            ):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            violations = scan_source_for_raw_raises(content, fpath)
            if violations:
                rel = os.path.relpath(fpath, root_dir)
                all_violations[rel] = violations

    return all_violations


# --------------------------------------------------------------------------- #
# Test Suite
# --------------------------------------------------------------------------- #


class I18nGuardTests(TestCase):
    """Test suite ensuring all user-facing backend errors are translated and Hindi catalog is sound."""

    def test_ast_guard_no_unmarked_validation_errors(self):
        """Scan all backend python files to ensure ValidationError and AttachmentError use _()."""
        violations = scan_codebase_for_raw_raises()
        if violations:
            msg_lines = ["Found untranslated raw string literals in exception raises:"]
            for fpath, items in sorted(violations.items()):
                msg_lines.append(f"\n{fpath}:")
                for lineno, exc_name, raws in items:
                    msg_lines.append(f"  line {lineno} in {exc_name}: {raws}")
            self.fail("\n".join(msg_lines))

    def test_hi_po_msgids_have_non_empty_msgstr(self):
        """Verify that every defined msgid in backend/locale/hi/LC_MESSAGES/django.po has a non-empty msgstr."""
        po_path = (
            Path(settings.BASE_DIR) / "locale" / "hi" / "LC_MESSAGES" / "django.po"
        )
        self.assertTrue(po_path.exists(), f"Hindi PO file does not exist at {po_path}")

        entries = parse_po_entries(po_path)
        # Exclude header (where msgid is empty)
        non_header_entries = [
            (msgid, msgstr, line) for msgid, msgstr, line in entries if msgid != ""
        ]
        self.assertGreater(
            len(non_header_entries), 0, "No entries found in Hindi django.po"
        )

        untranslated = [
            (line, msgid)
            for msgid, msgstr, line in non_header_entries
            if not msgstr.strip()
        ]
        if untranslated:
            formatted = "\n".join(
                f"  line {line}: {msgid!r}" for line, msgid in untranslated
            )
            self.fail(
                f"Found {len(untranslated)} untranslated entries in {po_path}:\n{formatted}"
            )

    def test_hi_po_named_placeholders_match(self):
        """Verify that named placeholders (e.g. %(name)s) match between msgid and msgstr."""
        po_path = (
            Path(settings.BASE_DIR) / "locale" / "hi" / "LC_MESSAGES" / "django.po"
        )
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
                for line, msgid, msgstr, id_ph, str_ph in mismatches
            )
            self.fail(f"Found placeholder mismatches in {po_path}:\n{formatted}")

    def test_guard_catches_bad_file_and_bad_literals(self):
        """Demonstrate that the AST guard accurately flags un-translated literals and passes marked ones."""
        bad_cases = [
            # Raw string in ValidationError
            ('raise ValidationError("Raw string")', "Raw string"),
            # Raw string in DRF ValidationError dict
            (
                'raise DRFValidationError({"qty": "Must be positive."})',
                "Must be positive.",
            ),
            # Raw f-string in ValidationError
            ('raise ValidationError(f"Invalid count {cnt}")', "{cnt}"),
            # Raw string in AttachmentError (arg index 1)
            (
                'raise AttachmentError("code", "Raw attachment error message")',
                "Raw attachment error message",
            ),
            # Raw string nested in list
            ('raise ValidationError(["Item error raw"])', "Item error raw"),
        ]

        for code, expected_snippet in bad_cases:
            violations = scan_source_for_raw_raises(code)
            self.assertTrue(
                len(violations) > 0,
                f"Expected AST guard to catch violation in: {code}",
            )
            found_raws = " ".join(" ".join(v[2]) for v in violations)
            self.assertIn(
                expected_snippet,
                found_raws,
                f"Expected {expected_snippet} in {found_raws}",
            )

        good_cases = [
            # Simple translated string
            'raise ValidationError(_("Valid translated message"))',
            # Translated string with named placeholder
            'raise ValidationError(_("Hello %(name)s") % {"name": user.name})',
            # Translation using gettext_lazy / _l
            'raise DRFValidationError({"field": _("Field error")})',
            # Machine code in AttachmentError with translated message
            'raise AttachmentError("machine_code", _("Valid message"))',
            # Variable / exception re-raise
            "raise ValidationError(existing_errors)",
            # Function helper call
            "raise DRFValidationError(_messages(exc))",
        ]

        for code in good_cases:
            violations = scan_source_for_raw_raises(code)
            self.assertEqual(
                violations,
                [],
                f"AST guard should not flag valid code: {code}, got: {violations}",
            )
