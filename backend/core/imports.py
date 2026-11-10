"""Bulk import of items, customers and suppliers from a spreadsheet (U-11).

Three steps, modelled on the opening stock template (G-6):

* ``build_template`` gives a header-only .xlsx to fill in.
* ``plan_import`` reads an uploaded .xlsx or .csv and validates every row through the
  real serializers (``is_valid`` only, nothing is written). Each row is ``ready``,
  ``skip`` (its name already exists: reported, never updated) or ``error``.
* ``commit_import`` saves every ready row through the serializers' ``save()`` inside
  one ``transaction.atomic()``. It is only called when the plan has no error rows.
"""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from rest_framework.exceptions import ValidationError as DRFValidationError

from buying.models import Supplier
from buying.serializers import SupplierSerializer
from selling.models import Customer
from selling.serializers import CustomerSerializer
from stock.models import UOM, Item
from stock.serializers import ItemSerializer

MAX_ROWS = 2000
MAX_BYTES = 5 * 1024 * 1024
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

READY = "ready"
SKIP = "skip"
ERROR = "error"


class ImportFileError(DRFValidationError):
    """The upload itself cannot be used (wrong type, unreadable, too big)."""


class ImportAborted(Exception):
    """A save failed after validation passed. Nothing was kept."""

    def __init__(self, plan):
        super().__init__("import aborted")
        self.plan = plan


class _RowError(Exception):
    """A row value the serializer cannot see, such as an unknown unit name."""


class _Rollback(Exception):
    """Internal: leaves the atomic block so every save is undone."""


@dataclass
class _Row:
    row: int
    status: str
    message: str
    data: dict
    serializer: object = None


@dataclass
class ImportPlan:
    kind: str
    rows: list = field(default_factory=list)

    def count(self, status):
        return sum(1 for r in self.rows if r.status == status)


# Column order is the template order. ``permission`` is the model the user needs
# ``can_create`` on. ``labels`` turns serializer field names back into column names
# so an error reads "gst_rate: ...", not "standard_rate: ...".
KINDS = {
    "items": {
        "columns": ("name", "unit", "gst_rate", "selling_price", "hsn"),
        "permission": "stock.Item",
        "serializer": ItemSerializer,
        "labels": {
            "stock_uom": "unit",
            "gst_rate": "gst_rate",
            "standard_rate": "selling_price",
            "gst_hsn_code": "hsn",
        },
    },
    "customers": {
        "columns": ("name", "phone", "gstin", "state_code"),
        "permission": "selling.Customer",
        "serializer": CustomerSerializer,
        "labels": {"state": "state_code"},
    },
    "suppliers": {
        "columns": ("name", "phone", "gstin", "state_code"),
        "permission": "buying.Supplier",
        "serializer": SupplierSerializer,
        "labels": {"state": "state_code"},
    },
}


def build_template(kind):
    """An .xlsx with the header row for ``kind`` (bytes)."""
    wb = Workbook()
    ws = wb.active
    ws.title = kind
    ws.append(list(KINDS[kind]["columns"]))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_csv(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return [list(r) for r in csv.reader(io.StringIO(text))]


def _read_xlsx(data):
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
    except Exception as exc:  # openpyxl raises several types for a damaged file
        raise ImportFileError(
            _("Could not read this file. Save it again as .xlsx or .csv.")
        ) from exc
    return rows


def read_rows(kind, upload):
    """``[(row_number, {column: text})]`` for the non-blank data rows.

    Row numbers are the spreadsheet's own (the header is row 1), so an error names
    the row the person sees.
    """
    if upload.size > MAX_BYTES:
        raise ImportFileError(_("The file is larger than 5 MB."))
    name = (upload.name or "").lower()
    data = upload.read()
    if name.endswith(".xlsx"):
        grid = _read_xlsx(data)
    elif name.endswith(".csv"):
        grid = _read_csv(data)
    else:
        raise ImportFileError(_("Upload an .xlsx or .csv file."))
    if not grid:
        raise ImportFileError(_("The file is empty."))
    headers = [_text(c).lower() for c in grid[0]]
    if "name" not in headers:
        raise ImportFileError(_("The first row must have a 'name' column."))
    index = {h: i for i, h in enumerate(headers)}
    columns = KINDS[kind]["columns"]

    rows = []
    for number, cells in enumerate(grid[1:], start=2):
        values = {
            col: (
                _text(cells[index[col]])
                if col in index and index[col] < len(cells)
                else ""
            )
            for col in columns
        }
        if not any(values.values()):
            continue
        rows.append((number, values))
    if len(rows) > MAX_ROWS:
        raise ImportFileError(
            _("This file has more than %(max)s rows. Split it into smaller files.")
            % {"max": MAX_ROWS}
        )
    return rows


def _gst_rate(value):
    """'18', '18.0' or '18%' become '18'. The serializer's choice list checks the rest."""
    text = value.rstrip("%").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise _RowError(_("GST rate must be a number, such as 18."))
    if number == number.to_integral_value():
        return str(int(number))
    return str(number.normalize())


def _item_payload(values):
    data = {"name": values["name"]}
    if values["unit"]:
        uom = UOM.objects.filter(name__iexact=values["unit"]).first()
        if uom is None:
            raise _RowError(
                _("Unit '%(unit)s' is not in the unit list.") % {"unit": values["unit"]}
            )
        data["stock_uom"] = uom.pk
    rate = _gst_rate(values["gst_rate"])
    if rate is not None:
        data["gst_rate"] = rate
    if values["selling_price"]:
        data["standard_rate"] = values["selling_price"]
    if values["hsn"]:
        data["gst_hsn_code"] = values["hsn"]
    return data


def _party_payload(values, company):
    data = {"name": values["name"], "company": company.pk}
    for column in ("phone", "gstin"):
        if values[column]:
            data[column] = values[column]
    code = values["state_code"]
    if code:
        # State codes are two digits; a spreadsheet may have stored "7" for "07".
        data["state"] = code.zfill(2) if code.isdigit() else code
    return data


def _already_exists(kind, name, company):
    if kind == "items":
        return Item.objects.filter(name__iexact=name).exists()
    model = Customer if kind == "customers" else Supplier
    return model.objects.filter(company=company, name__iexact=name).exists()


def _error_text(errors, labels):
    parts = []
    for name, detail in errors.items():
        items = detail if isinstance(detail, list) else [detail]
        text = " ".join(str(item) for item in items)
        if name == "non_field_errors":
            parts.append(text)
        else:
            parts.append(f"{labels.get(name, name)}: {text}")
    return "; ".join(parts)


def plan_import(kind, upload, company, request):
    """Validate every row without writing anything. Returns an ``ImportPlan``."""
    spec = KINDS[kind]
    plan = ImportPlan(kind=kind)
    seen = {}
    context = {"request": request}
    for number, values in read_rows(kind, upload):
        name = values["name"]
        key = name.lower()
        row = _Row(row=number, status=READY, message=_("Ready to import"), data=values)
        plan.rows.append(row)
        if key in seen:
            row.status = ERROR
            row.message = _("The same name is on row %(row)s of this file.") % {
                "row": seen[key]
            }
            continue
        if key:
            seen[key] = number
        if key and _already_exists(kind, name, company):
            row.status = SKIP
            row.message = _("Already exists")
            continue
        try:
            if kind == "items":
                payload = _item_payload(values)
            else:
                payload = _party_payload(values, company)
        except _RowError as exc:
            row.status = ERROR
            row.message = str(exc)
            continue
        serializer = spec["serializer"](data=payload, context=context)
        if serializer.is_valid():
            row.serializer = serializer
        else:
            row.status = ERROR
            row.message = _error_text(serializer.errors, spec["labels"])
    return plan


def preview_payload(plan):
    rows = [
        {"row": r.row, "status": r.status, "message": r.message, "data": r.data}
        for r in plan.rows
    ]
    return {
        "kind": plan.kind,
        "rows": rows,
        "ready": plan.count(READY),
        "skipped": plan.count(SKIP),
        "errors": plan.count(ERROR),
    }


def commit_import(plan):
    """Save every ready row in one transaction. Returns how many were created.

    Raises ``ImportAborted`` when a save fails after validation; the failing row is
    marked as an error on the plan and no row is kept.
    """
    ready = [r for r in plan.rows if r.status == READY]
    try:
        with transaction.atomic():
            for row in ready:
                try:
                    row.serializer.save()
                except (
                    DjangoValidationError,
                    DRFValidationError,
                    IntegrityError,
                ) as exc:
                    row.status = ERROR
                    row.message = str(exc)
                    raise _Rollback() from exc
    except _Rollback:
        raise ImportAborted(plan)
    return len(ready)
