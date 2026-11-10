"""U-2 backend: optional group on parties, and the names-to-complete list."""

from rest_framework import status

from buying.models import SupplierGroup
from core.test_e2e import BaseAPITest
from selling.models import Customer, CustomerGroup


class NamesToCompleteTest(BaseAPITest):
    def _list(self):
        res = self.client.get("/api/contacts/names-to-complete/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)
        return res.data

    def _rows(self, kind, pk):
        return [
            row
            for row in self._list()["items"]
            if row["kind"] == kind and row["id"] == pk
        ]

    def test_customer_with_only_a_name_saves_and_is_listed(self):
        res = self.client.post(
            "/api/selling/customers/",
            {"name": "Walk In Ramesh", "company": self.company.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["customer_group_name"], "All Customer Groups")

        rows = self._rows("customer", res.data["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["missing"], ["state", "group"])
        self.assertEqual(rows[0]["name"], "Walk In Ramesh")

    def test_state_drops_out_of_the_list_once_set(self):
        res = self.client.post(
            "/api/selling/customers/",
            {"name": "Walk In Sunita", "company": self.company.id},
            format="json",
        )
        pk = res.data["id"]
        self.client.patch(
            f"/api/selling/customers/{pk}/", {"state": "29"}, format="json"
        )
        rows = self._rows("customer", pk)
        self.assertEqual(rows[0]["missing"], ["group"], "group is still the root")

    def test_registered_party_without_gstin_is_listed(self):
        Customer.objects.filter(pk=self.customer.pk).update(
            gst_category="Registered Regular", gstin=None
        )
        rows = self._rows("customer", self.customer.pk)
        self.assertIn("gstin", rows[0]["missing"])

    def test_supplier_gets_the_root_group_when_none_is_sent(self):
        res = self.client.post(
            "/api/buying/suppliers/",
            {"name": "Walk In Supplies", "company": self.company.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(
            SupplierGroup.objects.get(pk=res.data["supplier_group"]).name,
            "All Supplier Groups",
        )
        rows = self._rows("supplier", res.data["id"])
        self.assertEqual(rows[0]["missing"], ["state", "group"])

    def test_root_customer_group_is_created_once_and_reused(self):
        for name in ("Ramesh", "Suresh"):
            res = self.client.post(
                "/api/selling/customers/",
                {"name": name, "company": self.company.id},
                format="json",
            )
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(
            CustomerGroup.objects.filter(name="All Customer Groups").count(), 1
        )

    def test_item_without_hsn_or_tax_template_is_listed(self):
        rows = self._rows("item", self.item.pk)
        self.assertEqual(rows[0]["missing"], ["hsn", "tax_template"])

    def test_count_matches_items(self):
        data = self._list()
        self.assertEqual(data["count"], len(data["items"]))

    def test_requires_login(self):
        self.client.credentials()
        res = self.client.get("/api/contacts/names-to-complete/")
        self.assertIn(
            res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        )

    def test_inactive_party_is_not_listed(self):
        res = self.client.post(
            "/api/selling/customers/",
            {"name": "Closed Shop", "company": self.company.id},
            format="json",
        )
        Customer.objects.filter(pk=res.data["id"]).update(is_active=False)
        self.assertEqual(self._rows("customer", res.data["id"]), [])
