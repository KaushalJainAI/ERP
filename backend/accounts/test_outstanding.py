"""Invoice outstanding amount, payment allocation, status and ageing (ERPNext G6)."""

import datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from accounts import mapping as acc_mapping
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    InvoiceStatus,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.services import outstanding as outstanding_service
from buying.models import PurchaseInvoice
from core.models import Company, DocStatus, SystemHealthCheck
from gst.tests.test_documents import PI_URL, SI_URL
from gst.tests.test_posting import PostingBase
from selling.models import SalesInvoice
from users.models import Role, RolePermission, User, UserRole

D = Decimal
AGEING_URL = "/api/accounts/reports/ageing/"
SI_LABEL = "selling.SalesInvoice"
PI_LABEL = "buying.PurchaseInvoice"


class OutstandingBase(PostingBase):
    """A tax company with a bank account; helpers to book invoices and payments."""

    _pe_seq = 0

    def si_obj(self, data):
        return SalesInvoice.objects.get(pk=data["id"])

    def pi_obj(self, data):
        return PurchaseInvoice.objects.get(pk=data["id"])

    def receive(self, customer, allocations, amount, submit=True, **extra):
        """A Receive payment; ``allocations`` = ``[(invoice, allocated_amount)]``."""
        return self._payment(
            PaymentType.RECEIVE,
            "Customer",
            customer.name,
            self.debtors,
            self.bank,
            SI_LABEL,
            allocations,
            amount,
            submit,
            extra,
        )

    def pay(self, supplier, allocations, amount, submit=True, **extra):
        return self._payment(
            PaymentType.PAY,
            "Supplier",
            supplier.name,
            self.bank,
            self.creditors,
            PI_LABEL,
            allocations,
            amount,
            submit,
            extra,
        )

    def _payment(
        self,
        payment_type,
        party_type,
        party_name,
        paid_from,
        paid_to,
        doctype,
        allocations,
        amount,
        submit,
        extra,
    ):
        OutstandingBase._pe_seq += 1
        fields = {
            "name": f"PE-OUT-{OutstandingBase._pe_seq:04d}",
            "payment_type": payment_type,
            "posting_date": datetime.date(2026, 4, 2),
            "company": self.company,
            "party_type": party_type,
            "party_name": party_name,
            "paid_from": paid_from,
            "paid_to": paid_to,
            "paid_amount": D(str(amount)),
            "received_amount": D(str(amount)),
        }
        fields.update(extra)
        pe = PaymentEntry.objects.create(**fields)
        for invoice, allocated in allocations:
            PaymentEntryReference.objects.create(
                payment_entry=pe,
                reference_doctype=doctype,
                reference_name=invoice.name,
                allocated_amount=D(str(allocated)),
            )
        if submit:
            pe.submit()
        return pe

    def reload(self, obj):
        obj.refresh_from_db()
        return obj

    def health(self, name="invoice_outstanding"):
        return SystemHealthCheck.objects.filter(check_name=name).latest("pk")


class SubmitAndTaggingTests(OutstandingBase):
    def test_sales_invoice_submit_is_unpaid_and_party_row_tagged(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100))
        self.assertEqual(d["status"], "Unpaid")
        self.assertEqual(D(d["outstanding_amount"]), D("236.00"))
        inv = self.si_obj(d)
        self.assertEqual(inv.status, InvoiceStatus.UNPAID)
        self.assertEqual(inv.outstanding_amount, inv.rounded_total)
        party_row = GLEntry.objects.get(voucher_no=d["name"], account=self.debtors)
        self.assertEqual(party_row.against_voucher_type, SI_LABEL)
        self.assertEqual(party_row.against_voucher_no, d["name"])
        others = GLEntry.objects.filter(voucher_no=d["name"]).exclude(pk=party_row.pk)
        self.assertTrue(others.exists())
        self.assertFalse(others.exclude(against_voucher_no__isnull=True).exists())
        self.integrity_ok()

    def test_purchase_invoice_submit_is_unpaid_and_party_row_tagged(self):
        d = self.purchase(self.sup_intra, (self.item18, 1, 100))
        self.assertEqual(d["status"], "Unpaid")
        self.assertEqual(D(d["outstanding_amount"]), D("118.00"))
        row = GLEntry.objects.get(voucher_no=d["name"], account=self.creditors)
        self.assertEqual((row.against_voucher_type, row.against_voucher_no), (PI_LABEL, d["name"]))
        self.assertEqual((row.party_type, row.party), ("Supplier", "Intra Sup"))

    def test_draft_invoice_has_zero_outstanding_and_draft_status(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual((d["status"], D(d["outstanding_amount"])), ("Draft", D("0")))

    def test_gst_invoice_with_rounding_outstanding_is_rounded_total(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        inv = self.si_obj(d)
        self.assertNotEqual(inv.grand_total, inv.rounded_total)
        self.assertEqual(inv.rounded_total, D("119.00"))
        self.assertEqual(inv.outstanding_amount, D("119.00"))
        self.assertEqual(outstanding_service.gl_outstanding(inv), D("119.00"))
        self.receive(self.cust_intra, [(inv, "119.00")], "119.00")
        self.assertEqual(self.reload(inv).status, "Paid")
        self.assertEqual(inv.outstanding_amount, D("0.00"))
        self.integrity_ok()

    def test_outstanding_cannot_go_negative_at_the_database(self):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 1, 100)))
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesInvoice.objects.filter(pk=inv.pk).update(outstanding_amount=D("-1"))

    def test_zero_allocation_is_rejected_by_the_database(self):
        pe = self.receive(self.cust_intra, [], 10, submit=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentEntryReference.objects.create(
                payment_entry=pe,
                reference_doctype=SI_LABEL,
                reference_name="X",
                allocated_amount=D("0.00"),
            )


class PaymentStatusTests(OutstandingBase):
    def test_partial_then_full_payment(self):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))  # 236
        pe = self.receive(self.cust_intra, [(inv, "100.00")], "100.00")
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Partly Paid", D("136.00")))
        # The payment's party line is split + tagged against the invoice.
        row = GLEntry.objects.get(voucher_no=pe.name, account=self.debtors)
        self.assertEqual((row.against_voucher_type, row.against_voucher_no), (SI_LABEL, inv.name))
        self.assertEqual((row.debit, row.credit), (D("0.00"), D("100.00")))
        self.assertEqual(acc_mapping.invoice_outstanding(inv), D("136.00"))
        self.receive(self.cust_intra, [(inv, "136.00")], "136.00")
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Paid", D("0.00")))
        self.integrity_ok()

    def test_two_payments_then_cancel_one(self):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))
        pe1 = self.receive(self.cust_intra, [(inv, "100.00")], "100.00")
        pe2 = self.receive(self.cust_intra, [(inv, "136.00")], "136.00")
        self.assertEqual(self.reload(inv).status, "Paid")
        pe2.cancel()
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Partly Paid", D("136.00")))
        pe1.cancel()
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Unpaid", D("236.00")))
        self.integrity_ok()

    def test_advance_remainder_is_an_untagged_balanced_line(self):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))
        pe = self.receive(self.cust_intra, [(inv, "100.00")], "300.00")
        rows = GLEntry.objects.filter(voucher_no=pe.name, account=self.debtors)
        tagged = rows.get(against_voucher_no=inv.name)
        advance = rows.get(against_voucher_no__isnull=True)
        self.assertEqual((tagged.credit, advance.credit), (D("100.00"), D("200.00")))
        self.assertEqual((advance.party_type, advance.party), ("Customer", "Intra Cust"))
        self.assert_balanced(pe.name)
        self.assertEqual(self.reload(inv).outstanding_amount, D("136.00"))

    def test_payment_without_references_is_all_on_account(self):
        pe = self.receive(self.cust_intra, [], "50.00")
        rows = GLEntry.objects.filter(voucher_no=pe.name, account=self.debtors)
        self.assertEqual(rows.count(), 1)
        self.assertIsNone(rows.get().against_voucher_no)
        self.assert_balanced(pe.name)

    def test_customer_outstanding_is_the_receivable_party_balance(self):
        # Invoice 236, a 100 allocated payment and a 300 payment with 200 unallocated:
        # owed 236 - 100 - 300 = -164 (in advance). The bank lines also carry the
        # party but are not receivable rows, so they must not count.
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))
        cust = outstanding_service.customer_outstanding
        self.assertEqual(cust(self.cust_intra), D("236.00"))
        self.receive(self.cust_intra, [(inv, "100.00")], "100.00")
        self.assertEqual(cust(self.cust_intra), D("136.00"))
        pe = self.receive(self.cust_intra, [], "300.00")
        self.assertEqual(cust(self.cust_intra), D("-164.00"))
        pe.cancel()
        self.assertEqual(cust(self.cust_intra), D("136.00"))
        self.assertEqual(cust(self.cust_intra, company=self.company), D("136.00"))

    def test_supplier_party_balance(self):
        inv = self.pi_obj(self.purchase(self.sup_intra, (self.item18, 1, 100)))  # 118
        self.pay(self.sup_intra, [(inv, "50.00")], "50.00")
        bal = outstanding_service.party_balance("Supplier", "Intra Sup", self.company.pk)
        self.assertEqual(bal, D("68.00"))

    def test_purchase_pay_flow(self):
        inv = self.pi_obj(self.purchase(self.sup_intra, (self.item18, 1, 100)))  # 118
        pe = self.pay(self.sup_intra, [(inv, "50.00")], "50.00")
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Partly Paid", D("68.00")))
        row = GLEntry.objects.get(voucher_no=pe.name, account=self.creditors)
        self.assertEqual((row.debit, row.credit), (D("50.00"), D("0.00")))
        self.assertEqual((row.against_voucher_type, row.against_voucher_no), (PI_LABEL, inv.name))
        self.assert_balanced(pe.name)
        pe2 = self.pay(self.sup_intra, [(inv, "68.00")], "68.00")
        self.assertEqual(self.reload(inv).status, "Paid")
        pe2.cancel()
        self.assertEqual(self.reload(inv).status, "Partly Paid")
        self.integrity_ok()

    def test_make_payment_entry_uses_the_remaining_outstanding(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100))
        inv = self.si_obj(d)
        self.receive(self.cust_intra, [(inv, "100.00")], "100.00")
        res = self.client.post(f"{SI_URL}{d['id']}/make-payment-entry/")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(D(res.data["paid_amount"]), D("136.00"))
        PaymentEntry.objects.get(pk=res.data["id"]).submit()
        self.assertEqual(self.reload(inv).status, "Paid")
        again = self.client.post(f"{SI_URL}{d['id']}/make-payment-entry/")
        self.assertEqual(again.status_code, 400)


class AllocationValidationTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))  # 236

    def assert_refused(self, pe):
        with self.assertRaises(ValidationError):
            pe.submit()
        self.assertEqual(self.reload(pe).docstatus, DocStatus.DRAFT)
        self.assertFalse(GLEntry.objects.filter(voucher_no=pe.name).exists())
        self.assertEqual(self.reload(self.inv).outstanding_amount, D("236.00"))

    def test_over_allocation_refused(self):
        self.assert_refused(
            self.receive(self.cust_intra, [(self.inv, "236.01")], "300", submit=False)
        )

    def test_sum_of_two_payments_cannot_over_allocate(self):
        pe1 = self.receive(self.cust_intra, [(self.inv, "200.00")], "200", submit=False)
        pe2 = self.receive(self.cust_intra, [(self.inv, "100.00")], "100", submit=False)
        pe1.submit()
        with self.assertRaises(ValidationError):
            pe2.submit()
        self.assertEqual(self.reload(self.inv).outstanding_amount, D("36.00"))
        self.assertEqual(self.reload(pe2).docstatus, DocStatus.DRAFT)

    def test_allocated_more_than_paid_refused(self):
        self.assert_refused(
            self.receive(self.cust_intra, [(self.inv, "100.00")], "50", submit=False)
        )

    def test_duplicate_reference_refused(self):
        self.assert_refused(
            self.receive(
                self.cust_intra,
                [(self.inv, "10.00"), (self.inv, "20.00")],
                "100",
                submit=False,
            )
        )

    def test_wrong_party_refused(self):
        self.assert_refused(
            self.receive(self.cust_inter, [(self.inv, "10.00")], "10", submit=False)
        )

    def test_wrong_party_account_refused(self):
        other = Account.objects.create(
            name="Debtors 2",
            company=self.company,
            parent=Account.objects.get(company=self.company, name="Assets"),
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        pe = self.receive(self.cust_intra, [(self.inv, "10.00")], "10", submit=False)
        PaymentEntry.objects.filter(pk=pe.pk).update(paid_from=other)
        self.assert_refused(self.reload(pe))

    def test_wrong_company_refused(self):
        other = Company.objects.create(name="Other Co", abbr="OTH")
        debtors = Account.objects.create(
            name="Debtors",
            company=other,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        bank = Account.objects.create(
            name="Bank",
            company=other,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        pe = self.receive(
            self.cust_intra,
            [(self.inv, "10.00")],
            "10",
            submit=False,
            company=other,
            paid_from=debtors,
            paid_to=bank,
        )
        self.assert_refused(pe)

    def test_wrong_doctype_refused(self):
        pe = self.receive(self.cust_intra, [(self.inv, "10.00")], "10", submit=False)
        PaymentEntryReference.objects.filter(payment_entry=pe).update(
            reference_doctype="stock.Item"
        )
        self.assert_refused(pe)

    def test_purchase_doctype_on_a_receive_refused(self):
        pe = self.receive(self.cust_intra, [(self.inv, "10.00")], "10", submit=False)
        PaymentEntryReference.objects.filter(payment_entry=pe).update(
            reference_doctype=PI_LABEL
        )
        self.assert_refused(pe)

    def test_draft_invoice_refused(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        draft = self.si_obj(d)
        pe = self.receive(self.cust_intra, [(draft, "10.00")], "10", submit=False)
        with self.assertRaises(ValidationError):
            pe.submit()

    def test_missing_invoice_refused(self):
        pe = self.receive(self.cust_intra, [(self.inv, "10.00")], "10", submit=False)
        PaymentEntryReference.objects.filter(payment_entry=pe).update(
            reference_name="SINV-NOPE"
        )
        self.assert_refused(pe)

    def test_cancelled_invoice_refused(self):
        other = self.si_obj(self.sale(self.cust_intra, (self.item18, 1, 100)))
        other.cancel()
        pe = self.receive(self.cust_intra, [(other, "10.00")], "10", submit=False)
        with self.assertRaises(ValidationError):
            pe.submit()

    def test_internal_transfer_cannot_allocate(self):
        pe = self.receive(
            self.cust_intra,
            [(self.inv, "10.00")],
            "10",
            submit=False,
            payment_type=PaymentType.INTERNAL_TRANSFER,
        )
        self.assert_refused(pe)

    def test_api_rejects_non_positive_allocation(self):
        body = {
            "payment_type": "Receive",
            "posting_date": "2026-04-02",
            "company": self.company.id,
            "party_type": "Customer",
            "party_name": self.cust_intra.name,
            "paid_from": self.debtors.id,
            "paid_to": self.bank.id,
            "paid_amount": "10.00",
            "received_amount": "10.00",
            "references": [
                {
                    "reference_doctype": SI_LABEL,
                    "reference_name": self.inv.name,
                    "allocated_amount": "0.00",
                }
            ],
        }
        res = self.client.post("/api/accounts/payment-entries/", body, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        body["references"][0]["allocated_amount"] = "10.00"
        res = self.client.post("/api/accounts/payment-entries/", body, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        res = self.client.post(f"/api/accounts/payment-entries/{res.data['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.reload(self.inv).status, "Partly Paid")


class CancelAndAmendTests(OutstandingBase):
    def test_cancel_invoice_with_linked_payment_refused_until_payment_cancelled(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100))
        inv = self.si_obj(d)
        pe = self.receive(self.cust_intra, [(inv, "100.00")], "100.00")
        res = self.client.post(f"{SI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(pe.name, str(res.data))
        self.reload(inv)
        self.assertEqual((inv.docstatus, inv.status), (DocStatus.SUBMITTED, "Partly Paid"))
        self.assertTrue(GLEntry.objects.filter(voucher_no=inv.name, is_cancelled=False).exists())

        pe.cancel()
        res = self.client.post(f"{SI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["status"], "Cancelled")
        self.reload(inv)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Cancelled", D("0.00")))
        self.integrity_ok()

    def test_cancel_purchase_invoice_with_linked_payment_refused(self):
        inv = self.pi_obj(self.purchase(self.sup_intra, (self.item18, 1, 100)))
        pe = self.pay(self.sup_intra, [(inv, "10.00")], "10.00")
        with self.assertRaises(ValidationError):
            inv.cancel()
        pe.cancel()
        inv.cancel()
        self.assertEqual(self.reload(inv).status, "Cancelled")

    def test_cancelled_invoice_without_payments(self):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 1, 100)))
        inv.cancel()
        self.assertEqual(
            (inv.status, inv.outstanding_amount), ("Cancelled", D("0.00"))
        )
        self.integrity_ok()

    def test_amend_resets_outstanding_and_status(self):
        d = self.sale(self.cust_intra, (self.item18, 2, 100))
        inv = self.si_obj(d)
        inv.cancel()
        self.assertEqual(self.reload(inv).status, "Cancelled")
        res = self.client.post(f"{SI_URL}{d['id']}/amend/")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.data["status"], D(res.data["outstanding_amount"])), ("Draft", D("0")))
        new = SalesInvoice.objects.get(pk=res.data["id"])
        self.assertEqual((new.status, new.outstanding_amount), ("Draft", D("0.00")))
        new.submit()
        self.reload(new)
        self.assertEqual((new.status, new.outstanding_amount), ("Unpaid", D("236.00")))
        self.integrity_ok()

    def test_amend_purchase_invoice_resets(self):
        inv = self.pi_obj(self.purchase(self.sup_intra, (self.item18, 1, 100)))
        inv.cancel()
        new = inv.amend()
        self.reload(new)
        self.assertEqual((new.status, new.outstanding_amount), ("Draft", D("0.00")))


class SelfHealingTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 2, 100)))
        self.pinv = self.pi_obj(self.purchase(self.sup_intra, (self.item18, 1, 100)))

    def test_verify_integrity_records_ok(self):
        self.integrity_ok()
        self.assertEqual(self.health().status, "ok")

    def test_tampered_outstanding_is_flagged_and_repost_fixes_it(self):
        SalesInvoice.objects.filter(pk=self.inv.pk).update(
            outstanding_amount=D("5.00"), status="Paid"
        )
        PurchaseInvoice.objects.filter(pk=self.pinv.pk).update(outstanding_amount=D("1.00"))
        with self.assertRaises(Exception):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        check = self.health()
        self.assertEqual(check.status, "alert")
        self.assertTrue(any(self.inv.name in e for e in check.details["errors"]))
        self.assertTrue(any(self.pinv.name in e for e in check.details["errors"]))
        out = StringIO()
        call_command("repost_outstanding", stdout=out)
        self.assertIn("2 corrected", out.getvalue())
        self.reload(self.inv)
        self.reload(self.pinv)
        self.assertEqual((self.inv.outstanding_amount, self.inv.status), (D("236.00"), "Unpaid"))
        self.assertEqual(self.pinv.outstanding_amount, D("118.00"))
        self.integrity_ok()
        self.assertEqual(self.health().status, "ok")

    def test_repost_can_be_limited_to_a_company(self):
        SalesInvoice.objects.filter(pk=self.inv.pk).update(outstanding_amount=D("5.00"))
        other = Company.objects.create(name="Elsewhere", abbr="ELS")
        self.assertEqual(outstanding_service.repost(company=other), 0)
        self.assertEqual(self.reload(self.inv).outstanding_amount, D("5.00"))
        call_command("repost_outstanding", company="TXC", stdout=StringIO())
        self.assertEqual(self.reload(self.inv).outstanding_amount, D("236.00"))

    def test_repost_unknown_company_is_an_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("repost_outstanding", company="NOPE", stdout=StringIO())

    def test_repost_rebuilds_from_the_ledger_after_a_payment(self):
        self.receive(self.cust_intra, [(self.inv, "36.00")], "36.00")
        SalesInvoice.objects.filter(pk=self.inv.pk).update(
            outstanding_amount=D("236.00"), status="Unpaid"
        )
        outstanding_service.repost()
        self.assertEqual(
            (self.reload(self.inv).outstanding_amount, self.inv.status),
            (D("200.00"), "Partly Paid"),
        )

    def test_legacy_untagged_invoice_uses_the_document_formula(self):
        # Simulate pre-migration data: the own party row carries no tag.
        GLEntry.objects.filter(voucher_no=self.inv.name).update(
            against_voucher_type=None, against_voucher_no=None
        )
        self.assertEqual(outstanding_service.gl_outstanding(self.inv), D("236.00"))
        self.receive(self.cust_intra, [(self.inv, "100.00")], "100.00")
        self.reload(self.inv)
        self.assertEqual(
            (self.inv.outstanding_amount, self.inv.status), (D("136.00"), "Partly Paid")
        )
        self.integrity_ok()


class StatusPropertyTests(OutstandingBase):
    def make(self, **kw):
        inv = self.si_obj(self.sale(self.cust_intra, (self.item18, 1, 100), **kw))
        return inv

    def test_overdue_when_unpaid_past_due(self):
        inv = self.make(due_date="2026-04-10")
        self.assertTrue(inv.is_overdue)
        self.assertEqual((inv.status, inv.display_status), ("Unpaid", "Overdue"))

    def test_partly_paid_past_due_is_overdue_too(self):
        inv = self.make(due_date="2026-04-10")
        self.receive(self.cust_intra, [(inv, "10.00")], "10.00")
        self.reload(inv)
        self.assertEqual((inv.status, inv.display_status), ("Partly Paid", "Overdue"))

    def test_paid_is_never_overdue(self):
        inv = self.make(due_date="2026-04-10")
        self.receive(self.cust_intra, [(inv, "118.00")], "118.00")
        self.reload(inv)
        self.assertFalse(inv.is_overdue)
        self.assertEqual(inv.display_status, "Paid")

    def test_not_yet_due_and_no_due_date(self):
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        inv = self.make(due_date=future)
        self.assertFalse(inv.is_overdue)
        self.assertEqual(inv.display_status, "Unpaid")
        no_due = self.make()
        self.assertFalse(no_due.is_overdue)

    def test_draft_and_cancelled_are_not_overdue(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item18, 1, 100)), due_date="2026-04-10"
            ),
        )
        draft = self.si_obj(d)
        self.assertEqual((draft.is_overdue, draft.display_status), (False, "Draft"))
        draft.submit()
        draft.cancel()
        self.assertEqual((draft.is_overdue, draft.display_status), (False, "Cancelled"))

    def test_status_is_in_the_api_response(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100), due_date="2026-04-10")
        res = self.client.get(f"{SI_URL}{d['id']}/")
        self.assertEqual(res.data["status"], "Unpaid")
        self.assertIs(res.data["is_overdue"], True)
        self.assertEqual(res.data["display_status"], "Overdue")


class ReadOnlyFieldTests(OutstandingBase):
    TAMPER = {"outstanding_amount": "999.00", "status": "Paid"}

    def test_si_create_ignores_client_supplied_values(self):
        body = self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)), **self.TAMPER)
        body.update({"is_overdue": True, "display_status": "Overdue"})
        d = self.create(SI_URL, body)
        self.assertEqual((d["status"], D(d["outstanding_amount"])), ("Draft", D("0")))
        self.assertIs(d["is_overdue"], False)
        inv = self.si_obj(d)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Draft", D("0.00")))

    def test_si_patch_ignores_client_supplied_values(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(f"{SI_URL}{d['id']}/", self.TAMPER, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.data["status"], D(res.data["outstanding_amount"])), ("Draft", D("0")))
        inv = self.si_obj(d)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Draft", D("0.00")))

    def test_pi_create_and_patch_ignore_client_supplied_values(self):
        body = self.pi_body(self.sup_intra, self.lines((self.item18, 1, 100)), **self.TAMPER)
        d = self.create(PI_URL, body)
        self.assertEqual((d["status"], D(d["outstanding_amount"])), ("Draft", D("0")))
        res = self.client.patch(f"{PI_URL}{d['id']}/", self.TAMPER, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        inv = self.pi_obj(d)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Draft", D("0.00")))

    def test_submitted_invoice_cannot_be_patched_into_paid(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100))
        self.client.patch(f"{SI_URL}{d['id']}/", self.TAMPER, format="json")
        inv = self.si_obj(d)
        self.assertEqual((inv.status, inv.outstanding_amount), ("Unpaid", D("118.00")))


class AgeingReportTests(OutstandingBase):
    AS_ON = "2026-07-15"

    def book(self, customer, due, posting="2026-04-01", amount=100):
        d = self.sale(
            customer,
            (self.item18, 1, amount),
            posting_date=posting,
            **({"due_date": due} if due else {}),
        )
        return self.si_obj(d)

    def setUp(self):
        super().setUp()
        # Each invoice is 118.00 (100 + 18% GST).
        self.a = self.book(self.cust_intra, "2026-07-01")  # 14 days   -> 0-30
        self.b = self.book(self.cust_intra, "2026-06-01")  # 44 days   -> 31-60
        self.c = self.book(self.cust_inter, "2026-04-20")  # 86 days   -> 61-90
        self.d = self.book(self.cust_inter, "2026-01-01")  # 195 days  -> 90+
        self.e = self.book(self.cust_inter, None)  # posting 2026-04-01: 105 -> 90+
        self.f = self.book(self.cust_intra, "2026-08-01")  # not yet due -> 0-30
        self.paid = self.book(self.cust_intra, "2026-01-01")
        self.receive(self.cust_intra, [(self.paid, "118.00")], "118.00")
        self.later = self.book(self.cust_intra, "2026-08-10", posting="2026-08-05")
        self.draft = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )

    def get(self, **params):
        params.setdefault("party_type", "Customer")
        params.setdefault("as_on", self.AS_ON)
        res = self.client.get(AGEING_URL, params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def test_rows_and_buckets(self):
        data = self.get(company=self.company.id)
        rows = {r["invoice"]: r for r in data["rows"]}
        self.assertEqual(
            set(rows),
            {self.a.name, self.b.name, self.c.name, self.d.name, self.e.name, self.f.name},
        )

        def bucket(inv):
            return {k: v for k, v in rows[inv.name]["buckets"].items() if D(v)}

        self.assertEqual(bucket(self.a), {"0-30": "118.00"})
        self.assertEqual(bucket(self.b), {"31-60": "118.00"})
        self.assertEqual(bucket(self.c), {"61-90": "118.00"})
        self.assertEqual(bucket(self.d), {"90+": "118.00"})
        self.assertEqual(bucket(self.e), {"90+": "118.00"})
        self.assertEqual(bucket(self.f), {"0-30": "118.00"})
        self.assertEqual(rows[self.a.name]["days_overdue"], 14)
        self.assertEqual(rows[self.e.name]["days_overdue"], 105)
        self.assertEqual(rows[self.f.name]["days_overdue"], 0)
        self.assertEqual(rows[self.a.name]["due_date"], "2026-07-01")
        self.assertIsNone(rows[self.e.name]["due_date"])
        self.assertEqual(rows[self.a.name]["rounded_total"], "118.00")

    def test_partial_payment_shows_the_remainder(self):
        self.receive(self.cust_intra, [(self.b, "18.00")], "18.00")
        rows = {r["invoice"]: r for r in self.get()["rows"]}
        self.assertEqual(rows[self.b.name]["outstanding"], "100.00")
        self.assertEqual(rows[self.b.name]["buckets"]["31-60"], "100.00")

    def test_totals_per_party_and_grand_total(self):
        data = self.get()
        parties = {p["party"]: p for p in data["parties"]}
        intra, inter = parties["Intra Cust"], parties["Inter Cust"]
        self.assertEqual(intra["outstanding"], "354.00")  # a + b + f
        self.assertEqual(
            intra["buckets"], {"0-30": "236.00", "31-60": "118.00", "61-90": "0.00", "90+": "0.00"}
        )
        self.assertEqual(inter["outstanding"], "354.00")  # c + d + e
        self.assertEqual(
            inter["buckets"], {"0-30": "0.00", "31-60": "0.00", "61-90": "118.00", "90+": "236.00"}
        )
        self.assertEqual(data["total"]["outstanding"], "708.00")
        self.assertEqual(
            data["total"]["buckets"],
            {"0-30": "236.00", "31-60": "118.00", "61-90": "118.00", "90+": "236.00"},
        )
        self.assertEqual(data["total"]["rounded_total"], "708.00")

    def test_party_filter_and_as_on(self):
        data = self.get(party="Inter Cust")
        self.assertEqual({r["party"] for r in data["rows"]}, {"Inter Cust"})
        self.assertEqual(data["total"]["outstanding"], "354.00")
        # A later as_on includes the invoice posted in August and ages the rest.
        later = self.get(as_on="2026-09-01")
        self.assertIn(self.later.name, {r["invoice"] for r in later["rows"]})

    def test_default_as_on_is_today(self):
        res = self.client.get(AGEING_URL, {"party_type": "Customer"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["as_on"], datetime.date.today().isoformat())

    def test_payable_ageing(self):
        pi = self.pi_obj(
            self.purchase(
                self.sup_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        data = self.get(party_type="Supplier")
        self.assertEqual([r["invoice"] for r in data["rows"]], [pi.name])
        self.assertEqual(data["rows"][0]["buckets"]["61-90"], "118.00")  # 75 days
        self.assertEqual(data["total"]["outstanding"], "118.00")
        self.pay(self.sup_intra, [(pi, "118.00")], "118.00")
        self.assertEqual(self.get(party_type="Supplier")["rows"], [])

    def test_validation(self):
        self.assertEqual(self.client.get(AGEING_URL).status_code, 400)
        self.assertEqual(self.client.get(AGEING_URL, {"party_type": "Other"}).status_code, 400)
        res = self.client.get(AGEING_URL, {"party_type": "Customer", "as_on": "nope"})
        self.assertEqual(res.status_code, 400)

    def test_rbac(self):
        user = User.objects.create_user(username="nobody", email="n@test.com", password="x")
        client = APIClient()
        self.assertEqual(client.get(AGEING_URL, {"party_type": "Customer"}).status_code, 401)
        client.force_authenticate(user)
        self.assertEqual(client.get(AGEING_URL, {"party_type": "Customer"}).status_code, 403)
        role = Role.objects.create(name="Ledger reader")
        RolePermission.objects.create(role=role, model_label="accounts.GLEntry", can_read=True)
        UserRole.objects.create(user=user, role=role)
        res = client.get(AGEING_URL, {"party_type": "Customer", "as_on": self.AS_ON})
        self.assertEqual(res.status_code, 200, res.content)
