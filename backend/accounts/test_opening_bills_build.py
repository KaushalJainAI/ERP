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


class CsvCorrectionTests(OpeningBuildBase):
    """G-3: the accountant's corrected CSV, under the same total checks."""

    HEADER = ["kind", "party_type", "party", "bill_date", "amount", "due_date"]

    def setUp(self):
        super().setUp()
        # Customer intra owes 236.00 (two invoices); inter has a 50.00 advance.
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-02-10")
        self.receive(self.cust_inter, [], "50.00")

    def write_csv(self, rows, header=None, bom=False):
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.remove, path)
        with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header or self.HEADER)
            writer.writerows(rows)
        return path

    def intra(self, bill_date, amount, due=""):
        return ["bill", "Customer", self.cust_intra.name, bill_date, amount, due]

    def test_corrected_split_is_committed_as_given(self):
        # The accountant knows the real bills: 200.00 in March, 36.00 in April.
        path = self.write_csv(
            [
                self.intra("2026-03-05", "200.00", "2026-06-03"),
                self.intra("2026-04-01", "36.00"),
                ["advance", "Customer", self.cust_inter.name, "", "50.00", ""],
            ],
            bom=True,  # saved from Excel
        )
        self.build(from_csv=path, commit=True)
        self.assertEqual(
            self.bills_for(self.cust_intra.name),
            [
                (datetime.date(2026, 3, 5), D("200.00")),
                (datetime.date(2026, 4, 1), D("36.00")),
            ],
        )
        due = OpeningBill.objects.get(bill_date="2026-04-01").due_date
        self.assertEqual(due, datetime.date(2026, 5, 1))  # blank: +30 credit days
        self.assertEqual(self.bills_for(self.cust_inter.name), [])
        self.integrity_ok()

    def test_mismatched_total_is_refused_naming_the_party(self):
        path = self.write_csv([self.intra("2026-03-05", "200.00")])
        with self.assertRaises(CommandError) as ctx:
            self.build(from_csv=path, commit=True)
        self.assertIn(self.cust_intra.name, str(ctx.exception))
        self.assertIn("236.00", str(ctx.exception))
        self.assertEqual(OpeningBill.objects.count(), 0)

    def test_dry_run_reports_problems_and_writes_nothing(self):
        path = self.write_csv([self.intra("2026-03-05", "200.00")])
        out = self.build(from_csv=path)
        self.assertIn("[DRY-RUN]", out)
        self.assertIn(self.cust_intra.name, out)
        self.assertEqual(OpeningBill.objects.count(), 0)

    def test_bills_for_a_party_with_only_an_advance_are_refused(self):
        path = self.write_csv(
            [
                self.intra("2026-03-05", "236.00"),
                ["bill", "Customer", self.cust_inter.name, "2026-03-05", "10.00", ""],
            ]
        )
        with self.assertRaises(CommandError) as ctx:
            self.build(from_csv=path, commit=True)
        self.assertIn(self.cust_inter.name, str(ctx.exception))

    def test_bad_rows_are_named_by_row_number(self):
        rows = [
            self.intra("2026-03-05", "236.00"),
            ["bill", "Customer", "Ghost Ltd", "2026-03-05", "1.00", ""],
            self.intra("05/03/2026", "1.00"),
            self.intra("2026-03-05", "-5"),
            self.intra("2026-03-05", "1.00", "2026-03-01"),
            self.intra("2026-05-05", "1.00"),  # after the cutover
        ]
        problems = opening_service.plan_from_csv(
            self.company,
            AS_ON,
            list(csv.DictReader(open(self.write_csv(rows), encoding="utf-8"))),
        )["problems"]
        text = "\n".join(problems)
        for row in ("Row 3", "Row 4", "Row 5", "Row 6", "Row 7"):
            self.assertIn(row, text)
        self.assertIn("Ghost Ltd", text)

    def test_missing_required_column_is_refused(self):
        path = self.write_csv([["Customer", "x"]], header=["party_type", "party"])
        with self.assertRaises(CommandError) as ctx:
            self.build(from_csv=path)
        self.assertIn("bill_date", str(ctx.exception))

    def test_generated_csv_round_trips_unchanged(self):
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.remove, path)
        self.build(out=path)
        self.build(from_csv=path, commit=True)
        self.assertEqual(
            sum(a for _, a in self.bills_for(self.cust_intra.name)), D("236.00")
        )
        self.integrity_ok()
