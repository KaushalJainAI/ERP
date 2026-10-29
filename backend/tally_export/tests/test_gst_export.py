"""GST tax ledgers travel as separate ledger entries of the Tally voucher."""

import shutil
import tempfile
from decimal import Decimal

from django.test import override_settings

from accounts.models import Account, GLEntry
from buying.models import Supplier
from gst.tests.test_documents import SI_URL
from gst.tests.test_posting import StockPostingBase
from selling.models import Customer
from stock.models import Item
from tally_export.models import TallyLedgerMap, TallyStockItemMap
from tally_export.services import export as svc
from tally_export.tests.test_export import parse_vouchers

D = Decimal
MEDIA = tempfile.mkdtemp(prefix="tally-export-gst-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA)
class GstExportTests(StockPostingBase):
    def map_everything(self):
        c = self.company
        for a in Account.objects.filter(company=c, is_group=False):
            TallyLedgerMap.objects.create(
                company=c, account=a, tally_ledger=f"T {a.name}"
            )
        for cust in Customer.objects.filter(company=c):
            TallyLedgerMap.objects.create(
                company=c,
                account=self.debtors,
                party_type="Customer",
                party=cust.name,
                tally_ledger=f"Cust {cust.name}",
            )
        for sup in Supplier.objects.filter(company=c):
            TallyLedgerMap.objects.create(
                company=c,
                account=self.creditors,
                party_type="Supplier",
                party=sup.name,
                tally_ledger=f"Sup {sup.name}",
            )
        for item in Item.objects.all():
            TallyStockItemMap.objects.create(
                company=c, item=item, tally_item=f"I {item.code}", tally_unit="Nos"
            )

    def test_intra_state_sale_has_cgst_sgst_ledger_entries_and_balances(self):
        self.perpetual()
        self.buy_stock((self.stock_item, 10, 50))
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.stock_lines((self.stock_item, 2, 100)),
                update_stock=True,
            ),
        )
        d = self.submit(SI_URL, d)
        self.map_everything()
        batch = svc.create_export(self.company)
        with batch.file.open("rb") as fh:
            parsed = {v["number"]: v for v in parse_vouchers(fh.read())}
        v = parsed[d["name"]]
        ledgers = dict(v["ledgers"])
        self.assertEqual(ledgers["Cust Intra Cust"], D("236.00"))
        self.assertEqual(ledgers["T Output CGST"], D("-18.00"))
        self.assertEqual(ledgers["T Output SGST"], D("-18.00"))
        self.assertNotIn("T Cost of Goods Sold", ledgers)
        self.assertEqual(v["allocs"], [("T Sales", D("-200.00"))])
        self.assertEqual(
            sum(a for _, a in v["ledgers"]) + sum(a for _, a in v["allocs"]), 0
        )
        # Every voucher of the export balances; GL party total matches.
        gl_party = GLEntry.objects.get(voucher_no=d["name"], account=self.debtors)
        self.assertEqual(ledgers["Cust Intra Cust"], gl_party.debit)
        for other in parsed.values():
            total = sum(a for _, a in other["ledgers"]) + sum(
                a for _, a in other["allocs"]
            )
            self.assertEqual(total, 0, other["number"])
