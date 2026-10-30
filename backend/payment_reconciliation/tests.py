"""Payment Reconciliation tool: net-zero reconciliation entry, outstanding, ageing,
unreconcile, API, RBAC and the Tally export of the entry."""

from buying.testing import next_bill_no
import datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from accounts.models import (
    GLEntry,
    InvoiceStatus,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentType,
)
from accounts.services import outstanding as outstanding_service
from buying.models import PurchaseInvoice, PurchaseInvoiceItem
from core.models import Company, DocStatus
from payment_reconciliation import services
from payment_reconciliation.models import (
    PaymentReconciliation,
    PaymentReconciliationAllocation,
)
from selling import mapping as selling_mapping
from selling.models import Customer, SalesInvoice, SalesInvoiceItem
from tally_export.tests.test_export import Base as TallyBase
from users.models import Role, RolePermission, User, UserRole

D = Decimal
SI = "selling.SalesInvoice"
PI = "buying.PurchaseInvoice"
PE = "accounts.PaymentEntry"
JE = "accounts.JournalEntry"
BASE_URL = "/api/payment-reconciliation/entries/"


class ReconBase(TallyBase):
    """Company with Debtors / Creditors, a service item (no stock) and one customer
    and one supplier."""

    def setUp(self):
        super().setUp()
        self.customer2 = Customer.objects.create(
            name="Other Cust",
            company=self.company,
            customer_group=self.customer.customer_group,
        )
        self.admin = User.objects.create_superuser(
            username="recon_admin", email="r@test.com", password="x"
        )

    # ------------------------------------------------------------- documents

    def bill(self, amount, date=datetime.date(2026, 4, 1), customer=None):
        si = SalesInvoice.objects.create(
            name=self.name("SINV"),
            customer=customer or self.customer,
            company=self.company,
            posting_date=date,
            update_stock=False,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si, item=self.service, qty=D(1), rate=D(amount)
        )
        si.submit()
        return SalesInvoice.objects.get(pk=si.pk)

    def purchase_bill(self, amount, date=datetime.date(2026, 4, 1)):
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name=self.name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=date,
            update_stock=False,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi, item=self.service, qty=D(1), rate=D(amount)
        )
        pi.submit()
        return PurchaseInvoice.objects.get(pk=pi.pk)

    def advance(self, amount, date=datetime.date(2026, 4, 2), customer=None):
        """A receipt with no invoice allocated: the whole amount is on account."""
        pay = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.RECEIVE,
            posting_date=date,
            company=self.company,
            party_type="Customer",
            party_name=(customer or self.customer).name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D(amount),
            received_amount=D(amount),
            reference_no="UTR-ADV",
        )
        pay.submit()
        return pay

    def supplier_advance(self, amount, date=datetime.date(2026, 4, 2)):
        pay = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.PAY,
            posting_date=date,
            company=self.company,
            party_type="Supplier",
            party_name=self.supplier.name,
            paid_from=self.bank,
            paid_to=self.creditors,
            paid_amount=D(amount),
            received_amount=D(amount),
        )
        pay.submit()
        return pay

    def credit_note(self, amount, customer=None):
        """A standalone-ish credit note that keeps its own outstanding."""
        original = self.bill(amount, customer=customer)
        ret = selling_mapping.make_sales_return(original)
        ret.save()
        ret.submit()
        return original, SalesInvoice.objects.get(pk=ret.pk)

    def fresh(self, doc):
        return type(doc).objects.get(pk=doc.pk)

    def alloc(self, source, invoice, amount, source_doctype=PE, invoice_doctype=SI):
        return {
            "source_doctype": source_doctype,
            "source_name": source.name,
            "invoice_doctype": invoice_doctype,
            "invoice_name": invoice.name,
            "amount": str(amount),
        }

    def reconcile(self, allocations, party_type="Customer", party="Cust", **kw):
        return services.reconcile(
            self.company, party_type, party, allocations, user=self.admin, **kw
        )

    def balance(self, party="Cust"):
        return outstanding_service.party_balance("Customer", party, self.company.pk)

    def integrity_ok(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def gl_rows(self, doc):
        return sorted(
            (e.account.name, e.debit, e.credit, e.against_voucher_no)
            for e in GLEntry.objects.filter(voucher_no=doc.name, is_cancelled=False)
        )


class ListingTests(ReconBase):
    def test_lists_payments_notes_and_invoices_of_the_party_only(self):
        inv = self.bill(1000)
        other = self.bill(700, customer=self.customer2)
        adv = self.advance(400)
        original, cn = self.credit_note(250)
        data = services.unreconciled(self.company, "Customer", "Cust")
        self.assertEqual([p["name"] for p in data["payments"]], [adv.name])
        self.assertEqual(data["payments"][0]["unallocated"], "400.00")
        self.assertEqual(data["payments"][0]["reference"], "UTR-ADV")
        self.assertEqual([n["name"] for n in data["notes"]], [cn.name])
        self.assertEqual(data["notes"][0]["unallocated"], "250.00")
        names = [i["name"] for i in data["invoices"]]
        self.assertIn(inv.name, names)
        self.assertNotIn(other.name, names)
        self.assertEqual(data["totals"]["payments"], "400.00")

    def test_a_receipt_fully_allocated_to_invoices_is_not_listed(self):
        inv = self.bill(500)
        self.receipt(inv, 500)
        self.assertEqual(services.list_payments(self.company, "Customer", "Cust"), [])

    def test_the_remainder_of_a_part_allocated_receipt_is_listed(self):
        inv = self.bill(300)
        pay = self.receipt(inv, 300)
        PaymentEntry.objects.filter(pk=pay.pk)  # (already submitted)
        # A receipt of 500 allocated 300 leaves 200 on account.
        inv2 = self.bill(300)
        pe = PaymentEntry.objects.create(
            name=self.name("PE"),
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date(2026, 4, 3),
            company=self.company,
            party_type="Customer",
            party_name="Cust",
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D(500),
            received_amount=D(500),
        )
        from accounts.models import PaymentEntryReference

        PaymentEntryReference.objects.create(
            payment_entry=pe,
            reference_doctype=SI,
            reference_name=inv2.name,
            allocated_amount=D(300),
        )
        pe.submit()
        rows = services.list_payments(self.company, "Customer", "Cust")
        self.assertEqual(
            [(r["name"], r["unallocated"]) for r in rows], [(pe.name, "200.00")]
        )

    def test_date_range_filters_by_posting_date(self):
        a = self.advance(100, date=datetime.date(2026, 4, 2))
        b = self.advance(200, date=datetime.date(2026, 5, 2))
        rows = services.list_payments(
            self.company, "Customer", "Cust", from_date=datetime.date(2026, 5, 1)
        )
        self.assertEqual([r["name"] for r in rows], [b.name])
        rows = services.list_payments(
            self.company, "Customer", "Cust", to_date=datetime.date(2026, 4, 30)
        )
        self.assertEqual([r["name"] for r in rows], [a.name])

    def test_advance_journal_entry_is_listed_but_imported_and_opening_are_not(self):
        def jv(name, **kw):
            j = JournalEntry.objects.create(
                name=name,
                posting_date=datetime.date(2026, 4, 2),
                company=self.company,
                **kw,
            )
            JournalEntryLine.objects.create(
                journal_entry=j, account=self.bank, debit=D(90)
            )
            JournalEntryLine.objects.create(
                journal_entry=j,
                account=self.debtors,
                credit=D(90),
                party_type="Customer",
                party="Cust",
            )
            j.submit()
            return j

        ok = jv("JV-ADV-1")
        jv("TLY-JV-1")
        jv("JV-OPEN-1", voucher_type="Opening Entry")
        rows = services.list_payments(self.company, "Customer", "Cust")
        self.assertEqual([r["name"] for r in rows], [ok.name])
        self.assertEqual(rows[0]["kind"], "Journal Entry")
        rows = services.list_payments(
            self.company, "Customer", "Cust", include_imported=True
        )
        self.assertIn("TLY-JV-1", [r["name"] for r in rows])

    def test_unknown_party_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.unreconciled(self.company, "Customer", "Nobody")


class ReconcileTests(ReconBase):
    def test_advance_against_invoice(self):
        inv = self.bill(600)
        adv = self.advance(1000)
        before = self.balance()
        self.assertEqual(before, D("-400.00"))
        rec = self.reconcile([self.alloc(adv, inv, 600)])
        self.assertEqual(rec.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(rec.total_allocated, D("600.00"))
        self.assertTrue(rec.name.startswith("PREC-"))
        inv = self.fresh(inv)
        self.assertEqual(
            (inv.outstanding_amount, inv.status), (D("0.00"), InvoiceStatus.PAID)
        )
        # Party balance is unchanged; the advance has 400 left.
        self.assertEqual(self.balance(), before)
        rows = services.list_payments(self.company, "Customer", "Cust")
        self.assertEqual(rows[0]["unallocated"], "400.00")
        # Net-zero voucher on Debtors, one row tagged to each side.
        self.assertEqual(
            self.gl_rows(rec),
            sorted(
                [
                    ("Debtors", D("0.00"), D("600.00"), inv.name),
                    ("Debtors", D("600.00"), D("0.00"), adv.name),
                ],
                key=lambda r: r[:3],
            ),
        )
        agg = GLEntry.objects.filter(voucher_no=rec.name)
        self.assertEqual(sum(e.debit for e in agg), sum(e.credit for e in agg))
        self.integrity_ok()

    def test_partial_allocation_then_remainder(self):
        inv = self.bill(1000)
        adv1 = self.advance(300)
        adv2 = self.advance(400, date=datetime.date(2026, 4, 3))
        self.reconcile([self.alloc(adv1, inv, 300)])
        inv = self.fresh(inv)
        self.assertEqual(
            (inv.outstanding_amount, inv.status),
            (D("700.00"), InvoiceStatus.PARTLY_PAID),
        )
        self.reconcile([self.alloc(adv2, inv, 400)])
        inv = self.fresh(inv)
        self.assertEqual(inv.outstanding_amount, D("300.00"))
        self.assertEqual(services.list_payments(self.company, "Customer", "Cust"), [])
        self.integrity_ok()

    def test_one_payment_across_two_invoices(self):
        i1, i2 = self.bill(300), self.bill(500, date=datetime.date(2026, 4, 5))
        adv = self.advance(700)
        rec = self.reconcile([self.alloc(adv, i1, 300), self.alloc(adv, i2, 400)])
        self.assertEqual(self.fresh(i1).outstanding_amount, D("0.00"))
        self.assertEqual(self.fresh(i2).outstanding_amount, D("100.00"))
        # The advance is one Dr row of 700.
        self.assertEqual(
            [r for r in self.gl_rows(rec) if r[3] == adv.name],
            [("Debtors", D("700.00"), D("0.00"), adv.name)],
        )
        self.integrity_ok()

    def test_credit_note_against_invoice(self):
        inv = self.bill(800)
        original, cn = self.credit_note(300)
        self.assertEqual(cn.outstanding_amount, D("-300.00"))
        before = self.balance()
        rec = self.reconcile([self.alloc(cn, inv, 300, source_doctype=SI)])
        self.assertEqual(self.fresh(inv).outstanding_amount, D("500.00"))
        cn = self.fresh(cn)
        self.assertEqual(cn.outstanding_amount, D("0.00"))
        self.assertEqual(cn.status, InvoiceStatus.RETURN)
        self.assertEqual(self.balance(), before)
        # Cancelling the reconciliation restores both.
        rec.cancel()
        self.assertEqual(self.fresh(inv).outstanding_amount, D("800.00"))
        self.assertEqual(self.fresh(cn).outstanding_amount, D("-300.00"))
        self.integrity_ok()

    def test_partial_credit_note_use_keeps_a_negative_balance(self):
        inv = self.bill(100)
        _o, cn = self.credit_note(300)
        self.reconcile([self.alloc(cn, inv, 100, source_doctype=SI)])
        self.assertEqual(self.fresh(cn).outstanding_amount, D("-200.00"))
        self.assertEqual(self.fresh(inv).outstanding_amount, D("0.00"))

    def test_ageing_shows_the_reconciled_figures(self):
        inv = self.bill(1000)
        adv = self.advance(400)
        self.reconcile([self.alloc(adv, inv, 400)])
        report = outstanding_service.ageing_report(
            "Customer", as_on=datetime.date(2026, 4, 10), company_id=self.company.pk
        )
        row = [r for r in report["rows"] if r["invoice"] == inv.name][0]
        self.assertEqual(row["outstanding"], "600.00")
        self.assertEqual(row["buckets"]["0-30"], "600.00")

    def test_supplier_side_mirror(self):
        pi = self.purchase_bill(900)
        adv = self.supplier_advance(500)
        before = outstanding_service.party_balance("Supplier", "Supp", self.company.pk)
        data = services.unreconciled(self.company, "Supplier", "Supp")
        self.assertEqual(data["payments"][0]["unallocated"], "500.00")
        rec = self.reconcile(
            [self.alloc(adv, pi, 500, invoice_doctype=PI)],
            party_type="Supplier",
            party="Supp",
        )
        self.assertEqual(self.fresh(pi).outstanding_amount, D("400.00"))
        self.assertEqual(
            outstanding_service.party_balance("Supplier", "Supp", self.company.pk),
            before,
        )
        rows = {(r[0], r[3]): r for r in self.gl_rows(rec)}
        self.assertEqual(rows[("Creditors", pi.name)][1], D("500.00"))  # Dr invoice
        self.assertEqual(rows[("Creditors", adv.name)][2], D("500.00"))  # Cr advance
        rec.cancel()
        self.assertEqual(self.fresh(pi).outstanding_amount, D("900.00"))
        self.integrity_ok()


class RejectionTests(ReconBase):
    def test_over_allocating_an_invoice_is_rejected(self):
        inv = self.bill(100)
        adv = self.advance(500)
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, inv, 150)])
        self.assertEqual(PaymentReconciliation.objects.count(), 0)
        self.assertEqual(self.fresh(inv).outstanding_amount, D("100.00"))

    def test_over_using_a_payment_is_rejected_even_across_entries(self):
        i1, i2 = self.bill(300), self.bill(300)
        adv = self.advance(400)
        self.reconcile([self.alloc(adv, i1, 300)])
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, i2, 150)])
        self.reconcile([self.alloc(adv, i2, 100)])

    def test_two_rows_together_over_use_a_payment(self):
        i1, i2 = self.bill(300), self.bill(300)
        adv = self.advance(400)
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, i1, 250), self.alloc(adv, i2, 250)])

    def test_cross_party_is_rejected(self):
        inv_other = self.bill(100, customer=self.customer2)
        adv = self.advance(100)  # belongs to Cust
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, inv_other, 100)])
        with self.assertRaises(ValidationError):
            # the other party's invoice under the other party's name, Cust's payment
            self.reconcile([self.alloc(adv, inv_other, 100)], party="Other Cust")

    def test_zero_negative_and_empty_allocations_are_rejected(self):
        inv, adv = self.bill(100), self.advance(100)
        with self.assertRaises(ValidationError):
            self.reconcile([])
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                self.reconcile([self.alloc(adv, inv, 0)])
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                self.reconcile([self.alloc(adv, inv, -5)])

    def test_wrong_document_kinds_are_rejected(self):
        inv, other = self.bill(100), self.bill(100)
        # An invoice cannot be the "payment" side.
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(other, inv, 50, source_doctype=SI)])
        # A purchase invoice cannot be reconciled for a customer.
        pi = self.purchase_bill(100)
        adv = self.advance(100)
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, pi, 50, invoice_doctype=PI)])

    def test_a_return_cannot_be_the_invoice_side(self):
        _o, cn = self.credit_note(100)
        adv = self.advance(100)
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, cn, 50)])

    def test_unsubmitted_and_cancelled_documents_are_rejected(self):
        inv = self.bill(100)
        adv = self.advance(100)
        adv.cancel()
        with self.assertRaises(ValidationError):
            self.reconcile([self.alloc(adv, inv, 50)])

    def test_a_reconciled_document_cannot_be_cancelled_until_unreconciled(self):
        inv, adv = self.bill(100), self.advance(100)
        rec = self.reconcile([self.alloc(adv, inv, 100)])
        with self.assertRaises(ValidationError):
            self.fresh(adv).cancel()
        with self.assertRaises(ValidationError):
            self.fresh(inv).cancel()
        rec.cancel()
        self.fresh(adv).cancel()
        self.fresh(inv).cancel()
        self.integrity_ok()

    def test_allocations_of_a_submitted_entry_are_immutable(self):
        inv, adv = self.bill(100), self.advance(100)
        rec = self.reconcile([self.alloc(adv, inv, 100)])
        row = rec.allocations.get()
        row.amount = D("1.00")
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        with self.assertRaises(ValidationError):
            rec.delete()
        rec.total_allocated = D("1.00")
        with self.assertRaises(ValidationError):
            rec.save()

    def test_db_constraint_on_amount(self):
        inv, adv = self.bill(100), self.advance(100)
        rec = PaymentReconciliation.objects.create(
            company=self.company,
            name="PREC-X",
            posting_date=datetime.date(2026, 4, 5),
            party_type="Customer",
            party_name="Cust",
            party_account=self.debtors,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentReconciliationAllocation.objects.create(
                reconciliation=rec,
                source_doctype=PE,
                source_name=adv.name,
                invoice_doctype=SI,
                invoice_name=inv.name,
                amount=D("0.00"),
            )


class UnreconcileTests(ReconBase):
    def test_unreconcile_restores_everything(self):
        inv, adv = self.bill(600), self.advance(1000)
        before = self.balance()
        rec = self.reconcile([self.alloc(adv, inv, 600)])
        rec.cancel()
        rec = self.fresh(rec)
        self.assertEqual(rec.docstatus, DocStatus.CANCELLED)
        inv = self.fresh(inv)
        self.assertEqual(
            (inv.outstanding_amount, inv.status), (D("600.00"), InvoiceStatus.UNPAID)
        )
        self.assertEqual(self.balance(), before)
        self.assertEqual(
            services.list_payments(self.company, "Customer", "Cust")[0]["unallocated"],
            "1000.00",
        )
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=rec.name, is_cancelled=False).exists()
        )
        # And it can be reconciled again.
        self.reconcile([self.alloc(adv, inv, 600)])
        self.integrity_ok()


class AutoAllocateTests(ReconBase):
    def test_oldest_invoice_first_oldest_payment_first(self):
        i1 = self.bill(300, date=datetime.date(2026, 4, 1))
        i2 = self.bill(500, date=datetime.date(2026, 4, 5))
        p1 = self.advance(200, date=datetime.date(2026, 4, 2))
        p2 = self.advance(400, date=datetime.date(2026, 4, 6))
        out = services.preview_allocation(
            self.company,
            "Customer",
            "Cust",
            [{"doctype": PE, "name": p2.name}, {"doctype": PE, "name": p1.name}],
            [{"doctype": SI, "name": i2.name}, {"doctype": SI, "name": i1.name}],
        )
        pairs = [
            (r["source_name"], r["invoice_name"], r["amount"])
            for r in out["allocations"]
        ]
        self.assertEqual(
            pairs,
            [
                (p1.name, i1.name, "200.00"),
                (p2.name, i1.name, "100.00"),
                (p2.name, i2.name, "300.00"),
            ],
        )
        self.assertEqual(out["total_allocated"], "600.00")
        self.assertEqual(out["unallocated_sources"], "0.00")
        self.assertEqual(out["unpaid_invoices"], "200.00")
        # What the preview returns is exactly what reconcile accepts.
        rec = self.reconcile(out["allocations"])
        self.assertEqual(rec.total_allocated, D("600.00"))
        self.assertEqual(self.fresh(i1).outstanding_amount, D("0.00"))
        self.assertEqual(self.fresh(i2).outstanding_amount, D("200.00"))

    def test_optional_amount_only_lowers_the_cap(self):
        inv, adv = self.bill(500), self.advance(400)
        out = services.preview_allocation(
            self.company,
            "Customer",
            "Cust",
            [{"doctype": PE, "name": adv.name, "amount": "9999"}],
            [{"doctype": SI, "name": inv.name, "amount": "100"}],
        )
        self.assertEqual(out["allocations"][0]["amount"], "100.00")

    def test_credit_note_participates_and_unknown_pick_is_rejected(self):
        inv = self.bill(400)
        _o, cn = self.credit_note(150)
        out = services.preview_allocation(
            self.company,
            "Customer",
            "Cust",
            [{"doctype": SI, "name": cn.name}],
            [{"doctype": SI, "name": inv.name}],
        )
        self.assertEqual(out["allocations"][0]["amount"], "150.00")
        with self.assertRaises(ValidationError):
            services.preview_allocation(
                self.company,
                "Customer",
                "Cust",
                [{"doctype": PE, "name": "PE-NOPE"}],
                [{"doctype": SI, "name": inv.name}],
            )


class ApiTests(ReconBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.hdr = {"HTTP_X_COMPANY_ID": str(self.company.pk)}

    def test_full_flow_over_http(self):
        inv, adv = self.bill(600), self.advance(1000)
        res = self.client.get(
            BASE_URL + "unreconciled/",
            {"party_type": "Customer", "party": "Cust"},
            **self.hdr,
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["payments"][0]["name"], adv.name)
        self.assertEqual(res.data["totals"]["invoices"], "600.00")
        res = self.client.post(
            BASE_URL + "allocate/",
            {
                "party_type": "Customer",
                "party": "Cust",
                "payments": [{"doctype": PE, "name": adv.name}],
                "invoices": [{"doctype": SI, "name": inv.name}],
            },
            format="json",
            **self.hdr,
        )
        self.assertEqual(res.status_code, 200, res.content)
        allocations = res.data["allocations"]
        res = self.client.post(
            BASE_URL + "reconcile/",
            {"party_type": "Customer", "party": "Cust", "allocations": allocations},
            format="json",
            **self.hdr,
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["total_allocated"], "600.00")
        self.assertEqual(self.fresh(inv).outstanding_amount, D("0.00"))
        pk = res.data["id"]
        self.assertEqual(self.client.get(BASE_URL, **self.hdr).data["count"], 1)
        res = self.client.post(f"{BASE_URL}{pk}/unreconcile/", **self.hdr)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["docstatus"], 2)
        self.assertEqual(self.fresh(inv).outstanding_amount, D("600.00"))

    def test_validation_errors_are_400(self):
        inv, adv = self.bill(100), self.advance(500)
        res = self.client.post(
            BASE_URL + "reconcile/",
            {
                "party_type": "Customer",
                "party": "Cust",
                "allocations": [self.alloc(adv, inv, 150)],
            },
            format="json",
            **self.hdr,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.data)
        res = self.client.get(
            BASE_URL + "unreconciled/", {"party_type": "Customer"}, **self.hdr
        )
        self.assertEqual(res.status_code, 400)

    def test_other_company_cannot_see_or_use_it(self):
        inv, adv = self.bill(100), self.advance(100)
        rec = self.reconcile([self.alloc(adv, inv, 100)])
        other = Company.objects.create(name="Other Co", abbr="OC")
        res = self.client.get(BASE_URL, HTTP_X_COMPANY_ID=str(other.pk))
        self.assertEqual(res.data["count"], 0)
        res = self.client.post(
            f"{BASE_URL}{rec.pk}/unreconcile/", HTTP_X_COMPANY_ID=str(other.pk)
        )
        self.assertEqual(res.status_code, 404)

    def test_rbac(self):
        inv, adv = self.bill(100), self.advance(100)
        user = User.objects.create_user(username="clerk", email="c@t.com", password="x")
        role = Role.objects.create(name="Reco Reader")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role,
            model_label="payment_reconciliation.PaymentReconciliation",
            can_read=True,
        )
        client = APIClient()
        client.force_authenticate(user)
        from core.models import UserCompany

        UserCompany.objects.get_or_create(
            user=user, defaults={"company": self.company, "is_default": True}
        )
        hdr = self.hdr
        self.assertEqual(
            client.get(
                BASE_URL + "unreconciled/",
                {"party_type": "Customer", "party": "Cust"},
                **hdr,
            ).status_code,
            200,
        )
        body = {
            "party_type": "Customer",
            "party": "Cust",
            "payments": [{"doctype": PE, "name": adv.name}],
            "invoices": [{"doctype": SI, "name": inv.name}],
        }
        self.assertEqual(
            client.post(BASE_URL + "allocate/", body, format="json", **hdr).status_code,
            200,
        )
        res = client.post(
            BASE_URL + "reconcile/",
            {
                "party_type": "Customer",
                "party": "Cust",
                "allocations": [self.alloc(adv, inv, 100)],
            },
            format="json",
            **hdr,
        )
        self.assertEqual(res.status_code, 403)
        rec = self.reconcile([self.alloc(adv, inv, 100)])
        self.assertEqual(
            client.post(f"{BASE_URL}{rec.pk}/unreconcile/", **hdr).status_code, 403
        )
        anon = APIClient()
        self.assertIn(anon.get(BASE_URL, **hdr).status_code, (401, 403))


class TallyExportTests(ReconBase):
    def setUp(self):
        super().setUp()
        self.map_all()

    def bills(self, voucher):
        out = []
        for e in voucher["el"].findall("ALLLEDGERENTRIES.LIST"):
            for b in e.findall("BILLALLOCATIONS.LIST"):
                out.append(
                    (
                        e.findtext("LEDGERNAME"),
                        b.findtext("BILLTYPE"),
                        b.findtext("NAME") or "",
                        signed(b.findtext("AMOUNT")),
                    )
                )
        return sorted(out)

    def test_advance_reconciliation_is_a_balanced_journal_with_bill_references(self):
        inv, adv = self.bill(600), self.advance(1000)
        rec = self.reconcile([self.alloc(adv, inv, 600)])
        batch = self.export()
        vouchers = self.vouchers(batch)
        v = vouchers[rec.name]
        self.assertEqual(v["type"], "Journal")
        party = "Cust  & Sons <Indore>"
        # Debit-positive: Cr invoice side, Dr advance side; Dr == Cr.
        self.assertEqual(
            sorted(v["ledgers"]), [(party, D("-600.00")), (party, D("600.00"))]
        )
        self.assertEqual(sum(a for _n, a in v["ledgers"]), D("0.00"))
        self.assertEqual(
            self.bills(v),
            sorted(
                [
                    (party, "Agst Ref", inv.name, D("-600.00")),
                    (party, "On Account", "", D("600.00")),
                ]
            ),
        )
        # The invoice and receipt were exported too (their own vouchers).
        self.assertIn(inv.name, vouchers)
        self.assertEqual(batch.vouchers.filter(doctype=rec.voucher_label()).count(), 1)

    def test_credit_note_side_references_the_note_by_name(self):
        inv = self.bill(800)
        _o, cn = self.credit_note(300)
        rec = self.reconcile([self.alloc(cn, inv, 300, source_doctype=SI)])
        v = self.vouchers(self.export())[rec.name]
        party = "Cust  & Sons <Indore>"
        self.assertEqual(
            self.bills(v),
            sorted(
                [
                    (party, "Agst Ref", inv.name, D("-300.00")),
                    (party, "Agst Ref", cn.name, D("300.00")),
                ]
            ),
        )

    def test_unreconcile_after_export_sends_a_cancel(self):
        inv, adv = self.bill(100), self.advance(100)
        rec = self.reconcile([self.alloc(adv, inv, 100)])
        self.export()
        rec.cancel()
        batch = self.export()
        v = self.vouchers(batch)[rec.name]
        self.assertEqual(v["action"], "Cancel")


def signed(text):
    from tally_export.tests.test_export import signed_debit

    return signed_debit(text)
