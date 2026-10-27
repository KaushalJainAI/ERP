"""Pure tax-engine tests (``accounts.services.taxes``): no database."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from accounts.services import taxes as t

D = Decimal


def line(amount, taxable=True, rates=None):
    return t.LineInput(D(amount), taxable, {k: D(v) for k, v in (rates or {}).items()})


def row(rate, comp=None, add_deduct="Add", memo=False):
    return t.RowInput(D(rate), add_deduct, comp, memo)


class EngineTests(SimpleTestCase):
    def test_exact_half_rounds_up(self):
        # 18% of 0.25 = 0.045: ROUND_HALF_UP gives 0.05 (half-even would give 0.04).
        res = t.calculate([line("0.25")], [row("18", "IGST")])
        self.assertEqual(res.rows[0].tax_amount, D("0.05"))
        self.assertEqual(res.grand_total, D("0.30"))
        self.assertEqual(res.lines[0].igst_amount, D("0.05"))

    def test_document_level_rounding_and_residual_on_last_taxed_line(self):
        res = t.calculate([line("0.25"), line("0.25")], [row("18", "IGST")])
        self.assertEqual(res.rows[0].tax_amount, D("0.09"))  # 0.09 not 0.05 + 0.05
        self.assertEqual([ln.igst_amount for ln in res.lines], [D("0.05"), D("0.04")])
        self.assertEqual(sum(ln.igst_amount for ln in res.lines), D("0.09"))

    def test_cumulative_total_add_and_deduct(self):
        res = t.calculate(
            [line("1000")], [row("10"), row("5", add_deduct="Deduct"), row("1")]
        )
        self.assertEqual(
            [r.total for r in res.rows], [D("1100.00"), D("1050.00"), D("1060.00")]
        )
        self.assertEqual(res.grand_total, D("1060.00"))
        self.assertEqual(res.total_taxes_and_charges, D("60.00"))

    def test_item_template_rate_overrides_row_rate_and_nontaxable_is_zero(self):
        lines = [
            line("100", rates={"CGST": "2.5"}),
            line("100", taxable=False, rates={"CGST": "2.5"}),
            line("100"),  # no template: the row's own rate
        ]
        res = t.calculate(lines, [row("9", "CGST")])
        self.assertEqual(res.rows[0].tax_amount, D("11.50"))  # 2.5 + 0 + 9
        self.assertEqual(res.rows[0].net_amount, D("200.00"))
        self.assertEqual(
            [ln.cgst_amount for ln in res.lines], [D("2.50"), D("0.00"), D("9.00")]
        )
        self.assertEqual([ln.gst_rate for ln in res.lines], [D("2.5"), D("0"), D("9")])

    def test_non_gst_row_ignores_treatment(self):
        res = t.calculate([line("100", taxable=False)], [row("2")])
        self.assertEqual(res.rows[0].tax_amount, D("2.00"))

    def test_gst_rate_is_igst_or_cgst_plus_sgst(self):
        intra = t.calculate([line("100")], [row("9", "CGST"), row("9", "SGST")])
        self.assertEqual(intra.lines[0].gst_rate, D("18.000"))
        self.assertEqual(intra.lines[0].igst_amount, D("0.00"))
        inter = t.calculate([line("100")], [row("18", "IGST")])
        self.assertEqual(inter.lines[0].gst_rate, D("18.000"))
        self.assertEqual(inter.lines[0].cgst_amount, D("0.00"))

    def test_rounded_total_nearest_rupee_half_up(self):
        policy = t.Policy(enable_rounded_total=True)
        res = t.calculate([line("123.45")], [], policy)
        self.assertEqual(
            (res.rounded_total, res.rounding_adjustment), (D("123.00"), D("-0.45"))
        )
        res = t.calculate([line("0.50")], [], policy)
        self.assertEqual(
            (res.rounded_total, res.rounding_adjustment), (D("1.00"), D("0.50"))
        )

    def test_rounding_off_means_rounded_equals_grand(self):
        res = t.calculate([line("123.45")], [])
        self.assertEqual(
            (res.rounded_total, res.rounding_adjustment), (D("123.45"), D("0.00"))
        )
        self.assertEqual(res.net_total, res.grand_total)

    def test_whole_rupee_gst_rounding_only_for_gst_rows(self):
        policy = t.Policy(round_off_gst_values=True)
        res = t.calculate([line("105")], [row("18", "IGST"), row("1.5")], policy)
        self.assertEqual(res.rows[0].tax_amount, D("19.00"))  # 18.90 -> 19
        self.assertEqual(res.rows[1].tax_amount, D("1.58"))  # 1.575 -> 1.58, not whole
        self.assertEqual(
            res.lines[0].igst_amount, D("19.00")
        )  # residual +0.10 on the line

    def test_negative_residual_never_drives_a_line_below_zero(self):
        policy = t.Policy(round_off_gst_values=True)
        # 10% of 1.40 twice = 0.14 + 0.14 = 0.28 -> whole rupee 0.00
        res = t.calculate([line("1.40"), line("1.40")], [row("10", "CGST")], policy)
        self.assertEqual(res.rows[0].tax_amount, D("0.00"))
        self.assertEqual([ln.cgst_amount for ln in res.lines], [D("0.00"), D("0.00")])

    def test_reverse_charge_memo_row_does_not_move_total(self):
        res = t.calculate(
            [line("1000")], [row("9", "CGST", memo=True), row("9", "SGST", memo=True)]
        )
        self.assertEqual(res.grand_total, D("1000.00"))
        self.assertEqual(res.total_taxes_and_charges, D("0.00"))
        self.assertEqual([r.tax_amount for r in res.rows], [D("90.00"), D("90.00")])
        self.assertEqual([r.total for r in res.rows], [D("1000.00"), D("1000.00")])

    def test_combined_rate_over_100_is_rejected(self):
        with self.assertRaises(ValidationError):
            t.calculate([line("100")], [row("60", "CGST"), row("60", "SGST")])

    def test_no_lines_no_rows(self):
        res = t.calculate([], [])
        self.assertEqual((res.net_total, res.grand_total), (D("0.00"), D("0.00")))
