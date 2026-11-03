"""Bank Account, statement import, reconciliation tool and the BRS report."""

import datetime
import json
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    BankAccount,
    BankClearance,
    BankTransaction,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentType,
)
from accounts.services import bank as bank_service
from accounts.services import bank_import
from core.models import AuditLog, Company, UserCompany
from users.models import Role, RolePermission, User, UserRole

D = datetime.date
Dec = Decimal
BASE = "/api/accounts/"

HDFC_CSV = """Account Statement for A/C 5010 0123 4567
From Date : 01/10/2026 To Date : 31/10/2026

Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
02/10/26,NEFT CR-ABC TRADERS,N123,02/10/26,,"10,000.00","10,000.00"
08/10/26,CHQ PAID 000123456,000123456,08/10/26,"2,000.00",,"8,000.00"
20/10/26,SMS CHARGES,,20/10/26,118.00,,"7,882.00"
********
STATEMENT SUMMARY :-
Opening Balance,Dr Count,Cr Count
0.00,2,1
"""


def make_company(name, abbr):
    company = Company.objects.create(name=name, abbr=abbr, default_currency="INR")

    def acc(nm, root, parent=None, **kw):
        return Account.objects.create(
            name=nm, company=company, root_type=root, parent=parent, **kw
        )

    assets = acc("Assets", AccountRootType.ASSET, is_group=True)
    equity = acc("Equity", AccountRootType.EQUITY, is_group=True)
    liab = acc("Liabilities", AccountRootType.LIABILITY, is_group=True)
    expense = acc("Expenses", AccountRootType.EXPENSE, is_group=True)
    return company, {
        "assets": assets,
        "bank": acc(
            "HDFC Bank", AccountRootType.ASSET, assets, account_type=AccountType.BANK
        ),
        "bank2": acc(
            "SBI Bank", AccountRootType.ASSET, assets, account_type=AccountType.BANK
        ),
        "cash": acc(
            "Cash", AccountRootType.ASSET, assets, account_type=AccountType.CASH
        ),
        "capital": acc("Capital", AccountRootType.EQUITY, equity),
        "creditors": acc(
            "Creditors",
            AccountRootType.LIABILITY,
            liab,
            account_type=AccountType.PAYABLE,
        ),
        "charges": acc("Bank Charges", AccountRootType.EXPENSE, expense),
    }


class BankBase(TestCase):
    def setUp(self):
        self.company, self.a = make_company("Bank Co", "BC")
        self.ba = BankAccount.objects.create(
            company=self.company,
            account=self.a["bank"],
            bank_name="HDFC Bank",
            account_number="50100123456789",
            ifsc_code="HDFC0001234",
            is_default=True,
        )
        self.root = User.objects.create_superuser("root", "r@x.com", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.root)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        self._n = 0

    # ------------------------------------------------------------- fixtures

    def je(self, date, lines, cheque_no=None, cheque_date=None):
        self._n += 1
        entry = JournalEntry.objects.create(
            name=f"JE-{self._n}",
            posting_date=date,
            company=self.company,
            cheque_no=cheque_no,
            cheque_date=cheque_date,
        )
        for account, dr, cr in lines:
            JournalEntryLine.objects.create(
                journal_entry=entry, account=account, debit=Dec(dr), credit=Dec(cr)
            )
        entry.submit()
        return entry

    def deposit(self, date, amount, **kw):
        return self.je(
            date, [(self.a["bank"], amount, 0), (self.a["capital"], 0, amount)], **kw
        )

    def withdraw(self, date, amount, **kw):
        return self.je(
            date, [(self.a["charges"], amount, 0), (self.a["bank"], 0, amount)], **kw
        )

    def payment(self, date, amount, reference_no):
        self._n += 1
        pe = PaymentEntry.objects.create(
            name=f"PE-{self._n}",
            payment_type=PaymentType.PAY,
            posting_date=date,
            company=self.company,
            party_type="Supplier",
            party_name="Some Supplier",
            paid_from=self.a["bank"],
            paid_to=self.a["creditors"],
            paid_amount=Dec(amount),
            received_amount=Dec(amount),
            reference_no=reference_no,
        )
        pe.submit()
        return pe

    def key(self, doc):
        return {
            "voucher_type": f"accounts.{type(doc).__name__}",
            "voucher_no": doc.name,
        }

    def upload(
        self, text=HDFC_CSV, mapping=None, bank_account=None, name="stmt.csv", **extra
    ):
        data = {
            "bank_account": (bank_account or self.ba).pk,
            "file": SimpleUploadedFile(
                name, text.encode("utf-8"), content_type="text/csv"
            ),
            **extra,
        }
        if mapping is not None:
            data["mapping"] = json.dumps(mapping)
        return self.client.post(
            f"{BASE}bank-statements/import-statement/", data, format="multipart"
        )

    def import_hdfc(self, text=HDFC_CSV):
        preview = self.client.post(
            f"{BASE}bank-statements/preview/",
            {
                "file": SimpleUploadedFile(
                    "s.csv", text.encode(), content_type="text/csv"
                )
            },
            format="multipart",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        res = self.upload(
            text, mapping=preview.data["mapping"], header_row=preview.data["header_row"]
        )
        self.assertIn(res.status_code, (200, 201), res.content)
        return res.data

    def tool(self, **params):
        res = self.client.get(
            f"{BASE}bank-reconciliation/",
            {
                "bank_account": self.ba.pk,
                "from_date": "2026-10-01",
                "to_date": "2026-10-31",
                **params,
            },
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def tx(self, ref):
        return (
            BankTransaction.objects.get(reference_no=ref)
            if ref
            else BankTransaction.objects.get(description="SMS CHARGES")
        )

    def match(self, tx, *docs):
        return self.client.post(
            f"{BASE}bank-reconciliation/match/",
            {"bank_transaction": tx.pk, "vouchers": [self.key(d) for d in docs]},
            format="json",
        )

    def brs(self, as_on="2026-10-31"):
        res = self.client.get(
            f"{BASE}reports/bank-reconciliation-statement/",
            {"bank_account": self.ba.pk, "as_on": as_on},
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.data


class BankAccountTests(BankBase):
    def test_must_be_a_postable_bank_account_of_the_same_company(self):
        for account, message in (
            (self.a["cash"], "type Bank"),
            (self.a["creditors"], "type Bank"),
        ):
            res = self.client.post(
                f"{BASE}bank-accounts/",
                {"account": account.pk, "bank_name": "X"},
                format="json",
            )
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn(message, str(res.data))
        _other, other_accounts = make_company("Other", "OT")
        res = self.client.post(
            f"{BASE}bank-accounts/",
            {"account": other_accounts["bank"].pk, "bank_name": "X"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_one_bank_account_per_gl_account_and_one_default(self):
        dup = self.client.post(
            f"{BASE}bank-accounts/",
            {"account": self.a["bank"].pk, "bank_name": "Again"},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)
        res = self.client.post(
            f"{BASE}bank-accounts/",
            {
                "account": self.a["bank2"].pk,
                "bank_name": "SBI",
                "ifsc_code": "sbin0000123",
                "is_default": True,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["ifsc_code"], "SBIN0000123")
        self.ba.refresh_from_db()
        self.assertFalse(self.ba.is_default)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankAccount.objects.filter(pk=self.ba.pk).update(is_default=True)

    def test_ifsc_validated_and_account_number_masked(self):
        res = self.client.post(
            f"{BASE}bank-accounts/",
            {"account": self.a["bank2"].pk, "bank_name": "SBI", "ifsc_code": "BAD"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.get(f"{BASE}bank-accounts/{self.ba.pk}/")
        self.assertEqual(res.data["account_number_last4"], "6789")
        self.assertNotIn("account_number", res.data)
        self.assertNotIn("50100123456789", json.dumps(res.data))

    def test_delete_blocked_once_it_has_history(self):
        self.import_hdfc()
        res = self.client.delete(f"{BASE}bank-accounts/{self.ba.pk}/")
        self.assertEqual(res.status_code, 409)


class ImportTests(BankBase):
    def test_preview_detects_header_and_maps_columns(self):
        res = self.client.post(
            f"{BASE}bank-statements/preview/",
            {
                "file": SimpleUploadedFile(
                    "s.csv", HDFC_CSV.encode(), content_type="text/csv"
                )
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.data["mapping"],
            {
                "date": "Date",
                "description": "Narration",
                "reference": "Chq./Ref.No.",
                "withdrawal": "Withdrawal Amt.",
                "deposit": "Deposit Amt.",
                "balance": "Closing Balance",
            },
        )
        self.assertEqual(res.data["header_row"], 2)

    def test_import_creates_lines_and_skips_footer_rows(self):
        summary = self.import_hdfc()
        self.assertEqual((summary["imported"], summary["duplicates"]), (3, 0))
        self.assertGreaterEqual(summary["skipped"], 1)
        lines = list(BankTransaction.objects.order_by("transaction_date"))
        self.assertEqual(
            [(t.transaction_date, t.withdrawal, t.deposit, t.balance) for t in lines],
            [
                (D(2026, 10, 2), Dec(0), Dec("10000.00"), Dec("10000.00")),
                (D(2026, 10, 8), Dec("2000.00"), Dec(0), Dec("8000.00")),
                (D(2026, 10, 20), Dec("118.00"), Dec(0), Dec("7882.00")),
            ],
        )
        self.assertTrue(AuditLog.objects.filter(action="import").exists())

    def test_reimport_and_overlap_are_deduplicated(self):
        self.import_hdfc()
        again = self.import_hdfc()
        self.assertEqual((again["imported"], again["duplicates"]), (0, 3))
        self.assertEqual(BankTransaction.objects.count(), 3)
        overlap = HDFC_CSV.replace(
            "********",
            '25/10/26,UPI-XYZ,U9,25/10/26,,500.00,"8,382.00"\n********',
        )
        res = self.import_hdfc(overlap)
        self.assertEqual((res["imported"], res["duplicates"]), (1, 3))
        self.assertEqual(BankTransaction.objects.count(), 4)

    def test_two_identical_lines_in_a_file_are_both_kept(self):
        text = (
            "Date,Description,Withdrawal,Deposit,Balance\n"
            "01/10/2026,CASH DEP,,100.00,100.00\n"
            "01/10/2026,CASH DEP,,100.00,200.00\n"
        )
        mapping = bank_import.preview(text.encode())["mapping"]
        res = self.upload(text, mapping=mapping)
        self.assertEqual(res.data["imported"], 2)
        again = self.upload(text, mapping=mapping)
        self.assertEqual(again.data["imported"], 0)
        self.assertEqual(BankTransaction.objects.count(), 2)

    def test_other_bank_layouts_and_date_formats(self):
        sbi = (
            "Txn Date,Value Date,Description,Ref No./Cheque No.,Debit,Credit,Balance\n"
            '1 Oct 2026,1 Oct 2026,BY TRANSFER,TXN1,,"1,50,000.00","1,50,000.00 CR"\n'
            '5 Oct 2026,5 Oct 2026,TO CHEQUE,000555,"25,000.50",,"1,24,999.50 CR"\n'
        )
        mapping = bank_import.preview(sbi.encode())["mapping"]
        self.assertEqual(self.upload(sbi, mapping=mapping).data["imported"], 2)
        icici = (
            "S No.;Transaction Date;Transaction Remarks;Withdrawal Amount (INR );"
            "Deposit Amount (INR );Balance (INR )\n"
            "1;02-10-2026;IMPS;75.00;0.00;10.00\n"
        )
        mapping = bank_import.preview(icici.encode())["mapping"]
        self.assertEqual(mapping["withdrawal"], "Withdrawal Amount (INR )")
        self.assertEqual(self.upload(icici, mapping=mapping).data["imported"], 1)
        signed = "Date,Narration,Amount,Dr/Cr\n03/10/2026,X,100.00,DR\n04/10/2026,Y,40.00,CR\n"
        mapping = bank_import.preview(signed.encode())["mapping"]
        self.assertEqual(self.upload(signed, mapping=mapping).data["imported"], 2)
        amounts = {
            t.description: t.amount
            for t in BankTransaction.objects.filter(description__in=["X", "Y"])
        }
        self.assertEqual(amounts, {"X": Dec("-100.00"), "Y": Dec("40.00")})

    def test_bad_rows_reject_the_whole_file(self):
        text = (
            "Date,Narration,Withdrawal,Deposit\n"
            "01/10/2026,OK,10.00,\n"
            "31/31/2026,BAD DATE,10.00,\n"
        )
        mapping = bank_import.preview(text.encode())["mapping"]
        res = self.upload(text, mapping=mapping)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid date", str(res.data))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_binary_and_missing_mapping_rejected(self):
        res = self.upload("a\x00b", mapping={})
        self.assertEqual(res.status_code, 400)
        res = self.upload(HDFC_CSV, mapping={"description": "Narration"}, header_row=3)
        self.assertEqual(res.status_code, 400)

    def test_lines_are_immutable(self):
        self.import_hdfc()
        tx = BankTransaction.objects.first()
        tx.description = "changed"
        with self.assertRaises(ValidationError):
            tx.save()
        with self.assertRaises(ValidationError):
            tx.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankTransaction.objects.create(
                company=self.company,
                bank_account=self.ba,
                statement_import=tx.statement_import,
                transaction_date=D(2026, 10, 1),
                withdrawal=Dec("5"),
                deposit=Dec("5"),
                dedupe_key="x",
            )


class ReconcileBase(BankBase):
    def setUp(self):
        super().setUp()
        self.v1 = self.deposit(D(2026, 10, 1), "10000.00", cheque_no="N123")
        self.v2 = self.payment(D(2026, 10, 5), "2000.00", "123456")
        self.v3 = self.deposit(D(2026, 10, 10), "3000.00", cheque_no="000999")
        self.v4 = self.withdraw(D(2026, 10, 12), "500.00", cheque_no="000789")
        self.import_hdfc()
        self.t_dep = self.tx("N123")
        self.t_chq = self.tx("000123456")
        self.t_fee = self.tx(None)


class ToolTests(ReconcileBase):
    def test_lists_unreconciled_lines_vouchers_and_suggestions(self):
        data = self.tool()
        self.assertEqual(len(data["transactions"]), 3)
        self.assertEqual(
            {v["voucher_no"] for v in data["vouchers"]},
            {self.v1.name, self.v2.name, self.v3.name, self.v4.name},
        )
        refs = {v["voucher_no"]: v["reference_no"] for v in data["vouchers"]}
        self.assertEqual(refs[self.v2.name], "123456")
        self.assertEqual(refs[self.v1.name], "N123")
        suggested = {
            (s["bank_transaction"], s["voucher_no"], s["basis"])
            for s in data["suggestions"]
        }
        self.assertEqual(
            suggested,
            {
                (self.t_dep.pk, self.v1.name, "reference"),
                (self.t_chq.pk, self.v2.name, "reference"),
            },
        )

    def test_date_proximity_suggests_when_there_is_no_reference(self):
        extra = self.deposit(D(2026, 10, 18), "777.00")
        text = (
            "Date,Narration,Withdrawal,Deposit\n20/10/2026,CASH DEPOSIT,,777.00\n"
            "01/01/2026,OLD,,777.00\n"
        )
        self.upload(text, mapping=bank_import.preview(text.encode())["mapping"])
        data = self.tool(from_date="2026-10-01")
        hit = [s for s in data["suggestions"] if s["voucher_no"] == extra.name]
        self.assertEqual([h["basis"] for h in hit], ["date"])
        # the January line is 9 months from the voucher: no suggestion
        january = BankTransaction.objects.get(description="OLD")
        wide = self.tool(from_date="2026-01-01")
        self.assertNotIn(
            january.pk, [s["bank_transaction"] for s in wide["suggestions"]]
        )

    def test_confirm_match_sets_clearance_date_and_links(self):
        res = self.match(self.t_dep, self.v1)
        self.assertEqual(res.status_code, 201, res.content)
        clearance = BankClearance.objects.get()
        self.assertEqual(clearance.clearance_date, D(2026, 10, 2))
        self.assertEqual(clearance.bank_transaction, self.t_dep)
        self.assertEqual(clearance.bank_account, self.ba)
        self.assertEqual(clearance.set_by, self.root)
        self.assertTrue(
            AuditLog.objects.filter(
                action="clear", model_label="accounts.BankClearance"
            ).exists()
        )
        data = self.tool()
        self.assertNotIn(self.t_dep.pk, [t["id"] for t in data["transactions"]])
        self.assertNotIn(self.v1.name, [v["voucher_no"] for v in data["vouchers"]])
        listed = self.client.get(
            f"{BASE}bank-transactions/", {"bank_account": self.ba.pk}
        )
        flags = {r["id"]: r["reconciled"] for r in listed.data["results"]}
        self.assertTrue(flags[self.t_dep.pk])
        self.assertFalse(flags[self.t_chq.pk])
        only = self.client.get(f"{BASE}bank-transactions/", {"status": "unreconciled"})
        self.assertEqual(len(only.data["results"]), 2)

    def test_amount_mismatch_and_double_match_are_rejected(self):
        res = self.match(self.t_dep, self.v3)
        self.assertEqual(res.status_code, 400)
        self.assertIn("total", str(res.data))
        # direction matters: a payment cannot match a deposit of the same size
        self.assertEqual(self.match(self.t_chq, self.v1).status_code, 400)
        self.assertEqual(self.match(self.t_dep, self.v1).status_code, 201)
        self.assertEqual(self.match(self.t_dep, self.v1).status_code, 400)
        again = self.match(self.t_fee, self.v1)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(BankClearance.objects.count(), 1)

    def test_many_to_one_match(self):
        a = self.deposit(D(2026, 10, 3), "400.00")
        b = self.deposit(D(2026, 10, 3), "600.00")
        text = (
            "Date,Narration,Withdrawal,Deposit\n04/10/2026,CHEQUE DEPOSIT 2,,1000.00\n"
        )
        self.upload(text, mapping=bank_import.preview(text.encode())["mapping"])
        tx = BankTransaction.objects.get(description="CHEQUE DEPOSIT 2")
        self.assertEqual(self.match(tx, a).status_code, 400)
        self.assertEqual(self.match(tx, a, b).status_code, 201)
        self.assertEqual(BankClearance.objects.filter(bank_transaction=tx).count(), 2)
        self.assertEqual(bank_service.check_integrity(), [])
        # unmatch undoes every leg
        res = self.client.post(
            f"{BASE}bank-reconciliation/unmatch/",
            {"bank_transaction": tx.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(bank_service.cleared_map(self.ba), {})

    def test_unmatch_restores_both_sides_and_keeps_history(self):
        self.match(self.t_dep, self.v1)
        res = self.client.post(
            f"{BASE}bank-reconciliation/unmatch/",
            {"bank_transaction": self.t_dep.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        data = self.tool()
        self.assertIn(self.t_dep.pk, [t["id"] for t in data["transactions"]])
        self.assertIn(self.v1.name, [v["voucher_no"] for v in data["vouchers"]])
        history = list(
            BankClearance.objects.order_by("id").values_list(
                "clearance_date", flat=True
            )
        )
        self.assertEqual(history, [D(2026, 10, 2), None])
        self.assertEqual(
            self.client.post(
                f"{BASE}bank-reconciliation/unmatch/",
                {"bank_transaction": self.t_dep.pk},
                format="json",
            ).status_code,
            400,
        )
        # and it can be matched again
        self.assertEqual(self.match(self.t_dep, self.v1).status_code, 201)

    def test_clearance_rows_are_append_only(self):
        self.match(self.t_dep, self.v1)
        row = BankClearance.objects.get()
        row.clearance_date = D(2026, 11, 1)
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankClearance.objects.create(
                company=self.company,
                bank_account=self.ba,
                voucher_type="x",
                voucher_no="y",
                clearance_date=None,
                bank_transaction=self.t_dep,
            )

    def test_cleared_voucher_cannot_be_cancelled_until_unmatched(self):
        self.match(self.t_dep, self.v1)
        with self.assertRaisesMessage(ValidationError, "cleared the bank"):
            self.v1.cancel()
        self.client.post(
            f"{BASE}bank-reconciliation/unmatch/",
            {"bank_transaction": self.t_dep.pk},
            format="json",
        )
        self.v1.cancel()

    def test_manual_clearance_and_unclear(self):
        res = self.client.post(
            f"{BASE}bank-reconciliation/clear/",
            {
                "bank_account": self.ba.pk,
                "vouchers": [{**self.key(self.v3), "clearance_date": "2026-10-15"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertNotIn(
            self.v3.name, [v["voucher_no"] for v in self.tool()["vouchers"]]
        )
        before = self.client.post(
            f"{BASE}bank-reconciliation/clear/",
            {
                "bank_account": self.ba.pk,
                "vouchers": [{**self.key(self.v4), "clearance_date": "2026-10-01"}],
            },
            format="json",
        )
        self.assertEqual(before.status_code, 400)  # before its posting date
        res = self.client.post(
            f"{BASE}bank-reconciliation/clear/",
            {
                "bank_account": self.ba.pk,
                "vouchers": [{**self.key(self.v3), "clearance_date": None}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIn(self.v3.name, [v["voucher_no"] for v in self.tool()["vouchers"]])
        # a voucher that never touched the bank cannot be cleared
        stray = self.je(
            D(2026, 10, 2),
            [(self.a["charges"], "1.00", 0), (self.a["capital"], 0, "1.00")],
        )
        res = self.client.post(
            f"{BASE}bank-reconciliation/clear/",
            {
                "bank_account": self.ba.pk,
                "vouchers": [{**self.key(stray), "clearance_date": "2026-10-03"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_matched_voucher_cannot_be_unmarked_directly(self):
        self.match(self.t_dep, self.v1)
        res = self.client.post(
            f"{BASE}bank-reconciliation/clear/",
            {
                "bank_account": self.ba.pk,
                "vouchers": [{**self.key(self.v1), "clearance_date": None}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("unmatch", str(res.data))

    def test_reconciled_up_to_hides_old_vouchers(self):
        self.ba.reconciled_up_to = D(2026, 10, 5)
        self.ba.save()
        names = {v["voucher_no"] for v in self.tool()["vouchers"]}
        self.assertEqual(names, {self.v3.name, self.v4.name})

    def test_bank_charge_becomes_a_draft_journal_that_then_matches(self):
        res = self.client.post(
            f"{BASE}bank-transactions/{self.t_fee.pk}/make-journal-entry/",
            {"account": self.a["charges"].pk},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        je = JournalEntry.objects.get(pk=res.data["id"])
        self.assertEqual(
            (je.docstatus, je.voucher_type, je.posting_date),
            (0, "Bank Entry", D(2026, 10, 20)),
        )
        self.assertEqual(
            (je.total_debit, je.total_credit), (Dec("118.00"), Dec("118.00"))
        )
        lines = {ln.account.name: (ln.debit, ln.credit) for ln in je.lines.all()}
        self.assertEqual(lines["HDFC Bank"], (Dec(0), Dec("118.00")))
        self.assertEqual(lines["Bank Charges"], (Dec("118.00"), Dec(0)))
        je.submit()
        data = self.tool()
        self.assertIn(
            (self.t_fee.pk, je.name),
            [(s["bank_transaction"], s["voucher_no"]) for s in data["suggestions"]],
        )
        self.assertEqual(self.match(self.t_fee, je).status_code, 201)

    def test_make_journal_entry_validations(self):
        url = f"{BASE}bank-transactions/{self.t_fee.pk}/make-journal-entry/"
        for body in (
            {},
            {"account": self.a["assets"].pk},
            {"account": self.a["bank"].pk},
        ):
            self.assertEqual(
                self.client.post(url, body, format="json").status_code, 400
            )
        _other, other_accounts = make_company("Other", "OT")
        res = self.client.post(
            url, {"account": other_accounts["charges"].pk}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(JournalEntry.objects.filter(docstatus=0).count(), 0)


class BrsTests(ReconcileBase):
    def test_figures_before_and_after_reconciling(self):
        self.match(self.t_dep, self.v1)
        self.match(self.t_chq, self.v2)
        brs = self.brs()
        # GL = 10000 - 2000 + 3000 - 500; uncleared: deposit 3000, cheque 500
        self.assertEqual(brs["gl_balance"], "10500.00")
        self.assertEqual(brs["uncleared_receipts_total"], "3000.00")
        self.assertEqual(brs["uncleared_payments_total"], "500.00")
        self.assertEqual(brs["bank_balance"], "8000.00")
        self.assertEqual(
            [r["voucher_no"] for r in brs["uncleared_receipts"]], [self.v3.name]
        )
        self.assertEqual(brs["uncleared_receipts"][0]["reference_no"], "000999")
        self.assertEqual(
            [r["voucher_no"] for r in brs["uncleared_payments"]], [self.v4.name]
        )
        # statement says 7882: the 118 charge is not in the books yet
        self.assertEqual(brs["statement_balance"], "7882.00")
        self.assertEqual(brs["statement_balance_date"], "2026-10-20")
        self.assertEqual(brs["difference"], "-118.00")
        # book the charge and match it: the statement ties out exactly
        fee = self.withdraw(D(2026, 10, 20), "118.00")
        self.match(self.t_fee, fee)
        brs = self.brs()
        self.assertEqual(brs["gl_balance"], "10382.00")
        self.assertEqual(brs["bank_balance"], "7882.00")
        self.assertEqual(brs["difference"], "0.00")
        self.assertEqual(
            Dec(brs["gl_balance"])
            - Dec(brs["uncleared_receipts_total"])
            + Dec(brs["uncleared_payments_total"]),
            Dec(brs["bank_balance"]),
        )

    def test_as_on_date_and_late_clearance(self):
        self.match(self.t_dep, self.v1)
        # as on 2026-10-01 only V1 exists; it clears on the 2nd, i.e. after that date
        brs = self.brs("2026-10-01")
        self.assertEqual(brs["gl_balance"], "10000.00")
        self.assertEqual(brs["uncleared_receipts_total"], "10000.00")
        self.assertEqual(brs["bank_balance"], "0.00")
        self.assertIsNone(brs["statement_balance"])
        self.assertIsNone(brs["difference"])
        self.assertEqual(self.brs("2026-10-02")["uncleared_receipts_total"], "0.00")


class IntegrityTests(ReconcileBase):
    def test_verify_integrity_green_then_detects_corruption(self):
        self.match(self.t_dep, self.v1)
        self.match(self.t_chq, self.v2)
        call_command("verify_integrity", stdout=StringIO())
        self.assertEqual(bank_service.check_integrity(), [])
        # a clearance for a voucher that never touched the bank
        stray = self.je(
            D(2026, 10, 2),
            [(self.a["charges"], "1.00", 0), (self.a["capital"], 0, "1.00")],
        )
        BankClearance.objects.create(
            company=self.company,
            bank_account=self.ba,
            voucher_type="accounts.JournalEntry",
            voucher_no=stray.name,
            clearance_date=D(2026, 10, 3),
        )
        errors = bank_service.check_integrity()
        self.assertTrue(
            any(
                "not a submitted voucher touching this bank account" in e
                for e in errors
            )
        )
        # a match whose amounts disagree
        BankClearance.objects.create(
            company=self.company,
            bank_account=self.ba,
            voucher_type="accounts.JournalEntry",
            voucher_no=self.v3.name,
            clearance_date=D(2026, 10, 20),
            bank_transaction=self.t_fee,
        )
        errors = bank_service.check_integrity()
        self.assertTrue(any("matched to vouchers totalling" in e for e in errors))


class AccessTests(ReconcileBase):
    def user_client(self, company, **perms):
        n = User.objects.count()
        user = User.objects.create_user(f"u{n}", f"u{n}@x.com", "x")
        role = Role.objects.create(name=f"R{user.pk}")
        for label, flags in perms.items():
            RolePermission.objects.create(
                role=role, model_label=label, **{f: True for f in flags}
            )
        UserRole.objects.create(user=user, role=role)
        UserCompany.objects.update_or_create(
            user=user, defaults={"company": company, "is_default": True}
        )
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_rbac(self):
        reader = self.user_client(
            self.company,
            **{
                "accounts.BankTransaction": ["can_read"],
                "accounts.BankAccount": ["can_read"],
            },
        )
        self.assertEqual(
            reader.get(
                f"{BASE}bank-reconciliation/", {"bank_account": self.ba.pk}
            ).status_code,
            200,
        )
        self.assertEqual(reader.get(f"{BASE}bank-accounts/").status_code, 200)
        self.assertEqual(
            reader.get(
                f"{BASE}reports/bank-reconciliation-statement/",
                {"bank_account": self.ba.pk},
            ).status_code,
            200,
        )
        # read-only roles cannot reconcile, import or edit accounts
        self.assertEqual(
            reader.post(
                f"{BASE}bank-reconciliation/match/",
                {"bank_transaction": self.t_dep.pk, "vouchers": [self.key(self.v1)]},
                format="json",
            ).status_code,
            403,
        )
        self.assertEqual(
            reader.post(
                f"{BASE}bank-statements/import-statement/",
                {"bank_account": self.ba.pk},
                format="multipart",
            ).status_code,
            403,
        )
        self.assertEqual(
            reader.post(
                f"{BASE}bank-accounts/",
                {"account": self.a["bank2"].pk, "bank_name": "x"},
                format="json",
            ).status_code,
            403,
        )
        nobody = self.user_client(self.company)
        for url in (
            "bank-reconciliation/",
            "bank-accounts/",
            "bank-transactions/",
            "bank-clearances/",
            "reports/bank-reconciliation-statement/",
        ):
            self.assertEqual(
                nobody.get(f"{BASE}{url}", {"bank_account": self.ba.pk}).status_code,
                403,
                url,
            )
        clerk = self.user_client(
            self.company,
            **{
                "accounts.BankClearance": ["can_create"],
                "accounts.BankTransaction": ["can_read"],
            },
        )
        self.assertEqual(
            clerk.post(
                f"{BASE}bank-reconciliation/match/",
                {"bank_transaction": self.t_dep.pk, "vouchers": [self.key(self.v1)]},
                format="json",
            ).status_code,
            201,
        )

    def test_company_isolation(self):
        other, _accounts = make_company("Other Co", "OC")
        perms = {
            "accounts.BankTransaction": ["can_read"],
            "accounts.BankAccount": ["can_read"],
            "accounts.BankClearance": ["can_read", "can_create"],
            "accounts.BankStatementImport": ["can_read", "can_create"],
        }
        stranger = self.user_client(other, **perms)
        self.assertEqual(stranger.get(f"{BASE}bank-accounts/").data["results"], [])
        self.assertEqual(stranger.get(f"{BASE}bank-transactions/").data["results"], [])
        self.assertEqual(stranger.get(f"{BASE}bank-clearances/").data["results"], [])
        self.assertEqual(
            stranger.get(
                f"{BASE}bank-reconciliation/", {"bank_account": self.ba.pk}
            ).status_code,
            404,
        )
        self.assertEqual(
            stranger.get(
                f"{BASE}reports/bank-reconciliation-statement/",
                {"bank_account": self.ba.pk},
            ).status_code,
            404,
        )
        self.assertEqual(
            stranger.post(
                f"{BASE}bank-reconciliation/match/",
                {"bank_transaction": self.t_dep.pk, "vouchers": [self.key(self.v1)]},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            stranger.post(
                f"{BASE}bank-reconciliation/clear/",
                {
                    "bank_account": self.ba.pk,
                    "vouchers": [{**self.key(self.v3), "clearance_date": "2026-10-15"}],
                },
                format="json",
            ).status_code,
            404,
        )
        res = stranger.post(
            f"{BASE}bank-statements/import-statement/",
            {
                "bank_account": self.ba.pk,
                "file": SimpleUploadedFile("s.csv", HDFC_CSV.encode()),
                "mapping": "{}",
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(BankClearance.objects.count(), 0)
        # a user cannot pass another company's id in the header either
        stranger.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(stranger.get(f"{BASE}bank-accounts/").status_code, 403)


class AcceptSuggestionsTests(ReconcileBase):
    """Confirming every suggestion at once (old statements: years of lines)."""

    def accept(self, **body):
        res = self.client.post(
            f"{BASE}bank-reconciliation/accept-suggestions/",
            {
                "bank_account": self.ba.pk,
                "from_date": "2026-10-01",
                "to_date": "2026-10-31",
                **body,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def test_accepts_every_suggestion_and_leaves_the_rest(self):
        result = self.accept()
        self.assertEqual((result["matched"], result["skipped"]), (2, []))
        data = self.tool()
        self.assertEqual(
            [t["id"] for t in data["transactions"]], [self.t_fee.pk]
        )  # the bank fee has no voucher: still to do by hand
        self.assertEqual(data["suggestions"], [])
        self.assertEqual(self.accept()["matched"], 0)  # idempotent

    def test_only_reference_skips_date_guesses(self):
        extra = self.deposit(D(2026, 10, 18), "777.00")
        text = "Date,Narration,Withdrawal,Deposit\n20/10/2026,CASH DEPOSIT,,777.00\n"
        self.upload(text, mapping=bank_import.preview(text.encode())["mapping"])
        self.assertEqual(self.accept(only_reference=True)["matched"], 2)
        still = {s["voucher_no"] for s in self.tool()["suggestions"]}
        self.assertEqual(still, {extra.name})
        self.assertEqual(self.accept()["matched"], 1)

    def test_bad_date_is_a_400(self):
        res = self.client.post(
            f"{BASE}bank-reconciliation/accept-suggestions/",
            {"bank_account": self.ba.pk, "from_date": "31/10/2026"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
