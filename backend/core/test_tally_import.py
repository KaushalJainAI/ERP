"""Unit tests for the pure mapping helpers of the Tally importer.

None of these touch the Tally database or the ERP database.
"""

import datetime
from decimal import Decimal

from core.tally_import import masters as m
from core.tally_import.stock import simulate_negative_items


def grp(name, parent, rev=False, dp=False, reserved=None):
    return {
        "name": name,
        "parent": parent,
        "isrevenue": rev,
        "isdeemedpositive": dp,
        "reservedname": reserved,
    }


GROUPS = {
    g["name"]: g
    for g in [
        grp("Current Assets", "Primary", dp=True),
        grp("Sundry Debtors", "Current Assets", dp=True),
        grp("Bank Accounts", "Current Assets", dp=True),
        grp("Cash-in-hand", "Current Assets", dp=True, reserved="Cash-in-Hand"),
        grp("Stock-in-hand", "Current Assets", dp=True, reserved="Stock-in-Hand"),
        grp("Current Liabilities", "Primary"),
        grp("Sundry Creditors", "Current Liabilities"),
        grp("Duties & Taxes", "Current Liabilities"),
        grp("Capital Account", "Primary"),
        grp("Reserves & Surplus", "Capital Account"),
        grp("Loans (Liability)", "Primary"),
        grp("Bank OD A/c", "Loans (Liability)"),
        grp("Sales Accounts", "Primary", rev=True),
        grp("Sales", "Sales Accounts", rev=True),
        grp("Purchase Accounts", "Primary", rev=True, dp=True),
        grp("Direct Expenses", "Primary", rev=True, dp=True),
        grp("Freight & Cartage", "Direct Expenses", rev=True, dp=True),
        grp("Suspense A/c", "Primary"),
    ]
}


# -- root type derivation --------------------------------------------------


def test_root_type_matrix():
    assert m.root_type_for("Purchase Accounts", True, True) == "Expense"
    assert m.root_type_for("Sales Accounts", True, False) == "Income"
    assert m.root_type_for("Current Assets", False, True) == "Asset"
    assert m.root_type_for("Current Liabilities", False, False) == "Liability"


def test_capital_account_override_is_equity():
    assert m.root_type_for("Capital Account", False, False) == "Equity"
    assert m.root_type_of_group(GROUPS, "Capital Account") == "Equity"
    assert m.root_type_of_group(GROUPS, "Reserves & Surplus") == "Equity"


def test_root_type_of_subgroup_follows_primary():
    assert m.root_type_of_group(GROUPS, "Bank Accounts") == "Asset"
    assert m.root_type_of_group(GROUPS, "Bank OD A/c") == "Liability"
    assert m.root_type_of_group(GROUPS, "Freight & Cartage") == "Expense"
    assert m.root_type_of_group(GROUPS, "Sales") == "Income"
    assert m.root_type_of_group(GROUPS, "Suspense A/c") == "Liability"


def test_account_type_and_party_kind():
    bank = m.classify_ledger(GROUPS, {"name": "HDFC", "parent": "Bank Accounts"})
    assert (bank.kind, bank.root_type, bank.account_type) == (
        "account",
        "Asset",
        "Bank",
    )
    od = m.classify_ledger(GROUPS, {"name": "BOI", "parent": "Bank OD A/c"})
    assert (od.root_type, od.account_type) == ("Liability", "Bank")
    cash = m.classify_ledger(GROUPS, {"name": "Cash", "parent": "Cash-in-hand"})
    assert cash.account_type == "Cash"
    tax = m.classify_ledger(GROUPS, {"name": "C Gst", "parent": "Duties & Taxes"})
    assert (tax.account_type, tax.root_type) == ("Tax", "Liability")
    stock = m.classify_ledger(GROUPS, {"name": "Stock @ 5%", "parent": "Stock-in-hand"})
    assert stock.account_type == "Stock"
    deb = m.classify_ledger(GROUPS, {"name": "A", "parent": "Sundry Debtors"})
    assert deb.kind == "debtor" and deb.root_type == "Asset"
    cred = m.classify_ledger(GROUPS, {"name": "B", "parent": "Sundry Creditors"})
    assert cred.kind == "creditor" and cred.root_type == "Liability"
    drawing = m.classify_ledger(GROUPS, {"name": "Lic", "parent": "Capital Account"})
    assert drawing.root_type == "Equity" and drawing.kind == "account"


def test_profit_and_loss_ledger_under_primary_is_equity():
    c = m.classify_ledger(GROUPS, {"name": "Profit & Loss A/c", "parent": "Primary"})
    assert (c.kind, c.root_type) == ("account", "Equity")


def test_group_chain_cycle_guard():
    cyc = {"A": grp("A", "B"), "B": grp("B", "A")}
    assert len(m.group_chain(cyc, "A")) == 2


# -- sign -> Dr/Cr ---------------------------------------------------------


def test_negative_is_debit_positive_is_credit():
    assert m.drcr(Decimal("-100.50")) == (Decimal("100.50"), Decimal("0.00"))
    assert m.drcr(Decimal("250")) == (Decimal("0.00"), Decimal("250.00"))
    assert m.drcr(Decimal("0")) == (Decimal("0.00"), Decimal("0.00"))


def test_drcr_uses_decimal_rounding_half_up():
    assert m.drcr("-0.005") == (Decimal("0.01"), Decimal("0.00"))
    d, c = m.drcr(Decimal("1234567.891"))
    assert isinstance(d, Decimal) and c == Decimal("1234567.89")


def test_signed_debit():
    assert m.signed_debit(Decimal("-31278626.56")) == Decimal("31278626.56")
    assert m.signed_debit(Decimal("905571.98")) == Decimal("-905571.98")


# -- naming ---------------------------------------------------------------


def test_clean_name_collapses_whitespace_and_truncates():
    assert (
        m.clean_name("  Shri Kasturi  Builders-Barnagar ")
        == "Shri Kasturi Builders-Barnagar"
    )
    assert len(m.clean_name("x" * 300)) == 200


def test_unique_names_suffix_on_clash_and_respect_reserved():
    out = m.assign_unique_names(["A  B", "A B", "Equity"], reserved={"Equity"})
    assert sorted([out["A B"], out["A  B"]]) == ["A B", "A B (2)"]
    assert out["Equity"] == "Equity (2)"
    assert len(set(out.values())) == 3


def test_case_variants_are_distinct_names():
    out = m.assign_unique_names(["TCS", "Tcs"])
    assert out == {"TCS": "TCS", "Tcs": "Tcs"}


def test_group_yields_to_ledger_on_collision():
    out = m.resolve_group_names(
        ["Freight & Cartage", "Sundry Debtors", "Sales"],
        reserved={"Freight & Cartage", m.DEBTORS_LEAF},
    )
    assert out["Freight & Cartage"] == "Freight & Cartage (Group)"
    assert out["Sundry Debtors"] == "Sundry Debtors (Group)"
    assert out["Sales"] == "Sales"


def test_fiscal_year_and_document_names():
    assert m.fiscal_year_name("2024-25") == "2024-2025"
    assert m.fiscal_year_dates("2026-27") == (
        datetime.date(2026, 4, 1),
        datetime.date(2027, 3, 31),
    )
    assert m.year_code("2024-25") == "2425"
    assert m.year_code("2017-18") == "1718"
    assert m.je_name("2024-25", "OPEN") == "TLY-2425-OPEN"
    assert m.je_name("2024-25", 123) == "TLY-2425-000123"
    assert len(m.je_name("2019-20", 999999)) <= 60


# -- UOM ------------------------------------------------------------------


def test_uom_normalisation():
    assert m.normalise_uom("k.g") == "Kg"
    assert m.normalise_uom("ltr.") == "Litre"
    assert m.normalise_uom("litr") == "Litre"
    assert m.normalise_uom("NO.") == "Nos"
    assert m.normalise_uom("Pic") == "Pcs"
    assert m.normalise_uom("pcs") == "Pcs"
    assert m.normalise_uom("Not Applicable") == "Nos"
    assert m.normalise_uom(None) == "Nos"
    assert m.normalise_uom("bundle") == "Bundle"


# -- GST helpers ------------------------------------------------------------


def test_gst_tag_only_for_unambiguous_names():
    assert m.gst_tag_for_ledger("Output CGST") == ("Output", "CGST")
    assert m.gst_tag_for_ledger("Input IGST") == ("Input", "IGST")
    assert m.gst_tag_for_ledger("SGST - Input") == ("Input", "SGST")
    assert m.gst_tag_for_ledger("C Gst") is None
    assert m.gst_tag_for_ledger("Input Vat5%") is None


def test_hsn_normalisation_repairs_lost_leading_zero():
    assert m.normalise_hsn("09109100") == "09109100"
    assert m.normalise_hsn("9109100") == "09109100"
    assert m.normalise_hsn("910") == "0910"
    assert m.normalise_hsn("12") is None
    assert m.normalise_hsn("ABCD") is None
    assert (
        m.hsn_from_xml("<A><HSNCODE>0910</HSNCODE><HSNCODE>09109100</HSNCODE></A>")
        == "09109100"
    )
    assert m.hsn_from_xml("<A/>") is None


def test_item_gst_flags():
    assert m.item_gst_flags("Exempt") == (True, False)
    assert m.item_gst_flags("Nil Rated") == (True, False)
    assert m.item_gst_flags("Taxable") == (False, False)
    assert m.item_gst_flags(None) == (False, False)


def test_state_code_from_name():
    assert m.state_code_from_name("Madhya Pradesh") == "23"
    assert m.state_code_from_name("Andhra Pradesh") == "37"
    assert m.state_code_from_name("") is None
    assert m.state_code_from_name("Nowhere") is None


def test_party_gst_fields():
    # valid GSTIN: category Registered Regular, state from prefix
    g, cat, st, invalid = m.party_gst_fields("27AAACR5055K1Z7", "Regular", "Gujarat")
    assert (cat, invalid) == ("Registered Regular", False) and g
    # invalid GSTIN: kept, state left null
    g, cat, st, invalid = m.party_gst_fields(
        "22AAAAA1234A1ZE", "Regular", "Madhya Pradesh"
    )
    assert (g, cat, st, invalid) == (
        "22AAAAA1234A1ZE",
        "Registered Regular",
        None,
        True,
    )
    # no GSTIN: Tally state name and registration type
    assert m.party_gst_fields(None, "Consumer", "Rajasthan") == (
        None,
        "Consumer",
        "08",
        False,
    )
    assert m.party_gst_fields("", "Unregistered", "") == (
        None,
        "Unregistered",
        None,
        False,
    )


# -- negative-stock simulation ----------------------------------------------


def test_negative_stock_simulation():
    openings = {"A": Decimal("10"), "B": Decimal("-5"), "C": Decimal("0")}
    events = [
        ("A", Decimal("-4")),
        ("A", Decimal("-7")),
        ("C", Decimal("3")),
        ("C", Decimal("-3")),
    ]
    low = simulate_negative_items(openings, events)
    assert low == {"A": Decimal("-1.000"), "B": Decimal("-5.000")}


# -- archive / header helpers ----------------------------------------------

from core.tally_import import archive as a  # noqa: E402
from core.tally_import.vouchers import je_header  # noqa: E402


def test_je_header_types_and_reference():
    assert je_header("Receipt", "CHQ 12", ["Receivable", "Bank"]) == {
        "voucher_type": "Bank Entry",
        "cheque_no": "CHQ 12",
    }
    assert (
        je_header("Payment", None, ["Cash", "Payable"])["voucher_type"] == "Cash Entry"
    )
    assert je_header("Receipt", "", [None])["voucher_type"] == "Journal Entry"
    assert je_header("Contra", None, ["Bank", "Cash"]) == {
        "voucher_type": "Contra Entry"
    }
    assert je_header("Purchase", " INV/9 ", []) == {
        "voucher_type": "Journal Entry",
        "bill_no": "INV/9",
    }
    assert je_header("Debit Note", "R1", [])["bill_no"] == "R1"
    assert je_header("Sales", "x" * 200, [])["cheque_no"] == "x" * 140


def test_item_tax_template_name():
    f = m.item_tax_template_name
    assert f("Taxable", None, Decimal("5")) == "GST 5%"
    assert f("Taxable", None, Decimal("12.00")) == "GST 12%"
    assert f("Taxable", None, Decimal("0.25")) == "GST 0.25%"
    assert f("Exempt", None, Decimal("0")) == "Exempted"
    assert f("Nil Rated", None, None) == "Nil Rated"
    assert f(None, "Yes", Decimal("5")) == "Non-GST"
    assert f("Taxable", None, None) is None


def test_item_line_values_signs_and_units():
    v = a.item_line_values(
        {
            "stockitemname": "Haldi ",
            "isdeemedpositive": True,
            "actualqty_num": Decimal("40"),
            "billedqty_num": Decimal("38.5"),
            "actualqty_unit": "k.g",
            "rate_num": Decimal("254.29"),
            "discount": Decimal("0"),
            "amount": Decimal("-10171.60"),
            "ledgername": "Purchase Gst",
        },
        {"k.g": "Kg"},
    )
    assert v["is_inward"] is True
    assert v["amount"] == Decimal("10171.60")
    assert v["qty"] == Decimal("40.000") and v["billed_qty"] == Decimal("38.500")
    assert v["uom"] == "Kg" and v["item_name"] == "Haldi"
    assert v["discount_percent"] is None
    v = a.item_line_values(
        {"stockitemname": "X", "isdeemedpositive": False, "amount": None}, {}
    )
    assert v["qty"] is None and v["amount"] == Decimal("0.00") and not v["is_inward"]


def test_address_values():
    v = a.address_values(
        "Ram Kirana-Barnagar",
        {
            "lines": ["Main  Road", "", "Near Bus Stand"],
            "pincode": Decimal("456771"),
            "state": "Madhya Pradesh",
            "country": "India",
            "mailingname": "Ram Kirana",
        },
    )
    assert v["address_title"] == "Ram Kirana"
    assert v["address_line1"] == "Main Road"
    assert v["address_line2"] == "Near Bus Stand"
    assert v["pincode"] == "456771" and v["state_id"] == "23"
    bad = a.address_values("L", {"lines": ["x"], "pincode": Decimal("4567")})
    assert bad["pincode"] == "" and bad["pincode_raw"] == "4567"
    assert bad["address_title"] == "L"
    assert a.address_values("L", {"lines": ["  "]}) is None
