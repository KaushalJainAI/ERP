"""Amount in words, Indian numbering."""

from decimal import Decimal

from django.test import SimpleTestCase

from printing.words import amount_in_words, integer_in_words

D = Decimal


class AmountInWordsEnglish(SimpleTestCase):
    def test_crore_example_from_the_spec(self):
        self.assertEqual(
            amount_in_words(D("12345678.50")),
            "Rupees One Crore Twenty Three Lakh Forty Five Thousand Six Hundred "
            "Seventy Eight and Fifty Paise Only",
        )

    def test_zero(self):
        self.assertEqual(amount_in_words(0), "Rupees Zero Only")
        self.assertEqual(amount_in_words(D("0.00")), "Rupees Zero Only")
        self.assertEqual(amount_in_words(None), "Rupees Zero Only")

    def test_paise_only(self):
        self.assertEqual(amount_in_words(D("0.50")), "Fifty Paise Only")
        self.assertEqual(amount_in_words(D("0.01")), "One Paise Only")

    def test_whole_rupees_have_no_paise_clause(self):
        self.assertEqual(amount_in_words(1), "Rupees One Only")
        self.assertEqual(
            amount_in_words(D("236.00")), "Rupees Two Hundred Thirty Six Only"
        )

    def test_rupees_and_paise(self):
        self.assertEqual(
            amount_in_words(D("1180.25")),
            "Rupees One Thousand One Hundred Eighty and Twenty Five Paise Only",
        )

    def test_lakh_and_thousand_grouping(self):
        self.assertEqual(amount_in_words(100000), "Rupees One Lakh Only")
        self.assertEqual(
            amount_in_words(1234567),
            "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only",
        )
        self.assertEqual(amount_in_words(1000), "Rupees One Thousand Only")
        self.assertEqual(
            amount_in_words(99999),
            "Rupees Ninety Nine Thousand Nine Hundred Ninety Nine Only",
        )

    def test_crore_groups_recurse(self):
        self.assertEqual(amount_in_words(10_000_000), "Rupees One Crore Only")
        self.assertEqual(integer_in_words(1_050_000_000), "One Hundred Five Crore")

    def test_skipped_groups(self):
        self.assertEqual(amount_in_words(10_000_005), "Rupees One Crore Five Only")
        self.assertEqual(amount_in_words(10_100_000), "Rupees One Crore One Lakh Only")

    def test_rounds_half_up_to_paise(self):
        self.assertEqual(amount_in_words(D("0.005")), "One Paise Only")
        self.assertEqual(amount_in_words(D("1.999")), "Rupees Two Only")

    def test_negative_credit_note(self):
        self.assertEqual(
            amount_in_words(D("-236.00")),
            "Minus Rupees Two Hundred Thirty Six Only",
        )
        self.assertEqual(amount_in_words(D("-0.50")), "Minus Fifty Paise Only")

    def test_unsupported_language_reads_in_english(self):
        self.assertEqual(amount_in_words(5, "ta"), "Rupees Five Only")


class AmountInWordsHindi(SimpleTestCase):
    def test_crore_example(self):
        self.assertEqual(
            amount_in_words(D("12345678.50"), "hi"),
            "एक करोड़ तेईस लाख पैंतालीस हज़ार छह सौ अठहत्तर रुपये और पचास पैसे मात्र",
        )

    def test_zero_paise_only_negative(self):
        self.assertEqual(amount_in_words(0, "hi"), "शून्य रुपये मात्र")
        self.assertEqual(amount_in_words(D("0.50"), "hi"), "पचास पैसे मात्र")
        self.assertEqual(amount_in_words(D("-100"), "hi"), "ऋण एक सौ रुपये मात्र")
