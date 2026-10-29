"""GST Task 3: GL posting of tax / round-off, mapping, outstanding, integrity, dashboard."""

from buying.testing import next_bill_no
import datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum

from accounts import mapping as acc_mapping
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    PaymentEntry,
    PaymentEntryReference,
    TaxTemplate,
)
from buying import mapping as buying_mapping
from buying.models import PurchaseInvoice, PurchaseInvoiceTax
from core.models import Company, DocStatus, FiscalYear, SystemHealthCheck
from gst.tests.test_documents import (
    PI_URL,
    PO_URL,
    SI_URL,
    SO_URL,
    GSTDocBase,
)
from selling import mapping as selling_mapping
from selling.models import SalesInvoice, SalesInvoiceItem, SalesInvoiceTax
from stock.models import Bin, Item, Warehouse

D = Decimal


class PostingBase(GSTDocBase):
    def setUp(self):
        super().setUp()
        self.round_off = self.company.round_off_account
        self.assertIsNotNone(self.round_off)
        self.bank = Account.objects.create(
            name="Bank",
            company=self.company,
            parent=Account.objects.get(company=self.company, name="Assets"),
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )

    # ------------------------------------------------------------- helpers

    def gl(self, voucher_no, live_only=True):
        """Sorted ``(account name, debit, credit)`` of a voucher."""
        qs = GLEntry.objects.filter(voucher_no=voucher_no)
        if live_only:
            qs = qs.filter(is_cancelled=False)
        return sorted((e.account.name, e.debit, e.credit) for e in qs)

    def assert_balanced(self, voucher_no):
        agg = GLEntry.objects.filter(voucher_no=voucher_no).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        self.assertEqual(agg["dr"], agg["cr"])

    def submit(self, url, data):
        res = self.client.post(f"{url}{data['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def sale(self, customer, *specs, **extra):
        d = self.create(SI_URL, self.si_body(customer, self.lines(*specs), **extra))
        return self.submit(SI_URL, d)

    def purchase(self, supplier, *specs, **extra):
        d = self.create(PI_URL, self.pi_body(supplier, self.lines(*specs), **extra))
        return self.submit(PI_URL, d)

    def integrity_ok(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def integrity_fails(self):
        with self.assertRaises(CommandError):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        return SystemHealthCheck.objects.filter(check_name="gst_integrity").latest("pk")

    def enable_rounding(self):
        Company.objects.filter(pk=self.company.pk).update(enable_rounded_total=True)
        self.company.refresh_from_db()


class SalesInvoicePostingTests(PostingBase):
    def test_intra_state_lines(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("236.00"), D("0.00")),
                    ("Sales", D("0.00"), D("200.00")),
                    ("Output CGST", D("0.00"), D("18.00")),
                    ("Output SGST", D("0.00"), D("18.00")),
                ]
            ),
        )
        self.assert_balanced(d["name"])
        party = GLEntry.objects.get(voucher_no=d["name"], account=self.debtors)
        self.assertEqual((party.party_type, party.party), ("Customer", "Intra Cust"))
        self.integrity_ok()

    def test_inter_state_lines(self):
        d = self.sale(self.cust_inter, (self.item18, 2, 100))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("236.00"), D("0.00")),
                    ("Sales", D("0.00"), D("200.00")),
                    ("Output IGST", D("0.00"), D("36.00")),
                ]
            ),
        )
        self.assert_balanced(d["name"])

    def test_sez_zero_rated_posts_no_tax_lines(self):
        d = self.sale(self.cust_sez, (self.item18, 1, 100))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [("Debtors", D("100.00"), D("0.00")), ("Sales", D("0.00"), D("100.00"))]
            ),
        )

    def test_sez_with_payment_posts_igst(self):
        d = self.sale(self.cust_sez, (self.item18, 1, 100), is_export_with_gst=True)
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("118.00"), D("0.00")),
                    ("Sales", D("0.00"), D("100.00")),
                    ("Output IGST", D("0.00"), D("18.00")),
                ]
            ),
        )

    def test_b2c_posts_cgst_sgst(self):
        d = self.sale(self.cust_b2c, (self.item5, 1, 200))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("210.00"), D("0.00")),
                    ("Sales", D("0.00"), D("200.00")),
                    ("Output CGST", D("0.00"), D("5.00")),
                    ("Output SGST", D("0.00"), D("5.00")),
                ]
            ),
        )

    def test_rounding_adjustment_positive_is_a_credit(self):
        self.enable_rounding()
        # 100.50 + 9.05 + 9.05 = 118.60 -> 119, adjustment +0.40
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        self.assertEqual(D(d["grand_total"]), D("118.60"))
        self.assertEqual(D(d["rounding_adjustment"]), D("0.40"))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("119.00"), D("0.00")),
                    ("Sales", D("0.00"), D("100.50")),
                    ("Output CGST", D("0.00"), D("9.05")),
                    ("Output SGST", D("0.00"), D("9.05")),
                    ("Round Off", D("0.00"), D("0.40")),
                ]
            ),
        )
        self.assert_balanced(d["name"])
        self.integrity_ok()

    def test_rounding_adjustment_negative_is_a_debit(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item_plain, 1, "10.40"), taxes=[])
        self.assertEqual(D(d["rounding_adjustment"]), D("-0.40"))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("10.00"), D("0.00")),
                    ("Sales", D("0.00"), D("10.40")),
                    ("Round Off", D("0.40"), D("0.00")),
                ]
            ),
        )
        self.assert_balanced(d["name"])
        self.integrity_ok()

    def test_rounding_without_round_off_account_is_refused_before_posting(self):
        self.enable_rounding()
        Company.objects.filter(pk=self.company.pk).update(round_off_account=None)
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item_plain, 1, "10.40")), taxes=[]
            ),
        )
        res = self.client.post(f"{SI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("round-off account", str(res.data))
        self.assertFalse(GLEntry.objects.filter(voucher_no=d["name"]).exists())
        self.assertEqual(
            SalesInvoice.objects.get(pk=d["id"]).docstatus, DocStatus.DRAFT
        )

    def test_deduct_row_posts_a_debit(self):
        extra = Account.objects.create(
            name="Withholding",
            company=self.company,
            parent=self.income.parent,
            root_type=AccountRootType.INCOME,
            account_type=AccountType.TAX,
        )
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_b2c,
                self.lines((self.item_plain, 1, 100)),
                taxes=[{"account": extra.id, "rate": "10", "add_deduct_tax": "Deduct"}],
            ),
        )
        self.assertEqual(D(d["grand_total"]), D("90.00"))
        d = self.submit(SI_URL, d)
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("90.00"), D("0.00")),
                    ("Withholding", D("10.00"), D("0.00")),
                    ("Sales", D("0.00"), D("100.00")),
                ]
            ),
        )

    def test_cancel_reverses_every_line_and_second_cancel_fails(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        res = self.client.post(f"{SI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        rows = GLEntry.objects.filter(voucher_no=d["name"])
        self.assertEqual(rows.count(), 10)
        self.assertFalse(rows.filter(is_cancelled=False).exists())
        net = {}
        for e in rows:
            net[e.account.name] = net.get(e.account.name, D("0")) + e.debit - e.credit
        self.assertEqual(set(net.values()), {D("0.00")})
        self.assertEqual(
            self.client.post(f"{SI_URL}{d['id']}/cancel/").status_code, 400
        )
        self.assertEqual(rows.count(), 10)
        self.integrity_ok()

    def test_closed_fiscal_year_blocks_submit_and_cancel(self):
        FiscalYear.objects.create(
            name="FY-CLOSED",
            start_date=datetime.date(2030, 4, 1),
            end_date=datetime.date(2031, 3, 31),
            is_closed=True,
        )
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                posting_date="2030-05-01",
            ),
        )
        res = self.client.post(f"{SI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("closed", str(res.data))
        self.assertFalse(GLEntry.objects.filter(voucher_no=d["name"]).exists())
        # An open year later closed refuses the cancel and reverses nothing.
        fy = FiscalYear.objects.create(
            name="FY-LATER",
            start_date=datetime.date(2032, 4, 1),
            end_date=datetime.date(2033, 3, 31),
        )
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                posting_date="2032-05-01",
            ),
        )
        self.submit(SI_URL, d)
        fy.is_closed = True
        fy.save()
        res = self.client.post(f"{SI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=d["name"], is_cancelled=True).exists()
        )

    def test_legacy_company_without_gst_posts_as_before(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100), taxes=[])
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [("Debtors", D("200.00"), D("0.00")), ("Sales", D("0.00"), D("200.00"))]
            ),
        )


class StockPostingBase(PostingBase):
    """Adds a perpetual-inventory capable stock item and warehouse."""

    def setUp(self):
        super().setUp()
        assets = Account.objects.get(company=self.company, name="Assets")
        expenses = Account.objects.get(company=self.company, name="Expenses")
        self.inventory = Account.objects.create(
            name="Stock In Hand",
            company=self.company,
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )
        self.cogs = Account.objects.create(
            name="Cost of Goods Sold",
            company=self.company,
            parent=expenses,
            root_type=AccountRootType.EXPENSE,
            account_type=AccountType.COST_OF_GOODS_SOLD,
        )
        self.adjust = Account.objects.create(
            name="Stock Adjustment",
            company=self.company,
            parent=expenses,
            root_type=AccountRootType.EXPENSE,
        )
        self.wh = Warehouse.objects.create(name="Main WH", company=self.company)
        self.stock_item = Item.objects.create(
            code="STK18",
            name="Stock 18",
            item_group=self.item18.item_group,
            stock_uom=self.item18.stock_uom,
            is_stock_item=True,
            gst_hsn_code="100610",
            item_tax_template=self.item18.item_tax_template,
        )

    def perpetual(self, on=True):
        Company.objects.filter(pk=self.company.pk).update(
            enable_perpetual_inventory=on,
            default_inventory_account=self.inventory if on else None,
            default_expense_account=self.cogs if on else None,
            stock_adjustment_account=self.adjust if on else None,
        )
        self.company.refresh_from_db()

    def stock_lines(self, *specs):
        return [
            {
                "item": it.id,
                "qty": str(qty),
                "rate": str(rate),
                **({"warehouse": self.wh.id} if it.is_stock_item else {}),
            }
            for it, qty, rate in specs
        ]

    def buy_stock(self, *specs, supplier=None, **extra):
        body = self.pi_body(
            supplier or self.sup_intra,
            self.stock_lines(*specs),
            update_stock=True,
            **extra,
        )
        return self.submit(PI_URL, self.create(PI_URL, body))

    def inventory_gl(self):
        agg = GLEntry.objects.filter(
            account=self.inventory, is_cancelled=False
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        return (agg["dr"] or D("0")) - (agg["cr"] or D("0"))

    def bin_value(self):
        return Bin.objects.filter(warehouse__company=self.company).aggregate(
            v=Sum("stock_value")
        )["v"] or D("0")


class PurchaseInvoicePostingTests(StockPostingBase):
    def test_intra_state_lines(self):
        d = self.purchase(self.sup_intra, (self.item18, 2, 100))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Purchases", D("200.00"), D("0.00")),
                    ("Input CGST", D("18.00"), D("0.00")),
                    ("Input SGST", D("18.00"), D("0.00")),
                    ("Creditors", D("0.00"), D("236.00")),
                ]
            ),
        )
        self.assert_balanced(d["name"])
        party = GLEntry.objects.get(voucher_no=d["name"], account=self.creditors)
        self.assertEqual((party.party_type, party.party), ("Supplier", "Intra Sup"))
        self.integrity_ok()

    def test_inter_state_lines(self):
        d = self.purchase(self.sup_inter, (self.item18, 1, 100))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Purchases", D("100.00"), D("0.00")),
                    ("Input IGST", D("18.00"), D("0.00")),
                    ("Creditors", D("0.00"), D("118.00")),
                ]
            ),
        )

    def test_rounding_positive_is_a_debit_negative_a_credit(self):
        self.enable_rounding()
        up = self.purchase(self.sup_intra, (self.item18, 1, "100.50"))
        self.assertEqual(
            self.gl(up["name"]),
            sorted(
                [
                    ("Purchases", D("100.50"), D("0.00")),
                    ("Input CGST", D("9.05"), D("0.00")),
                    ("Input SGST", D("9.05"), D("0.00")),
                    ("Round Off", D("0.40"), D("0.00")),
                    ("Creditors", D("0.00"), D("119.00")),
                ]
            ),
        )
        down = self.purchase(self.sup_intra, (self.item_plain, 1, "10.40"), taxes=[])
        self.assertEqual(
            self.gl(down["name"]),
            sorted(
                [
                    ("Purchases", D("10.40"), D("0.00")),
                    ("Round Off", D("0.00"), D("0.40")),
                    ("Creditors", D("0.00"), D("10.00")),
                ]
            ),
        )
        self.assert_balanced(up["name"])
        self.assert_balanced(down["name"])
        self.integrity_ok()

    def test_perpetual_on_inventory_excludes_tax(self):
        self.perpetual(True)
        d = self.buy_stock((self.stock_item, 10, 50))
        self.assertEqual(D(d["grand_total"]), D("590.00"))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Stock In Hand", D("500.00"), D("0.00")),
                    ("Input CGST", D("45.00"), D("0.00")),
                    ("Input SGST", D("45.00"), D("0.00")),
                    ("Creditors", D("0.00"), D("590.00")),
                ]
            ),
        )
        self.assertEqual(self.inventory_gl(), self.bin_value())
        self.assertEqual(self.bin_value(), D("500.00"))
        self.integrity_ok()

    def test_perpetual_on_mixed_stock_and_service_lines(self):
        self.perpetual(True)
        d = self.buy_stock((self.stock_item, 3, "33.33"), (self.item18, 1, 100))
        # net 99.99 + 100 = 199.99; stock 99.99; expense 100
        self.assertEqual(D(d["net_total"]), D("199.99"))
        rows = {name: (dr, cr) for name, dr, cr in self.gl(d["name"])}
        self.assertEqual(rows["Stock In Hand"], (D("99.99"), D("0.00")))
        self.assertEqual(rows["Purchases"], (D("100.00"), D("0.00")))
        self.assertEqual(self.inventory_gl(), self.bin_value())
        self.assert_balanced(d["name"])
        self.integrity_ok()

    def test_perpetual_off_expenses_the_net_total(self):
        self.perpetual(False)
        d = self.buy_stock((self.stock_item, 10, 50))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Purchases", D("500.00"), D("0.00")),
                    ("Input CGST", D("45.00"), D("0.00")),
                    ("Input SGST", D("45.00"), D("0.00")),
                    ("Creditors", D("0.00"), D("590.00")),
                ]
            ),
        )
        self.assertEqual(self.bin_value(), D("500.00"))
        self.integrity_ok()

    def test_sale_of_stock_keeps_cogs_pair_free_of_tax(self):
        self.perpetual(True)
        self.buy_stock((self.stock_item, 10, 50))
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.stock_lines((self.stock_item, 4, 100)),
                update_stock=True,
            ),
        )
        d = self.submit(SI_URL, d)
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("472.00"), D("0.00")),
                    ("Sales", D("0.00"), D("400.00")),
                    ("Output CGST", D("0.00"), D("36.00")),
                    ("Output SGST", D("0.00"), D("36.00")),
                    ("Cost of Goods Sold", D("200.00"), D("0.00")),
                    ("Stock In Hand", D("0.00"), D("200.00")),
                ]
            ),
        )
        self.assertEqual(self.inventory_gl(), self.bin_value())
        self.assertEqual(self.bin_value(), D("300.00"))
        self.integrity_ok()

    def test_cancel_reverses_gl_and_stock(self):
        self.perpetual(True)
        d = self.buy_stock((self.stock_item, 10, 50))
        res = self.client.post(f"{PI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=d["name"], is_cancelled=False).exists()
        )
        self.assertEqual(self.inventory_gl(), D("0.00"))
        self.assertEqual(self.bin_value(), D("0.00"))
        self.assertEqual(
            self.client.post(f"{PI_URL}{d['id']}/cancel/").status_code, 400
        )
        self.integrity_ok()


class ReverseChargePostingTests(PostingBase):
    def test_intra_state_rcm_pair(self):
        d = self.purchase(self.sup_unreg, (self.item18, 1, 100), is_reverse_charge=True)
        self.assertEqual(D(d["grand_total"]), D("100.00"))
        self.assertEqual(D(d["total_taxes_and_charges"]), D("0.00"))
        self.assertEqual(D(d["rounded_total"]), D("100.00"))
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Purchases", D("100.00"), D("0.00")),
                    ("Input CGST", D("9.00"), D("0.00")),
                    ("Input SGST", D("9.00"), D("0.00")),
                    ("RCM Payable CGST", D("0.00"), D("9.00")),
                    ("RCM Payable SGST", D("0.00"), D("9.00")),
                    ("Creditors", D("0.00"), D("100.00")),
                ]
            ),
        )
        self.assert_balanced(d["name"])
        self.integrity_ok()

    def test_inter_state_rcm_pair_and_cancel(self):
        sup = self.supplier(
            "Unreg Delhi", None, None, category="Unregistered", state="07"
        )
        d = self.purchase(sup, (self.item18, 1, 100), is_reverse_charge=True)
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Purchases", D("100.00"), D("0.00")),
                    ("Input IGST", D("18.00"), D("0.00")),
                    ("RCM Payable IGST", D("0.00"), D("18.00")),
                    ("Creditors", D("0.00"), D("100.00")),
                ]
            ),
        )
        self.client.post(f"{PI_URL}{d['id']}/cancel/")
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=d["name"], is_cancelled=False).exists()
        )
        self.integrity_ok()

    def test_missing_rcm_account_is_refused_before_posting(self):
        Account.objects.filter(
            company=self.company, gst_direction="Reverse Charge", gst_component="SGST"
        ).delete()
        d = self.create(
            PI_URL,
            self.pi_body(
                self.sup_unreg,
                self.lines((self.item18, 1, 100)),
                is_reverse_charge=True,
            ),
        )
        res = self.client.post(f"{PI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("Reverse Charge", str(res.data))
        self.assertFalse(GLEntry.objects.filter(voucher_no=d["name"]).exists())


class MappingTests(PostingBase):
    def submitted_order(self, url, body):
        return self.submit(url, self.create(url, body))

    def test_so_to_si_copies_template_rows_and_flags(self):
        so = self.submitted_order(
            SO_URL, self.so_body(self.cust_inter, self.lines((self.item18, 10, 100)))
        )
        res = self.client.post(f"{SO_URL}{so['id']}/make-sales-invoice/")
        self.assertEqual(res.status_code, 201, res.content)
        d = res.data
        self.assertEqual(d["taxes_and_charges"], so["taxes_and_charges"])
        self.assertEqual(d["place_of_supply"], "07")
        self.assertEqual(self.components(d), {"IGST": D("180.00")})
        self.assertEqual(D(d["net_total"]), D("1000.00"))
        self.assertEqual(D(d["grand_total"]), D("1180.00"))
        self.assertEqual(d["gst_category"], "Registered Regular")
        line = d["items"][0]
        self.assertEqual(line["igst_amount"], "180.00")
        self.assertEqual(line["gst_hsn_code"], "100610")
        d = self.submit(SI_URL, d)
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("1180.00"), D("0.00")),
                    ("Sales", D("0.00"), D("1000.00")),
                    ("Output IGST", D("0.00"), D("180.00")),
                ]
            ),
        )

    def test_partial_invoicing_recomputes_tax_on_remaining_qty(self):
        so = self.submitted_order(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 10, 100)))
        )
        # Invoice 4 of 10 by hand (ORM), submit, then map the remainder.
        so_obj = so_model().objects.get(pk=so["id"])
        part = SalesInvoice.objects.create(
            name="SINV-PART-1",
            customer=self.cust_intra,
            company=self.company,
            posting_date=datetime.date(2026, 4, 1),
            sales_order=so_obj,
            update_stock=False,
            debit_to=self.debtors,
            income_account=self.income,
            taxes_and_charges=so_obj.taxes_and_charges,
            place_of_supply=so_obj.place_of_supply,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=part, item=self.item18, qty=D("4"), rate=D("100")
        )
        for row in so_obj.taxes.all():
            SalesInvoiceTax.objects.create(
                sales_invoice=part,
                idx=row.idx,
                account=row.account,
                rate=row.rate,
                description=row.description,
            )
        part.calculate_taxes_and_totals(resolve_lines=True)
        self.assertEqual(part.grand_total, D("472.00"))
        part.submit()
        rest = selling_mapping.make_sales_invoice(so_obj)
        self.assertEqual(rest.net_total, D("600.00"))
        self.assertEqual(rest.total_taxes_and_charges, D("108.00"))
        self.assertEqual(rest.grand_total, D("708.00"))
        self.assertEqual(
            sorted(rest.taxes.values_list("tax_amount", flat=True)),
            [D("54.00"), D("54.00")],
        )

    def test_po_to_pi_copies_rows_and_rcm_flag(self):
        po = self.submitted_order(
            PO_URL,
            self.po_body(
                self.sup_unreg,
                self.lines((self.item18, 2, 100)),
                is_reverse_charge=True,
            ),
        )
        res = self.client.post(f"{PO_URL}{po['id']}/make-purchase-invoice/")
        self.assertEqual(res.status_code, 201, res.content)
        d = res.data
        self.assertTrue(d["is_reverse_charge"])
        self.assertEqual(self.components(d), {"CGST": D("18.00"), "SGST": D("18.00")})
        self.assertEqual(D(d["grand_total"]), D("200.00"))
        self.assertEqual(D(d["total_taxes_and_charges"]), D("0.00"))
        self.submit(PI_URL, d)
        self.integrity_ok()

    def test_po_to_pi_normal_and_partial(self):
        po = self.submitted_order(
            PO_URL, self.po_body(self.sup_inter, self.lines((self.item18, 10, 100)))
        )
        po_obj = po_model().objects.get(pk=po["id"])
        part = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name="PINV-PART-1",
            supplier=self.sup_inter,
            company=self.company,
            posting_date=datetime.date(2026, 4, 1),
            purchase_order=po_obj,
            update_stock=False,
            credit_to=self.creditors,
            expense_account=self.expense,
            taxes_and_charges=po_obj.taxes_and_charges,
            place_of_supply=po_obj.place_of_supply,
        )
        from buying.models import PurchaseInvoiceItem

        PurchaseInvoiceItem.objects.create(
            purchase_invoice=part, item=self.item18, qty=D("3"), rate=D("100")
        )
        for row in po_obj.taxes.all():
            PurchaseInvoiceTax.objects.create(
                purchase_invoice=part,
                idx=row.idx,
                account=row.account,
                rate=row.rate,
                description=row.description,
            )
        part.calculate_taxes_and_totals(resolve_lines=True)
        part.submit()
        rest = buying_mapping.make_purchase_invoice(po_obj)
        self.assertEqual(rest.net_total, D("700.00"))
        self.assertEqual(rest.total_taxes_and_charges, D("126.00"))
        self.assertEqual(rest.taxes.get().tax_amount, D("126.00"))


def so_model():
    from selling.models import SalesOrder

    return SalesOrder


def po_model():
    from buying.models import PurchaseOrder

    return PurchaseOrder


class PaymentAndDashboardTests(PostingBase):
    def test_outstanding_is_rounded_total_and_payment_clears_it(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        inv = SalesInvoice.objects.get(pk=d["id"])
        self.assertEqual(acc_mapping.invoice_outstanding(inv), D("119.00"))
        res = self.client.post(f"{SI_URL}{d['id']}/make-payment-entry/")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(D(res.data["paid_amount"]), D("119.00"))
        self.assertEqual(D(res.data["received_amount"]), D("119.00"))
        ref = PaymentEntryReference.objects.get(payment_entry_id=res.data["id"])
        self.assertEqual(ref.allocated_amount, D("119.00"))
        PaymentEntry.objects.get(pk=res.data["id"]).submit()
        self.assertEqual(acc_mapping.invoice_outstanding(inv), D("0.00"))
        self.integrity_ok()

    def test_partial_payment_leaves_remainder(self):
        d = self.purchase(self.sup_intra, (self.item18, 1, 100))
        inv = PurchaseInvoice.objects.get(pk=d["id"])
        self.assertEqual(acc_mapping.invoice_outstanding(inv), D("118.00"))
        res = self.client.post(f"{PI_URL}{d['id']}/make-payment-entry/")
        self.assertEqual(D(res.data["paid_amount"]), D("118.00"))

    def test_dashboard_sales_excludes_gst_receivables_include_it(self):
        self.sale(self.cust_intra, (self.item18, 2, 100))  # net 200, gross 236
        self.purchase(self.sup_intra, (self.item18, 1, 100))  # net 100, gross 118
        res = self.client.get("/api/core/dashboard/", {"company": self.company.id})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(D(res.data["total_sales"]), D("200.00"))
        self.assertEqual(D(res.data["total_purchases"]), D("100.00"))
        self.assertEqual(D(res.data["receivables_outstanding"]), D("236.00"))
        self.assertEqual(D(res.data["payables_outstanding"]), D("118.00"))

    def test_dashboard_open_orders_compare_net_values(self):
        so = self.submit(
            SO_URL,
            self.create(
                SO_URL,
                self.so_body(self.cust_intra, self.lines((self.item18, 10, 100))),
            ),
        )
        res = self.client.get("/api/core/dashboard/", {"company": self.company.id})
        self.assertEqual(D(res.data["open_sales_orders"]["value"]), D("1000.00"))
        inv = self.client.post(f"{SO_URL}{so['id']}/make-sales-invoice/")
        self.submit(SI_URL, inv.data)
        res = self.client.get("/api/core/dashboard/", {"company": self.company.id})
        self.assertEqual(res.data["open_sales_orders"]["count"], 0)


class IntegrityCorruptionTests(PostingBase):
    def setUp(self):
        super().setUp()
        self.enable_rounding()
        self.si = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        self.pi = self.purchase(self.sup_intra, (self.item18, 1, "100.50"))
        rcm = self.purchase(
            self.sup_unreg, (self.item18, 1, 100), is_reverse_charge=True
        )
        self.rcm_name = rcm["name"]

    def errors(self):
        return self.integrity_fails().details["errors"]

    def test_clean_database_is_green(self):
        self.integrity_ok()
        check = SystemHealthCheck.objects.filter(check_name="gst_integrity").latest(
            "pk"
        )
        self.assertEqual(check.status, "ok")

    def test_altered_tax_row_amount_is_detected(self):
        SalesInvoiceTax.objects.filter(sales_invoice_id=self.si["id"], idx=1).update(
            tax_amount=D("9.50")
        )
        errors = self.errors()
        self.assertTrue(any(self.si["name"] in e for e in errors), errors)

    def test_altered_header_total_is_detected(self):
        SalesInvoice.objects.filter(pk=self.si["id"]).update(
            net_total=D("99.00"), grand_total=D("117.10"), rounded_total=D("117.50")
        )
        self.assertTrue(any("net_total" in e for e in self.errors()))

    def test_missing_tax_gl_line_is_detected(self):
        GLEntry.objects.filter(
            voucher_no=self.si["name"], account__name="Output SGST"
        ).delete()
        errors = self.errors()
        self.assertTrue(
            any("Output SGST" in e and self.si["name"] in e for e in errors), errors
        )

    def test_mismatched_round_off_line_is_detected(self):
        GLEntry.objects.filter(
            voucher_no=self.pi["name"], account__name="Round Off"
        ).update(debit=D("0.30"))
        errors = self.errors()
        self.assertTrue(
            any("round-off" in e and self.pi["name"] in e for e in errors), errors
        )

    def test_missing_rcm_credit_is_detected(self):
        GLEntry.objects.filter(
            voucher_no=self.rcm_name, account__name="RCM Payable CGST"
        ).delete()
        self.assertTrue(any("RCM Payable CGST" in e for e in self.errors()))

    def test_wrong_party_line_is_detected(self):
        GLEntry.objects.filter(voucher_no=self.si["name"], account=self.debtors).update(
            debit=D("118.60")
        )
        self.assertTrue(any("party GL line" in e for e in self.errors()))

    def test_line_gst_not_matching_row_is_detected(self):
        SalesInvoiceItem.objects.filter(sales_invoice_id=self.si["id"]).update(
            cgst_amount=D("1.00")
        )
        self.assertTrue(any("CGST row" in e for e in self.errors()))

    def test_igst_on_intra_state_invoice_is_detected(self):
        row = SalesInvoiceTax.objects.get(sales_invoice_id=self.si["id"], idx=1)
        SalesInvoiceTax.objects.filter(pk=row.pk).update(
            account=self.tag[("Output", "IGST")]
        )
        self.assertTrue(self.errors())

    def test_warnings_do_not_fail(self):
        from selling.models import Customer

        Customer.objects.filter(pk=self.cust_inter.pk).update(gstin="07AAGCS4567K1ZP")
        self.integrity_ok()
        check = SystemHealthCheck.objects.filter(check_name="gst_integrity").latest(
            "pk"
        )
        self.assertEqual(check.status, "ok")
        self.assertTrue(
            any("Invalid party GSTINs" in w for w in check.details["warnings"])
        )


class LineAmountRoundingTests(PostingBase):
    def test_line_amount_rounds_half_up(self):
        # 0.5 x 0.01 = 0.005 -> 0.01 (half-even would give 0.00)
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_b2c,
                [{"item": self.item_plain.id, "qty": "0.5", "rate": "0.01"}],
                taxes=[],
            ),
        )
        self.assertEqual(D(d["items"][0]["amount"]), D("0.01"))
        self.assertEqual(D(d["net_total"]), D("0.01"))


class DryRunAndFilterTests(PostingBase):
    def test_dry_run_returns_template_and_state_names(self):
        res = self.client.post(
            "/api/accounts/taxes/calculate/",
            {
                "doctype": "selling.SalesInvoice",
                "company": self.company.id,
                "party": self.cust_inter.id,
                "items": [{"item": self.item18.id, "qty": "2", "rate": "100"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["taxes_and_charges_name"], "Output GST Out-state")
        self.assertEqual(res.data["place_of_supply"], "07")
        self.assertEqual(res.data["place_of_supply_name"], "Delhi")
        self.assertEqual(
            res.data["taxes_and_charges"],
            self.template("Output GST Out-state").id,
        )

    def test_dry_run_names_are_null_for_zero_rated(self):
        res = self.client.post(
            "/api/accounts/taxes/calculate/",
            {
                "doctype": "selling.SalesInvoice",
                "company": self.company.id,
                "party": self.cust_sez.id,
                "items": [{"item": self.item18.id, "qty": "1", "rate": "100"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(res.data["taxes_and_charges_name"])
        self.assertEqual(res.data["place_of_supply_name"], "Gujarat")

    def test_supply_kind_filter_includes_generic_templates(self):
        generic = TaxTemplate.objects.create(
            company=self.company,
            title="Generic Output",
            template_type="Sales",
            gst_supply_type="",
        )
        url = "/api/accounts/tax-templates/"

        def titles(kind):
            res = self.client.get(
                url,
                {
                    "company": self.company.id,
                    "template_type": "Sales",
                    "supply_kind": kind,
                },
            )
            self.assertEqual(res.status_code, 200, res.content)
            rows = res.data["results"] if "results" in res.data else res.data
            return {r["title"] for r in rows}

        self.assertEqual(titles("INTRA"), {"Output GST In-state", generic.title})
        self.assertEqual(titles("INTER"), {"Output GST Out-state", generic.title})
        bad = self.client.get(url, {"supply_kind": "XX"})
        self.assertEqual(bad.status_code, 400)


class GuardRaisesCleanly(PostingBase):
    def test_posting_validation_error_is_a_validation_error(self):
        d = self.create(
            SI_URL,
            self.si_body(self.cust_intra, self.lines((self.item_plain, 1, "10.40"))),
        )
        self.enable_rounding()
        Company.objects.filter(pk=self.company.pk).update(round_off_account=None)
        inv = SalesInvoice.objects.get(pk=d["id"])
        with self.assertRaises(ValidationError):
            inv.submit()
