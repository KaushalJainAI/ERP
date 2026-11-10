"""Document numbers follow the Indian financial year (S-2).

``<SERIES>-<FY>-NNNN``: an order dated 2027-04-02 is ``SO-2728-0001``; one dated
2027-03-31 stays in ``SO-2627-``. Also a guard that no production code hard-codes
a ``PREFIX-20YY-`` naming literal again.
"""

import datetime
import pathlib
import re

from rest_framework import status

from accounts.mapping import financial_year_series
from core.test_e2e import BaseAPITest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
YEAR_LITERAL = re.compile(r'"[A-Z-]+-20\d\d-"')
SKIP_DIRS = {"migrations", "venv", ".venv", "node_modules", "__pycache__", "erpnext"}


class FinancialYearNamingTests(BaseAPITest):
    def _so(self, transaction_date):
        payload = {
            "customer": self.customer.id,
            "company": self.company.id,
            "transaction_date": transaction_date,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "1.000",
                    "rate": "10.00",
                }
            ],
        }
        res = self.client.post("/api/selling/sales-orders/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        return res.data["name"]

    def test_order_dated_after_april_first_gets_next_fy_series(self):
        self.assertEqual(self._so("2027-04-02"), "SO-2728-0001")

    def test_order_dated_march_thirty_first_stays_in_previous_fy(self):
        self.assertEqual(self._so("2027-03-31"), "SO-2627-0001")

    def test_series_counter_continues_within_a_fy(self):
        self.assertEqual(self._so("2027-03-30"), "SO-2627-0001")
        self.assertEqual(self._so("2027-03-31"), "SO-2627-0002")

    def test_journal_entry_gets_fy_series(self):
        payload = {
            "posting_date": "2027-04-02",
            "company": self.company.id,
            "lines": [
                {"account": self.bank.id, "debit": "100.00", "credit": "0.00"},
                {
                    "account": self.sales_income.id,
                    "debit": "0.00",
                    "credit": "100.00",
                },
            ],
        }
        res = self.client.post("/api/accounts/journal-entries/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["name"], "JV-2728-0001")

    def test_payment_entry_gets_fy_series(self):
        payload = {
            "payment_type": "Receive",
            "posting_date": "2027-04-02",
            "company": self.company.id,
            "party_type": "Customer",
            "party_name": self.customer.name,
            "paid_from": self.debtors.id,
            "paid_to": self.bank.id,
            "paid_amount": "10.00",
            "received_amount": "10.00",
        }
        res = self.client.post("/api/accounts/payment-entries/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["name"], "PE-2728-0001")

    def test_every_converted_series_fits_sixteen_characters(self):
        for prefix in (
            "SO",
            "SINV",
            "SRET",
            "PO",
            "PINV",
            "PRET",
            "JV",
            "PE",
            "TAX",
            "STE",
            "SRECO",
            "WO",
            "BATCH",
        ):
            name = financial_year_series(prefix, datetime.date(2027, 4, 2)) + "0001"
            self.assertLessEqual(len(name), 16, name)


def test_no_hardcoded_year_prefix_in_backend_python():
    """Guard: production code must not hard-code a ``PREFIX-20YY-`` series."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        parts = set(path.relative_to(BACKEND).parts)
        if parts & SKIP_DIRS or "tests" in parts:
            continue
        if path.name.startswith("test_") or path.name in {"tests.py", "conftest.py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if YEAR_LITERAL.search(line):
                offenders.append(f"{path.relative_to(BACKEND)}:{number}")
    assert offenders == [], f"Hard-coded year series found: {offenders}"
