"""Pure GST constants (no model imports, so any app may import this module)."""

import datetime
import re
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

#: Regular-taxpayer GSTIN: 2-digit state, 10-char PAN, entity number, "Z", checksum.
#: (UIN, TDS/TCS and NRI formats are out of scope for v1.)
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_CHECKSUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_LENGTH = 15

#: Allowed digit lengths of an HSN / SAC code on an item.
HSN_LENGTHS = (4, 6, 8)

#: GSTR-1 B2C-large threshold (invoice value, inter-state, unregistered buyer).
#: Task 4 (reports) picks the row whose ``effective_from`` is the latest one on or
#: before the invoice date.
B2CL_THRESHOLDS = (
    (datetime.date(2024, 8, 1), Decimal("100000")),
    (datetime.date.min, Decimal("250000")),
)


def b2cl_threshold(on_date):
    """B2CL invoice-value threshold in force on ``on_date``."""
    for effective_from, amount in B2CL_THRESHOLDS:
        if on_date >= effective_from:
            return amount
    return B2CL_THRESHOLDS[-1][1]


class GSTComponent(models.TextChoices):
    CGST = "CGST", "CGST"
    SGST = "SGST", "SGST"
    IGST = "IGST", "IGST"
    CESS = "CESS", "CESS"


class GSTDirection(models.TextChoices):
    INPUT = "Input", _("Input")
    OUTPUT = "Output", _("Output")
    REVERSE_CHARGE = "Reverse Charge", _("Reverse Charge")


class GSTTreatment(models.TextChoices):
    TAXABLE = "Taxable", _("Taxable")
    NIL_RATED = "Nil-Rated", _("Nil-Rated")
    EXEMPTED = "Exempted", _("Exempted")
    NON_GST = "Non-GST", _("Non-GST")


class TemplateType(models.TextChoices):
    SALES = "Sales", _("Sales")
    PURCHASE = "Purchase", _("Purchase")


class GSTSupplyType(models.TextChoices):
    INTRA_STATE = "Intra-State", _("Intra-State")
    INTER_STATE = "Inter-State", _("Inter-State")


class ChargeType(models.TextChoices):
    # v1: only "On Net Total" is supported (enforced by a CHECK constraint).
    ON_NET_TOTAL = "On Net Total", _("On Net Total")


class AddDeduct(models.TextChoices):
    ADD = "Add", _("Add")
    DEDUCT = "Deduct", _("Deduct")


class TaxCategory(models.TextChoices):
    # v1: only "Total" is supported; "Valuation and Total" is reserved.
    TOTAL = "Total", _("Total")
