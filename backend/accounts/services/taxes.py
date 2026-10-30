"""Pure tax engine (no I/O, no model imports).

Mirrors ERPNext ``taxes_and_totals.calculate_taxes`` for the ``On Net Total`` charge
type (the only one supported in v1; see docs/gst_design.md section 3.3):

* per-line tax is ``rate / 100 * line_amount`` kept **unrounded**;
* a tax row's amount is the sum over lines, rounded **once** per row (document-level
  rounding, ROUND_HALF_UP), or to whole rupees for GST rows when
  ``Policy.round_off_gst_values`` is on;
* the running ``total`` is cumulative (``set_cumulative_total``); reverse-charge
  memo rows are shown but do not move the total;
* the optional rupee round-off is ``rounded_total = round(grand_total)``;
* per-line GST columns (cgst/sgst/igst) are rounded per line and the row-level
  rounding residual is pushed onto the last taxed line
  (``adjust_rounding_in_item_wise_tax_details``).

The orchestration (supply kind, templates, persistence) lives in
``gst.services.supply`` and ``accounts.models.TaxableDocument``.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
RUPEE = Decimal("1")
HUNDRED = Decimal("100")
GST_HEADS = ("CGST", "SGST", "IGST")
#: GST-rate precision stored on a line (DecimalField(7, 3)).
RATE_QUANT = Decimal("0.001")


def money(value) -> Decimal:
    """Round to 2 dp, half up (the single money rounding rule, G3)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def whole_rupee(value) -> Decimal:
    """Round to whole rupees (still carried as a 2-dp Decimal), half up."""
    return Decimal(value).quantize(RUPEE, rounding=ROUND_HALF_UP).quantize(CENT)


@dataclass
class LineInput:
    amount: Decimal
    taxable: bool = True  # gst_treatment == "Taxable"
    #: component -> percent from the line's item-tax-rate snapshot.
    item_tax_rate: Dict[str, Decimal] = field(default_factory=dict)


@dataclass
class RowInput:
    rate: Decimal
    add_deduct: str = "Add"
    #: GST head of the row's account, ``None`` for a non-GST tax row.
    component: Optional[str] = None
    #: Reverse-charge memo row: computed and shown, but excluded from the total.
    memo: bool = False


@dataclass
class Policy:
    enable_rounded_total: bool = False
    round_off_gst_values: bool = False


@dataclass
class LineResult:
    gst_rate: Decimal = ZERO
    cgst_amount: Decimal = ZERO
    sgst_amount: Decimal = ZERO
    igst_amount: Decimal = ZERO


@dataclass
class RowResult:
    net_amount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    total: Decimal = ZERO


@dataclass
class Result:
    net_total: Decimal
    lines: List[LineResult]
    rows: List[RowResult]
    total_taxes_and_charges: Decimal
    grand_total: Decimal
    rounding_adjustment: Decimal
    rounded_total: Decimal


def rate_for(line: LineInput, row: RowInput) -> Decimal:
    """The percent applied to ``line`` by ``row`` (item template beats the row rate)."""
    if row.component:
        if not line.taxable:
            return Decimal("0")
        if row.component in line.item_tax_rate:
            return Decimal(line.item_tax_rate[row.component])
    return Decimal(row.rate)


def calculate(
    lines: List[LineInput], rows: List[RowInput], policy: Optional[Policy] = None
) -> Result:
    policy = policy or Policy()
    net_total = sum((ln.amount for ln in lines), ZERO)

    # 1. Unrounded per-(row, line) tax and the percent used.
    rates = [[rate_for(ln, r) for ln in lines] for r in rows]
    raw = [
        [rates[ri][li] / HUNDRED * ln.amount for li, ln in enumerate(lines)]
        for ri in range(len(rows))
    ]

    # 2. Row totals (document-level rounding) and the cumulative total.
    row_results: List[RowResult] = []
    prev_total = net_total
    for ri, row in enumerate(rows):
        total_raw = sum(raw[ri], Decimal("0"))
        if row.component and policy.round_off_gst_values:
            tax_amount = whole_rupee(total_raw)
        else:
            tax_amount = money(total_raw)
        base = sum(
            (ln.amount for li, ln in enumerate(lines) if rates[ri][li] != 0), ZERO
        )
        if row.memo:
            total = prev_total
        elif row.add_deduct == "Deduct":
            total = prev_total - tax_amount
        else:
            total = prev_total + tax_amount
        row_results.append(
            RowResult(net_amount=base, tax_amount=tax_amount, total=total)
        )
        prev_total = total

    grand_total = prev_total
    total_taxes = grand_total - net_total

    # 3. Rounding.
    if policy.enable_rounded_total:
        rounded_total = whole_rupee(grand_total)
        rounding_adjustment = rounded_total - grand_total
    else:
        rounded_total = grand_total
        rounding_adjustment = ZERO

    # 4. Per-line GST columns.
    line_results = [LineResult() for _line in lines]
    head_amounts = [{h: ZERO for h in GST_HEADS} for _line in lines]
    head_rates = [{h: Decimal("0") for h in GST_HEADS} for _line in lines]
    for ri, row in enumerate(rows):
        comp = row.component
        if comp not in GST_HEADS:
            continue  # non-GST rows and CESS have no per-line column
        per_line = [money(raw[ri][li]) for li in range(len(lines))]
        taxed = [li for li in range(len(lines)) if raw[ri][li] > 0]
        residual = row_results[ri].tax_amount - sum(per_line, ZERO)
        if abs(residual) > Decimal("0.5") + CENT * len(lines):
            raise ValidationError(
                _(
                    "Tax rounding drift: row total and line amounts disagree "
                    "by %(residual)s."
                )
                % {"residual": residual}
            )
        if residual and taxed:
            # Positive residual goes on the last taxed line; a negative one spills
            # backwards so no line amount is driven below zero.
            for li in reversed(taxed):
                if residual > 0:
                    per_line[li] += residual
                    residual = ZERO
                else:
                    take = min(per_line[li], -residual)
                    per_line[li] -= take
                    residual += take
                if not residual:
                    break
        for li in range(len(lines)):
            head_amounts[li][comp] += per_line[li]
            head_rates[li][comp] += rates[ri][li]
    for li, res in enumerate(line_results):
        res.cgst_amount = head_amounts[li]["CGST"]
        res.sgst_amount = head_amounts[li]["SGST"]
        res.igst_amount = head_amounts[li]["IGST"]
        igst_rate = head_rates[li]["IGST"]
        gst_rate = (
            igst_rate
            if igst_rate > 0
            else (head_rates[li]["CGST"] + head_rates[li]["SGST"])
        )
        if gst_rate > HUNDRED:
            raise ValidationError(
                _("The combined GST rate of a line cannot exceed 100%.")
            )
        res.gst_rate = gst_rate.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)

    return Result(
        net_total=net_total,
        lines=line_results,
        rows=row_results,
        total_taxes_and_charges=total_taxes,
        grand_total=grand_total,
        rounding_adjustment=rounding_adjustment,
        rounded_total=rounded_total,
    )


def negate(result: Result) -> Result:
    """The mirror image of ``result``: every amount negated, rates untouched.

    A return (credit / debit note) is calculated as its positive twin and then
    negated, so its tax is exactly the mirror of what the original charged
    (``ROUND_HALF_UP`` rounds away from zero, so it is symmetric) and the
    per-line residual logic only ever sees positive amounts.
    """
    return Result(
        net_total=-result.net_total,
        lines=[
            LineResult(
                gst_rate=ln.gst_rate,
                cgst_amount=-ln.cgst_amount,
                sgst_amount=-ln.sgst_amount,
                igst_amount=-ln.igst_amount,
            )
            for ln in result.lines
        ],
        rows=[
            RowResult(
                net_amount=-r.net_amount, tax_amount=-r.tax_amount, total=-r.total
            )
            for r in result.rows
        ],
        total_taxes_and_charges=-result.total_taxes_and_charges,
        grand_total=-result.grand_total,
        rounding_adjustment=-result.rounding_adjustment,
        rounded_total=-result.rounded_total,
    )
