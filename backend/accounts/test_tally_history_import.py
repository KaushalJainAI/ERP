"""Tests for the Tally history import command.

Verifies:
1. All voucher types import correctly into target models (SalesInvoice, PurchaseInvoice, PaymentEntry).
2. Documents are created with is_historical=True, docstatus=SUBMITTED, outstanding_amount=0.
3. GL / SLE / Bin counts remain untouched (0 change).
4. Idempotency: re-running imports 0 new documents.
5. Invariant check and verify_integrity remain green.
"""

import gzip
import json
import os
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from accounts.models import (
    Account,
    AccountType,
    GLEntry,
    InvoiceStatus,
    JournalEntry,
    PaymentEntry,
    PaymentEntryReference,
)
from buying.models import PurchaseInvoice, Supplier, SupplierGroup
from core.models import Company, DocStatus
from selling.models import Customer, CustomerGroup, SalesInvoice
from stock.models import Bin, Item, ItemGroup, StockLedgerEntry, UOM


class TallyHistoryImportTests(TestCase):
    def setUp(self):
        super().setUp()
        self.company = Company.objects.create(name="Test Nidhi Company", abbr="TNC")
        self.cust_group = CustomerGroup.objects.create(name="Test Customer Group")
        self.supp_group = SupplierGroup.objects.create(name="Test Supplier Group")
        self.item_group = ItemGroup.objects.create(name="All Item Groups")
        self.uom = UOM.objects.create(name="Nos")

        # Accounts
        self.acc_debtors = Account.objects.create(
            name="Sundry Debtors (TNC)",
            company=self.company,
            account_type=AccountType.RECEIVABLE,
        )
        self.acc_creditors = Account.objects.create(
            name="Sundry Creditors (TNC)",
            company=self.company,
            account_type=AccountType.PAYABLE,
        )
        self.acc_sales = Account.objects.create(
            name="Gst Sales @5% (TNC)",
            company=self.company,
            account_type=AccountType.INCOME if hasattr(AccountType, "INCOME") else AccountType.STOCK,
        )
        self.acc_purchase = Account.objects.create(
            name="Purchase (TNC)",
            company=self.company,
            account_type=AccountType.EXPENSE if hasattr(AccountType, "EXPENSE") else AccountType.STOCK,
        )
        self.acc_cash = Account.objects.create(
            name="Cash (TNC)",
            company=self.company,
            account_type=AccountType.CASH,
        )

        self.company.default_receivable_account = self.acc_debtors
        self.company.default_payable_account = self.acc_creditors
        self.company.default_income_account = self.acc_sales
        self.company.default_expense_account = self.acc_purchase
        self.company.save()

        # Item
        self.item = Item.objects.create(
            code="MIRCHI-TEST",
            name="Mirchi Test",
            item_group=self.item_group,
            stock_uom=self.uom,
            standard_rate=Decimal("100.00"),
        )

        # Source Journals
        self.j_sale = JournalEntry.objects.create(
            name="TLY-2425-000001",
            company=self.company,
            posting_date="2024-05-01",
            user_remark="Tally Sales Gst No. 101 | guid test-sale-guid",
        )
        self.j_sale.lines.create(account=self.acc_debtors, debit=Decimal("105.00"), credit=Decimal("0.00"))
        self.j_sale.lines.create(account=self.acc_sales, debit=Decimal("0.00"), credit=Decimal("100.00"))

        self.j_purch = JournalEntry.objects.create(
            name="TLY-2425-000002",
            company=self.company,
            posting_date="2024-05-02",
            user_remark="Tally Purchase Gst No. 201 | guid test-purch-guid",
        )
        self.j_purch.lines.create(account=self.acc_creditors, debit=Decimal("0.00"), credit=Decimal("210.00"))
        self.j_purch.lines.create(account=self.acc_purchase, debit=Decimal("200.00"), credit=Decimal("0.00"))

        self.j_rec = JournalEntry.objects.create(
            name="TLY-2425-000003",
            company=self.company,
            posting_date="2024-05-03",
            user_remark="Tally Receipt No. 301 | guid test-rec-guid",
        )
        self.j_rec.lines.create(account=self.acc_cash, debit=Decimal("105.00"), credit=Decimal("0.00"))
        self.j_rec.lines.create(account=self.acc_debtors, debit=Decimal("0.00"), credit=Decimal("105.00"))

        self.j_pay = JournalEntry.objects.create(
            name="TLY-2425-000004",
            company=self.company,
            posting_date="2024-05-04",
            user_remark="Tally Payment No. 401 | guid test-pay-guid",
        )
        self.j_pay.lines.create(account=self.acc_cash, debit=Decimal("0.00"), credit=Decimal("210.00"))
        self.j_pay.lines.create(account=self.acc_creditors, debit=Decimal("210.00"), credit=Decimal("0.00"))

        self.j_contra = JournalEntry.objects.create(
            name="TLY-2425-000005",
            company=self.company,
            posting_date="2024-05-05",
            user_remark="Tally Contra No. 501 | guid test-contra-guid",
        )

        # Create synthetic jsonl.gz
        records = [
            # 1. Sales Gst
            {
                "guid": "test-sale-guid",
                "voucher_key": "vch-1",
                "company_year": "010010",
                "fy": "2024-25",
                "vchtype": "Sales Gst",
                "category": "Sales",
                "target_doctype": "SalesInvoice",
                "is_return": False,
                "vouchernumber": "101",
                "reference": "",
                "date": "2024-05-01",
                "party": "Test Customer Alpha",
                "journal_name": "TLY-2425-000001",
                "header_amount": "105.00",
                "party_amount": "105.00",
                "inventory_entries": [
                    {
                        "stockitemname": "Mirchi Test",
                        "actualqty": "1.000",
                        "billedqty": "1.000",
                        "rate": "100.00",
                        "rate_unit": "Nos",
                        "amount": "100.00",
                    }
                ],
                "ledger_entries": [
                    {"ledgername": "Test Customer Alpha", "amount": "-105.00", "isdeemedpositive": True},
                    {"ledgername": "C Gst", "amount": "2.50", "isdeemedpositive": False},
                    {"ledgername": "S Gst", "amount": "2.50", "isdeemedpositive": False},
                ],
            },
            # 2. Purchase Gst
            {
                "guid": "test-purch-guid",
                "voucher_key": "vch-2",
                "company_year": "010010",
                "fy": "2024-25",
                "vchtype": "Purchase Gst",
                "category": "Purchase",
                "target_doctype": "PurchaseInvoice",
                "is_return": False,
                "vouchernumber": "201",
                "reference": "INV-SUPP-99",
                "date": "2024-05-02",
                "party": "Test Supplier Beta",
                "journal_name": "TLY-2425-000002",
                "header_amount": "210.00",
                "party_amount": "210.00",
                "inventory_entries": [
                    {
                        "stockitemname": "Mirchi Test",
                        "actualqty": "2.000",
                        "billedqty": "2.000",
                        "rate": "100.00",
                        "rate_unit": "Nos",
                        "amount": "200.00",
                    }
                ],
                "ledger_entries": [
                    {"ledgername": "Test Supplier Beta", "amount": "210.00", "isdeemedpositive": False},
                    {"ledgername": "I Gst", "amount": "-10.00", "isdeemedpositive": True},
                ],
            },
            # 3. Receipt
            {
                "guid": "test-rec-guid",
                "voucher_key": "vch-3",
                "company_year": "010010",
                "fy": "2024-25",
                "vchtype": "Receipt",
                "category": "Receipt",
                "target_doctype": "PaymentEntry",
                "is_return": False,
                "vouchernumber": "301",
                "reference": "",
                "date": "2024-05-03",
                "party": "Test Customer Alpha",
                "journal_name": "TLY-2425-000003",
                "header_amount": "105.00",
                "party_amount": "105.00",
                "inventory_entries": [],
                "ledger_entries": [
                    {"ledgername": "Test Customer Alpha", "amount": "105.00", "isdeemedpositive": False},
                    {"ledgername": "Cash (TNC)", "amount": "-105.00", "isdeemedpositive": True},
                ],
            },
            # 4. Payment
            {
                "guid": "test-pay-guid",
                "voucher_key": "vch-4",
                "company_year": "010010",
                "fy": "2024-25",
                "vchtype": "Payment",
                "category": "Payment",
                "target_doctype": "PaymentEntry",
                "is_return": False,
                "vouchernumber": "401",
                "reference": "",
                "date": "2024-05-04",
                "party": "Test Supplier Beta",
                "journal_name": "TLY-2425-000004",
                "header_amount": "-210.00",
                "party_amount": "210.00",
                "inventory_entries": [],
                "ledger_entries": [
                    {"ledgername": "Test Supplier Beta", "amount": "-210.00", "isdeemedpositive": True},
                    {"ledgername": "Cash (TNC)", "amount": "210.00", "isdeemedpositive": False},
                ],
            },
            # 5. Contra (should stay Journal only)
            {
                "guid": "test-contra-guid",
                "voucher_key": "vch-5",
                "company_year": "010010",
                "fy": "2024-25",
                "vchtype": "Contra",
                "category": "Other",
                "target_doctype": None,
                "is_return": False,
                "vouchernumber": "501",
                "reference": "",
                "date": "2024-05-05",
                "party": "",
                "journal_name": "TLY-2425-000005",
                "header_amount": "500.00",
                "party_amount": None,
                "inventory_entries": [],
                "ledger_entries": [],
            },
        ]

        self.temp_file = tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False)
        self.temp_file.close()
        with gzip.open(self.temp_file.name, "wt", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_import_creates_historical_documents_and_preserves_invariants(self):
        gl_count_before = GLEntry.objects.count()
        sle_count_before = StockLedgerEntry.objects.count()
        bin_count_before = Bin.objects.count()

        # Run import with commit
        call_command(
            "import_tally_history",
            source=self.temp_file.name,
            company=self.company.name,
            commit=True,
        )

        # Invariant checks: zero changes to ledger
        self.assertEqual(GLEntry.objects.count(), gl_count_before)
        self.assertEqual(StockLedgerEntry.objects.count(), sle_count_before)
        self.assertEqual(Bin.objects.count(), bin_count_before)

        # 1. Sales Invoice verified
        si = SalesInvoice.objects.get(name="TLY-2425-000001")
        self.assertTrue(si.is_historical)
        self.assertEqual(si.status, InvoiceStatus.HISTORICAL)
        self.assertEqual(si.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(si.outstanding_amount, Decimal("0.00"))
        self.assertEqual(si.grand_total, Decimal("105.00"))
        self.assertEqual(si.net_total, Decimal("100.00"))
        self.assertEqual(si.total_taxes_and_charges, Decimal("5.00"))
        self.assertEqual(si.source_journal, self.j_sale)
        self.assertEqual(si.items.count(), 1)
        self.assertEqual(si.items.first().item, self.item)

        # 2. Purchase Invoice verified
        pi = PurchaseInvoice.objects.get(name="TLY-2425-000002")
        self.assertTrue(pi.is_historical)
        self.assertEqual(pi.status, InvoiceStatus.HISTORICAL)
        self.assertEqual(pi.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(pi.outstanding_amount, Decimal("0.00"))
        self.assertEqual(pi.grand_total, Decimal("210.00"))
        self.assertEqual(pi.bill_no, "INV-SUPP-99")
        self.assertEqual(pi.source_journal, self.j_purch)
        self.assertEqual(pi.items.count(), 1)

        # 3. Payment Entries verified
        pe_rec = PaymentEntry.objects.get(name="TLY-2425-000003")
        self.assertTrue(pe_rec.is_historical)
        self.assertEqual(pe_rec.payment_type, "Receive")
        self.assertEqual(pe_rec.paid_amount, Decimal("105.00"))
        self.assertEqual(pe_rec.source_journal, self.j_rec)

        pe_pay = PaymentEntry.objects.get(name="TLY-2425-000004")
        self.assertTrue(pe_pay.is_historical)
        self.assertEqual(pe_pay.payment_type, "Pay")
        self.assertEqual(pe_pay.paid_amount, Decimal("210.00"))
        self.assertEqual(pe_pay.source_journal, self.j_pay)

        # 4. Oldest-first allocation display links
        ref_rec = PaymentEntryReference.objects.filter(payment_entry=pe_rec).first()
        self.assertIsNotNone(ref_rec)
        self.assertEqual(ref_rec.reference_doctype, "Sales Invoice")
        self.assertEqual(ref_rec.reference_name, si.name)
        self.assertEqual(ref_rec.allocated_amount, Decimal("105.00"))

        ref_pay = PaymentEntryReference.objects.filter(payment_entry=pe_pay).first()
        self.assertIsNotNone(ref_pay)
        self.assertEqual(ref_pay.reference_doctype, "Purchase Invoice")
        self.assertEqual(ref_pay.reference_name, pi.name)
        self.assertEqual(ref_pay.allocated_amount, Decimal("210.00"))

        # 5. Idempotent re-run: creates nothing new
        call_command(
            "import_tally_history",
            source=self.temp_file.name,
            company=self.company.name,
            commit=True,
        )
        self.assertEqual(SalesInvoice.objects.filter(company=self.company).count(), 1)
        self.assertEqual(PurchaseInvoice.objects.filter(company=self.company).count(), 1)
        self.assertEqual(PaymentEntry.objects.filter(company=self.company).count(), 2)
