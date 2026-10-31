"""Opening bills (planning 09, G-1): posting, settlement, reports and guards."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.models import (
    GLEntry,
    OpeningBill,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.services import outstanding as outstanding_service
from accounts.services.allocation import suggest_allocation
from accounts.services.collections import bills_to_pay, collections
from accounts.test_outstanding import OutstandingBase
from core.models import DocStatus

D = Decimal
BILL_LABEL = "accounts.OpeningBill"
AS_ON = "2026-03-31"


class OpeningBillBase(OutstandingBase):
    _bill_seq = 0

    def bill(
        self,
        party_type="Customer",
        party=None,
        amount="100.00",
        bill_date="2026-01-10",
        due_date="2026-04-10",
        submit=True,
        **extra,
    ):
        OpeningBillBase._bill_seq += 1
        if party_type == "Customer":
            party = party or self.cust_intra.name
            account = self.debtors
        else:
            party = party or self.sup_intra.name
            account = self.creditors
        fields = {
            "name": f"OB-T-{OpeningBillBase._bill_seq:04d}",
            "company": self.company,
            "party_type": party_type,
            "party": party,
            "account": account,
            "bill_date": datetime.date.fromisoformat(bill_date),
            "due_date": datetime.date.fromisoformat(due_date),
            "as_on": datetime.date.fromisoformat(AS_ON),
            "amount": D(amount),
            "reference": "TLY-TEST",
        }
        fields.update(extra)
        ob = OpeningBill.objects.create(**fields)
        if submit:
            ob.submit()
        return ob

    def settle(self, bill, amount, submit=True, posting="2026-04-02"):
        """A receipt (Customer) or payment (Supplier) allocated to an opening bill."""
        OutstandingBase._pe_seq += 1
        if bill.party_type == "Customer":
            payment_type, paid_from, paid_to = (
                PaymentType.RECEIVE,
                self.debtors,
                self.bank,
            )
        else:
            payment_type, paid_from, paid_to = (
                PaymentType.PAY,
                self.bank,
                self.creditors,
            )
        pe = PaymentEntry.objects.create(
            name=f"PE-OB-{OutstandingBase._pe_seq:04d}",
            payment_type=payment_type,
            posting_date=datetime.date.fromisoformat(posting),
            company=self.company,
            party_type=bill.party_type,
            party_name=bill.party,
            paid_from=paid_from,
            paid_to=paid_to,
            paid_amount=D(amount),
            received_amount=D(amount),
        )
        PaymentEntryReference.objects.create(
            payment_entry=pe,
            reference_doctype=BILL_LABEL,
            reference_name=bill.name,
            allocated_amount=D(amount),
        )
        if submit:
            pe.submit()
        return pe

    def fresh(self, bill):
        bill.refresh_from_db()
        return bill


class OpeningBillPostingTests(OpeningBillBase):
    def test_submitted_bill_posts_no_gl_and_is_owed_in_full(self):
        ob = self.bill(amount="100.00")
        self.assertEqual(ob.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(GLEntry.objects.filter(voucher_no=ob.name).count(), 0)
        self.assertEqual(self.fresh(ob).outstanding_amount, D("100.00"))
        self.integrity_ok()

    def test_draft_bill_owes_nothing(self):
        ob = self.bill(submit=False)
        self.assertEqual(ob.docstatus, DocStatus.DRAFT)
        self.assertEqual(self.fresh(ob).outstanding_amount, D("0.00"))

    def test_submit_refuses_unknown_party_and_leaves_draft(self):
        ob = self.bill(party="Nobody Ltd", submit=False)
        with self.assertRaises(ValidationError):
            ob.submit()
        self.assertEqual(self.fresh(ob).docstatus, DocStatus.DRAFT)

    def test_submit_refuses_wrong_account_type(self):
        ob = self.bill(submit=False, account=self.creditors)  # payable for a customer
        with self.assertRaises(ValidationError):
            ob.submit()
        self.assertEqual(self.fresh(ob).docstatus, DocStatus.DRAFT)

    def test_submit_refuses_bill_dated_after_as_on(self):
        ob = self.bill(submit=False, bill_date="2026-04-01", due_date="2026-05-01")
        with self.assertRaises(ValidationError):
            ob.submit()

    def test_database_checks_refuse_bad_rows(self):
        cases = [
            {"amount": D("0.00")},
            {"amount": D("-5.00")},
            {"outstanding_amount": D("101.00")},
            {"outstanding_amount": D("-1.00")},
            {"due_date": datetime.date(2026, 1, 1)},  # before the bill date
            {"party_type": "Employee"},
        ]
        for i, extra in enumerate(cases):
            with self.subTest(extra=extra), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    OpeningBill.objects.create(
                        name=f"OB-BAD-{i}",
                        company=self.company,
                        party_type=extra.get("party_type", "Customer"),
                        party=self.cust_intra.name,
                        account=self.debtors,
                        bill_date=datetime.date(2026, 1, 10),
                        due_date=extra.get("due_date", datetime.date(2026, 4, 10)),
                        as_on=datetime.date(2026, 3, 31),
                        amount=extra.get("amount", D("100.00")),
                        outstanding_amount=extra.get("outstanding_amount", D("0.00")),
                    )


class OpeningBillSettlementTests(OpeningBillBase):
    def test_receipt_settles_bill_and_cancel_restores_it(self):
        ob = self.bill(amount="100.00")
        pe = self.settle(ob, "40.00")
        self.assertEqual(self.fresh(ob).outstanding_amount, D("60.00"))
        party_row = GLEntry.objects.get(
            voucher_no=pe.name, account=self.debtors, is_cancelled=False
        )
        self.assertEqual(party_row.against_voucher_type, BILL_LABEL)
        self.assertEqual(party_row.against_voucher_no, ob.name)
        self.integrity_ok()

        pe.cancel()
        self.assertEqual(self.fresh(ob).outstanding_amount, D("100.00"))
        self.integrity_ok()

    def test_full_settlement_clears_the_bill(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "100.00")
        self.assertEqual(self.fresh(ob).outstanding_amount, D("0.00"))
        self.integrity_ok()

    def test_two_receipts_cannot_settle_more_than_the_bill(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "60.00")
        second = self.settle(ob, "60.00", submit=False)
        with self.assertRaises(ValidationError):
            second.submit()
        self.assertEqual(self.fresh(ob).outstanding_amount, D("40.00"))
        self.assertEqual(self.fresh(second).docstatus, DocStatus.DRAFT)
        self.assertFalse(GLEntry.objects.filter(voucher_no=second.name).exists())

    def test_receipt_for_exactly_the_remaining_balance_is_accepted(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "60.00")
        self.settle(ob, "40.00")
        self.assertEqual(self.fresh(ob).outstanding_amount, D("0.00"))
        self.integrity_ok()

    def test_receipt_from_a_different_customer_is_refused(self):
        ob = self.bill(amount="100.00")
        other = self.settle(ob, "10.00", submit=False)
        other.party_name = self.cust_inter.name
        other.save(update_fields=["party_name"])
        with self.assertRaises(ValidationError):
            other.submit()
        self.assertEqual(self.fresh(ob).outstanding_amount, D("100.00"))

    def test_bill_cannot_be_cancelled_while_a_receipt_is_live(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "40.00")
        with self.assertRaises(ValidationError):
            ob.cancel()
        ob = self.fresh(ob)
        self.assertEqual(ob.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(ob.outstanding_amount, D("60.00"))

    def test_bill_can_be_cancelled_once_its_receipts_are_cancelled(self):
        ob = self.bill(amount="100.00")
        pe = self.settle(ob, "40.00")
        pe.cancel()
        ob.cancel()
        ob = self.fresh(ob)
        self.assertEqual(ob.docstatus, DocStatus.CANCELLED)
        self.assertEqual(ob.outstanding_amount, D("0.00"))
        self.integrity_ok()

    def test_supplier_payment_settles_a_supplier_bill(self):
        ob = self.bill(party_type="Supplier", amount="200.00")
        self.settle(ob, "150.00")
        self.assertEqual(self.fresh(ob).outstanding_amount, D("50.00"))
        self.integrity_ok()

    def test_suggestion_settles_oldest_first_across_bills_and_invoices(self):
        # The opening bill is due before the invoice, so it is suggested first.
        ob = self.bill(amount="100.00", due_date="2026-04-10")
        self.sale(
            self.cust_intra,
            (self.item18, 1, 100),
            posting_date="2026-05-01",
            due_date="2026-06-01",
        )
        refs, unallocated, total = suggest_allocation(
            "Customer", self.cust_intra.name, self.company, "150.00"
        )
        self.assertEqual(refs[0]["reference_doctype"], BILL_LABEL)
        self.assertEqual(refs[0]["reference_name"], ob.name)
        self.assertEqual(refs[0]["allocated_amount"], D("100.00"))
        self.assertEqual(refs[1]["reference_doctype"], "selling.SalesInvoice")
        self.assertEqual(refs[1]["allocated_amount"], D("50.00"))
        self.assertEqual(unallocated, D("0.00"))
        self.assertEqual(total, D("150.00"))


class OpeningBillReportTests(OpeningBillBase):
    def test_ageing_lists_the_bill_in_its_bucket(self):
        ob = self.bill(amount="100.00", bill_date="2026-01-10", due_date="2026-04-10")
        res = outstanding_service.ageing_report(
            "Customer",
            as_on=datetime.date(2026, 7, 15),
            company_id=self.company.id,
        )
        row = next(r for r in res["rows"] if r["invoice"] == ob.name)
        self.assertTrue(row["is_opening"])
        self.assertEqual(row["party"], self.cust_intra.name)
        self.assertEqual(row["outstanding"], "100.00")
        self.assertEqual(row["days_overdue"], 96)
        self.assertEqual(row["buckets"]["90+"], "100.00")
        party = next(p for p in res["parties"] if p["party"] == self.cust_intra.name)
        self.assertEqual(party["outstanding"], "100.00")

    def test_ageing_reflects_a_receipt(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "30.00")
        res = outstanding_service.ageing_report(
            "Customer", as_on=datetime.date(2026, 7, 15), company_id=self.company.id
        )
        row = next(r for r in res["rows"] if r["invoice"] == ob.name)
        self.assertEqual(row["outstanding"], "70.00")

    def test_money_to_collect_includes_the_bill(self):
        ob = self.bill(amount="100.00", due_date="2026-04-10")
        res = collections(self.company, as_on="2026-07-15")
        self.assertEqual(res["totals"]["total_receivables"], "100.00")
        self.assertEqual(res["totals"]["total_overdue"], "100.00")
        customer = res["customers"][0]
        self.assertEqual(customer["customer_name"], self.cust_intra.name)
        self.assertEqual(customer["bills"][0]["invoice"], ob.name)
        self.assertTrue(customer["bills"][0]["is_opening"])

    def test_money_to_collect_totals_match_ageing(self):
        self.bill(amount="100.00")
        self.sale(
            self.cust_intra,
            (self.item18, 1, 100),
            posting_date="2026-02-01",
            due_date="2026-05-01",
        )
        as_on = "2026-07-15"
        col = collections(self.company, as_on=as_on)
        ageing = outstanding_service.ageing_report(
            "Customer",
            as_on=datetime.date.fromisoformat(as_on),
            company_id=self.company.id,
        )
        self.assertEqual(
            col["totals"]["total_receivables"], ageing["total"]["outstanding"]
        )
        self.assertEqual(col["totals"]["invoice_count"], len(ageing["rows"]))

    def test_bill_dated_after_the_report_date_is_not_listed(self):
        self.bill(amount="100.00")
        col = collections(self.company, as_on="2026-01-05")
        self.assertEqual(col["totals"]["total_receivables"], "0.00")

    def test_bills_to_pay_includes_a_supplier_bill(self):
        ob = self.bill(party_type="Supplier", amount="200.00", due_date="2026-02-01")
        res = bills_to_pay(self.company, as_on="2026-04-01")
        self.assertEqual(res["totals"]["total_payables"], "200.00")
        self.assertEqual(res["totals"]["total_overdue"], "200.00")
        self.assertEqual(res["bills"][0]["invoice"], ob.name)
        self.assertTrue(res["bills"][0]["is_opening"])
        self.assertEqual(res["bills"][0]["supplier_name"], self.sup_intra.name)
