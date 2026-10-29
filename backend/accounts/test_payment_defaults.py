"""Payment Entry defaults: operators enter only party + amount (+ bank account);
the other leg, party type and received amount are filled by the server."""

from decimal import Decimal

from rest_framework import status

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    PaymentEntry,
)
from core.test_e2e import BaseAPITest

URL = "/api/accounts/payment-entries/"


class PaymentEntryDefaultsTests(BaseAPITest):
    def _post(self, **extra):
        payload = {
            "posting_date": "2026-04-05",
            "company": self.company.id,
            "paid_amount": "500.00",
            **extra,
        }
        return self.client.post(URL, payload, format="json")

    def _submit(self, res):
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        out = self.client.post(f"{URL}{res.data['id']}/submit/")
        self.assertEqual(out.status_code, status.HTTP_200_OK, out.content)

    def test_receive_with_party_and_amount_only(self):
        res = self._post(payment_type="Receive", party_name=self.customer.name)
        self._submit(res)
        self.assertEqual(res.data["party_type"], "Customer")
        self.assertEqual(res.data["paid_from"], self.debtors.id)
        self.assertEqual(res.data["paid_to"], self.bank.id)
        self.assertEqual(Decimal(res.data["received_amount"]), Decimal("500.00"))

    def test_pay_with_party_and_amount_only(self):
        res = self._post(payment_type="Pay", party_name=self.supplier.name)
        self._submit(res)
        self.assertEqual(res.data["party_type"], "Supplier")
        self.assertEqual(res.data["paid_from"], self.bank.id)
        self.assertEqual(res.data["paid_to"], self.creditors.id)
        self.assertEqual(Decimal(res.data["received_amount"]), Decimal("500.00"))

    def test_party_type_follows_the_party_that_exists(self):
        # Paying a customer (e.g. a refund): only a Customer of that name exists.
        res = self._post(payment_type="Pay", party_name=self.customer.name)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["party_type"], "Customer")
        self.assertEqual(res.data["paid_from"], self.bank.id)
        self.assertEqual(res.data["paid_to"], self.debtors.id)

    def test_explicit_bank_account_is_kept(self):
        cash = Account.objects.create(
            name="Cash in Hand",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        res = self._post(
            payment_type="Receive", party_name=self.customer.name, paid_to=cash.id
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["paid_to"], cash.id)
        self.assertEqual(res.data["paid_from"], self.debtors.id)

    def test_explicit_accounts_and_type_are_kept(self):
        res = self._post(
            payment_type="Pay",
            party_type="Supplier",
            party_name=self.supplier.name,
            paid_from=self.bank.id,
            paid_to=self.creditors.id,
            received_amount="400.00",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["paid_from"], self.bank.id)
        self.assertEqual(res.data["paid_to"], self.creditors.id)
        self.assertEqual(Decimal(res.data["received_amount"]), Decimal("400.00"))

    def test_internal_transfer_requires_both_accounts(self):
        res = self._post(payment_type="Internal Transfer")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_from", res.data)
        self.assertIn("paid_to", res.data)

    def test_internal_transfer_with_accounts(self):
        cash = Account.objects.create(
            name="Cash in Hand",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        res = self._post(
            payment_type="Internal Transfer", paid_from=self.bank.id, paid_to=cash.id
        )
        self._submit(res)
        self.assertIsNone(res.data["party_type"])

    def test_receive_without_party_or_accounts_is_rejected(self):
        res = self._post(payment_type="Receive")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("party_name", res.data)

    def test_company_default_party_account_is_used(self):
        other = Account.objects.create(
            name="Trade Debtors North",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.company.default_receivable_account = other
        self.company.save()
        res = self._post(payment_type="Receive", party_name=self.customer.name)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["paid_from"], other.id)

    def test_update_fills_only_keys_sent_as_null(self):
        res = self._post(payment_type="Receive", party_name=self.customer.name)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        pe = PaymentEntry.objects.get(pk=res.data["id"])
        # Not sent -> untouched (received amount follows the paid amount).
        patch = self.client.patch(
            f"{URL}{pe.id}/", {"paid_amount": "700.00"}, format="json"
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK, patch.content)
        self.assertEqual(patch.data["paid_to"], self.bank.id)
        self.assertEqual(Decimal(patch.data["received_amount"]), Decimal("700.00"))
        # Sent as null -> refilled with the default.
        patch = self.client.patch(f"{URL}{pe.id}/", {"paid_to": None}, format="json")
        self.assertEqual(patch.status_code, status.HTTP_200_OK, patch.content)
        self.assertEqual(patch.data["paid_to"], self.bank.id)
