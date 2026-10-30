import datetime
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.services.terms import calculate_due_date, due_date_for
from buying.models import Supplier, SupplierGroup
from buying.serializers import SupplierSerializer
from core.models import Company
from core.views import CompanySerializer
from selling.models import Customer, CustomerGroup
from selling.serializers import CustomerSerializer


class CreditDaysTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Masala Ltd",
            abbr="NML",
            default_currency="INR",
            default_customer_credit_days=90,
            default_supplier_credit_days=None,
        )
        self.cust_group = CustomerGroup.objects.create(name="Commercial", is_group=True)
        self.supp_group = SupplierGroup.objects.create(name="Raw Spices", is_group=True)

        self.customer = Customer.objects.create(
            name="Spice Trader",
            company=self.company,
            customer_group=self.cust_group,
            credit_days=None,
        )
        self.supplier = Supplier.objects.create(
            name="Chilli Farmer",
            company=self.company,
            supplier_group=self.supp_group,
            credit_days=None,
        )

    def test_default_customer_credit_days_90(self):
        base_date = datetime.date(2027, 1, 1)
        due_date = calculate_due_date("Customer", self.customer, base_date)
        # Jan 2027 (31 days) + Feb (28 days) + Mar (31 days) = 90 days -> 2027-04-01
        self.assertEqual(due_date, datetime.date(2027, 4, 1))

    def test_customer_credit_days_override(self):
        base_date = datetime.date(2027, 1, 1)
        self.customer.credit_days = 30
        self.customer.save()
        due_date = calculate_due_date("Customer", self.customer, base_date)
        self.assertEqual(due_date, datetime.date(2027, 1, 31))

    def test_customer_zero_credit_days(self):
        base_date = datetime.date(2027, 1, 1)
        self.customer.credit_days = 0
        self.customer.save()
        due_date = calculate_due_date("Customer", self.customer, base_date)
        self.assertEqual(due_date, base_date)

    def test_supplier_unset_default_results_in_zero_days(self):
        base_date = datetime.date(2027, 1, 1)
        due_date = calculate_due_date("Supplier", self.supplier, base_date)
        self.assertEqual(due_date, base_date)

    def test_supplier_company_default(self):
        base_date = datetime.date(2027, 1, 1)
        self.company.default_supplier_credit_days = 15
        self.company.save()
        due_date = calculate_due_date("Supplier", self.supplier, base_date)
        self.assertEqual(due_date, datetime.date(2027, 1, 16))

    def test_supplier_custom_override(self):
        base_date = datetime.date(2027, 1, 1)
        self.company.default_supplier_credit_days = 15
        self.company.save()
        self.supplier.credit_days = 45
        self.supplier.save()
        due_date = calculate_due_date("Supplier", self.supplier, base_date)
        self.assertEqual(due_date, datetime.date(2027, 2, 15))

    def test_case_insensitive_party_type_and_alias(self):
        base_date = datetime.date(2027, 1, 1)
        due_1 = calculate_due_date("customer", self.customer, base_date)
        due_2 = calculate_due_date("CUSTOMER", self.customer, base_date)
        due_3 = due_date_for(self.customer, base_date)
        self.assertEqual(due_1, datetime.date(2027, 4, 1))
        self.assertEqual(due_2, datetime.date(2027, 4, 1))
        self.assertEqual(due_3, datetime.date(2027, 4, 1))

    def test_invalid_party_type_raises_value_error(self):
        with self.assertRaises(ValueError):
            calculate_due_date("Employee", self.customer, datetime.date(2027, 1, 1))

    def test_negative_credit_days_rejected_on_customer(self):
        self.customer.credit_days = -5
        with self.assertRaises(ValidationError):
            self.customer.full_clean()

    def test_negative_credit_days_rejected_on_supplier(self):
        self.supplier.credit_days = -10
        with self.assertRaises(ValidationError):
            self.supplier.full_clean()

    def test_negative_credit_days_rejected_on_company(self):
        self.company.default_customer_credit_days = -1
        with self.assertRaises(ValidationError):
            self.company.full_clean()

        self.company.default_customer_credit_days = 90
        self.company.default_supplier_credit_days = -5
        with self.assertRaises(ValidationError):
            self.company.full_clean()

    def test_negative_credit_days_rejected_by_serializers(self):
        cust_ser = CustomerSerializer(
            data={
                "name": "Invalid Customer",
                "company": self.company.pk,
                "customer_group": self.cust_group.pk,
                "credit_days": -10,
            }
        )
        self.assertFalse(cust_ser.is_valid())
        self.assertIn("credit_days", cust_ser.errors)

        supp_ser = SupplierSerializer(
            data={
                "name": "Invalid Supplier",
                "company": self.company.pk,
                "supplier_group": self.supp_group.pk,
                "credit_days": -5,
            }
        )
        self.assertFalse(supp_ser.is_valid())
        self.assertIn("credit_days", supp_ser.errors)

        comp_ser = CompanySerializer(
            instance=self.company,
            data={"default_customer_credit_days": -30},
            partial=True,
        )
        self.assertFalse(comp_ser.is_valid())
        self.assertIn("default_customer_credit_days", comp_ser.errors)
