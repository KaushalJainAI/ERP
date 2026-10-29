"""Bank statement import (CSV).

Indian banks export different layouts (SBI, HDFC, ICICI ...: different column names,
date formats, a metadata preamble above the header, separate or single amount
columns), so import is two steps: ``preview`` reads the file and proposes a column
mapping, the user confirms or corrects it, ``run_import`` stores the lines.

Only CSV is supported: ``openpyxl`` is not in requirements.txt. Export the statement
as CSV (or "Save as CSV" from Excel).

Lines are stored as immutable ``BankTransaction`` rows. Re-importing the same or an
overlapping statement is a no-op: a line is identified by
``sha256(date|withdrawal|deposit|reference|description)`` plus its occurrence number
within the file (two identical lines in one statement are two real transactions).
"""

import csv
import datetime
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from accounts.models import BankStatementImport, BankTransaction

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000
ZERO = Decimal("0.00")

#: Mapping field -> header names (lower case, punctuation stripped) it may carry.
SYNONYMS = {
    "date": ["date", "txn date", "transaction date", "tran date", "value date", "posting date"],
    "description": [
        "description", "narration", "particulars", "transaction remarks",
        "transaction details", "remarks", "details",
    ],
    "reference": [
        "reference", "ref no", "chq ref no", "chq no", "cheque no", "ref no cheque no",
        "chequereference no", "cheque number", "instrument no", "utr no", "ref",
    ],
    "withdrawal": [
        "withdrawal", "withdrawal amt", "withdrawal amount", "withdrawal amount inr",
        "debit", "debit amount", "dr", "paid out",
    ],
    "deposit": [
        "deposit", "deposit amt", "deposit amount", "deposit amount inr",
        "credit", "credit amount", "cr", "paid in",
    ],
    "balance": ["balance", "closing balance", "running balance", "balance inr", "available balance"],
    "amount": ["amount", "transaction amount", "amount inr"],
    "dr_cr": ["dr cr", "cr dr", "type", "transaction type", "drcr"],
}
MAPPING_FIELDS = tuple(SYNONYMS)

DATE_FORMATS = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y",
    "%d-%b-%y", "%d %b %y", "%d %B %Y", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d",
)


def _simplify(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


# ------------------------------------------------------------------ reading


def read_rows(raw):
    """Decode the file and split it into rows of cells (delimiter sniffed)."""
    if len(raw) > MAX_BYTES:
        raise ValidationError(_("The file is too large (limit 5 MB)."))
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise ValidationError(_("Could not read the file as text."))
    if "\x00" in text:
        raise ValidationError(_("This looks like a binary file; upload a CSV file."))
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), dialect)
    ]
    rows = [row for row in rows if any(row)]
    if not rows:
        raise ValidationError(_("The file is empty."))
    if len(rows) > MAX_ROWS:
        raise ValidationError(_("The file has too many rows (limit 20,000)."))
    return rows


def detect_header(rows):
    """Index of the header row: the first of the top 40 rows naming a date column
    and a debit / withdrawal or amount column."""
    for index, row in enumerate(rows[:40]):
        names = [_simplify(c) for c in row]
        has_date = any(n in SYNONYMS["date"] for n in names)
        has_amount = any(
            n in SYNONYMS["withdrawal"] + SYNONYMS["amount"] for n in names
        )
        if has_date and has_amount:
            return index
    return None


def suggest_mapping(headers):
    """``{field: header text}`` guessed from the header names."""
    names = [_simplify(h) for h in headers]
    mapping = {}
    for field, words in SYNONYMS.items():
        for header, name in zip(headers, names):
            if name in words and header not in mapping.values():
                mapping[field] = header
                break
    return mapping


def preview(raw):
    rows = read_rows(raw)
    header_index = detect_header(rows)
    if header_index is None:
        return {
            "header_row": None,
            "columns": rows[0],
            "mapping": {},
            "sample": rows[1:6],
            "row_count": len(rows),
        }
    headers = rows[header_index]
    return {
        "header_row": header_index,
        "columns": headers,
        "mapping": suggest_mapping(headers),
        "sample": rows[header_index + 1 : header_index + 6],
        "row_count": len(rows) - header_index - 1,
    }


# ------------------------------------------------------------------ parsing


def parse_date(text, date_format=None):
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ([date_format] if date_format else DATE_FORMATS):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(text):
    """Decimal from "1,23,456.78", "INR 500 Dr" ... ``None`` for blank. Raises
    ``ValueError`` for text that is not an amount."""
    text = (text or "").strip()
    if not text or text in ("-", "--"):
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    cleaned = re.sub(r"(?i)\b(inr|rs\.?|dr|cr)\b|[^0-9.]", "", text)
    if not cleaned:
        raise ValueError(text)
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(text)
    value = value.quantize(Decimal("0.01"))
    return -value if negative else value


def _norm_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def dedupe_hash(date, withdrawal, deposit, reference, description):
    payload = "|".join(
        [
            date.isoformat(),
            f"{withdrawal:.2f}",
            f"{deposit:.2f}",
            _norm_text(reference),
            _norm_text(description),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_lines(rows, header_index, mapping, date_format=None):
    """``(lines, skipped, errors)``. A line is a dict ready for ``BankTransaction``.
    A row with no parsable date and no amount is a preamble / footer / blank row
    (skipped); one with an amount but a bad date or amount is an error."""
    headers = rows[header_index]
    unknown = [v for v in mapping.values() if v and v not in headers]
    if unknown:
        raise ValidationError(
            _("Mapped column(s) not found in the file: %(cols)s")
            % {"cols": ", ".join(unknown)}
        )
    index = {f: headers.index(h) for f, h in mapping.items() if h}
    if "date" not in index:
        raise ValidationError(_("Map the date column."))
    if not ("withdrawal" in index or "deposit" in index or "amount" in index):
        raise ValidationError(
            _("Map the withdrawal and deposit columns (or a single amount column).")
        )

    def cell(row, field):
        i = index.get(field)
        return row[i] if i is not None and i < len(row) else ""

    lines, skipped, errors = [], 0, []
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        try:
            withdrawal = parse_amount(cell(row, "withdrawal")) or ZERO
            deposit = parse_amount(cell(row, "deposit")) or ZERO
            single = parse_amount(cell(row, "amount"))
            balance = parse_amount(cell(row, "balance"))
        except ValueError as exc:
            if parse_date(cell(row, "date"), date_format) is None:
                skipped += 1  # a footer / summary row
            else:
                errors.append({"row": offset, "error": _("Invalid amount: %(v)s") % {"v": exc}})
            continue
        if single is not None and withdrawal == 0 and deposit == 0:
            marker = _simplify(cell(row, "dr_cr"))
            if marker in ("dr", "debit", "d", "withdrawal") or single < 0:
                withdrawal = abs(single)
            else:
                deposit = abs(single)
        withdrawal, deposit = abs(withdrawal), abs(deposit)
        date = parse_date(cell(row, "date"), date_format)
        if withdrawal == 0 and deposit == 0:
            skipped += 1
            continue
        if date is None:
            errors.append(
                {"row": offset, "error": _("Invalid date: %(v)s") % {"v": cell(row, "date")}}
            )
            continue
        if withdrawal > 0 and deposit > 0:
            errors.append(
                {"row": offset, "error": _("A line cannot have both a withdrawal and a deposit.")}
            )
            continue
        lines.append(
            {
                "row_no": offset,
                "transaction_date": date,
                "description": cell(row, "description"),
                "reference_no": cell(row, "reference")[:140],
                "withdrawal": withdrawal,
                "deposit": deposit,
                "balance": balance,
            }
        )
    return lines, skipped, errors


# ------------------------------------------------------------------ importing


def run_import(user, bank_account, file_name, raw, mapping, date_format=None, header_row=None):
    """Parse and store a statement; all or nothing. Returns the import summary."""
    rows = read_rows(raw)
    if header_row is None:
        header_row = detect_header(rows)
    if header_row is None or header_row >= len(rows):
        raise ValidationError(_("Could not find the header row; choose it."))
    mapping = {k: v for k, v in mapping.items() if k in MAPPING_FIELDS and v}
    lines, skipped, errors = parse_lines(rows, header_row, mapping, date_format)
    if errors:
        raise ValidationError(
            {"rows": [f"{e['row']}: {e['error']}" for e in errors[:50]]}
        )
    if not lines:
        raise ValidationError(_("No transaction lines were found in the file."))

    seen = {}
    for line in lines:
        digest = dedupe_hash(
            line["transaction_date"],
            line["withdrawal"],
            line["deposit"],
            line["reference_no"],
            line["description"],
        )
        n = seen.get(digest, 0)
        seen[digest] = n + 1
        line["dedupe_key"] = f"{digest}:{n}"

    with transaction.atomic():
        existing = set(
            BankTransaction.objects.filter(
                bank_account=bank_account,
                dedupe_key__in=[line["dedupe_key"] for line in lines],
            ).values_list("dedupe_key", flat=True)
        )
        fresh = [line for line in lines if line["dedupe_key"] not in existing]
        duplicates = len(lines) - len(fresh)
        batch = None
        if fresh:
            dates = [line["transaction_date"] for line in fresh]
            batch = BankStatementImport.objects.create(
                company_id=bank_account.company_id,
                bank_account=bank_account,
                file_name=file_name[:255],
                file_sha256=hashlib.sha256(raw).hexdigest(),
                column_mapping=mapping,
                row_count=len(lines),
                imported_count=len(fresh),
                duplicate_count=duplicates,
                skipped_count=skipped,
                from_date=min(dates),
                to_date=max(dates),
                imported_by=user if getattr(user, "is_authenticated", False) else None,
            )
            BankTransaction.objects.bulk_create(
                [
                    BankTransaction(
                        company_id=bank_account.company_id,
                        bank_account=bank_account,
                        statement_import=batch,
                        row_no=line["row_no"],
                        transaction_date=line["transaction_date"],
                        description=line["description"],
                        reference_no=line["reference_no"],
                        withdrawal=line["withdrawal"],
                        deposit=line["deposit"],
                        balance=line["balance"],
                        dedupe_key=line["dedupe_key"],
                    )
                    for line in fresh
                ]
            )
    return {
        "import_id": batch.pk if batch else None,
        "rows": len(lines),
        "imported": len(fresh),
        "duplicates": duplicates,
        "skipped": skipped,
    }
