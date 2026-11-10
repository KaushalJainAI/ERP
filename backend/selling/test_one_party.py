"""S-8: one party, one record. Names, registered-party state, GSTIN warnings."""

from rest_framework import status

from buying.models import Supplier
from core.test_e2e import BaseAPITest
from selling.models import Customer

REG_GSTIN = "27AAACR5055K1Z7"  # Maharashtra (27), valid checksum


class OnePartyAPITest(BaseAPITest):
    def _customer(self, **overrides):
        payload = {
            "name": "Sharma Traders",
            "company": self.company.id,
            "customer_group": self.cust_group.id,
        }
        payload.update(overrides)
        return self.client.post("/api/selling/customers/", payload, format="json")

    def test_name_taken_ignoring_case_is_refused(self):
        before = Customer.objects.count()
        self._customer(name="Sharma Traders")
        res = self._customer(name="sharma traders")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("name", res.data)
        self.assertIn("Sharma Traders", str(res.data["name"]))
        self.assertEqual(Customer.objects.count(), before + 1, "no second row")

    def test_renaming_a_party_to_its_own_name_in_other_case_is_allowed(self):
        res = self.client.patch(
            f"/api/selling/customers/{self.customer.id}/",
            {"name": "ACME CORP"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)

    def test_supplier_name_taken_ignoring_case_is_refused(self):
        res = self.client.post(
            "/api/buying/suppliers/",
            {
                "name": "bolt SUPPLY",
                "company": self.company.id,
                "supplier_group": self.supp_group.id,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("Bolt Supply", str(res.data["name"]))
        self.assertEqual(Supplier.objects.filter(name__iexact="bolt supply").count(), 1)

    def test_registered_customer_with_only_a_gstin_gets_its_state(self):
        res = self._customer(
            name="Reg Trader",
            gst_category="Registered Regular",
            gstin=REG_GSTIN,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["state"], "27")

    def test_registered_customer_without_gstin_is_refused(self):
        before = Customer.objects.count()
        res = self._customer(name="No GSTIN", gst_category="Registered Regular")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("gstin", res.data)
        self.assertEqual(Customer.objects.count(), before, "nothing saved")

    def test_duplicate_gstin_returns_a_warning_and_still_saves(self):
        first = self._customer(
            name="Head Office", gst_category="Registered Regular", gstin=REG_GSTIN
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        self.assertEqual(first.data["warnings"], [])

        branch = self._customer(
            name="Branch Office", gst_category="Registered Regular", gstin=REG_GSTIN
        )
        self.assertEqual(branch.status_code, status.HTTP_201_CREATED, branch.content)
        self.assertEqual(branch.data["warnings"], ["Already saved as Head Office"])

    def test_inactive_party_with_the_same_gstin_is_not_warned_about(self):
        Customer.objects.filter(pk=self.customer.pk).update(
            gstin=REG_GSTIN, gst_category="Registered Regular", state=None
        )
        Customer.objects.filter(pk=self.customer.pk).update(is_active=False)
        res = self._customer(
            name="Fresh Party", gst_category="Registered Regular", gstin=REG_GSTIN
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["warnings"], [])
