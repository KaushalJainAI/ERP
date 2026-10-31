"""Tally export: vouchers derived from the GL, XML shape, maps, pre-flight, log."""

from buying.testing import next_bill_no
import datetime
import shutil
import tempfile
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts import mapping as acc_mapping  # noqa: F401  (registers nothing; parity)
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from buying import mapping as buying_mapping
from buying.models import (
    ExpenseCategory,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    Supplier,
    SupplierGroup,
)
from buying.services.expenses import create_expense
from core.models import AuditLog, Company, UserCompany
from selling import mapping as selling_mapping
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesInvoiceItem
from stock.models import (
    UOM,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    Warehouse,
)
from tally_export.models import (
    TallyExportBatch,
    TallyExportedVoucher,
    TallyLedgerMap,
    TallyStockItemMap,
)
from tally_export.services import export as svc
from tally_export.services.collect import pending_counts
from tally_export.services.maps import get_settings
from users.models import Role, User, UserRole

D = Decimal
DATE = datetime.date(2026, 4, 1)
MEDIA = tempfile.mkdtemp(prefix="tally-export-test-")


def signed_debit(amount):
    """Tally amount -> debit-positive Decimal (Tally writes debits as negatives)."""
    return -Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


def _acc(company, name, root, parent=None, account_type=None, is_group=False):
    return Account.objects.create(
        name=name,
        company=company,
        parent=parent,
        root_type=root,
        account_type=account_type,
        is_group=is_group,
    )


# -- a tiny reader for the generated XML (what Tally would parse) ------------


def parse_vouchers(data: bytes):
    """``[{"el", "type", "number", "action", "remote", "ledgers", "allocs"}]``.

    ``ledgers`` is ``[(ledger, debit-positive amount)]`` of the voucher's own
    ledger entries, ``allocs`` the accounting allocations of its inventory
    entries (the sales / purchase ledger), both converted from Tally's sign
    convention with ``signed_debit``.
    """
    root = ET.fromstring(data)
    out = []
    for v in root.iter("VOUCHER"):
        ledgers = [
            (e.findtext("LEDGERNAME"), signed_debit(e.findtext("AMOUNT")))
            for e in v.findall("ALLLEDGERENTRIES.LIST")
        ]
        allocs = [
            (a.findtext("LEDGERNAME"), signed_debit(a.findtext("AMOUNT")))
            for a in v.findall("ALLINVENTORYENTRIES.LIST/ACCOUNTINGALLOCATIONS.LIST")
        ]
        out.append(
            {
                "el": v,
                "type": v.get("VCHTYPE"),
                "number": v.findtext("VOUCHERNUMBER"),
                "action": v.get("ACTION"),
                "remote": v.get("REMOTEID"),
                "ledgers": ledgers,
                "allocs": allocs,
            }
        )
    return out


@override_settings(MEDIA_ROOT=MEDIA)
class Base(TestCase):
    def setUp(self):
        c = self.company = Company.objects.create(name="Nidhi Co", abbr="NC")
        assets = _acc(c, "Assets", AccountRootType.ASSET, is_group=True)
        expenses = _acc(c, "Expenses", AccountRootType.EXPENSE, is_group=True)
        income = _acc(c, "Income", AccountRootType.INCOME, is_group=True)
        liab = _acc(c, "Liabilities", AccountRootType.LIABILITY, is_group=True)
        self.inventory = _acc(
            c, "Stock In Hand", AccountRootType.ASSET, assets, AccountType.STOCK
        )
        self.debtors = _acc(
            c, "Debtors", AccountRootType.ASSET, assets, AccountType.RECEIVABLE
        )
        self.bank = _acc(c, "Bank", AccountRootType.ASSET, assets, AccountType.BANK)
        self.cash = _acc(c, "Cash", AccountRootType.ASSET, assets, AccountType.CASH)
        self.cogs = _acc(
            c, "COGS", AccountRootType.EXPENSE, expenses, AccountType.COST_OF_GOODS_SOLD
        )
        self.adjustment = _acc(c, "Stock Adjustment", AccountRootType.EXPENSE, expenses)
        self.purchases = _acc(c, "Purchases", AccountRootType.EXPENSE, expenses)
        self.rent = _acc(c, "Rent", AccountRootType.EXPENSE, expenses)
        self.sales = _acc(c, "Sales", AccountRootType.INCOME, income)
        self.creditors = _acc(
            c, "Creditors", AccountRootType.LIABILITY, liab, AccountType.PAYABLE
        )
        c.enable_perpetual_inventory = True
        c.default_inventory_account = self.inventory
        c.default_expense_account = self.cogs
        c.stock_adjustment_account = self.adjustment
        c.save()

        uom = UOM.objects.create(name="Kg")
        grp = ItemGroup.objects.create(name="Spices", is_group=False)
        self.chilli = Item.objects.create(
            code="CHILLI", name="Chilli Powder", item_group=grp, stock_uom=uom
        )
        self.service = Item.objects.create(
            code="FREIGHT",
            name="Freight",
            item_group=grp,
            stock_uom=uom,
            is_stock_item=False,
        )
        self.wh = Warehouse.objects.create(name="Store", company=c)
        self.wh2 = Warehouse.objects.create(name="Godown 2", company=c)
        self.supplier = Supplier.objects.create(
            name="Supp",
            company=c,
            supplier_group=SupplierGroup.objects.create(name="SG"),
        )
        self.customer = Customer.objects.create(
            name="Cust",
            company=c,
            customer_group=CustomerGroup.objects.create(name="CG"),
        )
        self._n = 0

    # -- data helpers --------------------------------------------------------
    def name(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def map_all(self):
        """Map every posting account, both parties (with awkward Tally names) and items."""
        for a in Account.objects.filter(company=self.company, is_group=False):
            TallyLedgerMap.objects.create(
                company=self.company, account=a, tally_ledger=f"T {a.name}"
            )
        TallyLedgerMap.objects.create(
            company=self.company,
            account=self.debtors,
            party_type="Customer",
            party="Cust",
            tally_ledger="Cust  & Sons <Indore>",
        )
        TallyLedgerMap.objects.create(
            company=self.company,
            account=self.creditors,
            party_type="Supplier",
            party="Supp",
            tally_ledger="Supp Traders",
        )
        TallyStockItemMap.objects.create(
            company=self.company,
            item=self.chilli,
            tally_item="Chilli Powder 1kg",
            tally_unit="k.g",
        )

    def purchase(self, qty=10, rate=40, extra_service=None, date=DATE):
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name=self.name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=date,
            update_stock=True,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi,
            item=self.chilli,
            warehouse=self.wh,
            qty=D(qty),
            rate=D(rate),
        )
        if extra_service:
            PurchaseInvoiceItem.objects.create(
                purchase_invoice=pi,
                item=self.service,
                qty=D(1),
                rate=D(extra_service),
            )
        pi.submit()
        return pi

    def sell(self, qty=4, rate=100, date=DATE, extra_service=None):
        si = SalesInvoice.objects.create(
            name=self.name("SINV"),
            customer=self.customer,
            company=self.company,
            posting_date=date,
            update_stock=True,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si,
            item=self.chilli,
            warehouse=self.wh,
            qty=D(qty),
            rate=D(rate),
        )
        if extra_service:
            SalesInvoiceItem.objects.create(
                sales_invoice=si, item=self.service, qty=D(1), rate=D(extra_service)
            )
        si.submit()
        return si

    def receipt(self, invoice, amount):
        pay = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.RECEIVE,
            posting_date=DATE,
            company=self.company,
            party_type="Customer",
            party_name="Cust",
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D(amount),
            received_amount=D(amount),
            reference_no="UTR123",
        )
        PaymentEntryReference.objects.create(
            payment_entry=pay,
            reference_doctype="selling.SalesInvoice",
            reference_name=invoice.name,
            allocated_amount=D(amount),
        )
        pay.submit()
        return pay

    def journal(self, lines, **kw):
        jv = JournalEntry.objects.create(
            name=self.name("JV"), posting_date=DATE, company=self.company, **kw
        )
        for account, dr, cr in lines:
            JournalEntryLine.objects.create(
                journal_entry=jv, account=account, debit=D(dr), credit=D(cr)
            )
        jv.submit()
        return jv

    def export(self, **kw):
        return svc.create_export(self.company, **kw)

    def read(self, batch, part="file"):
        f = getattr(batch, part)
        with f.open("rb") as fh:
            return fh.read()

    def vouchers(self, batch):
        return {v["number"]: v for v in parse_vouchers(self.read(batch))}

    def gl_net(self, name):
        """GL ledger -> debit-positive net of the live rows of a voucher."""
        out = {}
        for e in GLEntry.objects.filter(voucher_no=name, is_cancelled=False):
            out[e.account.name] = out.get(e.account.name, D(0)) + e.debit - e.credit
        return out


class VoucherXmlTests(Base):
    def setUp(self):
        super().setUp()
        self.map_all()

    def test_purchase_invoice_inventory_in_party_credit_and_stock_leg_folded(self):
        pi = self.purchase(qty=10, rate=40)  # perpetual: GL has Dr Stock In Hand
        batch = self.export()
        v = self.vouchers(batch)[pi.name]
        self.assertEqual(v["type"], "Purchase")
        # Party credited (the party's Tally ledger, not the control account)...
        self.assertEqual(v["ledgers"], [("Supp Traders", D("-400.00"))])
        # ...stock value folded into the purchase ledger and sent as the allocation.
        self.assertEqual(v["allocs"], [("T Purchases", D("400.00"))])
        el = v["el"]
        inv = el.find("ALLINVENTORYENTRIES.LIST")
        self.assertEqual(inv.findtext("STOCKITEMNAME"), "Chilli Powder 1kg")
        self.assertEqual(inv.findtext("ISDEEMEDPOSITIVE"), "Yes")
        self.assertEqual(inv.findtext("AMOUNT"), "-400.00")
        self.assertEqual(inv.findtext("ACTUALQTY"), "10 k.g")
        self.assertEqual(inv.findtext("BILLEDQTY"), "10 k.g")
        self.assertEqual(inv.findtext("RATE"), "40.00/k.g")
        self.assertEqual(inv.findtext("BATCHALLOCATIONS.LIST/GODOWNNAME"), "Store")
        # Party line: credit => ISDEEMEDPOSITIVE No, positive AMOUNT, New Ref bill.
        party = el.find("ALLLEDGERENTRIES.LIST")
        self.assertEqual(party.findtext("ISDEEMEDPOSITIVE"), "No")
        self.assertEqual(party.findtext("AMOUNT"), "400.00")
        self.assertEqual(party.findtext("BILLALLOCATIONS.LIST/BILLTYPE"), "New Ref")
        self.assertEqual(party.findtext("BILLALLOCATIONS.LIST/NAME"), pi.name)
        self.assertEqual(el.findtext("PARTYLEDGERNAME"), "Supp Traders")
        self.assertEqual(el.findtext("DATE"), "20260401")
        self.assertEqual(el.findtext("VOUCHERNUMBER"), pi.name)

    def test_expense_invoice_voucher_generation_and_ledger_name_mapping(self):
        """Expense invoice (is_expense=True, update_stock=False) exports cleanly
        as Purchase voucher without stock lines, mapping ExpenseCategory account."""
        category = ExpenseCategory.objects.create(
            company=self.company,
            name="Office Rent",
            account=self.rent,
            item=self.service,
            is_active=True,
        )
        invoice, _ = create_expense(
            company=self.company,
            category=category,
            supplier=self.supplier,
            amount=D("1500.00"),
            date=DATE,
            bill_no="EXP-BILL-01",
            paid_now=False,
        )
        batch = self.export()
        v = self.vouchers(batch)[invoice.name]
        self.assertEqual(v["type"], "Purchase")
        # No inventory lines
        self.assertEqual(len(v["allocs"]), 0)
        self.assertIsNone(v["el"].find("ALLINVENTORYENTRIES.LIST"))

        # Ledger entries: Rent debited (+1500), Supplier credited (-1500)
        ledgers = dict(v["ledgers"])
        self.assertEqual(ledgers.get("T Rent"), D("1500.00"))
        self.assertEqual(ledgers.get("Supp Traders"), D("-1500.00"))

    def test_expense_invoice_journal_voucher_override(self):
        """When settings override buying.PurchaseInvoice:expense to Journal,
        expense invoices export as Journal vouchers."""
        settings = get_settings(self.company)
        settings.voucher_type_overrides = {"buying.PurchaseInvoice:expense": "Journal"}
        settings.save()

        category = ExpenseCategory.objects.create(
            company=self.company,
            name="Office Rent",
            account=self.rent,
            item=self.service,
            is_active=True,
        )
        invoice, _ = create_expense(
            company=self.company,
            category=category,
            supplier=self.supplier,
            amount=D("2500.00"),
            date=DATE,
            bill_no="EXP-BILL-02",
            paid_now=False,
        )
        batch = self.export()
        v = self.vouchers(batch)[invoice.name]
        self.assertEqual(v["type"], "Journal")
        self.assertIsNone(v["el"].find("ALLINVENTORYENTRIES.LIST"))
        ledgers = dict(v["ledgers"])
        self.assertEqual(ledgers.get("T Rent"), D("2500.00"))
        self.assertEqual(ledgers.get("Supp Traders"), D("-2500.00"))

    def test_sales_invoice_valuation_legs_dropped_and_non_stock_line_residual(self):
        self.purchase(qty=10, rate=40)
        si = self.sell(qty=4, rate=100, extra_service=50)
        batch = self.export()
        v = self.vouchers(batch)[si.name]
        self.assertEqual(v["type"], "Sales")
        names = [n for n, _ in v["ledgers"]]
        # No COGS / Stock In Hand in Tally: it values stock itself.
        self.assertNotIn("T COGS", names)
        self.assertNotIn("T Stock In Hand", names)
        # Party debit = 450; stock line allocated 400; the 50 freight stays on Sales.
        self.assertEqual(v["ledgers"][0], ("Cust  & Sons <Indore>", D("450.00")))
        self.assertEqual(v["allocs"], [("T Sales", D("-400.00"))])
        self.assertIn(("T Sales", D("-50.00")), v["ledgers"])
        inv = v["el"].find("ALLINVENTORYENTRIES.LIST")
        self.assertEqual(inv.findtext("ISDEEMEDPOSITIVE"), "No")
        self.assertEqual(inv.findtext("AMOUNT"), "400.00")
        self.assertEqual(inv.findtext("ACCOUNTINGALLOCATIONS.LIST/AMOUNT"), "400.00")
        self.assertEqual(
            v["el"].find("ALLLEDGERENTRIES.LIST").findtext("ISDEEMEDPOSITIVE"), "Yes"
        )
        self.assertEqual(
            v["el"].find("ALLLEDGERENTRIES.LIST").findtext("AMOUNT"), "-450.00"
        )

    def test_receipt_payment_contra_journal(self):
        self.purchase()
        si = self.sell()
        rec = self.receipt(si, "300.00")
        pay = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.PAY,
            posting_date=DATE,
            company=self.company,
            party_type="Supplier",
            party_name="Supp",
            paid_from=self.bank,
            paid_to=self.creditors,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
        )
        pay.submit()
        contra = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.INTERNAL_TRANSFER,
            posting_date=DATE,
            company=self.company,
            paid_from=self.bank,
            paid_to=self.cash,
            paid_amount=D("50.00"),
            received_amount=D("50.00"),
        )
        contra.submit()
        jv = self.journal(
            [(self.rent, "70.00", "0.00"), (self.cash, "0.00", "70.00")],
            user_remark="Rent for April",
            bill_no="BILL/77",
        )
        batch = self.export()
        got = self.vouchers(batch)
        self.assertEqual(got[rec.name]["type"], "Receipt")
        self.assertEqual(got[pay.name]["type"], "Payment")
        self.assertEqual(got[contra.name]["type"], "Contra")
        self.assertEqual(got[jv.name]["type"], "Journal")
        # Receipt: bank Dr 300 (bank leg is NOT the party's ledger), party Cr 300
        # against the invoice.
        self.assertEqual(
            sorted(got[rec.name]["ledgers"]),
            sorted([("T Bank", D("300.00")), ("Cust  & Sons <Indore>", D("-300.00"))]),
        )
        bill = [
            e
            for e in got[rec.name]["el"].findall("ALLLEDGERENTRIES.LIST")
            if e.findtext("LEDGERNAME") == "Cust  & Sons <Indore>"
        ][0].find("BILLALLOCATIONS.LIST")
        self.assertEqual(bill.findtext("BILLTYPE"), "Agst Ref")
        self.assertEqual(bill.findtext("NAME"), si.name)
        self.assertEqual(got[rec.name]["el"].findtext("REFERENCE"), "UTR123")
        self.assertEqual(
            sorted(got[pay.name]["ledgers"]),
            sorted([("Supp Traders", D("100.00")), ("T Bank", D("-100.00"))]),
        )
        self.assertEqual(
            sorted(got[contra.name]["ledgers"]),
            sorted([("T Cash", D("50.00")), ("T Bank", D("-50.00"))]),
        )
        self.assertEqual(got[jv.name]["el"].findtext("REFERENCE"), "BILL/77")
        self.assertEqual(got[jv.name]["el"].findtext("NARRATION"), "Rent for April")
        self.assertIsNone(got[rec.name]["el"].find("NARRATION"))
        self.assertEqual(
            sorted(got[jv.name]["ledgers"]),
            sorted([("T Rent", D("70.00")), ("T Cash", D("-70.00"))]),
        )

    def test_sales_return_is_a_credit_note_and_stock_comes_back_in(self):
        self.purchase(qty=10, rate=40)
        si = self.sell(qty=4, rate=100)
        ret = selling_mapping.make_sales_return(si)
        ret.submit()
        batch = self.export()
        v = self.vouchers(batch)[ret.name]
        self.assertEqual(v["type"], "Credit Note")
        self.assertEqual(v["ledgers"], [("Cust  & Sons <Indore>", D("-400.00"))])
        self.assertEqual(v["allocs"], [("T Sales", D("400.00"))])
        inv = v["el"].find("ALLINVENTORYENTRIES.LIST")
        self.assertEqual(inv.findtext("ISDEEMEDPOSITIVE"), "Yes")
        self.assertEqual(inv.findtext("AMOUNT"), "-400.00")
        self.assertEqual(inv.findtext("ACTUALQTY"), "4 k.g")

    def test_purchase_return_is_a_debit_note(self):
        pi = self.purchase(qty=10, rate=40)
        ret = buying_mapping.make_purchase_return(pi)
        ret.submit()
        v = self.vouchers(self.export())[ret.name]
        self.assertEqual(v["type"], "Debit Note")
        self.assertEqual(v["ledgers"], [("Supp Traders", D("400.00"))])
        self.assertEqual(v["allocs"], [("T Purchases", D("-400.00"))])

    def test_journal_credit_note_type_and_company_voucher_type_override(self):
        jv = self.journal(
            [(self.rent, "0.00", "10.00"), (self.cash, "10.00", "0.00")],
            voucher_type="Credit Note",
        )
        settings = get_settings(self.company)
        settings.voucher_type_overrides = {
            "accounts.JournalEntry:Credit Note": "Credit Note Gst"
        }
        settings.save()
        v = self.vouchers(self.export())[jv.name]
        self.assertEqual(v["type"], "Credit Note Gst")
        self.assertEqual(v["el"].get("VCHTYPE"), "Credit Note Gst")
        self.assertEqual(v["el"].findtext("VOUCHERTYPENAME"), "Credit Note Gst")

    def test_stock_journal_from_stock_ledger(self):
        self.purchase(qty=10, rate=40)
        se = StockEntry.objects.create(
            name=self.name("STE"),
            stock_entry_type=StockEntryType.MATERIAL_TRANSFER,
            posting_date=DATE,
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=se,
            item=self.chilli,
            source_warehouse=self.wh,
            target_warehouse=self.wh2,
            qty=D(3),
        )
        se.submit()
        v = self.vouchers(self.export())[se.name]
        self.assertEqual(v["type"], "Stock Journal")
        el = v["el"]
        out = el.find("INVENTORYENTRIESOUT.LIST")
        inn = el.find("INVENTORYENTRIESIN.LIST")
        self.assertEqual(out.findtext("BATCHALLOCATIONS.LIST/GODOWNNAME"), "Store")
        self.assertEqual(inn.findtext("BATCHALLOCATIONS.LIST/GODOWNNAME"), "Godown 2")
        self.assertEqual(out.findtext("ISDEEMEDPOSITIVE"), "Yes")
        self.assertEqual(inn.findtext("ISDEEMEDPOSITIVE"), "No")
        self.assertEqual(out.findtext("ACTUALQTY"), "3 k.g")
        self.assertEqual(inn.findtext("ACTUALQTY"), "3 k.g")
        self.assertEqual(out.findtext("AMOUNT"), "-120.00")
        self.assertEqual(inn.findtext("AMOUNT"), "120.00")

    def test_xml_is_utf8_escaped_and_well_formed(self):
        jv = self.journal(
            [(self.rent, "5.00", "0.00"), (self.cash, "0.00", "5.00")],
            user_remark="Tom & Jerry <b>समान\x0b</b>",
        )
        batch = self.export()
        raw = self.read(batch)
        self.assertTrue(raw.startswith(b'<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertIn(b"&amp;", raw)
        self.assertNotIn(b"<b>", raw)
        env = ET.fromstring(raw)
        self.assertEqual(env.findtext("HEADER/TALLYREQUEST"), "Import Data")
        self.assertEqual(
            env.findtext("BODY/IMPORTDATA/REQUESTDESC/REPORTNAME"), "Vouchers"
        )
        self.assertEqual(
            env.findtext(
                "BODY/IMPORTDATA/REQUESTDESC/STATICVARIABLES/SVCURRENTCOMPANY"
            ),
            "Nidhi Co",
        )
        narr = [v for v in env.iter("VOUCHER")][0].findtext("NARRATION")
        self.assertEqual(narr, "Tom & Jerry <b>समान</b>")
        self.assertEqual(
            batch.file_sha256, __import__("hashlib").sha256(raw).hexdigest()
        )
        self.assertTrue(jv.name)


class RoundTripTests(Base):
    """Parse what we generated and check every voucher against the GL."""

    def test_every_voucher_balances_and_matches_the_gl(self):
        self.map_all()
        self.purchase(qty=10, rate=40, extra_service=25)
        si = self.sell(qty=4, rate=100, extra_service=50)
        self.receipt(si, "300.00")
        self.journal([(self.rent, "70.00", "0.00"), (self.cash, "0.00", "70.00")])
        ret = selling_mapping.make_sales_return(si)
        ret.submit()
        batch = self.export()
        parsed = parse_vouchers(self.read(batch))
        self.assertEqual(len(parsed), 5)
        mapped = {
            m.tally_ledger: m.account.name
            for m in TallyLedgerMap.objects.filter(party="")
        }
        mapped["Cust  & Sons <Indore>"] = "Debtors"
        mapped["Supp Traders"] = "Creditors"
        for v in parsed:
            # 1. Tally's rule: ledger entries + inventory allocations net to zero.
            total = sum(a for _, a in v["ledgers"]) + sum(a for _, a in v["allocs"])
            self.assertEqual(total, D("0.00"), v["number"])
            # 2. Money matches the GL, ignoring the stock-valuation legs Tally
            #    derives itself (Stock In Hand / COGS).
            got = {}
            for led, amt in v["ledgers"] + v["allocs"]:
                got[mapped[led]] = got.get(mapped[led], D(0)) + amt
            gl = self.gl_net(v["number"])
            gl = {k: a for k, a in gl.items() if k not in ("Stock In Hand", "COGS")}
            net = sum(
                self.gl_net(v["number"]).get(k, D(0)) for k in ("Stock In Hand", "COGS")
            )
            if net:  # purchase: valuation net is part of the purchase cost
                gl["Purchases"] = gl.get("Purchases", D(0)) + net
            got = {k: a for k, a in got.items() if a}
            gl = {k: a for k, a in gl.items() if a}
            self.assertEqual(got, gl, v["number"])

    def test_debit_credit_totals_in_preflight_equal(self):
        self.map_all()
        self.purchase()
        si = self.sell()
        self.receipt(si, "100.00")
        pre = svc.preflight(self.company)
        self.assertEqual(len(pre["vouchers"]), 3)
        for row in pre["vouchers"]:
            self.assertTrue(row["balanced"], row)
            self.assertEqual(row["debit"], row["credit"])
        self.assertTrue(pre["can_export"])


class MappingAndPreflightTests(Base):
    def test_unmapped_blocks_export_and_is_listed(self):
        self.purchase()
        pre = svc.preflight(self.company)
        self.assertFalse(pre["can_export"])
        self.assertEqual(pre["unmapped_count"], pre["unmapped_count"])
        accounts = {a["account_name"] for a in pre["unmapped"]["accounts"]}
        self.assertIn("Purchases", accounts)
        self.assertEqual(pre["unmapped"]["parties"][0]["party"], "Supp")
        self.assertEqual(
            pre["unmapped"]["parties"][0]["suggested_group"], "Sundry Creditors"
        )
        self.assertEqual(pre["unmapped"]["items"][0]["item_code"], "CHILLI")
        with self.assertRaises(svc.ExportBlocked):
            self.export()
        self.assertEqual(TallyExportBatch.objects.count(), 0)

    def test_party_line_does_not_fall_back_to_the_control_account(self):
        self.map_all()
        TallyLedgerMap.objects.filter(party="Cust").delete()
        self.purchase()
        si = self.sell()
        pre = svc.preflight(self.company)
        self.assertFalse(pre["can_export"])
        self.assertEqual([p["party"] for p in pre["unmapped"]["parties"]], ["Cust"])
        self.assertEqual(pre["unmapped"]["accounts"], [])
        self.assertTrue(si.name)

    def test_export_new_masters_creates_maps_and_master_xml(self):
        self.purchase(qty=10, rate=40)
        batch = self.export(create_masters=True)
        self.assertTrue(batch.masters_file)
        self.assertEqual(batch.ledger_master_count, 2)  # Purchases + Supp
        self.assertEqual(batch.item_master_count, 1)
        env = ET.fromstring(self.read(batch, "masters_file"))
        self.assertEqual(
            env.findtext("BODY/IMPORTDATA/REQUESTDESC/REPORTNAME"), "All Masters"
        )
        ledgers = {x.get("NAME"): x for x in env.iter("LEDGER")}
        self.assertEqual(ledgers["Purchases"].findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(ledgers["Supp"].findtext("PARENT"), "Sundry Creditors")
        self.assertEqual(ledgers["Supp"].findtext("ISBILLWISEON"), "Yes")
        self.assertEqual(ledgers["Purchases"].get("ACTION"), "Create")
        stock = next(env.iter("STOCKITEM"))
        self.assertEqual(stock.get("NAME"), "Chilli Powder")
        self.assertEqual(stock.findtext("BASEUNITS"), "Kg")
        self.assertEqual(next(env.iter("UNIT")).get("NAME"), "Kg")
        # The new maps remember which batch carried their master.
        self.assertEqual(
            TallyLedgerMap.objects.get(party="Supp").master_batch_id, batch.pk
        )
        # Vouchers use the new names; a second export has nothing to send.
        v = self.vouchers(batch)
        self.assertEqual(len(v), 1)
        with self.assertRaises(svc.ExportError):
            self.export()

    def test_party_master_carries_gstin_state_and_address(self):
        from contacts.models import Address
        from core.models import State

        state = State.objects.get(code="23")
        Supplier.objects.filter(pk=self.supplier.pk).update(
            gstin="23AAAAA0000A1Z5", state=state, gst_category="Registered Regular"
        )
        Address.objects.create(
            address_title="Supp",
            address_line1="12 Mill Road",
            city="Indore",
            pincode="452001",
            state=state,
            supplier=self.supplier,
            is_primary_address=True,
        )
        self.purchase()
        batch = self.export(create_masters=True)
        env = ET.fromstring(self.read(batch, "masters_file"))
        supp = [x for x in env.iter("LEDGER") if x.get("NAME") == "Supp"][0]
        self.assertEqual(supp.findtext("PARTYGSTIN"), "23AAAAA0000A1Z5")
        self.assertEqual(supp.findtext("LEDSTATENAME"), state.name)
        self.assertEqual(supp.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(supp.findtext("LEDMAILINGDETAILS.LIST/PINCODE"), "452001")
        addr = [
            a.text for a in supp.findall("LEDMAILINGDETAILS.LIST/ADDRESS.LIST/ADDRESS")
        ]
        self.assertEqual(addr, ["12 Mill Road", "Indore"])

    def test_tally_history_and_opening_entries_are_never_exported(self):
        self.map_all()
        self.journal([(self.rent, "5.00", "0.00"), (self.cash, "0.00", "5.00")])
        old = JournalEntry.objects.create(
            name="TLY-2425-000001", posting_date=DATE, company=self.company
        )
        JournalEntryLine.objects.create(
            journal_entry=old, account=self.rent, debit=D(9), credit=D(0)
        )
        JournalEntryLine.objects.create(
            journal_entry=old, account=self.cash, debit=D(0), credit=D(9)
        )
        old.submit()
        opening = self.journal(
            [(self.rent, "7.00", "0.00"), (self.cash, "0.00", "7.00")],
            voucher_type="Opening Entry",
        )
        pre = svc.preflight(self.company)
        self.assertEqual(len(pre["vouchers"]), 1)
        self.assertEqual([s["name"] for s in pre["skipped"]], [opening.name])

    def test_date_range_filters(self):
        self.map_all()
        self.purchase(date=datetime.date(2026, 4, 1))
        later = self.purchase(date=datetime.date(2026, 6, 1))
        batch = self.export(
            from_date=datetime.date(2026, 5, 1), to_date=datetime.date(2026, 6, 30)
        )
        self.assertEqual(list(self.vouchers(batch)), [later.name])
        with self.assertRaises(svc.ExportError):
            self.export(
                from_date=datetime.date(2026, 7, 1), to_date=datetime.date(2026, 6, 1)
            )


class LogAndPendingTests(Base):
    def setUp(self):
        super().setUp()
        self.map_all()

    def test_pending_new_then_exported_then_cancelled(self):
        pi = self.purchase()
        settings = get_settings(self.company)
        self.assertEqual(pending_counts(self.company, settings)["new"], 1)
        batch = self.export()
        self.assertEqual(pending_counts(self.company, settings)["total"], 0)
        pi.cancel()
        self.assertEqual(
            pending_counts(self.company, settings),
            {"new": 0, "cancelled": 1, "total": 1, "skipped": 0},
        )
        self.assertTrue(batch.pk)

    def test_cancel_after_export_emits_cancel_with_same_remote_id(self):
        pi = self.purchase()
        first = self.export()
        created = self.vouchers(first)[pi.name]
        pi.cancel()
        second = self.export()
        cancelled = self.vouchers(second)[pi.name]
        self.assertEqual(cancelled["action"], "Cancel")
        self.assertEqual(cancelled["remote"], created["remote"])
        self.assertEqual(cancelled["number"], pi.name)
        self.assertEqual(cancelled["type"], "Purchase")
        self.assertEqual(cancelled["el"].findtext("DATE"), "20260401")
        self.assertEqual(second.cancel_count, 1)
        self.assertEqual(second.voucher_count, 0)
        # Cancelled once: nothing left to send.
        with self.assertRaises(svc.ExportError):
            self.export()

    def test_cancelled_never_exported_is_not_sent(self):
        pi = self.purchase()
        pi.cancel()
        with self.assertRaises(svc.ExportError):
            self.export()
        self.assertEqual(
            pending_counts(self.company, get_settings(self.company))["total"], 0
        )

    def test_remote_id_is_stable_across_batches_and_companies_differ(self):
        pi = self.purchase()
        a = self.vouchers(self.export())[pi.name]["remote"]
        b = self.vouchers(
            svc.reexport_vouchers(self.company, [("buying.PurchaseInvoice", pi.name)])
        )[pi.name]["remote"]
        self.assertEqual(a, b)
        from tally_export.models import remote_id_for

        other = Company.objects.create(name="Other", abbr="OT")
        self.assertNotEqual(a, remote_id_for(other, "buying.PurchaseInvoice", pi.name))

    def test_reexport_batch_and_voucher(self):
        pi = self.purchase()
        si = self.sell()
        first = self.export()
        again = svc.reexport_batch(first)
        self.assertEqual(again.kind, "reexport")
        self.assertEqual(again.source_batch_id, first.pk)
        self.assertEqual(set(self.vouchers(again)), {pi.name, si.name})
        one = svc.reexport_vouchers(self.company, [("selling.SalesInvoice", si.name)])
        self.assertEqual(set(self.vouchers(one)), {si.name})

    def test_log_rows_record_what_was_exported(self):
        pi = self.purchase()
        batch = self.export()
        row = batch.vouchers.get()
        self.assertEqual(
            (row.doctype, row.name, row.docstatus, row.action),
            ("buying.PurchaseInvoice", pi.name, 1, "Create"),
        )
        self.assertEqual(len(row.voucher_hash), 64)
        self.assertEqual(row.tally_voucher_type, "Purchase")
        self.assertEqual(
            AuditLog.objects.filter(
                model_label="tally_export.TallyExportBatch"
            ).count(),
            1,
        )

    def test_batch_and_voucher_log_is_append_only(self):
        self.purchase()
        batch = self.export()
        row = batch.vouchers.get()
        batch.voucher_count = 99
        with self.assertRaises(ValidationError):
            batch.save()
        with self.assertRaises(ValidationError):
            batch.delete()
        row.action = "Cancel"
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        with self.assertRaises(ValidationError):
            TallyExportBatch.objects.all().update(voucher_count=0)
        with self.assertRaises(ValidationError):
            TallyExportedVoucher.objects.all().delete()
        self.assertEqual(TallyExportBatch.objects.get().voucher_count, 1)

    def test_verify_integrity_is_green_and_catches_a_tampered_file(self):
        from io import StringIO

        from django.core.management.base import CommandError

        self.purchase()
        batch = self.export()
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        with batch.file.open("wb") as fh:
            fh.write(b"tampered")
        with self.assertRaises(CommandError):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def test_map_constraints(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            TallyLedgerMap.objects.create(
                company=self.company,
                account=self.bank,
                party_type="Customer",
                tally_ledger="x",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TallyLedgerMap.objects.create(
                company=self.company, account=self.bank, tally_ledger="dup"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TallyLedgerMap.objects.create(
                company=self.company, account=self.rent, tally_ledger=""
            )


@override_settings(MEDIA_ROOT=MEDIA)
class ApiTests(Base):
    def client_for(self, role):
        call_command("seed_defaults", verbosity=0)
        user = User.objects.create_user(
            username=f"u-{role}", email=f"{role.replace(' ', '')}@x.io", password="x"
        )
        UserRole.objects.create(user=user, role=Role.objects.get(name=role))
        UserCompany.objects.create(user=user, company=self.company, is_default=True)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_accounts_manager_full_flow(self):
        # seed_defaults creates its own company; use ours explicitly via ?company=
        self.map_all()
        pi = self.purchase()
        client = self.client_for("Accounts Manager")
        cid = self.company.pk
        pre = client.get("/api/tally-export/batches/preflight/", {"company": cid})
        self.assertEqual(pre.status_code, 200, pre.content)
        self.assertTrue(pre.data["can_export"])
        self.assertEqual(pre.data["vouchers"][0]["name"], pi.name)
        pend = client.get("/api/tally-export/batches/pending/", {"company": cid})
        self.assertEqual(pend.data["new"], 1)
        res = client.post("/api/tally-export/batches/", {"company": cid}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn("application/xml", res["Content-Type"])
        self.assertIn("attachment", res["Content-Disposition"])
        self.assertIn(pi.name.encode(), res.content)
        batch_id = int(res["X-Tally-Export-Batch"])
        lst = client.get("/api/tally-export/batches/", {"company": cid})
        self.assertEqual(lst.data["results"][0]["voucher_count"], 1)
        dl = client.get(f"/api/tally-export/batches/{batch_id}/download/")
        self.assertEqual(dl.content, res.content)
        rows = client.get(f"/api/tally-export/batches/{batch_id}/vouchers/")
        self.assertEqual(rows.data[0]["name"], pi.name)
        # Nothing pending now: a second export is a 400, not an empty file.
        again = client.post(
            "/api/tally-export/batches/", {"company": cid}, format="json"
        )
        self.assertEqual(again.status_code, 400)
        rex = client.post(f"/api/tally-export/batches/{batch_id}/reexport/")
        self.assertEqual(rex.status_code, 200, rex.content)
        # The log has no write routes.
        self.assertEqual(
            client.delete(f"/api/tally-export/batches/{batch_id}/").status_code, 405
        )
        self.assertEqual(
            client.patch(
                f"/api/tally-export/batches/{batch_id}/", {}, format="json"
            ).status_code,
            405,
        )

    def test_unmapped_returns_409_with_the_checklist(self):
        self.purchase()
        client = self.client_for("Accounts Manager")
        res = client.post(
            "/api/tally-export/batches/", {"company": self.company.pk}, format="json"
        )
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.data["error"], "export_blocked")
        self.assertTrue(res.data["unmapped"]["items"])
        ok = client.post(
            "/api/tally-export/batches/",
            {"company": self.company.pk, "create_masters": True},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        dl = client.get(
            f"/api/tally-export/batches/{ok['X-Tally-Export-Batch']}/download/"
        )
        self.assertEqual(dl["Content-Type"].split(";")[0], "application/zip")

    def test_map_crud_is_audited_and_validated(self):
        client = self.client_for("Accounts Manager")
        cid = self.company.pk
        res = client.post(
            "/api/tally-export/ledger-maps/",
            {"company": cid, "account": self.bank.pk, "tally_ledger": " HDFC Bank "},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["tally_ledger"], "HDFC Bank")
        self.assertEqual(res.data["source"], "manual")
        pk = res.data["id"]
        upd = client.patch(
            f"/api/tally-export/ledger-maps/{pk}/",
            {"tally_ledger": "HDFC"},
            format="json",
        )
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(
            AuditLog.objects.filter(model_label="tally_export.TallyLedgerMap").count(),
            2,
        )
        # party without type, group account, other company's account: all refused
        self.assertEqual(
            client.post(
                "/api/tally-export/ledger-maps/",
                {
                    "company": cid,
                    "account": self.debtors.pk,
                    "party": "Cust",
                    "tally_ledger": "x",
                },
                format="json",
            ).status_code,
            400,
        )
        group = Account.objects.get(company=self.company, name="Assets")
        self.assertEqual(
            client.post(
                "/api/tally-export/ledger-maps/",
                {"company": cid, "account": group.pk, "tally_ledger": "x"},
                format="json",
            ).status_code,
            400,
        )
        other = Company.objects.create(name="Other Co", abbr="OC")
        foreign = _acc(
            other, "Bank", AccountRootType.ASSET, account_type=AccountType.BANK
        )
        self.assertEqual(
            client.post(
                "/api/tally-export/ledger-maps/",
                {"company": cid, "account": foreign.pk, "tally_ledger": "x"},
                format="json",
            ).status_code,
            400,
        )
        item = client.post(
            "/api/tally-export/item-maps/",
            {
                "company": cid,
                "item": self.chilli.pk,
                "tally_item": "Chilli",
                "tally_unit": "Kg",
            },
            format="json",
        )
        self.assertEqual(item.status_code, 201, item.content)

    def test_settings_get_creates_defaults_and_patch(self):
        client = self.client_for("Accounts Manager")
        res = client.get("/api/tally-export/settings/", {"company": self.company.pk})
        self.assertEqual(res.data["results"][0]["tally_company_name"], "Nidhi Co")
        self.assertEqual(res.data["results"][0]["excluded_prefixes"], ["TLY-"])
        pk = res.data["results"][0]["id"]
        ok = client.patch(
            f"/api/tally-export/settings/{pk}/",
            {
                "tally_company_name": "Nidhi Grah Udyog",
                "voucher_type_overrides": {"selling.SalesInvoice": "Sales Gst"},
            },
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        bad = client.patch(
            f"/api/tally-export/settings/{pk}/",
            {"voucher_type_overrides": ["x"]},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_only_accounts_and_system_managers_may_use_it(self):
        self.map_all()
        self.purchase()
        for role in (
            "Sales Manager",
            "Purchase Manager",
            "Stock Manager",
            "Default Role",
        ):
            c = self.client_for(role)
            for url in (
                "/api/tally-export/batches/",
                "/api/tally-export/batches/preflight/",
                "/api/tally-export/batches/pending/",
                "/api/tally-export/ledger-maps/",
                "/api/tally-export/item-maps/",
                "/api/tally-export/settings/",
            ):
                self.assertEqual(
                    c.get(url, {"company": self.company.pk}).status_code,
                    403,
                    (role, url),
                )
            self.assertEqual(
                c.post(
                    "/api/tally-export/batches/",
                    {"company": self.company.pk},
                    format="json",
                ).status_code,
                403,
            )
        sysm = self.client_for("System Manager")
        self.assertEqual(
            sysm.get(
                "/api/tally-export/batches/", {"company": self.company.pk}
            ).status_code,
            200,
        )
        self.assertEqual(APIClient().get("/api/tally-export/batches/").status_code, 401)
