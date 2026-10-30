import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework import status

from accounts.models import (
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.services.allocation import suggest_allocation
from accounts.test_outstanding import OutstandingBase

D = Decimal


class OldestFirstAllocationTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.cust_intra.credit_days = 30
        self.cust_intra.save(update_fields=["credit_days"])

    def test_exact_amount(self):
        # 1 bill: 100 + 18% GST = 118.00
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        res = suggest_allocation("Customer", self.cust_intra, self.company, D("118.00"))
        self.assertEqual(len(res.references), 1)
        self.assertEqual(res.references[0]["reference_name"], inv1.name)
        self.assertEqual(res.references[0]["allocated_amount"], D("118.00"))
        self.assertEqual(res.unallocated, D("0.00"))
        self.assertEqual(res.total_allocated, D("118.00"))

    def test_part_of_bill(self):
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        res = suggest_allocation("Customer", self.cust_intra, self.company, D("50.00"))
        self.assertEqual(len(res.references), 1)
        self.assertEqual(res.references[0]["reference_name"], inv1.name)
        self.assertEqual(res.references[0]["allocated_amount"], D("50.00"))
        self.assertEqual(res.unallocated, D("0.00"))
        self.assertEqual(res.total_allocated, D("50.00"))

    def test_across_multiple_bills(self):
        # Bill 1: older due date (2026-05-01), 118.00
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        # Bill 2: newer due date (2026-06-01), 236.00
        inv2 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 2, 100),
                posting_date="2026-05-01",
                due_date="2026-06-01",
            )
        )

        res = suggest_allocation("Customer", self.cust_intra, self.company, D("200.00"))
        self.assertEqual(len(res.references), 2)
        # Oldest bill allocated in full
        self.assertEqual(res.references[0]["reference_name"], inv1.name)
        self.assertEqual(res.references[0]["allocated_amount"], D("118.00"))
        # Newer bill partially allocated: 200 - 118 = 82
        self.assertEqual(res.references[1]["reference_name"], inv2.name)
        self.assertEqual(res.references[1]["allocated_amount"], D("82.00"))
        self.assertEqual(res.unallocated, D("0.00"))
        self.assertEqual(res.total_allocated, D("200.00"))

    def test_more_than_owed(self):
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        res = suggest_allocation("Customer", self.cust_intra, self.company, D("150.00"))
        self.assertEqual(len(res.references), 1)
        self.assertEqual(res.references[0]["reference_name"], inv1.name)
        self.assertEqual(res.references[0]["allocated_amount"], D("118.00"))
        self.assertEqual(res.unallocated, D("32.00"))
        self.assertEqual(res.total_allocated, D("118.00"))

    def test_nothing_owed(self):
        res = suggest_allocation("Customer", self.cust_intra, self.company, D("100.00"))
        self.assertEqual(len(res.references), 0)
        self.assertEqual(res.unallocated, D("100.00"))
        self.assertEqual(res.total_allocated, D("0.00"))

    def test_supplier_allocation(self):
        pinv1 = self.pi_obj(
            self.purchase(
                self.sup_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                bill_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        res = suggest_allocation("Supplier", self.sup_intra, self.company, D("118.00"))
        self.assertEqual(len(res.references), 1)
        self.assertEqual(res.references[0]["reference_name"], pinv1.name)
        self.assertEqual(res.references[0]["allocated_amount"], D("118.00"))
        self.assertEqual(res.unallocated, D("0.00"))

    def test_api_endpoint(self):
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        url = (
            f"/api/accounts/payment-entries/suggest-allocation/"
            f"?party_type=Customer&party={self.cust_intra.name}&company={self.company.id}&amount=50.00"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data["references"]), 1)
        self.assertEqual(data["references"][0]["reference_name"], inv1.name)
        self.assertEqual(data["references"][0]["allocated_amount"], "50.00")
        self.assertEqual(data["unallocated"], "0.00")
        self.assertEqual(data["total_allocated"], "50.00")

    def test_api_endpoint_missing_params(self):
        url = "/api/accounts/payment-entries/suggest-allocation/?party_type=Customer"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refuse_over_allocation_on_submit(self):
        inv1 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-04-01",
                due_date="2026-05-01",
            )
        )
        # Attempt to allocate 200 against an invoice that only has 118 outstanding
        pe = PaymentEntry.objects.create(
            name="PE-OVER-01",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("200.00"),
            received_amount=D("200.00"),
        )
        PaymentEntryReference.objects.create(
            payment_entry=pe,
            reference_doctype="selling.SalesInvoice",
            reference_name=inv1.name,
            allocated_amount=D("200.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            pe.submit()
        self.assertIn("exceeds the outstanding amount", str(ctx.exception))
