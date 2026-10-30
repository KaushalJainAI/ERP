"""Registers and party statement: Sales / Purchase Register, Day Book, Cash / Bank Book,
Statement of Account. Figures are hand-computed from the GST fixtures of
``gst.tests`` (18% item: net 100 -> CGST 9 + SGST 9, or IGST 18)."""

import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.reports import registers
from accounts.services import outstanding as outstanding_service
from core.models import Company, NamingSeries, UserCompany
from gst.tests.test_documents import SI_URL
from gst.tests.test_posting import PostingBase
from selling.models import Customer, SalesInvoice
from stock.models import UOM, Item, ItemGroup
from users.models import Role, RolePermission, User, UserRole

D = Decimal
BASE = "/api/accounts/reports/"
SALES = BASE + "sales-register/"
PURCHASE = BASE + "purchase-register/"
DAYBOOK = BASE + "day-book/"
CASHBOOK = BASE + "cash-book/"
STATEMENT = BASE + "party-statement/"

OCT = {"from_date": "2026-10-01", "to_date": "2026-10-31"}


def on(day):
    return {"posting_date": f"2026-{day}"}


class RegisterBase(PostingBase):
    """Intra Cust: S0 590 (Sep) - P0 100 (Sep) + S1 236 - P1 236 + S3 105 (Oct);
    Inter Cust: S2 1180 - P2 500; Intra Sup: PI1 118 - PP 50; S4 is cancelled."""

    def setUp(self):
        super().setUp()
        self.s0 = self.sale(self.cust_intra, (self.item18, 1, 500), **on("09-15"))
        self.p0 = self.pay(self.s0, "2026-09-20", "100.00")
        self.s1 = self.sale(self.cust_intra, (self.item18, 2, 100), **on("10-05"))
        s4 = self.sale(self.cust_intra, (self.item18, 1, 100), **on("10-07"))
        self.cancel_doc(SI_URL, s4)
        self.s4 = s4
        self.pi1 = self.purchase(self.sup_intra, (self.item18, 1, 100), **on("10-08"))
        self.s2 = self.sale(self.cust_inter, (self.item18, 1, 1000), **on("10-10"))
        self.p1 = self.pay(self.s1, "2026-10-12", "236.00")
        self.pp = self.pay(self.pi1, "2026-10-15", "50.00", kind="purchase")
        self.s3 = self.sale(self.cust_intra, (self.item5, 1, 100), **on("10-20"))
        self.p2 = self.pay(self.s2, "2026-10-25", "500.00")

    # ------------------------------------------------------------- helpers

    def cancel_doc(self, url, doc):
        res = self.client.post(f"{url}{doc['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)

    def pay(self, inv, date, amount, kind="sales"):
        """A submitted Payment Entry of ``amount`` against an invoice, on ``date``."""
        sales = kind == "sales"
        party_account = self.debtors if sales else self.creditors
        entry = PaymentEntry.objects.create(
            name=NamingSeries.next("PE-2026-"),
            payment_type=PaymentType.RECEIVE if sales else PaymentType.PAY,
            posting_date=datetime.date.fromisoformat(date),
            company=self.company,
            party_type="Customer" if sales else "Supplier",
            party_name=self._party_name(inv, sales),
            paid_from=party_account if sales else self.bank,
            paid_to=self.bank if sales else party_account,
            paid_amount=D(amount),
            received_amount=D(amount),
        )
        PaymentEntryReference.objects.create(
            payment_entry=entry,
            reference_doctype=(
                "selling.SalesInvoice" if sales else "buying.PurchaseInvoice"
            ),
            reference_name=inv["name"],
            allocated_amount=D(amount),
        )
        entry.submit()
        return entry

    def _party_name(self, inv, sales):
        from buying.models import PurchaseInvoice

        if sales:
            return SalesInvoice.objects.get(pk=inv["id"]).customer.name
        return PurchaseInvoice.objects.get(pk=inv["id"]).supplier.name

    def get(self, url, expect=200, client=None, **params):
        params.setdefault("company", self.company.id)
        res = (client or self.client).get(url, params)
        self.assertEqual(res.status_code, expect, res.content)
        return res.data

    def by_name(self, report):
        return {r["name"]: r for r in report["rows"]}


class SalesRegisterTests(RegisterBase):
    def test_rows_tax_heads_and_totals(self):
        report = self.get(SALES, **OCT)
        # Draft / cancelled / out-of-period invoices are not listed.
        self.assertEqual(
            [r["name"] for r in report["rows"]],
            [self.s1["name"], self.s2["name"], self.s3["name"]],
        )
        rows = self.by_name(report)
        s1 = rows[self.s1["name"]]
        self.assertEqual(s1["posting_date"], "2026-10-05")
        self.assertEqual(s1["party"], "Intra Cust")
        self.assertEqual(s1["party_gstin"], self.cust_intra.gstin)
        self.assertEqual(s1["net_total"], "200.00")
        self.assertEqual(
            s1["taxes"],
            {
                "CGST": "18.00",
                "SGST": "18.00",
                "IGST": "0.00",
                "CESS": "0.00",
                "OTHER": "0.00",
            },
        )
        self.assertEqual(s1["grand_total"], "236.00")
        self.assertEqual(s1["rounded_total"], "236.00")
        self.assertEqual(s1["rounding_adjustment"], "0.00")
        self.assertEqual(s1["outstanding"], "0.00")  # settled by P1
        s2 = rows[self.s2["name"]]
        self.assertEqual(s2["taxes"]["IGST"], "180.00")
        self.assertEqual(s2["taxes"]["CGST"], "0.00")
        self.assertEqual(s2["outstanding"], "680.00")  # 1180 - 500
        s3 = rows[self.s3["name"]]
        self.assertEqual((s3["taxes"]["CGST"], s3["taxes"]["SGST"]), ("2.50", "2.50"))
        self.assertEqual(s3["outstanding"], "105.00")

        t = report["totals"]
        self.assertEqual(t["net_total"], "1300.00")
        self.assertEqual(
            t["taxes"],
            {
                "CGST": "20.50",
                "SGST": "20.50",
                "IGST": "180.00",
                "CESS": "0.00",
                "OTHER": "0.00",
            },
        )
        self.assertEqual(t["grand_total"], "1521.00")
        self.assertEqual(t["outstanding"], "785.00")
        self.assertEqual(report["pagination"]["count"], 3)
        self.integrity_ok()

    def test_row_identity_net_plus_heads_is_grand_total(self):
        for r in self.get(SALES, **OCT)["rows"]:
            heads = sum((D(v) for v in r["taxes"].values()), D("0"))
            self.assertEqual(D(r["net_total"]) + heads, D(r["grand_total"]))
            self.assertEqual(
                D(r["grand_total"]) + D(r["rounding_adjustment"]), D(r["rounded_total"])
            )

    def test_party_filter(self):
        report = self.get(SALES, party=self.cust_inter.id, **OCT)
        self.assertEqual([r["name"] for r in report["rows"]], [self.s2["name"]])
        self.assertEqual(report["totals"]["grand_total"], "1180.00")

    def test_party_of_another_company_is_rejected(self):
        other = Company.objects.create(name="Other Co", abbr="OTH")
        stranger = Customer.objects.create(
            name="Stranger", company=other, customer_group=self.cgroup
        )
        self.get(SALES, 400, party=stranger.id, **OCT)

    def test_item_group_filter_includes_child_groups(self):
        uom = UOM.objects.get(name="Nos")
        snacks = ItemGroup.objects.create(name="Snacks")
        chips = ItemGroup.objects.create(name="Chips", parent=snacks)
        chip = Item.objects.create(
            code="CHIP",
            name="Chip",
            item_group=chips,
            stock_uom=uom,
            is_stock_item=False,
            gst_hsn_code="100610",
            item_tax_template=self.item18.item_tax_template,
        )
        d = self.sale(self.cust_intra, (chip, 1, 100), **on("10-28"))
        report = self.get(SALES, item_group=chips.id, **OCT)
        self.assertEqual([r["name"] for r in report["rows"]], [d["name"]])
        report = self.get(SALES, item_group=snacks.id, **OCT)  # parent group -> child
        self.assertEqual([r["name"] for r in report["rows"]], [d["name"]])
        goods = self.item18.item_group_id
        report = self.get(SALES, item_group=goods, **OCT)
        self.assertEqual(len(report["rows"]), 3)  # the 3 original Oct invoices
        self.get(SALES, 400, item_group=999999, **OCT)

    def test_rounding_column(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"), **on("10-29"))
        row = self.by_name(self.get(SALES, **OCT))[d["name"]]
        self.assertEqual(row["grand_total"], "118.60")
        self.assertEqual(row["rounding_adjustment"], "0.40")
        self.assertEqual(row["rounded_total"], "119.00")

    def test_return_notes_are_negative_whatever_the_stored_sign(self):
        inv = SalesInvoice.objects.get(pk=self.s1["id"])
        inv.is_return = True  # the field is added by the returns feature
        (positive,) = registers._rows(SalesInvoice, [inv])
        inv.net_total, inv.grand_total, inv.rounded_total = -200, -236, -236
        (signed,) = registers._rows(SalesInvoice, [inv])
        for row in (positive, signed):
            self.assertTrue(row["is_return"])
            self.assertEqual(row["net_total"], D("-200.00"))
            self.assertEqual(row["grand_total"], D("-236.00"))
            self.assertEqual(row["heads"]["CGST"], D("-18.00"))
            self.assertEqual(row["rounding_adjustment"], D("0"))

    def make_return(self, original, date):
        """A submitted credit note against ``original`` (a real Sales Return)."""
        res = self.client.post(f"{SI_URL}{original['id']}/make-sales-return/")
        self.assertEqual(res.status_code, 201, res.content)
        # NB: the mapping's default "SINV-RET-2026-0001" is 18 chars and fails the GST
        # 16-character invoice-number rule at submit, so the test names the note itself.
        SalesInvoice.objects.filter(pk=res.data["id"]).update(
            posting_date=date, name=f"SRET-{res.data['id']:04d}"
        )
        res = self.client.post(f"{SI_URL}{res.data['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def test_real_credit_note_is_listed_with_negative_figures(self):
        ret = self.make_return(self.s3, "2026-10-22")
        report = self.get(SALES, **OCT)
        row = self.by_name(report)[ret["name"]]
        self.assertTrue(row["is_return"])
        self.assertEqual(row["return_against"], self.s3["name"])
        self.assertEqual(row["net_total"], "-100.00")
        self.assertEqual(
            (row["taxes"]["CGST"], row["taxes"]["SGST"]), ("-2.50", "-2.50")
        )
        self.assertEqual(row["grand_total"], "-105.00")
        heads = sum((D(v) for v in row["taxes"].values()), D("0"))
        self.assertEqual(D(row["net_total"]) + heads, D(row["grand_total"]))
        # The register nets the note: 1300 - 100 net, 1521 - 105 gross.
        self.assertEqual(report["totals"]["net_total"], "1200.00")
        self.assertEqual(report["totals"]["grand_total"], "1416.00")
        self.assertEqual(report["totals"]["taxes"]["CGST"], "18.00")
        self.integrity_ok()

    def test_credit_note_flows_through_the_statement(self):
        ret = self.make_return(self.s3, "2026-10-22")
        rep = self.get(
            STATEMENT, party_type="Customer", party=self.cust_intra.id, **OCT
        )
        self.assertEqual(
            [(r["voucher_no"], r["balance"]) for r in rep["rows"]],
            [
                (self.s1["name"], "726.00"),
                (self.p1.name, "490.00"),
                (self.s3["name"], "595.00"),
                (ret["name"], "490.00"),
            ],
        )
        credit = rep["rows"][-1]
        self.assertEqual((credit["debit"], credit["credit"]), ("0.00", "105.00"))
        gl = outstanding_service.party_balance(
            "Customer", self.cust_intra.name, self.company.pk
        )
        self.assertEqual(D(rep["closing_balance"]), gl)
        self.assertEqual(rep["closing_balance"], "490.00")  # 595 - 105

    def test_pagination_keeps_whole_range_totals(self):
        page1 = self.get(SALES, page=1, page_size=2, **OCT)
        page2 = self.get(SALES, page=2, page_size=2, **OCT)
        self.assertEqual(len(page1["rows"]), 2)
        self.assertEqual(len(page2["rows"]), 1)
        self.assertEqual(page1["totals"], page2["totals"])
        self.assertEqual(page1["pagination"]["num_pages"], 2)

    def test_validation(self):
        self.get(SALES, 400)  # no period
        self.get(SALES, 400, from_date="2026-10-31", to_date="2026-10-01")
        self.get(SALES, 400, from_date="x", to_date="2026-10-01")
        # The company now comes from the active-company header/default, not the
        # query string; omitting `company` is valid.
        res = self.client.get(SALES, OCT)
        self.assertEqual(res.status_code, 200)
        self.get(SALES, 400, page_size=99999, **OCT)


class PurchaseRegisterTests(RegisterBase):
    def test_purchase_rows(self):
        report = self.get(PURCHASE, **OCT)
        self.assertEqual([r["name"] for r in report["rows"]], [self.pi1["name"]])
        row = report["rows"][0]
        self.assertEqual(row["party"], "Intra Sup")
        self.assertEqual(row["net_total"], "100.00")
        self.assertEqual((row["taxes"]["CGST"], row["taxes"]["SGST"]), ("9.00", "9.00"))
        self.assertEqual(row["grand_total"], "118.00")
        self.assertEqual(row["outstanding"], "68.00")  # 118 - 50
        self.assertEqual(report["totals"]["grand_total"], "118.00")
        self.assertEqual(report["kind"], "purchase")

    def test_reverse_charge_tax_goes_to_its_own_column(self):
        d = self.purchase(
            self.sup_inter, (self.item18, 1, 100), is_reverse_charge=True, **on("10-30")
        )
        row = self.by_name(self.get(PURCHASE, **OCT))[d["name"]]
        self.assertTrue(row["is_reverse_charge"])
        self.assertEqual(row["taxes"]["IGST"], "0.00")
        self.assertEqual(row["reverse_charge_tax"], "18.00")
        self.assertEqual(row["grand_total"], row["net_total"])


class DayBookTests(RegisterBase):
    def test_vouchers_in_order_with_daily_totals(self):
        report = self.get(DAYBOOK, **OCT)
        vouchers = report["vouchers"]
        names = [v["voucher_no"] for v in vouchers]
        self.assertEqual(
            names,
            [
                self.s1["name"],
                self.pi1["name"],
                self.s2["name"],
                self.p1.name,
                self.pp.name,
                self.s3["name"],
                self.p2.name,
            ],
        )
        self.assertNotIn(self.s4["name"], names)  # cancelled pair hidden
        for v in vouchers:  # every voucher balances
            self.assertEqual(v["total_debit"], v["total_credit"])
            self.assertEqual(
                sum(D(line["debit"]) for line in v["lines"]), D(v["total_debit"])
            )
        s1 = vouchers[0]
        self.assertEqual(
            sorted(
                (x["account"], x["debit"], x["credit"], x["dr_cr"]) for x in s1["lines"]
            ),
            sorted(
                [
                    ("Debtors", "236.00", "0.00", "Dr"),
                    ("Sales", "0.00", "200.00", "Cr"),
                    ("Output CGST", "0.00", "18.00", "Cr"),
                    ("Output SGST", "0.00", "18.00", "Cr"),
                ]
            ),
        )
        self.assertEqual(s1["party"], "Intra Cust")
        self.assertEqual(report["totals"]["vouchers"], 7)
        self.assertEqual(report["totals"]["debit"], "2425.00")
        self.assertEqual(report["totals"]["credit"], "2425.00")
        daily = {d["date"]: d for d in report["daily_totals"]}
        self.assertEqual(
            list(daily),
            [
                "2026-10-05",
                "2026-10-08",
                "2026-10-10",
                "2026-10-12",
                "2026-10-15",
                "2026-10-20",
                "2026-10-25",
            ],
        )
        self.assertEqual(daily["2026-10-10"]["debit"], "1180.00")
        self.assertEqual(daily["2026-10-25"]["vouchers"], 1)
        self.assertEqual(
            sum(D(d["debit"]) for d in daily.values()), D(report["totals"]["debit"])
        )

    def test_matches_gl_sum_for_the_period(self):
        from django.db.models import Sum

        live = GLEntry.objects.filter(
            company=self.company,
            is_cancelled=False,
            posting_date__gte="2026-10-01",
            posting_date__lte="2026-10-31",
        ).aggregate(dr=Sum("debit"))
        self.assertEqual(D(self.get(DAYBOOK, **OCT)["totals"]["debit"]), live["dr"])

    def test_cancelled_voucher_is_hidden_with_its_reversal(self):
        # S4's original and reversal rows are all is_cancelled=True.
        self.assertTrue(GLEntry.objects.filter(voucher_no=self.s4["name"]).exists())
        self.assertFalse(
            GLEntry.objects.filter(
                voucher_no=self.s4["name"], is_cancelled=False
            ).exists()
        )
        report = self.get(DAYBOOK, from_date="2026-10-07", to_date="2026-10-07")
        self.assertEqual(report["vouchers"], [])
        self.assertEqual(report["totals"]["debit"], "0.00")

    def test_voucher_type_filter_and_pagination(self):
        pe_type = GLEntry.objects.filter(voucher_no=self.p1.name).first().voucher_type
        report = self.get(DAYBOOK, voucher_type=pe_type, **OCT)
        self.assertEqual(
            [v["voucher_no"] for v in report["vouchers"]],
            [self.p1.name, self.pp.name, self.p2.name],
        )
        page2 = self.get(DAYBOOK, page=2, page_size=3, **OCT)
        self.assertEqual(
            [v["voucher_no"] for v in page2["vouchers"]],
            [self.p1.name, self.pp.name, self.s3["name"]],
        )
        self.assertEqual(page2["totals"]["debit"], "2425.00")


class CashBookTests(RegisterBase):
    def test_running_balance_and_against(self):
        report = self.get(CASHBOOK, account=self.bank.id, **OCT)
        self.assertEqual(report["account"]["name"], "Bank")
        self.assertEqual(report["opening_balance"], "100.00")  # P0 in September
        rows = report["rows"]
        self.assertEqual(
            [(r["voucher_no"], r["debit"], r["credit"], r["balance"]) for r in rows],
            [
                (self.p1.name, "236.00", "0.00", "336.00"),
                (self.pp.name, "0.00", "50.00", "286.00"),
                (self.p2.name, "500.00", "0.00", "786.00"),
            ],
        )
        self.assertEqual(rows[0]["against"], "Debtors")
        self.assertEqual(rows[0]["party"], "Intra Cust")
        self.assertEqual(rows[1]["against"], "Creditors")
        self.assertEqual(rows[1]["party"], "Intra Sup")
        self.assertEqual(rows[2]["party"], "Inter Cust")
        self.assertEqual(report["total_debit"], "736.00")
        self.assertEqual(report["total_credit"], "50.00")
        self.assertEqual(report["closing_balance"], "786.00")

    def test_closing_equals_gl_balance(self):
        from django.db.models import Sum

        agg = GLEntry.objects.filter(
            account=self.bank, is_cancelled=False, posting_date__lte="2026-10-31"
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        report = self.get(CASHBOOK, account=self.bank.id, **OCT)
        self.assertEqual(D(report["closing_balance"]), agg["dr"] - agg["cr"])

    def test_later_page_continues_the_running_balance(self):
        page = self.get(CASHBOOK, account=self.bank.id, page=2, page_size=2, **OCT)
        self.assertEqual([r["balance"] for r in page["rows"]], ["786.00"])
        self.assertEqual(page["closing_balance"], "786.00")

    def test_only_cash_or_bank_accounts_of_the_company(self):
        self.get(CASHBOOK, 400, account=self.debtors.id, **OCT)
        self.get(CASHBOOK, 400, account=999999, **OCT)
        self.get(CASHBOOK, 400, **OCT)  # account missing
        other = Company.objects.create(name="Other Co", abbr="OTH")
        root = Account.objects.create(
            name="Assets", company=other, root_type=AccountRootType.ASSET
        )
        foreign = Account.objects.create(
            name="Cash",
            company=other,
            parent=root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        self.get(CASHBOOK, 400, account=foreign.id, **OCT)
        # ... but valid for its own company (empty book).
        report = self.get(CASHBOOK, company=other.id, account=foreign.id, **OCT)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["closing_balance"], "0.00")


class PartyStatementTests(RegisterBase):
    def statement(self, party, party_type="Customer", **extra):
        return self.get(
            STATEMENT, party_type=party_type, party=party.id, **{**OCT, **extra}
        )

    def test_customer_statement(self):
        rep = self.statement(self.cust_intra)
        self.assertEqual(rep["party"]["name"], "Intra Cust")
        self.assertEqual(rep["opening_balance"], "490.00")  # 590 - 100
        self.assertEqual(rep["opening_dr_cr"], "Dr")
        self.assertEqual(
            [
                (r["voucher_no"], r["debit"], r["credit"], r["balance"], r["dr_cr"])
                for r in rep["rows"]
            ],
            [
                (self.s1["name"], "236.00", "0.00", "726.00", "Dr"),
                (self.p1.name, "0.00", "236.00", "490.00", "Dr"),
                (self.s3["name"], "105.00", "0.00", "595.00", "Dr"),
            ],
        )
        self.assertEqual(rep["total_debit"], "341.00")
        self.assertEqual(rep["total_credit"], "236.00")
        self.assertEqual(rep["closing_balance"], "595.00")
        self.assertEqual(rep["closing_dr_cr"], "Dr")

    def test_closing_equals_the_gl_party_balance(self):
        for party, kind in (
            (self.cust_intra, "Customer"),
            (self.cust_inter, "Customer"),
            (self.sup_intra, "Supplier"),
        ):
            rep = self.statement(party, kind)
            gl = outstanding_service.party_balance(kind, party.name, self.company.pk)
            self.assertEqual(D(rep["closing_balance"]), gl, party.name)
            self.assertEqual(D(rep["ledger_balance"]), gl)
            # opening + movement = closing, from the rows themselves
            sign = 1 if kind == "Customer" else -1
            moved = sum((D(r["debit"]) - D(r["credit"]) for r in rep["rows"]), D("0"))
            self.assertEqual(
                D(rep["opening_balance"]) + moved * sign, D(rep["closing_balance"])
            )
            if rep["rows"]:
                self.assertEqual(rep["rows"][-1]["balance"], rep["closing_balance"])

    def test_supplier_statement_is_in_the_suppliers_sense(self):
        rep = self.statement(self.sup_intra, "Supplier")
        self.assertEqual(rep["opening_balance"], "0.00")
        self.assertEqual(
            [(r["voucher_no"], r["balance"], r["dr_cr"]) for r in rep["rows"]],
            [(self.pi1["name"], "118.00", "Cr"), (self.pp.name, "68.00", "Cr")],
        )
        self.assertEqual(rep["closing_balance"], "68.00")

    def test_ageing_summary_is_as_on_the_end_date(self):
        rep = self.statement(self.cust_intra)
        ageing = rep["ageing"]
        self.assertEqual(ageing["as_on"], "2026-10-31")
        self.assertEqual(
            sorted((r["invoice"], r["outstanding"]) for r in ageing["rows"]),
            sorted([(self.s0["name"], "490.00"), (self.s3["name"], "105.00")]),
        )
        self.assertEqual(ageing["total"]["outstanding"], "595.00")
        # An earlier end date leaves out later invoices.
        early = self.statement(self.cust_intra, to_date="2026-10-06")
        self.assertEqual(early["closing_balance"], "726.00")
        # Ageing uses the invoices' *current* outstanding: S1 is settled, so only S0.
        self.assertEqual(
            [r["invoice"] for r in early["ageing"]["rows"]], [self.s0["name"]]
        )
        self.assertEqual(early["ledger_balance"], "595.00")  # all dates

    def test_balance_reconciles_with_pages(self):
        full = self.statement(self.cust_intra)
        one = self.statement(self.cust_intra, page=2, page_size=1)
        self.assertEqual(one["rows"][0]["voucher_no"], self.p1.name)
        self.assertEqual(one["rows"][0]["balance"], full["rows"][1]["balance"])
        self.assertEqual(one["closing_balance"], full["closing_balance"])

    def test_cancelled_invoice_is_excluded(self):
        names = [r["voucher_no"] for r in self.statement(self.cust_intra)["rows"]]
        self.assertNotIn(self.s4["name"], names)

    def test_validation_and_company_isolation(self):
        self.get(STATEMENT, 400, party=self.cust_intra.id, **OCT)  # no party_type
        self.get(STATEMENT, 400, party_type="Employee", party=1, **OCT)
        self.get(STATEMENT, 400, party_type="Customer", **OCT)  # no party
        # A supplier id is not a customer of this company.
        other = Company.objects.create(name="Other Co", abbr="OTH")
        stranger = Customer.objects.create(
            name="Stranger", company=other, customer_group=self.cgroup
        )
        self.get(STATEMENT, 400, party_type="Customer", party=stranger.id, **OCT)
        ok = self.get(
            STATEMENT, company=other.id, party_type="Customer", party=stranger.id, **OCT
        )
        self.assertEqual(ok["rows"], [])
        self.assertEqual(ok["closing_balance"], "0.00")


class IsolationAndRBACTests(RegisterBase):
    def setUp(self):
        super().setUp()
        self.other = Company.objects.create(name="Other Co", abbr="OTH")

    def test_other_company_sees_none_of_these_books(self):
        for url in (SALES, PURCHASE):
            report = self.get(url, company=self.other.id, **OCT)
            self.assertEqual(report["rows"], [])
            self.assertEqual(report["totals"]["grand_total"], "0.00")
        book = self.get(DAYBOOK, company=self.other.id, **OCT)
        self.assertEqual(book["vouchers"], [])
        self.assertEqual(book["totals"]["debit"], "0.00")

    def user_client(self, *labels):
        tag = f"u{len(labels)}{'_'.join(lb[-4:] for lb in labels)}"
        user = User.objects.create_user(
            username=tag, email=f"{tag}@test.com", password="x"
        )
        role = Role.objects.create(name=f"R{user.pk}")
        for label in labels:
            RolePermission.objects.create(role=role, model_label=label, can_read=True)
        UserRole.objects.create(user=user, role=role)
        UserCompany.objects.create(user=user, company=self.company, is_default=True)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_anonymous_is_denied(self):
        res = APIClient().get(SALES, {"company": self.company.id, **OCT})
        self.assertIn(res.status_code, (401, 403))

    def test_user_without_roles_is_denied_everywhere(self):
        client = self.user_client()
        args = {
            SALES: {},
            PURCHASE: {},
            DAYBOOK: {},
            CASHBOOK: {"account": self.bank.id},
            STATEMENT: {"party_type": "Customer", "party": self.cust_intra.id},
        }
        for url, extra in args.items():
            self.get(url, 403, client=client, **extra, **OCT)

    def test_each_report_needs_its_own_read_right(self):
        gl = self.user_client("accounts.GLEntry")
        self.get(DAYBOOK, client=gl, **OCT)
        self.get(CASHBOOK, client=gl, account=self.bank.id, **OCT)
        self.get(SALES, 403, client=gl, **OCT)
        self.get(PURCHASE, 403, client=gl, **OCT)
        # The statement also needs the party master.
        self.get(
            STATEMENT,
            403,
            client=gl,
            party_type="Customer",
            party=self.cust_intra.id,
            **OCT,
        )

        sales = self.user_client("selling.SalesInvoice")
        self.get(SALES, client=sales, **OCT)
        self.get(PURCHASE, 403, client=sales, **OCT)
        self.get(DAYBOOK, 403, client=sales, **OCT)

        accountant = self.user_client(
            "accounts.GLEntry", "selling.Customer", "buying.Supplier"
        )
        self.get(
            STATEMENT,
            client=accountant,
            party_type="Customer",
            party=self.cust_intra.id,
            **OCT,
        )
        self.get(
            STATEMENT,
            client=accountant,
            party_type="Supplier",
            party=self.sup_intra.id,
            **OCT,
        )
        only_customers = self.user_client("accounts.GLEntry", "selling.Customer")
        self.get(
            STATEMENT,
            403,
            client=only_customers,
            party_type="Supplier",
            party=self.sup_intra.id,
            **OCT,
        )

    def test_reports_never_write(self):
        before = (GLEntry.objects.count(), SalesInvoice.objects.count())
        for url, extra in (
            (SALES, {}),
            (DAYBOOK, {}),
            (CASHBOOK, {"account": self.bank.id}),
            (STATEMENT, {"party_type": "Customer", "party": self.cust_intra.id}),
        ):
            self.get(url, **extra, **OCT)
            res = self.client.post(url, {})
            self.assertEqual(res.status_code, 405)
        self.assertEqual(
            before, (GLEntry.objects.count(), SalesInvoice.objects.count())
        )
        self.integrity_ok()


class TallyIdentityTests(RegisterBase):
    """Rows of an imported ``TLY-*`` Journal Entry carry Tally's own type and number
    (``tally``); every other row has ``tally: null``."""

    NAME = "TLY-2627-000001"

    def setUp(self):
        super().setUp()
        from accounts.models import JournalEntry, JournalEntryLine

        je = JournalEntry.objects.create(
            company=self.company,
            name=self.NAME,
            posting_date=datetime.date(2026, 10, 21),
            voucher_type="Journal Entry",
            user_remark="Tally Receipt No. 2627/1 | being cash received",
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.bank, debit=D("77.00")
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self.debtors,
            credit=D("77.00"),
            party_type="Customer",
            party="Intra Cust",
        )
        je.recompute_totals()
        je.save()
        je.submit()
        self.je = je

    @property
    def expected(self):
        return {
            "id": self.je.pk,
            "voucher_type": "Receipt",
            "voucher_no": "2627/1",
        }

    def test_cash_book(self):
        rows = {
            r["voucher_no"]: r
            for r in self.get(CASHBOOK, account=self.bank.id, **OCT)["rows"]
        }
        self.assertEqual(rows[self.NAME]["tally"], self.expected)
        self.assertEqual(rows[self.NAME]["voucher_type"], "accounts.JournalEntry")
        self.assertIsNone(rows[self.p1.name]["tally"])

    def test_day_book(self):
        vouchers = {v["voucher_no"]: v for v in self.get(DAYBOOK, **OCT)["vouchers"]}
        self.assertEqual(vouchers[self.NAME]["tally"], self.expected)
        self.assertIsNone(vouchers[self.s1["name"]]["tally"])

    def test_party_statement(self):
        rep = self.get(
            STATEMENT, party_type="Customer", party=self.cust_intra.id, **OCT
        )
        rows = {r["voucher_no"]: r for r in rep["rows"]}
        self.assertEqual(rows[self.NAME]["tally"], self.expected)
        self.assertIsNone(rows[self.s1["name"]]["tally"])

    def test_general_ledger(self):
        res = self.client.get(
            "/api/accounts/reports/general-ledger/",
            {"company": self.company.id, "account": self.bank.id, **OCT},
        )
        self.assertEqual(res.status_code, 200, res.content)
        rows = {r["voucher_no"]: r for r in res.data["entries"]}
        self.assertEqual(rows[self.NAME]["tally"], self.expected)
        self.assertIsNone(rows[self.p1.name]["tally"])

    def test_one_bulk_query_per_page_not_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.get(CASHBOOK, account=self.bank.id, **OCT)
        lookups = [
            q
            for q in ctx.captured_queries
            if "user_remark" in q["sql"] and "TLY-" in q["sql"]
        ]
        self.assertEqual(len(lookups), 1)
