"""Tally history import (planning/11-tally-history-import.md).

A small synthetic history file, one voucher of each kind plus the ones that must be
skipped, against submitted TLY-* journals. Checks: each voucher type becomes the
right historical document, totals tie to the journal to the paisa, nothing is
posted (GL / SLE / Bin unchanged, verify_integrity green), re-runs create nothing,
non-tying / unmatched vouchers are skipped with a reason, nothing is invented.
"""

import gzip
import json
import os
import tempfile
from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.management.commands.export_tally_history import (
    JournalIndex,
    write_history,
)
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    InvoiceStatus,
    JournalEntry,
    PaymentEntry,
    PaymentEntryReference,
)
from accounts.services import tally_history as svc
from buying.models import PurchaseInvoice, Supplier, SupplierGroup
from core.models import Company, DocStatus
from selling.models import Customer, CustomerGroup, SalesInvoice
from stock.models import UOM, Bin, Item, ItemGroup, StockLedgerEntry
from tally_export.models import TallyStockItemMap


def _snapshot():
    return (
        GLEntry.objects.count(),
        StockLedgerEntry.objects.count(),
        Bin.objects.count(),
        svc.books_fingerprint(),
        Customer.objects.count(),
        Supplier.objects.count(),
        Item.objects.count(),
    )


class TallyHistoryImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Nidhi Test", abbr="NT", default_currency="INR"
        )

        def acc(name, root, parent=None, **kw):
            return Account.objects.create(
                name=name, company=cls.company, root_type=root, parent=parent, **kw
            )

        assets = acc("Assets", AccountRootType.ASSET, is_group=True)
        liab = acc("Liabilities", AccountRootType.LIABILITY, is_group=True)
        income = acc("Income", AccountRootType.INCOME, is_group=True)
        expense = acc("Expenses", AccountRootType.EXPENSE, is_group=True)
        cls.debtors = acc(
            "Sundry Debtors",
            AccountRootType.ASSET,
            assets,
            account_type=AccountType.RECEIVABLE,
        )
        cls.creditors = acc(
            "Sundry Creditors",
            AccountRootType.LIABILITY,
            liab,
            account_type=AccountType.PAYABLE,
        )
        cls.bank = acc(
            "Bank of India",
            AccountRootType.ASSET,
            assets,
            account_type=AccountType.BANK,
        )
        cls.cash = acc(
            "Cash", AccountRootType.ASSET, assets, account_type=AccountType.CASH
        )
        cls.cgst = acc(
            "C Gst", AccountRootType.LIABILITY, liab, account_type=AccountType.TAX
        )
        cls.sgst = acc(
            "S Gst", AccountRootType.LIABILITY, liab, account_type=AccountType.TAX
        )
        cls.sales = acc("Sales Gst", AccountRootType.INCOME, income)
        cls.purchase = acc("Purchase Gst", AccountRootType.EXPENSE, expense)
        cls.freight = acc("Freight Outward", AccountRootType.EXPENSE, expense)
        cls.round_off = acc(
            "Round Off",
            AccountRootType.EXPENSE,
            expense,
            account_type=AccountType.ROUND_OFF,
        )

        group, uom = ItemGroup.objects.create(name="Masala"), UOM.objects.create(
            name="Kg"
        )
        cls.mirchi = Item.objects.create(
            code="MIRCHI", name="Mirchi Powder", item_group=group, stock_uom=uom
        )
        cls.haldi = Item.objects.create(
            code="HALDI", name="Haldi", item_group=group, stock_uom=uom
        )
        cls.freight_item = Item.objects.create(
            code="TALLY-LINE",
            name="Tally line",
            item_group=group,
            stock_uom=uom,
            is_stock_item=False,
        )
        TallyStockItemMap.objects.create(
            company=cls.company, item=cls.mirchi, tally_item="Lal Mirchi Powder"
        )

        cls.ram = Customer.objects.create(
            name="Ram Traders",
            company=cls.company,
            customer_group=CustomerGroup.objects.create(name="Retail"),
        )
        cls.shyam = Supplier.objects.create(
            name="Shyam Spices",
            company=cls.company,
            supplier_group=SupplierGroup.objects.create(name="Raw"),
        )

    # ------------------------------------------------------------------ fixture

    def journal(self, seq, vtype, no, date, lines):
        je = JournalEntry.objects.create(
            name=f"TLY-2425-{seq:06d}",
            company=self.company,
            posting_date=date,
            user_remark=f"Tally {vtype} No. {no} | test",
        )
        for account, dr, cr, *party in lines:
            je.lines.create(
                account=account,
                debit=D(dr),
                credit=D(cr),
                party_type=party[0] if party else None,
                party=party[1] if party else None,
            )
        je.submit()
        return je

    def rec(self, je, vtype, no, date, party, party_amount, items=(), reference=""):
        return {
            "fy": "2024-25",
            "vchtype": vtype,
            "vouchernumber": no,
            "reference": reference,
            "date": date,
            "party": party,
            "journal_name": je.name if je else None,
            "match_error": (
                None if je else "no TLY journal with this type, number and date"
            ),
            "party_amount": party_amount,
            "inventory_entries": [
                {
                    "stockitemname": n,
                    "billedqty": q,
                    "actualqty": q,
                    "rate": r,
                    "rate_unit": "Kg",
                    "amount": a,
                }
                for n, q, r, a in items
            ],
            "ledger_entries": [],
        }

    def setUp(self):
        ram, shyam = ("Customer", "Ram Traders"), ("Supplier", "Shyam Spices")
        j = self.journal
        self.j_sale = j(
            1,
            "Sales Gst",
            "1",
            "2024-05-01",
            [
                (self.debtors, "1050.00", "0", *ram),
                (self.sales, "0", "1000.00"),
                (self.cgst, "0", "25.00"),
                (self.sgst, "0", "25.00"),
            ],
        )
        self.j_sale_round = j(
            2,
            "Sales Gst",
            "2",
            "2024-05-03",
            [
                (self.debtors, "105.00", "0", *ram),
                (self.sales, "0", "99.80"),
                (self.cgst, "0", "2.50"),
                (self.sgst, "0", "2.50"),
                (self.round_off, "0", "0.20"),
            ],
        )
        self.j_credit = j(
            3,
            "Credit Note",
            "1",
            "2024-05-10",
            [
                (self.sales, "100.00", "0"),
                (self.cgst, "2.50", "0"),
                (self.sgst, "2.50", "0"),
                (self.debtors, "0", "105.00", *ram),
            ],
        )
        self.j_purchase = j(
            4,
            "Purchase Gst",
            "1",
            "2024-05-02",
            [
                (self.purchase, "2000.00", "0"),
                (self.cgst, "50.00", "0"),
                (self.sgst, "50.00", "0"),
                (self.creditors, "0", "2100.00", *shyam),
            ],
        )
        self.j_debit = j(
            5,
            "Debit Note",
            "1",
            "2024-05-12",
            [
                (self.creditors, "210.00", "0", *shyam),
                (self.purchase, "0", "200.00"),
                (self.cgst, "0", "5.00"),
                (self.sgst, "0", "5.00"),
            ],
        )
        self.j_receipt = j(
            6,
            "Receipt",
            "1",
            "2024-05-20",
            [
                (self.bank, "1000.00", "0"),
                (self.debtors, "0", "1000.00", *ram),
            ],
        )
        self.j_payment = j(
            7,
            "Payment",
            "1",
            "2024-05-21",
            [
                (self.creditors, "2100.00", "0", *shyam),
                (self.cash, "0", "2100.00"),
            ],
        )
        self.j_contra = j(
            8,
            "Contra",
            "1",
            "2024-05-22",
            [(self.bank, "500.00", "0"), (self.cash, "0", "500.00")],
        )
        self.j_not_tying = j(
            9,
            "Sales Gst",
            "3",
            "2024-05-23",
            [
                (self.debtors, "500.00", "0", *ram),
                (self.sales, "0", "450.00"),
                (self.freight, "0", "50.00"),
            ],
        )
        self.j_unknown_item = j(
            10,
            "Sales Gst",
            "4",
            "2024-05-24",
            [
                (self.debtors, "300.00", "0", *ram),
                (self.sales, "0", "300.00"),
            ],
        )
        self.j_no_items = j(
            11,
            "Purchase Gst",
            "2",
            "2024-05-25",
            [
                (self.freight, "700.00", "0"),
                (self.creditors, "0", "700.00", *shyam),
            ],
        )
        self.j_cash_sale = j(
            12,
            "Sales Gst",
            "5",
            "2024-05-26",
            [(self.cash, "80.00", "0"), (self.sales, "0", "80.00")],
        )

        r = self.rec
        self.records = [
            r(
                self.j_sale,
                "Sales Gst",
                "1",
                "2024-05-01",
                "Ram Traders",
                "1050.00",
                [
                    ("Lal Mirchi Powder", "10", "60", "600.00"),
                    ("Haldi", "5", "80", "400.00"),
                ],
            ),
            r(
                self.j_sale_round,
                "Sales Gst",
                "2",
                "2024-05-03",
                "Ram Traders",
                "105.00",
                [("Haldi", "1.2475", "80", "99.80")],
            ),
            r(
                self.j_credit,
                "Credit Note",
                "1",
                "2024-05-10",
                "Ram Traders",
                "105.00",
                [("Haldi", "1.25", "80", "100.00")],
            ),
            r(
                self.j_purchase,
                "Purchase Gst",
                "1",
                "2024-05-02",
                "Shyam Spices",
                "2100.00",
                [("Lal Mirchi Powder", "-40", "50", "-2000.00")],
                reference="SS/101",
            ),
            r(
                self.j_debit,
                "Debit Note",
                "1",
                "2024-05-12",
                "Shyam Spices",
                "210.00",
                [("Lal Mirchi Powder", "4", "50", "200.00")],
            ),
            r(self.j_receipt, "Receipt", "1", "2024-05-20", "Ram Traders", "1000.00"),
            r(self.j_payment, "Payment", "1", "2024-05-21", "Shyam Spices", "2100.00"),
            r(self.j_contra, "Contra", "1", "2024-05-22", "", None),
            r(
                self.j_not_tying,
                "Sales Gst",
                "3",
                "2024-05-23",
                "Ram Traders",
                "500.00",
                [("Haldi", "5", "90", "450.00")],
            ),
            r(
                self.j_unknown_item,
                "Sales Gst",
                "4",
                "2024-05-24",
                "Ram Traders",
                "300.00",
                [("Kesar", "1", "300", "300.00")],
            ),
            r(
                self.j_no_items,
                "Purchase Gst",
                "2",
                "2024-05-25",
                "Shyam Spices",
                "700.00",
            ),
            r(
                self.j_cash_sale,
                "Sales Gst",
                "5",
                "2024-05-26",
                "Cash",
                None,
                [("Haldi", "1", "80", "80.00")],
            ),
            r(
                None,
                "Sales Gst",
                "99",
                "2024-05-27",
                "Ram Traders",
                "10.00",
                [("Haldi", "1", "10", "10.00")],
            ),
        ]
        fd, self.path = tempfile.mkstemp(suffix=".jsonl.gz")
        os.close(fd)
        with gzip.open(self.path, "wt", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(rec) + "\n")

    def tearDown(self):
        os.remove(self.path)

    def run_cmd(self, *args):
        out = StringIO()
        call_command(
            "import_tally_history",
            "--source",
            self.path,
            "--company",
            self.company.name,
            *args,
            stdout=out,
        )
        return out.getvalue()

    def commit(self, **kw):
        return svc.run_import(self.path, self.company, commit=True, **kw)

    # ------------------------------------------------------------------ tests

    def test_dry_run_is_the_default_and_saves_nothing(self):
        before = _snapshot()
        out = self.run_cmd()
        self.assertIn("DRY RUN", out)
        self.assertIn("item lines do not tie to the journal", out)
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.assertEqual(PurchaseInvoice.objects.count(), 0)
        self.assertEqual(PaymentEntry.objects.count(), 0)
        self.assertEqual(_snapshot(), before)

    def test_each_voucher_type_becomes_its_historical_document(self):
        self.run_cmd("--commit")

        si = SalesInvoice.objects.get(name=self.j_sale.name)
        self.assertEqual(
            (si.is_historical, si.docstatus, si.status),
            (True, DocStatus.SUBMITTED, InvoiceStatus.HISTORICAL),
        )
        self.assertEqual(si.source_journal_id, self.j_sale.pk)
        self.assertEqual(si.customer, self.ram)
        self.assertEqual(si.debit_to, self.debtors)
        self.assertEqual(si.income_account, self.sales)
        self.assertEqual(
            (si.net_total, si.total_taxes_and_charges, si.grand_total),
            (D("1000.00"), D("50.00"), D("1050.00")),
        )
        self.assertEqual(
            (si.rounded_total, si.outstanding_amount, si.is_return),
            (D("1050.00"), D("0.00"), False),
        )
        self.assertEqual(
            sorted((i.item.code, i.qty, i.rate, i.amount) for i in si.items.all()),
            [
                ("HALDI", D("5.000"), D("80.00"), D("400.00")),
                ("MIRCHI", D("10.000"), D("60.00"), D("600.00")),
            ],
        )
        self.assertEqual(
            sorted((t.account.name, t.tax_amount) for t in si.taxes.all()),
            [("C Gst", D("25.00")), ("S Gst", D("25.00"))],
        )

        rounded = SalesInvoice.objects.get(name=self.j_sale_round.name)
        self.assertEqual(
            (
                rounded.net_total,
                rounded.grand_total,
                rounded.rounding_adjustment,
                rounded.rounded_total,
            ),
            (D("99.80"), D("104.80"), D("0.20"), D("105.00")),
        )

        cn = SalesInvoice.objects.get(name=self.j_credit.name)
        self.assertTrue(cn.is_return)
        self.assertEqual(
            (cn.net_total, cn.grand_total, cn.rounded_total),
            (D("-100.00"), D("-105.00"), D("-105.00")),
        )
        self.assertEqual(cn.items.get().qty, D("-1.250"))

        pi = PurchaseInvoice.objects.get(name=self.j_purchase.name)
        self.assertEqual(
            (pi.supplier, pi.bill_no, pi.credit_to, pi.expense_account),
            (self.shyam, "SS/101", self.creditors, self.purchase),
        )
        self.assertEqual(
            (pi.grand_total, pi.rounded_total, pi.status),
            (D("2100.00"), D("2100.00"), InvoiceStatus.HISTORICAL),
        )
        self.assertEqual(pi.items.get().qty, D("40.000"))

        dn = PurchaseInvoice.objects.get(name=self.j_debit.name)
        self.assertTrue(dn.is_return)
        self.assertEqual(dn.rounded_total, D("-210.00"))

        rec = PaymentEntry.objects.get(name=self.j_receipt.name)
        self.assertEqual(
            (
                rec.payment_type,
                rec.party_type,
                rec.party_name,
                rec.paid_from,
                rec.paid_to,
                rec.paid_amount,
            ),
            (
                "Receive",
                "Customer",
                "Ram Traders",
                self.debtors,
                self.bank,
                D("1000.00"),
            ),
        )
        self.assertTrue(rec.is_historical)
        self.assertEqual(rec.source_journal_id, self.j_receipt.pk)
        pay = PaymentEntry.objects.get(name=self.j_payment.name)
        self.assertEqual(
            (pay.payment_type, pay.paid_from, pay.paid_to),
            ("Pay", self.cash, self.creditors),
        )

        # Contra stays a journal only.
        self.assertFalse(PaymentEntry.objects.filter(name=self.j_contra.name).exists())

    def test_books_do_not_change_and_integrity_stays_green(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        before = (
            GLEntry.objects.count(),
            StockLedgerEntry.objects.count(),
            Bin.objects.count(),
            svc.books_fingerprint(),
        )
        self.commit()
        after = (
            GLEntry.objects.count(),
            StockLedgerEntry.objects.count(),
            Bin.objects.count(),
            svc.books_fingerprint(),
        )
        self.assertEqual(after, before)
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def test_rerun_creates_nothing(self):
        first = self.commit()
        created = sum(c["created"] for c in first.counts.values())
        self.assertEqual(created, 7)
        refs = PaymentEntryReference.objects.count()
        second = self.commit()
        self.assertEqual(sum(c["created"] for c in second.counts.values()), 0)
        self.assertEqual(sum(c["already"] for c in second.counts.values()), 7)
        self.assertEqual(
            SalesInvoice.objects.count()
            + PurchaseInvoice.objects.count()
            + PaymentEntry.objects.count(),
            7,
        )
        self.assertEqual(PaymentEntryReference.objects.count(), refs)

    def test_vouchers_that_do_not_tie_are_skipped_never_forced(self):
        before = _snapshot()[4:]
        result = self.commit()
        self.assertEqual(result.reasons["item lines do not tie to the journal"], 1)
        self.assertEqual(result.reasons["unmatched Tally item"], 1)
        self.assertEqual(result.unmatched_items, {"Kesar": 1})
        self.assertEqual(result.reasons["voucher has no item lines"], 1)
        self.assertEqual(
            result.reasons["journal has no party line (e.g. a cash sale)"], 1
        )
        self.assertEqual(result.reasons["no TLY journal matched in the export"], 1)
        for je in (
            self.j_not_tying,
            self.j_unknown_item,
            self.j_no_items,
            self.j_cash_sale,
        ):
            self.assertFalse(SalesInvoice.objects.filter(name=je.name).exists())
            self.assertFalse(PurchaseInvoice.objects.filter(name=je.name).exists())
        # No customer, supplier or item was invented.
        self.assertEqual(_snapshot()[4:], before)

    def test_tally_party_amount_must_match_the_journal(self):
        self.records = [dict(self.records[0], party_amount="1049.00")]
        with gzip.open(self.path, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(self.records[0]) + "\n")
        result = self.commit()
        self.assertEqual(
            result.reasons["Tally party amount differs from the journal"], 1
        )
        self.assertEqual(SalesInvoice.objects.count(), 0)

    def test_payments_link_invoices_oldest_first_for_display_only(self):
        self.commit()
        refs = {
            (
                r.payment_entry.name,
                r.reference_doctype,
                r.reference_name,
            ): r.allocated_amount
            for r in PaymentEntryReference.objects.select_related("payment_entry")
        }
        self.assertEqual(
            refs,
            {
                (self.j_receipt.name, "selling.SalesInvoice", self.j_sale.name): D(
                    "1000.00"
                ),
                (
                    self.j_payment.name,
                    "buying.PurchaseInvoice",
                    self.j_purchase.name,
                ): D("2100.00"),
            },
        )
        # Outstanding stays 0 on every historical invoice, also after a rebuild.
        from accounts.services import outstanding

        for inv in [*SalesInvoice.objects.all(), *PurchaseInvoice.objects.all()]:
            self.assertEqual(
                outstanding.derive(inv), (D("0.00"), InvoiceStatus.HISTORICAL)
            )

    def test_placeholder_item_only_when_it_already_exists(self):
        result = self.commit(placeholder_item=self.freight_item)
        pi = PurchaseInvoice.objects.get(name=self.j_no_items.name)
        line = pi.items.get()
        self.assertEqual((line.item, line.amount), (self.freight_item, D("700.00")))
        self.assertEqual(result.reasons["voucher has no item lines"], 0)
        out = StringIO()
        with self.assertRaisesMessage(Exception, "does not exist"):
            call_command(
                "import_tally_history",
                "--source",
                self.path,
                "--company",
                self.company.name,
                "--placeholder-item",
                "NOPE",
                stdout=out,
            )

    def test_year_filter_and_party_report(self):
        result = svc.run_import(self.path, self.company, year="2023-24")
        self.assertEqual(sum(c["tally"] for c in result.counts.values()), 0)
        result = svc.run_import(self.path, self.company)
        ram = next(r for r in result.party_rows if r["party"] == "Ram Traders")
        # As of the last created document (21 May): sales 1050 + 105 - credit note
        # 105 - receipt 1000 = 50, the same as the ledger. The skipped vouchers are
        # later, so they are not in either side yet.
        self.assertEqual(ram["as_of"], "2024-05-21")
        self.assertEqual(ram["documents_net"], D("50.00"))
        self.assertEqual(ram["ledger_balance"], D("50.00"))
        self.assertEqual(ram["difference"], D("0.00"))
        shyam = next(r for r in result.party_rows if r["party"] == "Shyam Spices")
        self.assertEqual(
            (shyam["documents_net"], shyam["ledger_balance"]),
            (D("-210.00"), D("-210.00")),
        )


class ExportMatchingTests(TestCase):
    """export_tally_history: voucher -> TLY journal by FY, type, number and date."""

    def setUp(self):
        self.company = Company.objects.create(
            name="Match Co", abbr="MC", default_currency="INR"
        )
        mk = lambda name, remark, date, total: JournalEntry.objects.create(
            name=name,
            company=self.company,
            posting_date=date,
            user_remark=remark,
            total_debit=D(total),
            total_credit=D(total),
            docstatus=DocStatus.SUBMITTED,
        )
        mk("TLY-2425-000001", "Tally Sales Gst No. 7 | x", "2024-06-01", "100.00")
        mk("TLY-2425-000002", "Tally Receipt No.  | x", "2024-06-02", "50.00")
        mk("TLY-2425-000003", "Tally Receipt No.  | y", "2024-06-02", "75.00")
        mk("TLY-2324-000001", "Tally Sales Gst No. 7 | z", "2024-06-01", "100.00")

    def records(self):
        def r(vtype, no, date, amount, fy="2024-25"):
            return {
                "fy": fy,
                "vchtype": vtype,
                "vouchernumber": no,
                "date": date,
                "party": "P",
                "ledger_entries": [
                    {"ledgername": "P", "amount": f"-{amount}"},
                    {"ledgername": "X", "amount": amount},
                ],
                "inventory_entries": [],
            }

        return [
            r("Sales Gst", "7", "2024-06-01", "100.00"),
            r("Receipt", "", "2024-06-02", "75.00"),
            r("Receipt", "", "2024-06-02", "60.00"),
            r("Payment", "1", "2024-06-03", "10.00"),
        ]

    def test_matches_by_year_type_number_date_then_total(self):
        index = JournalIndex(self.company)
        fd, path = tempfile.mkstemp(suffix=".jsonl.gz")
        os.close(fd)
        try:
            stats = write_history(self.records(), index, path)
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh]
        finally:
            os.remove(path)
        self.assertEqual(
            [r["journal_name"] for r in rows],
            ["TLY-2425-000001", "TLY-2425-000003", None, None],
        )
        self.assertIn("share this type, number and date", rows[2]["match_error"])
        self.assertEqual(
            rows[3]["match_error"], "no TLY journal with this type, number and date"
        )
        self.assertEqual(rows[0]["party_amount"], "100.00")
        self.assertEqual((stats["matched"], stats["unmatched"]), (2, 2))
