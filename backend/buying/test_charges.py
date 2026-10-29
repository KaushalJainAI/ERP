"""Purchase bill: supplier bill no / date and extra charges (freight, hamali...)."""

import datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from buying.models import (
    PurchaseChargeTemplate,
    PurchaseInvoice,
    PurchaseInvoiceCharge,
    PurchaseInvoiceItem,
    Supplier,
    SupplierGroup,
)
from buying.services import charges as svc
from core.models import Company, DocStatus
from stock.models import UOM, Bin, Item, ItemGroup, StockLedgerEntry, Warehouse
from stock.services import ledger as stock_ledger
from users.models import User

D = Decimal
DATE = datetime.date(2026, 4, 1)


def _acc(company, name, root, parent=None, account_type=None, is_group=False):
    return Account.objects.create(
        name=name,
        company=company,
        parent=parent,
        root_type=root,
        account_type=account_type,
        is_group=is_group,
    )


class SplitMathTests(TestCase):
    def test_split_by_amount_remainder_on_last_line(self):
        shares = svc.split(D("100"), [D("400"), D("1000")])
        self.assertEqual(shares, [D("28.57"), D("71.43")])
        self.assertEqual(sum(shares), D("100.00"))

    def test_split_by_qty_remainder_on_last_line(self):
        shares = svc.split(D("100"), [D("10"), D("20")])
        self.assertEqual(shares, [D("33.33"), D("66.67")])

    def test_shares_always_sum_exactly_and_never_negative(self):
        # 10 equal lines and 5 paise: half-up rounding would overshoot.
        shares = svc.split(D("0.05"), [D("1")] * 10)
        self.assertEqual(sum(shares), D("0.05"))
        self.assertTrue(all(s >= 0 for s in shares))
        shares = svc.split(D("1000.01"), [D("3"), D("3"), D("3")])
        self.assertEqual(sum(shares), D("1000.01"))

    def test_zero_weight_lines_get_nothing(self):
        shares = svc.split(D("10"), [D("0"), D("5"), D("5"), D("0")])
        self.assertEqual(shares, [D("0.00"), D("5.00"), D("5.00"), D("0.00")])

    def test_allocate_sums_several_charges_per_line(self):
        out = svc.allocate(
            [(D("100"), "Amount"), (D("30"), "Qty")],
            [("a", D("400"), D("10")), ("b", D("1000"), D("20"))],
        )
        self.assertEqual(out["a"], D("28.57") + D("10.00"))
        self.assertEqual(out["b"], D("71.43") + D("20.00"))
        self.assertEqual(sum(out.values()), D("130.00"))


class ChargesBase(TestCase):
    perpetual = True

    def setUp(self):
        self.company = c = Company.objects.create(name="Spice Co", abbr="SC")
        assets = _acc(c, "Assets", AccountRootType.ASSET, is_group=True)
        self.expenses = expenses = _acc(
            c, "Expenses", AccountRootType.EXPENSE, is_group=True
        )
        liab = _acc(c, "Liabilities", AccountRootType.LIABILITY, is_group=True)
        self.inventory = _acc(
            c, "Stock In Hand", AccountRootType.ASSET, assets, AccountType.STOCK
        )
        self.cogs = _acc(
            c,
            "COGS",
            AccountRootType.EXPENSE,
            expenses,
            AccountType.COST_OF_GOODS_SOLD,
        )
        self.adjustment = _acc(c, "Stock Adjustment", AccountRootType.EXPENSE, expenses)
        self.purchases = _acc(c, "Purchases", AccountRootType.EXPENSE, expenses)
        self.creditors = _acc(
            c, "Creditors", AccountRootType.LIABILITY, liab, AccountType.PAYABLE
        )
        svc.ensure_charge_setup(c)
        self.freight = Account.objects.get(company=c, name="Freight Inward")
        self.eiiv = Account.objects.get(company=c, name=svc.EIIV_NAME)
        if self.perpetual:
            c.enable_perpetual_inventory = True
            c.default_inventory_account = self.inventory
            c.default_expense_account = self.cogs
            c.stock_adjustment_account = self.adjustment
            c.save()
        uom = UOM.objects.create(name="Kg")
        grp = ItemGroup.objects.create(name="Spices", is_group=False)
        self.chilli = Item.objects.create(
            code="CHILLI", name="Chilli", item_group=grp, stock_uom=uom
        )
        self.turmeric = Item.objects.create(
            code="TURM", name="Turmeric", item_group=grp, stock_uom=uom
        )
        self.service = Item.objects.create(
            code="SERV",
            name="Service",
            item_group=grp,
            stock_uom=uom,
            is_stock_item=False,
        )
        self.wh = Warehouse.objects.create(name="Store", company=c)
        self.supplier = Supplier.objects.create(
            name="Supp",
            company=c,
            supplier_group=SupplierGroup.objects.create(name="SG"),
        )
        self._n = 0

    def bill(self, lines, charges=(), submit=True, bill_no=None, **extra):
        """lines: (item, qty, rate); charges: dicts for PurchaseInvoiceCharge."""
        self._n += 1
        pi = PurchaseInvoice.objects.create(
            name=f"PINV-{self._n:04d}",
            supplier=self.supplier,
            company=self.company,
            posting_date=DATE,
            update_stock=True,
            credit_to=self.creditors,
            expense_account=self.purchases,
            bill_no=bill_no if bill_no is not None else f"B/{self._n}",
            **extra,
        )
        for item, qty, rate in lines:
            PurchaseInvoiceItem.objects.create(
                purchase_invoice=pi,
                item=item,
                warehouse=None if not item.is_stock_item else self.wh,
                qty=D(qty),
                rate=D(rate),
            )
        for i, ch in enumerate(charges, start=1):
            PurchaseInvoiceCharge.objects.create(
                purchase_invoice=pi, idx=i, **{"distribute_by": "Amount", **ch}
            )
        pi.calculate_taxes_and_totals(resolve_lines=True)
        if submit:
            pi.submit()
        return pi

    def gl(self, name):
        out = {}
        for e in GLEntry.objects.filter(voucher_no=name, is_cancelled=False):
            out[e.account.name] = out.get(e.account.name, D(0)) + e.debit - e.credit
        return out

    def assert_balanced(self, name):
        agg = GLEntry.objects.filter(voucher_no=name).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        self.assertEqual(agg["dr"], agg["cr"])

    def sles(self, name):
        return {
            s.item.code: s
            for s in StockLedgerEntry.objects.filter(
                voucher_no=name, is_cancelled=False
            )
        }


class PerpetualChargeTests(ChargesBase):
    def test_on_bill_freight_by_amount(self):
        pi = self.bill(
            [(self.chilli, 10, 40), (self.turmeric, 20, 50)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                }
            ],
        )
        pi.refresh_from_db()
        # 400 + 1000 + 100 on the bill, no tax.
        self.assertEqual(pi.net_total, D("1400.00"))
        self.assertEqual(pi.total_taxes_and_charges, D("100.00"))
        self.assertEqual(pi.grand_total, D("1500.00"))
        self.assertEqual(pi.rounded_total, D("1500.00"))
        sles = self.sles(pi.name)
        self.assertEqual(sles["CHILLI"].stock_value_difference, D("428.57"))
        self.assertEqual(sles["TURM"].stock_value_difference, D("1071.43"))
        self.assertEqual(sles["CHILLI"].valuation_rate, D("42.857000"))
        self.assertEqual(sles["TURM"].valuation_rate, D("53.571500"))
        gl = self.gl(pi.name)
        self.assertEqual(gl["Stock In Hand"], D("1500.00"))
        self.assertEqual(gl["Creditors"], D("-1500.00"))
        self.assertEqual(gl["Freight Inward"], D("100.00"))
        self.assertEqual(gl[svc.EIIV_NAME], D("-100.00"))
        self.assertNotIn("Purchases", gl)  # all stock: no expense residue
        self.assert_balanced(pi.name)
        # GL inventory == Bin stock value.
        self.assertEqual(Bin.objects.aggregate(t=Sum("stock_value"))["t"], D("1500.00"))
        party = GLEntry.objects.get(voucher_no=pi.name, account=self.creditors)
        self.assertEqual(party.against_voucher_no, pi.name)
        self.assertEqual(pi.outstanding_amount, D("1500.00"))

    def test_off_bill_hamali_by_qty_leaves_payable_alone(self):
        pi = self.bill(
            [(self.chilli, 10, 40), (self.turmeric, 20, 50)],
            [
                {
                    "description": "Hamali",
                    "amount": D("100"),
                    "distribute_by": "Qty",
                    "included_in_bill": False,
                }
            ],
        )
        pi.refresh_from_db()
        self.assertEqual(pi.grand_total, D("1400.00"))
        sles = self.sles(pi.name)
        self.assertEqual(sles["CHILLI"].stock_value_difference, D("433.33"))
        self.assertEqual(sles["TURM"].stock_value_difference, D("1066.67"))
        gl = self.gl(pi.name)
        self.assertEqual(gl["Creditors"], D("-1400.00"))
        self.assertEqual(gl["Stock In Hand"], D("1500.00"))
        self.assertEqual(gl[svc.EIIV_NAME], D("-100.00"))
        self.assertNotIn("Freight Inward", gl)
        self.assert_balanced(pi.name)
        self.assertEqual(pi.charges_off_bill, D("100.00"))
        self.assertEqual(pi.charges_on_bill, D("0.00"))

    def test_non_stock_line_gets_no_share(self):
        pi = self.bill(
            [(self.chilli, 10, 40), (self.service, 1, 600)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("50"),
                    "included_in_bill": True,
                }
            ],
        )
        sles = self.sles(pi.name)
        self.assertEqual(list(sles), ["CHILLI"])
        # the whole 50 lands on the only stock line, not 20 / 600 of it
        self.assertEqual(sles["CHILLI"].stock_value_difference, D("450.00"))
        gl = self.gl(pi.name)
        self.assertEqual(gl["Stock In Hand"], D("450.00"))
        self.assertEqual(gl["Purchases"], D("600.00"))  # the service stays expense
        self.assertEqual(gl["Creditors"], D("-1050.00"))
        self.assert_balanced(pi.name)

    def test_cancel_reverses_everything(self):
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                },
                {
                    "description": "Hamali",
                    "amount": D("25"),
                    "distribute_by": "Qty",
                },
            ],
        )
        self.assertEqual(Bin.objects.get().stock_value, D("525.00"))
        pi.cancel()
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=pi.name, is_cancelled=False).exists()
        )
        net = GLEntry.objects.filter(voucher_no=pi.name).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        self.assertEqual(net["dr"], net["cr"])
        self.assertEqual(Bin.objects.get().stock_value, D("0.00"))
        self.assertEqual(Bin.objects.get().actual_qty, D("0.000"))

    def test_repost_stock_reproduces_the_valuation(self):
        self.bill(
            [(self.chilli, 10, 40), (self.turmeric, 20, 50)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                }
            ],
        )
        before = {
            b.item.code: (b.actual_qty, b.stock_value, b.valuation_rate)
            for b in Bin.objects.select_related("item")
        }
        stock_ledger.repost()
        after = {
            b.item.code: (b.actual_qty, b.stock_value, b.valuation_rate)
            for b in Bin.objects.select_related("item")
        }
        self.assertEqual(before, after)
        self.assertEqual(after["CHILLI"][1], D("428.57"))

    def test_verify_integrity_is_green(self):
        self.bill(
            [(self.chilli, 10, 40), (self.turmeric, 20, 50)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                },
                {"description": "Hamali", "amount": D("33.33"), "distribute_by": "Qty"},
            ],
        )
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def test_rupee_rounding_follows_the_bill_charges(self):
        round_off = _acc(
            self.company,
            "Round Off",
            AccountRootType.EXPENSE,
            self.expenses,
            AccountType.ROUND_OFF,
        )
        Company.objects.filter(pk=self.company.pk).update(
            enable_rounded_total=True, round_off_account=round_off
        )
        self.company.refresh_from_db()
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("10.60"),
                    "included_in_bill": True,
                }
            ],
        )
        pi.refresh_from_db()
        self.assertEqual(pi.grand_total, D("410.60"))
        self.assertEqual(pi.rounded_total, D("411.00"))
        self.assertEqual(pi.rounding_adjustment, D("0.40"))

    def test_missing_eiiv_account_refuses_submit(self):
        self.eiiv.delete()
        with self.assertRaises(ValidationError):
            self.bill(
                [(self.chilli, 10, 40)],
                [{"description": "Hamali", "amount": D("25")}],
            )

    def test_charge_rules_at_submit(self):
        # amount-based spread over lines with no value
        with self.assertRaises(ValidationError):
            self.bill(
                [(self.chilli, 10, 0)],
                [{"description": "Hamali", "amount": D("25")}],
            )
        # on-bill charge without an account is refused by the table itself
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.bill(
                [(self.chilli, 10, 40)],
                [{"description": "F", "amount": D("25"), "included_in_bill": True}],
                submit=False,
            )
        # only non-stock lines: nothing to spread over
        with self.assertRaises(ValidationError):
            self.bill(
                [(self.service, 1, 100)],
                [{"description": "Hamali", "amount": D("25")}],
            )
        # a charge needs the stock to be updated
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [{"description": "Hamali", "amount": D("25")}],
            submit=False,
        )
        pi.update_stock = False
        pi.save()
        with self.assertRaises(ValidationError):
            pi.submit()
        # amount must be positive (DB check)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseInvoiceCharge.objects.create(
                purchase_invoice=pi, description="x", amount=D("0")
            )

    def test_amend_copies_the_charges(self):
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                }
            ],
        )
        pi.cancel()
        amended = pi.amend()
        self.assertEqual(amended.charges.count(), 1)
        self.assertEqual(amended.bill_no, pi.bill_no)  # cancelled one is free
        amended.refresh_from_db()
        self.assertEqual(amended.grand_total, D("500.00"))


class NonPerpetualChargeTests(ChargesBase):
    perpetual = False

    def test_on_bill_charge_is_an_expense_and_valuation_still_includes_it(self):
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                }
            ],
        )
        gl = self.gl(pi.name)
        self.assertEqual(
            gl,
            {
                "Purchases": D("400.00"),
                "Freight Inward": D("100.00"),
                "Creditors": D("-500.00"),
            },
        )
        self.assertEqual(
            self.sles(pi.name)["CHILLI"].stock_value_difference, D("500.00")
        )
        self.assert_balanced(pi.name)

    def test_off_bill_charge_has_no_gl(self):
        pi = self.bill(
            [(self.chilli, 10, 40)],
            [{"description": "Hamali", "amount": D("25"), "distribute_by": "Qty"}],
        )
        self.assertEqual(
            self.gl(pi.name),
            {"Purchases": D("400.00"), "Creditors": D("-400.00")},
        )
        self.assertEqual(
            self.sles(pi.name)["CHILLI"].stock_value_difference, D("425.00")
        )
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())


class BillNoTests(ChargesBase):
    perpetual = False

    def test_bill_no_required_at_submit(self):
        with self.assertRaises(ValidationError):
            self.bill([(self.chilli, 10, 40)], bill_no="")
        pi = self.bill([(self.chilli, 10, 40)], bill_no="  ", submit=False)
        self.assertEqual(pi.bill_no, "")  # stripped
        with self.assertRaises(ValidationError):
            pi.submit()

    def test_return_does_not_need_a_bill_no(self):
        orig = self.bill([(self.chilli, 10, 40)])
        from buying import mapping

        ret = mapping.make_purchase_return(orig)
        ret.submit()
        self.assertEqual(ret.docstatus, DocStatus.SUBMITTED)

    def test_unique_per_supplier_among_live_invoices(self):
        first = self.bill([(self.chilli, 1, 40)], bill_no="INV/9")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.bill([(self.chilli, 1, 40)], bill_no="inv/9", submit=False)
        # another supplier may use the same number
        other = Supplier.objects.create(
            name="Other",
            company=self.company,
            supplier_group=SupplierGroup.objects.get(name="SG"),
        )
        self.supplier = other
        self.bill([(self.chilli, 1, 40)], bill_no="INV/9")
        # a cancelled invoice frees the number
        first.cancel()
        self.supplier = first.supplier
        self.bill([(self.chilli, 1, 40)], bill_no="INV/9")

    def test_bill_date_not_after_posting_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.bill(
                [(self.chilli, 1, 40)],
                bill_date=DATE + datetime.timedelta(days=1),
                submit=False,
            )
        pi = self.bill([(self.chilli, 1, 40)], bill_date=DATE)
        self.assertEqual(pi.bill_date, DATE)


class ApiTests(ChargesBase):
    perpetual = True

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="chg_admin", email="chg@test.com", password="x"
        )
        self.client.force_authenticate(self.admin)
        self.client.defaults["HTTP_X_COMPANY_ID"] = str(self.company.pk)

    def body(self, **extra):
        body = {
            "supplier": self.supplier.pk,
            "company": self.company.pk,
            "posting_date": "2026-04-01",
            "bill_no": "SUP/100",
            "bill_date": "2026-03-30",
            "update_stock": True,
            "credit_to": self.creditors.pk,
            "expense_account": self.purchases.pk,
            "items": [
                {
                    "item": self.chilli.pk,
                    "warehouse": self.wh.pk,
                    "qty": "10",
                    "rate": "40",
                },
                {
                    "item": self.turmeric.pk,
                    "warehouse": self.wh.pk,
                    "qty": "20",
                    "rate": "50",
                },
            ],
            "charges": [
                {
                    "description": "Freight",
                    "account": self.freight.pk,
                    "amount": "100.00",
                    "distribute_by": "Amount",
                    "included_in_bill": True,
                },
                {
                    "description": "Hamali",
                    "amount": "30.00",
                    "distribute_by": "Qty",
                    "included_in_bill": False,
                },
            ],
        }
        body.update(extra)
        return body

    def test_create_submit_and_read_back(self):
        res = self.client.post(
            "/api/buying/purchase-invoices/", self.body(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        d = res.data
        self.assertEqual(d["bill_no"], "SUP/100")
        self.assertEqual(d["grand_total"], "1500.00")
        self.assertEqual(d["total_taxes_and_charges"], "100.00")
        self.assertEqual(d["charges_on_bill"], "100.00")
        self.assertEqual(d["charges_off_bill"], "30.00")
        self.assertEqual(len(d["charges"]), 2)
        landed = {i["item_code"]: i["landed_rate"] for i in d["items"]}
        # chilli: 400 + 28.57 + 10 = 438.57 -> /10 kg
        self.assertEqual(landed["CHILLI"], "43.857000")
        # turmeric: 1000 + 71.43 + 20 = 1091.43 -> /20 kg
        self.assertEqual(landed["TURM"], "54.571500")
        res = self.client.post(f"/api/buying/purchase-invoices/{d['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Bin.objects.aggregate(t=Sum("stock_value"))["t"], D("1530.00"))
        listing = self.client.get("/api/buying/purchase-invoices/?search=SUP/100")
        self.assertEqual(listing.data["results"][0]["bill_no"], "SUP/100")

    def test_duplicate_bill_no_and_future_bill_date_are_400(self):
        self.assertEqual(
            self.client.post(
                "/api/buying/purchase-invoices/", self.body(), format="json"
            ).status_code,
            201,
        )
        res = self.client.post(
            "/api/buying/purchase-invoices/",
            self.body(bill_no="sup/100"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("bill_no", res.data)
        res = self.client.post(
            "/api/buying/purchase-invoices/",
            self.body(bill_no="X1", bill_date="2026-04-02"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("bill_date", res.data)

    def test_on_bill_charge_needs_an_account(self):
        body = self.body()
        body["charges"][0].pop("account")
        res = self.client.post("/api/buying/purchase-invoices/", body, format="json")
        self.assertEqual(res.status_code, 400)

    def test_patch_replaces_charges_and_recomputes(self):
        d = self.client.post(
            "/api/buying/purchase-invoices/", self.body(), format="json"
        ).data
        res = self.client.patch(
            f"/api/buying/purchase-invoices/{d['id']}/", {"charges": []}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["grand_total"], "1400.00")
        self.assertEqual(res.data["charges"], [])

    def test_dry_run_preview_includes_bill_charges(self):
        res = self.client.post(
            "/api/accounts/taxes/calculate/",
            {
                "doctype": "buying.PurchaseInvoice",
                "company": self.company.pk,
                "party": self.supplier.pk,
                "items": [{"item": self.chilli.pk, "qty": "10", "rate": "40"}],
                "charges": [
                    {"amount": "100", "included_in_bill": True},
                    {"amount": "30", "included_in_bill": False},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["grand_total"], "500.00")
        self.assertEqual(res.data["net_total"], "400.00")

    def test_template_endpoints(self):
        res = self.client.get("/api/buying/charge-templates/")
        names = {r["name"] for r in res.data["results"]}
        self.assertEqual(
            names, {"Freight Inward", "Hamali / Loading", "Unloading", "Cartage"}
        )
        PurchaseChargeTemplate.objects.all().delete()
        res = self.client.post("/api/buying/charge-templates/ensure-defaults/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 4)
        by = {r["name"]: r for r in res.data}
        self.assertTrue(by["Freight Inward"]["included_in_bill"])
        self.assertEqual(by["Hamali / Loading"]["distribute_by"], "Qty")
