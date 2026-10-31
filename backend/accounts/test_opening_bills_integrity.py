"""Opening bills in verify_integrity (planning 09, G-4)."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import OpeningBill
from accounts.services import opening as opening_service
from accounts.test_opening_bills import OpeningBillBase
from core.models import SystemHealthCheck
from selling.models import Customer

D = Decimal


class OpeningBillIntegrityTests(OpeningBillBase):
    def verify(self):
        out = StringIO()
        call_command("verify_integrity", stdout=out, stderr=StringIO())
        return out.getvalue()

    def refused(self):
        with self.assertRaises(CommandError):
            call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
        return SystemHealthCheck.objects.filter(
            check_name="opening_bill_integrity"
        ).latest("pk")

    def health(self):
        return SystemHealthCheck.objects.filter(
            check_name="opening_bill_integrity"
        ).latest("pk")

    def test_settled_books_pass_without_a_warning(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        ob = self.bill(amount="118.00")
        self.settle(ob, "40.00")
        out = self.verify()
        self.assertNotIn("[opening bill warning]", out)
        self.assertEqual(self.health().status, "ok")
        self.assertEqual(self.health().details["warnings"], [])

    def test_drifted_outstanding_is_reported_and_repost_repairs_it(self):
        ob = self.bill(amount="100.00")
        self.settle(ob, "40.00")
        # The stored value ignores the receipt: 60 is owed, 100 is stored.
        OpeningBill.objects.filter(pk=ob.pk).update(outstanding_amount=D("100.00"))

        health = self.refused()
        self.assertEqual(health.status, "alert")
        drift = [e for e in health.details["errors"] if ob.name in e]
        self.assertEqual(len(drift), 1)
        self.assertIn("Drift", drift[0])

        self.assertEqual(opening_service.repost(), 1)
        self.assertEqual(
            OpeningBill.objects.get(pk=ob.pk).outstanding_amount, D("60.00")
        )
        self.verify()

    def test_outstanding_drift_on_an_unpaid_bill_is_reported(self):
        ob = self.bill(amount="100.00")
        OpeningBill.objects.filter(pk=ob.pk).update(outstanding_amount=D("90.00"))
        health = self.refused()
        self.assertTrue(any(ob.name in e for e in health.details["errors"]))

    def test_bills_that_do_not_add_up_to_the_ledger_are_a_warning_not_an_error(self):
        # The ledger holds 118.00 for this customer; the opening bill says 100.00.
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.bill(amount="100.00")
        out = self.verify()  # must not raise
        self.assertIn("[opening bill warning]", out)
        health = self.health()
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.details["errors"], [])
        self.assertTrue(
            any(self.cust_intra.name in w for w in health.details["warnings"])
        )

    def test_bills_that_tie_to_the_ledger_produce_no_warning(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-01-10")
        self.bill(amount="118.00")
        self.verify()
        self.assertEqual(self.health().details["warnings"], [])

    def test_bills_of_a_party_that_no_longer_exists_are_reported(self):
        self.bill(amount="100.00")
        Customer.objects.filter(pk=self.cust_intra.pk).update(name="Renamed Cust")
        health = self.refused()
        self.assertTrue(
            any(
                "Intra Cust" in e and "not a Customer" in e
                for e in health.details["errors"]
            )
        )

    def test_cancelled_bills_are_not_compared_with_the_ledger(self):
        ob = self.bill(amount="100.00")
        ob.cancel()
        self.verify()
        self.assertEqual(self.health().details["warnings"], [])
