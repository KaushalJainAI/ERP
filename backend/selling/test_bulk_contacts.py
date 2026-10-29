"""Test bulk customer contact updating and missing phone filtering."""

from core.test_e2e import BaseAPITest
from contacts.models import Address
from selling.models import Customer


class BulkCustomerContactsTests(BaseAPITest):
    def test_bulk_update_customer_contacts_and_address_sync(self):
        # Customer without phone
        c1 = Customer.objects.create(
            name="Ramesh Traders",
            company=self.company,
            customer_group=self.cust_group,
            phone=None,
            email=None,
        )
        c2 = Customer.objects.create(
            name="Suresh Kirana",
            company=self.company,
            customer_group=self.cust_group,
            phone="",
            email="",
        )
        addr1 = Address.objects.create(
            address_title="Ramesh Billing",
            address_line1="Main Road",
            customer=c1,
            phone=None,
        )

        # Filter missing phone
        res = self.client.get("/api/selling/customers/?missing_phone=true")
        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data["results"]]
        self.assertIn(c1.id, ids)
        self.assertIn(c2.id, ids)

        # Bulk update
        payload = [
            {"id": c1.id, "phone": "9826011111", "email": "ramesh@example.com"},
            {"id": c2.id, "phone": "9826022222"},
        ]
        res = self.client.post(
            "/api/selling/customers/bulk-update-contacts/",
            payload,
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["updated"], 2)

        c1.refresh_from_db()
        c2.refresh_from_db()
        addr1.refresh_from_db()

        self.assertEqual(c1.phone, "9826011111")
        self.assertEqual(c1.email, "ramesh@example.com")
        self.assertEqual(c2.phone, "9826022222")
        self.assertEqual(addr1.phone, "9826011111")
