"""The opening bills generator (planning 09, G-2): plan, dry run and commit."""

import csv
import datetime
import os
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import GLEntry, OpeningBill
from accounts.services import opening as opening_service
from accounts.services.outstanding import party_balance
from accounts.test_outstanding import OutstandingBase
from core.models import DocStatus

D = Decimal
AS_ON = datetime.date(2026, 4, 30)


class OpeningBuildBase(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.cust_intra.credit_days = 30
        self.cust_intra.save(update_fields=["credit_days"])

    def build(self, **options):
        out = StringIO()
        call_command(
            "build_opening_bills",
            as_on=AS_ON.isoformat(),
            company=self.company.abbr,
            stdout=out,
            **options,
        )
        return out.getvalue()

    def bills_for(self, party):
        return list(
            OpeningBill.objects.filter(party=party, docstatus=DocStatus.SUBMITTED)
            .order_by("bill_date", "name")
            .values_list("bill_date", "amount")
        )

    def plan(self):
        return opening_service.plan_opening_bills(self.company, AS_ON)

    def rows_for(self, party, kind="bill"):
        return [
            r for r in self.plan()["rows"] if r["party"] == party and r["kind"] == kind
        ]


class PlanTests(OpeningBuildBase):
    def test_simple_party_is_one_bill_for_its_invoice_with_credit_days(self):
        inv = self.sale(
            self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10"
        )
        rows = self.rows_for(self.cust_intra.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], D("118.00"))
        self.assertEqual(rows[0]["bill_date"], datetime.date(2026, 1, 10))
        self.assertEqual(rows[0]["reference"], inv["name"])
        self.assertEqual(rows[0]["due_date"], datetime.date(2026, 2, 9))  # +30 days
        self.assertEqual(self.plan()["problems"], [])

    def test_part_paid_party_walks_newest_first_and_splits_the_last_entry(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-02-10")
        # 236.00 billed, 100.00 received on account: 136.00 still owed.
        self.receive(self.cust_intra, [], "100.00")
        rows = self.rows_for(self.cust_intra.name)
        self.assertEqual(
            sorted((r["bill_date"], r["amount"]) for r in rows),
            [
                (datetime.date(2026, 1, 10), D("18.00")),  # the rest of the older one
                (datetime.date(2026, 2, 10), D("118.00")),  # the newest, whole
            ],
        )
        self.assertEqual(sum(r["amount"] for r in rows), D("136.00"))
        self.assertEqual(self.plan()["problems"], [])

    def test_credit_balance_is_an_advance_and_creates_no_bill(self):
        self.receive(self.cust_inter, [], "50.00")
        self.assertEqual(self.rows_for(self.cust_inter.name), [])
        advances = self.rows_for(self.cust_inter.name, kind="advance")
        self.assertEqual(len(advances), 1)
        self.assertEqual(advances[0]["amount"], D("50.00"))

    def test_party_with_no_debits_is_an_advance(self):
        # A supplier we only paid: no purchase, so no credit to walk.
        self.pay(self.sup_intra, [], "30.00")
        self.assertEqual(self.rows_for(self.sup_intra.name), [])
        advances = self.rows_for(self.sup_intra.name, kind="advance")
        self.assertEqual([a["amount"] for a in advances], [D("30.00")])

    def test_party_not_in_the_masters_is_for_review(self):
        self.receive(self.cust_intra, [], "70.00", party_name="Ghost Ltd")
        plan = self.plan()
        self.assertTrue(any("Ghost Ltd" in p for p in plan["problems"]))
        ghost = [r for r in plan["rows"] if r["party"] == "Ghost Ltd"]
        self.assertEqual([r["kind"] for r in ghost], ["review"])

    def test_plan_is_read_only(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        before = GLEntry.objects.count()
        self.plan()
        self.assertEqual(GLEntry.objects.count(), before)
        self.assertEqual(OpeningBill.objects.count(), 0)


class CommandTests(OpeningBuildBase):
    def test_dry_run_is_the_default_and_writes_nothing(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        gl_before = GLEntry.objects.count()
        out = self.build()
        self.assertIn("[DRY-RUN]", out)
        self.assertEqual(OpeningBill.objects.count(), 0)
        self.assertEqual(GLEntry.objects.count(), gl_before)

    def test_out_writes_the_plan_as_csv_in_a_dry_run(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.receive(self.cust_inter, [], "50.00")
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        try:
            self.build(out=path)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        finally:
            os.remove(path)
        self.assertEqual(
            list(rows[0].keys()),
            [
                "kind",
                "party_type",
                "party",
                "bill_date",
                "amount",
                "due_date",
                "reference",
                "method",
            ],
        )
        kinds = {(r["kind"], r["party"]) for r in rows}
        self.assertIn(("bill", self.cust_intra.name), kinds)
        self.assertIn(("advance", self.cust_inter.name), kinds)
        self.assertEqual(OpeningBill.objects.count(), 0)

    def test_commit_creates_submitted_bills_that_tie_to_the_ledger(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-02-10")
        self.receive(self.cust_intra, [], "100.00")
        self.receive(self.cust_inter, [], "50.00")  # advance: no bill
        gl_before = GLEntry.objects.count()

        self.build(commit=True)

        bills = OpeningBill.objects.filter(docstatus=DocStatus.SUBMITTED)
        self.assertEqual(bills.count(), 2)
        self.assertEqual(GLEntry.objects.count(), gl_before)  # no GL from the bills
        for bill in bills:
            self.assertEqual(bill.outstanding_amount, bill.amount)
            self.assertEqual(bill.as_on, AS_ON)
        total = sum(b.amount for b in bills)
        self.assertEqual(
            total,
            party_balance(
                "Customer", self.cust_intra.name, self.company.pk, as_on=AS_ON
            ),
        )
        self.assertEqual(
            self.bills_for(self.cust_inter.name),
            [],
        )
        self.integrity_ok()

    def test_commit_is_refused_when_bills_already_exist_for_the_date(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.build(commit=True)
        count = OpeningBill.objects.count()
        with self.assertRaises(CommandError) as ctx:
            self.build(commit=True)
        self.assertIn("already exist", str(ctx.exception))
        self.assertEqual(OpeningBill.objects.count(), count)

    def test_commit_refused_naming_a_party_to_review_and_creates_nothing(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.receive(self.cust_intra, [], "70.00", party_name="Ghost Ltd")
        with self.assertRaises(CommandError) as ctx:
            self.build(commit=True)
        self.assertIn("Ghost Ltd", str(ctx.exception))
        self.assertEqual(OpeningBill.objects.count(), 0)
        self.assertIn("Ghost Ltd", self.build())  # the dry run names it too

    def test_dry_run_and_commit_together_are_refused(self):
        with self.assertRaises(CommandError):
            call_command(
                "build_opening_bills",
                as_on=AS_ON.isoformat(),
                company=self.company.abbr,
                dry_run=True,
                commit=True,
                stdout=StringIO(),
            )

    def test_as_on_is_required_and_must_be_a_date(self):
        with self.assertRaises(CommandError):
            call_command(
                "build_opening_bills", company=self.company.abbr, stdout=StringIO()
            )
        with self.assertRaises(CommandError):
            call_command(
                "build_opening_bills",
                as_on="31/04/2026",
                company=self.company.abbr,
                stdout=StringIO(),
            )
