"""Phase-3 Task 1(a): batches in the stock ledger, FEFO, reports, RBAC."""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, DocStatus, DummySubmittableDoc
from stock.models import (
    UOM,
    Batch,
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    StockLedgerEntry,
    Warehouse,
)
from stock.services import batches as batch_service
from stock.services import ledger as stock_service
from users.models import Role, RolePermission, User, UserRole

D0 = datetime.date


class BatchBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Test", abbr="NT", default_currency="INR"
        )
        uom = UOM.objects.create(name="Kg")
        group = ItemGroup.objects.create(name="Spices", is_group=False)
        mk = lambda code, **kw: Item.objects.create(  # noqa: E731
            code=code, name=code, item_group=group, stock_uom=uom, **kw
        )
        self.haldi = mk(
            "HALDI-W", has_batch_no=True, has_expiry_date=True, shelf_life_in_days=365
        )
        self.salt = mk("SALT", has_batch_no=True)  # batch, no expiry
        self.plain = mk("POUCH")  # not batch-tracked
        self.wh = Warehouse.objects.create(name="Store", company=self.company)
        self.wh2 = Warehouse.objects.create(name="Store 2", company=self.company)
        self._n = 0

    def doc(self):
        self._n += 1
        return DummySubmittableDoc.objects.create(
            name=f"DOC-{self._n}", title="t", docstatus=DocStatus.SUBMITTED
        )

    def mkbatch(self, no, item=None, **kw):
        return Batch.objects.create(batch_no=no, item=item or self.haldi, **kw)

    def post(self, item, wh, qty, rate=D("0"), batch=None, date=D0(2026, 4, 1), **kw):
        doc = kw.pop("doc", None) or self.doc()
        entry = {
            "item": item,
            "warehouse": wh,
            "qty": D(qty),
            "valuation_rate": D(rate),
            "batch": batch,
            **kw,
        }
        stock_service.post(doc, [entry], posting_date=date)
        return doc

    def entry(self, etype, lines, date=D0(2026, 4, 1), name=None):
        self._n += 1
        ste = StockEntry.objects.create(
            name=name or f"STE-B-{self._n}",
            stock_entry_type=etype,
            posting_date=date,
            company=self.company,
        )
        for ln in lines:
            StockEntryItem.objects.create(stock_entry=ste, **ln)
        return ste


class LedgerBatchTests(BatchBase):
    def test_batch_required_for_batch_item_and_forbidden_otherwise(self):
        with self.assertRaisesMessage(ValidationError, "Batch required"):
            self.post(self.haldi, self.wh, 5, 10)
        b = self.mkbatch("B-X", self.haldi)
        with self.assertRaisesMessage(ValidationError, "not batch-tracked"):
            self.post(self.plain, self.wh, 5, 10, batch=b)
        # non-batch item without a batch behaves exactly as before
        self.post(self.plain, self.wh, 5, 10)
        self.assertEqual(Bin.objects.get(item=self.plain).actual_qty, D("5.000"))

    def test_wrong_item_and_disabled_batch(self):
        other = self.mkbatch("B-SALT", self.salt)
        with self.assertRaisesMessage(ValidationError, "different item"):
            self.post(self.haldi, self.wh, 5, 10, batch=other)
        off = self.mkbatch("B-OFF", self.haldi, is_disabled=True)
        with self.assertRaisesMessage(ValidationError, "disabled"):
            self.post(self.haldi, self.wh, 5, 10, batch=off)

    def test_out_above_batch_balance_refused_even_if_bin_has_stock(self):
        a, b = self.mkbatch("A"), self.mkbatch("B")
        self.post(self.haldi, self.wh, 10, 100, a)
        self.post(self.haldi, self.wh, 10, 100, b)
        with self.assertRaisesMessage(ValidationError, "Insufficient stock in batch"):
            self.post(self.haldi, self.wh, -15, 0, a)
        self.post(self.haldi, self.wh, -10, 0, a)

    def test_expired_out_refused_but_allowed_with_flag(self):
        a = self.mkbatch("EXP", expiry_date=D0(2026, 4, 10))
        self.post(self.haldi, self.wh, 10, 100, a)
        with self.assertRaisesMessage(ValidationError, "expired on 2026-04-10"):
            self.post(self.haldi, self.wh, -2, 0, a, date=D0(2026, 4, 11))
        # not expired on the expiry day itself
        self.post(self.haldi, self.wh, -2, 0, a, date=D0(2026, 4, 10))
        self.post(
            self.haldi, self.wh, -3, 0, a, date=D0(2026, 4, 11), allow_expired=True
        )
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh, a), D("5"))

    def test_two_batches_costed_per_batch(self):
        a, b = self.mkbatch("T108"), self.mkbatch("T114")
        self.post(self.haldi, self.wh, 100, 108, a)
        self.post(self.haldi, self.wh, 100, 114, b)
        self.assertEqual(
            Bin.objects.get(item=self.haldi).valuation_rate, D("111.000000")
        )
        self.post(self.haldi, self.wh, -50, 0, a)
        self.post(self.haldi, self.wh, -50, 0, b)
        sles = list(StockLedgerEntry.objects.filter(qty__lt=0).order_by("id"))
        self.assertEqual(sles[0].valuation_rate, D("108.000000"))
        self.assertEqual(sles[0].stock_value_difference, D("-5400.00"))
        self.assertEqual(sles[1].valuation_rate, D("114.000000"))
        self.assertEqual(sles[1].stock_value_difference, D("-5700.00"))

    def test_whole_batch_issue_releases_exact_value(self):
        a = self.mkbatch("ODD")
        # 3 units for 100.00: rate 33.333333 -> naive 3 * rate = 99.999999 -> 100.00
        # 7 units for 100.01 would drift; the whole-batch issue must be exact.
        self.post(self.haldi, self.wh, 7, 0, a, incoming_value=D("100.01"))
        self.post(self.haldi, self.wh, -7, 0, a)
        qty, value = batch_service.batch_position(self.haldi, self.wh, a)
        self.assertEqual((qty, value), (D("0"), D("0.00")))
        self.assertEqual(Bin.objects.get(item=self.haldi).stock_value, D("0.00"))

    def test_incoming_value_is_exact(self):
        a = self.mkbatch("EXACT")
        self.post(self.haldi, self.wh, 3, 0, a, incoming_value=D("100.00"))
        sle = StockLedgerEntry.objects.get(batch=a)
        self.assertEqual(sle.stock_value_difference, D("100.00"))
        self.assertEqual(sle.valuation_rate, D("33.333333"))
        # plain items accept it too
        self.post(self.plain, self.wh, 3, 0, incoming_value=D("10.01"))
        self.assertEqual(Bin.objects.get(item=self.plain).stock_value, D("10.01"))

    def test_transfer_keeps_batch_and_cost(self):
        a, b = self.mkbatch("TA"), self.mkbatch("TB")
        self.post(self.haldi, self.wh, 10, 100, a)
        self.post(self.haldi, self.wh, 10, 200, b)
        ste = self.entry(
            StockEntryType.MATERIAL_TRANSFER,
            [
                dict(
                    item=self.haldi,
                    source_warehouse=self.wh,
                    target_warehouse=self.wh2,
                    qty=D("4"),
                    batch=a,
                )
            ],
        )
        ste.submit()
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh2, a), D("4"))
        self.assertEqual(
            Bin.objects.get(item=self.haldi, warehouse=self.wh2).stock_value,
            D("400.00"),
        )
        self.assertEqual(batch_service.batch_rate(self.haldi, self.wh2, a), D("100"))

    def test_reverse_copies_batch_and_refuses_negative_batch(self):
        a, b = self.mkbatch("RA"), self.mkbatch("RB")
        doc_a = self.post(self.haldi, self.wh, 10, 100, a)
        self.post(self.haldi, self.wh, 10, 100, b)
        self.post(self.haldi, self.wh, -8, 0, a)
        # Bin would stay positive (12 - 10 = 2) but batch A would go to -8.
        with self.assertRaisesMessage(ValidationError, "batch 'RA'"):
            with transaction.atomic():
                stock_service.reverse(doc_a)
        self.assertEqual(Bin.objects.get(item=self.haldi).actual_qty, D("12.000"))
        # An undisturbed batch reverses and the reversal row carries the batch.
        doc_b = StockLedgerEntry.objects.get(batch=b).voucher_no
        stock_service.reverse(DummySubmittableDoc.objects.get(name=doc_b))
        rows = StockLedgerEntry.objects.filter(batch=b)
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(r.is_cancelled for r in rows))

    def test_repost_stock_reproduces_bins(self):
        far = D0(2030, 1, 1)
        a = self.mkbatch("PA", expiry_date=far)
        b = self.mkbatch("PB", expiry_date=far)
        self.post(self.haldi, self.wh, 100, 108, a)
        self.post(self.haldi, self.wh, 100, 114, b)
        self.post(self.haldi, self.wh, -30, 0, a)
        self.post(self.plain, self.wh, 5, 3)
        before = {
            (x.item_id, x.warehouse_id): (x.actual_qty, x.stock_value, x.valuation_rate)
            for x in Bin.objects.all()
        }
        call_command("repost_stock")
        after = {
            (x.item_id, x.warehouse_id): (x.actual_qty, x.stock_value, x.valuation_rate)
            for x in Bin.objects.all()
        }
        self.assertEqual(before, after)
        self.assertEqual(batch_service.integrity_errors(), [])


class StockEntryBatchTests(BatchBase):
    def receipt(self, **line):
        base = dict(item=self.haldi, target_warehouse=self.wh, qty=D("100"))
        base.update(line)
        return self.entry(StockEntryType.MATERIAL_RECEIPT, [base])

    def test_receipt_creates_batch_with_shelf_life_expiry_and_number(self):
        ste = self.receipt(
            valuation_rate=D("110"),
            manufacturing_date=D0(2026, 4, 1),
            supplier_batch_no="SUP-77",
        )
        ste.submit()
        line = ste.items.get()
        b = line.batch
        self.assertIsNotNone(b)
        self.assertEqual(b.batch_no, "HALDI-W-260401-01")
        self.assertEqual(b.expiry_date, D0(2027, 4, 1))
        self.assertEqual(b.supplier_batch_no, "SUP-77")
        self.assertEqual(
            (b.reference_doctype, b.reference_name), ("stock.StockEntry", ste.name)
        )
        self.assertEqual(StockLedgerEntry.objects.get().batch, b)
        # the second one on the same day gets -02
        ste2 = self.receipt(valuation_rate=D("110"))
        ste2.submit()
        self.assertEqual(ste2.items.get().batch.batch_no, "HALDI-W-260401-02")
        # manufacturing date defaults to the posting date
        self.assertEqual(ste2.items.get().batch.manufacturing_date, D0(2026, 4, 1))

    def test_receipt_into_existing_batch_and_pick_and_enter_conflict(self):
        b = self.mkbatch("EXIST", expiry_date=D0(2027, 1, 1))
        self.receipt(batch=b, valuation_rate=D("5")).submit()
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh, b), D("100"))
        bad = self.receipt(batch=b, supplier_batch_no="X")
        with self.assertRaisesMessage(ValidationError, "not both"):
            bad.submit()

    def test_expiry_item_without_expiry_source_refused(self):
        self.haldi.shelf_life_in_days = None
        self.haldi.save()
        with self.assertRaisesMessage(ValidationError, "tracks expiry"):
            self.receipt().submit()
        # explicit expiry date works
        self.receipt(expiry_date=D0(2027, 1, 1), valuation_rate=D("1")).submit()
        # batch item without expiry tracking needs none
        self.entry(
            StockEntryType.MATERIAL_RECEIPT,
            [dict(item=self.salt, target_warehouse=self.wh, qty=D("3"))],
        ).submit()
        # ... and may not be given one
        with self.assertRaisesMessage(ValidationError, "does not track expiry"):
            self.entry(
                StockEntryType.MATERIAL_RECEIPT,
                [
                    dict(
                        item=self.salt,
                        target_warehouse=self.wh,
                        qty=D("3"),
                        expiry_date=D0(2027, 1, 1),
                    )
                ],
            ).submit()

    def test_non_batch_item_with_batch_refused(self):
        other = self.mkbatch("B1")
        ste = self.entry(
            StockEntryType.MATERIAL_RECEIPT,
            [dict(item=self.plain, target_warehouse=self.wh, qty=D("2"), batch=other)],
        )
        with self.assertRaisesMessage(ValidationError, "not batch-tracked"):
            ste.submit()

    def test_issue_without_batch_picks_oldest_manufactured_first(self):
        # FEFO would take EARLY (expiring first); FIFO takes the oldest-made lot.
        early = self.mkbatch(
            "EARLY", expiry_date=D0(2026, 12, 1), manufacturing_date=D0(2026, 3, 1)
        )
        late = self.mkbatch(
            "LATE", expiry_date=D0(2027, 6, 1), manufacturing_date=D0(2026, 1, 1)
        )
        self.post(self.haldi, self.wh, 30, 10, late)
        self.post(self.haldi, self.wh, 50, 10, early)
        ste = self.entry(
            StockEntryType.MATERIAL_ISSUE,
            [dict(item=self.haldi, source_warehouse=self.wh, qty=D("60"))],
        )
        ste.submit()
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh, late), D("0"))
        self.assertEqual(
            batch_service.batch_balance(self.haldi, self.wh, early), D("20")
        )

    def test_material_issue_allows_expired_but_transfer_does_not(self):
        old = self.mkbatch("OLD", expiry_date=D0(2026, 3, 1))
        self.post(self.haldi, self.wh, 10, 10, old, date=D0(2026, 2, 1))
        trf = self.entry(
            StockEntryType.MATERIAL_TRANSFER,
            [
                dict(
                    item=self.haldi,
                    source_warehouse=self.wh,
                    target_warehouse=self.wh2,
                    qty=D("1"),
                    batch=old,
                )
            ],
        )
        with self.assertRaisesMessage(ValidationError, "expired"):
            trf.submit()
        iss = self.entry(
            StockEntryType.MATERIAL_ISSUE,
            [dict(item=self.haldi, source_warehouse=self.wh, qty=D("10"), batch=old)],
        )
        iss.submit()
        self.assertEqual(batch_service.batch_balance(self.haldi, self.wh, old), D("0"))

    def test_cancel_receipt_after_batch_sold_blocked_and_amend_reuses_batch(self):
        ste = self.receipt(valuation_rate=D("10"))
        ste.submit()
        batch = ste.items.get().batch
        self.entry(
            StockEntryType.MATERIAL_ISSUE,
            [dict(item=self.haldi, source_warehouse=self.wh, qty=D("40"), batch=batch)],
        ).submit()
        with self.assertRaises(ValidationError):
            ste.cancel()
        ste.refresh_from_db()
        self.assertEqual(ste.docstatus, DocStatus.SUBMITTED)

    def test_amend_copies_batch_so_resubmit_reuses_lot(self):
        ste = self.receipt(valuation_rate=D("10"))
        ste.submit()
        batch = ste.items.get().batch
        ste.cancel()
        amended = ste.amend()
        self.assertEqual(amended.items.get().batch, batch)
        amended.submit()
        self.assertEqual(Batch.objects.count(), 1)
        self.assertEqual(
            batch_service.batch_balance(self.haldi, self.wh, batch), D("100")
        )


class FefoTests(BatchBase):
    def test_ordering_exclusions_and_shortfall(self):
        today = D0(2026, 4, 1)
        nox = self.mkbatch("NOEXP", manufacturing_date=D0(2026, 1, 1))
        e2 = self.mkbatch(
            "E2", expiry_date=D0(2026, 9, 1), manufacturing_date=D0(2026, 2, 1)
        )
        e1b = self.mkbatch(
            "E1B", expiry_date=D0(2026, 8, 1), manufacturing_date=D0(2026, 3, 1)
        )
        e1a = self.mkbatch(
            "E1A", expiry_date=D0(2026, 8, 1), manufacturing_date=D0(2026, 2, 1)
        )
        expired = self.mkbatch("EXPD", expiry_date=D0(2026, 3, 1))
        empty = self.mkbatch("EMPTY", expiry_date=D0(2026, 7, 1))
        disabled = self.mkbatch("DIS", expiry_date=D0(2026, 7, 1))
        for b in (nox, e2, e1b, e1a, expired, disabled):
            self.post(self.haldi, self.wh, 10, 5, b, date=D0(2026, 2, 1))
        Batch.objects.filter(pk=disabled.pk).update(is_disabled=True)
        self.post(self.haldi, self.wh, 10, 5, empty, date=D0(2026, 2, 1))
        self.post(self.haldi, self.wh, -10, 0, empty, date=D0(2026, 2, 2))
        self.post(self.haldi, self.wh2, 99, 5, e1a, date=D0(2026, 2, 1))  # other wh

        names = [
            c.batch_no
            for c in batch_service.fefo_candidates(self.haldi, self.wh, today)
        ]
        self.assertEqual(names, ["E1A", "E1B", "E2", "NOEXP"])

        plan = batch_service.fefo(self.haldi, self.wh, D("25"), today)
        self.assertEqual(plan["allocated_qty"], D("25.000"))
        self.assertEqual(plan["shortfall"], D("0.000"))
        self.assertEqual(
            [(a["batch_no"], a["allocate_qty"]) for a in plan["allocations"]],
            [("E1A", D("10.000")), ("E1B", D("10.000")), ("E2", D("5.000"))],
        )
        plan = batch_service.fefo(self.haldi, self.wh, D("100"), today)
        self.assertEqual(plan["allocated_qty"], D("40.000"))
        self.assertEqual(plan["shortfall"], D("60.000"))

    def test_endpoints(self):
        admin = User.objects.create_superuser("fefo_admin", "fa@t.com", "pw")
        c = APIClient()
        c.force_authenticate(admin)
        e1 = self.mkbatch(
            "API-1", expiry_date=D0(2030, 1, 1), manufacturing_date=D0(2019, 1, 1)
        )
        e0 = self.mkbatch(
            "API-0", expiry_date=D0(2029, 1, 1), manufacturing_date=D0(2019, 2, 1)
        )
        dead = self.mkbatch("API-DEAD", expiry_date=D0(2020, 1, 1))
        for b in (e1, e0, dead):
            self.post(self.haldi, self.wh, 10, 5, b, date=D0(2019, 6, 1))
        res = c.get(
            f"/api/stock/batches/fefo/?item={self.haldi.id}&warehouse={self.wh.id}"
            "&qty=15&posting_date=2026-04-01"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["requested_qty"], "15.000")
        self.assertEqual(res.data["allocated_qty"], "15.000")
        self.assertEqual(
            [a["batch_no"] for a in res.data["allocations"]], ["API-0", "API-1"]
        )
        self.assertEqual(
            set(res.json()["allocations"][0]),
            {"batch", "batch_no", "expiry_date", "available_qty", "allocate_qty"},
        )
        bad = c.get(
            f"/api/stock/batches/fefo/?item={self.haldi.id}&warehouse={self.wh.id}"
        )
        self.assertEqual(bad.status_code, 400)

        lst = c.get(
            f"/api/stock/batches/?item={self.haldi.id}&warehouse={self.wh.id}"
            "&available=1&posting_date=2026-04-01"
        )
        self.assertEqual(lst.status_code, 200, lst.content)
        rows = lst.json()["results"]
        # The picker lists in FIFO order (oldest manufactured first), not by expiry.
        self.assertEqual([r["batch_no"] for r in rows], ["API-1", "API-0"])
        self.assertEqual(rows[0]["available_qty"], "10.000")
        self.assertEqual(
            rows[1]["days_to_expiry"], (D0(2029, 1, 1) - D0(2026, 4, 1)).days
        )

        bal = c.get(f"/api/stock/batches/{e0.id}/balances/")
        self.assertEqual(bal.status_code, 200)
        self.assertEqual(bal.json()["warehouses"][0]["qty"], "10.000")
        self.assertEqual(bal.json()["total_stock_value"], "50.00")

        tr = c.get(f"/api/stock/batches/{e0.id}/trace/")
        self.assertEqual(tr.status_code, 200)
        self.assertEqual(len(tr.json()["entries"]), 1)
        self.assertEqual(
            tr.json()["entries"][0]["voucher_type"], "core.DummySubmittableDoc"
        )


class BatchApiTests(BatchBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("b_admin", "ba@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def test_create_auto_number_manual_and_shelf_life(self):
        r = self.c.post(
            "/api/stock/batches/",
            {"item": self.haldi.id, "manufacturing_date": "2026-05-01"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertRegex(r.data["batch_no"], r"^HALDI-W-\d{6}-01$")
        self.assertEqual(r.data["expiry_date"], "2027-05-01")
        r = self.c.post(
            "/api/stock/batches/",
            {
                "item": self.haldi.id,
                "batch_no": "MANUAL-1",
                "expiry_date": "2027-01-01",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        dup = self.c.post(
            "/api/stock/batches/",
            {
                "item": self.haldi.id,
                "batch_no": "MANUAL-1",
                "expiry_date": "2027-01-01",
            },
            format="json",
        )
        self.assertEqual(dup.status_code, 400)
        # No dates at all: the manufacturing date defaults to today and the expiry
        # follows from the item's shelf life.
        today = batch_service.datetime.date.today()
        auto = self.c.post(
            "/api/stock/batches/", {"item": self.haldi.id}, format="json"
        )
        self.assertEqual(auto.status_code, 201, auto.content)
        self.assertEqual(auto.data["manufacturing_date"], today.isoformat())
        self.assertEqual(
            auto.data["expiry_date"],
            (today + batch_service.datetime.timedelta(days=365)).isoformat(),
        )

    def test_batch_item_validation_and_delete_protection(self):
        r = self.c.post("/api/stock/batches/", {"item": self.plain.id}, format="json")
        self.assertEqual(r.status_code, 400)
        b = self.mkbatch("USED", expiry_date=D0(2027, 1, 1))
        self.post(self.haldi, self.wh, 1, 1, b)
        d = self.c.delete(f"/api/stock/batches/{b.id}/")
        self.assertEqual(d.status_code, 409)
        # item cannot change once it has stock
        p = self.c.patch(
            f"/api/stock/batches/{b.id}/", {"item": self.salt.id}, format="json"
        )
        self.assertEqual(p.status_code, 400)
        # expiry stays editable
        p = self.c.patch(
            f"/api/stock/batches/{b.id}/", {"expiry_date": "2027-02-01"}, format="json"
        )
        self.assertEqual(p.status_code, 200, p.content)
        # unused batch deletes
        u = self.mkbatch("UNUSED", expiry_date=D0(2027, 1, 1))
        self.assertEqual(self.c.delete(f"/api/stock/batches/{u.id}/").status_code, 204)

    def test_sle_serializer_exposes_batch(self):
        b = self.mkbatch("SLEB", expiry_date=D0(2027, 1, 1))
        self.post(self.haldi, self.wh, 1, 1, b)
        r = self.c.get("/api/stock/sle/").json()["results"][0]
        self.assertEqual(r["batch"], b.id)
        self.assertEqual(r["batch_no"], "SLEB")
        self.assertEqual(self.c.get(f"/api/stock/sle/?batch={b.id}").json()["count"], 1)

    def test_stock_entry_line_batch_fields_roundtrip(self):
        payload = {
            "stock_entry_type": "Material Receipt",
            "posting_date": "2026-04-01",
            "company": self.company.id,
            "items": [
                {
                    "item": self.haldi.id,
                    "target_warehouse": self.wh.id,
                    "qty": "10.000",
                    "valuation_rate": "50.000000",
                    "supplier_batch_no": "S-1",
                    "manufacturing_date": "2026-04-01",
                }
            ],
        }
        r = self.c.post("/api/stock/stock-entries/", payload, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data["items"][0]["batch"])
        sub = self.c.post(f"/api/stock/stock-entries/{r.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)
        got = self.c.get(f"/api/stock/stock-entries/{r.data['id']}/").json()
        self.assertEqual(got["items"][0]["batch_no"], "HALDI-W-260401-01")
        # pick + enter in one line is a 400 at save time
        existing = Batch.objects.get()
        payload["items"][0]["batch"] = existing.id
        bad = self.c.post("/api/stock/stock-entries/", payload, format="json")
        self.assertEqual(bad.status_code, 400)


class ItemToggleAndConstraintTests(BatchBase):
    def test_item_toggle_refused_once_sle_exists(self):
        admin = User.objects.create_superuser("t_admin", "ta@t.com", "pw")
        c = APIClient()
        c.force_authenticate(admin)
        # free before any stock
        r = c.patch(
            f"/api/stock/items/{self.plain.id}/", {"has_batch_no": True}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.post(self.salt, self.wh, 1, 1, self.mkbatch("S1", self.salt))
        r = c.patch(
            f"/api/stock/items/{self.salt.id}/", {"has_batch_no": False}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        r = c.patch(
            f"/api/stock/items/{self.salt.id}/",
            {"has_expiry_date": True, "shelf_life_in_days": 30},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        # the model guard backs the API (admin / ORM)
        self.salt.has_batch_no = False
        with self.assertRaises(ValidationError):
            self.salt.save()
        # unrelated edits still fine
        self.salt.refresh_from_db()
        self.salt.name = "Rock salt"
        self.salt.save()

    def test_item_serializer_combination_rules(self):
        admin = User.objects.create_superuser("t_admin2", "ta2@t.com", "pw")
        c = APIClient()
        c.force_authenticate(admin)
        r = c.patch(
            f"/api/stock/items/{self.plain.id}/",
            {"has_expiry_date": True},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        r = c.patch(
            f"/api/stock/items/{self.plain.id}/",
            {"shelf_life_in_days": 10},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_check_constraints_fire_on_direct_orm_writes(self):
        group = ItemGroup.objects.get()
        uom = UOM.objects.get()
        for kw in (
            dict(has_expiry_date=True, has_batch_no=False),
            dict(has_batch_no=True, has_expiry_date=False, shelf_life_in_days=5),
            dict(has_batch_no=True, has_expiry_date=True, shelf_life_in_days=0),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Item.objects.bulk_create(
                    [
                        Item(
                            code="BAD",
                            name="BAD",
                            item_group=group,
                            stock_uom=uom,
                            **kw,
                        )
                    ]
                )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Batch.objects.create(
                batch_no="BADB",
                item=self.haldi,
                manufacturing_date=D0(2026, 2, 1),
                expiry_date=D0(2026, 1, 1),
            )
        ste = StockEntry.objects.create(
            name="S-CHK",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=D0(2026, 4, 1),
            company=self.company,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockEntryItem.objects.create(
                stock_entry=ste,
                item=self.haldi,
                qty=D("1"),
                manufacturing_date=D0(2026, 2, 1),
                expiry_date=D0(2026, 1, 1),
            )

    def test_batch_item_cannot_change_once_stocked(self):
        b = self.mkbatch("IMM")
        self.post(self.haldi, self.wh, 1, 1, b)
        b.item = self.salt
        with self.assertRaises(ValidationError):
            b.save()


class ReportTests(BatchBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("r_admin", "ra@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def test_batch_wise_balance_matches_bin_and_respects_as_of(self):
        a, b = self.mkbatch("RA", expiry_date=D0(2027, 1, 1)), self.mkbatch("RB")
        self.post(self.haldi, self.wh, 100, 108, a, date=D0(2026, 4, 1))
        self.post(self.haldi, self.wh, 50, 114, b, date=D0(2026, 4, 10))
        self.post(self.haldi, self.wh, -40, 0, a, date=D0(2026, 4, 20))
        res = self.c.get("/api/stock/reports/batch-wise-balance/")
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        by = {r["batch_no"]: r for r in data["rows"]}
        self.assertEqual(by["RA"]["balance_qty"], "60.000")
        self.assertEqual(by["RA"]["valuation_rate"], "108.000000")
        self.assertEqual(by["RA"]["stock_value"], "6480.00")
        self.assertEqual(by["RB"]["balance_qty"], "50.000")
        bin_ = Bin.objects.get(item=self.haldi, warehouse=self.wh)
        self.assertEqual(data["total_balance_qty"], str(bin_.actual_qty))
        self.assertEqual(data["total_stock_value"], str(bin_.stock_value))
        for k in (
            "item_code",
            "item_name",
            "warehouse_name",
            "batch_no",
            "manufacturing_date",
            "expiry_date",
            "days_to_expiry",
            "balance_qty",
            "valuation_rate",
            "stock_value",
        ):
            self.assertIn(k, by["RA"])
        asof = self.c.get(
            "/api/stock/reports/batch-wise-balance/?as_of=2026-04-05"
        ).json()
        self.assertEqual([r["batch_no"] for r in asof["rows"]], ["RA"])
        self.assertEqual(asof["rows"][0]["balance_qty"], "100.000")
        # zero balances hidden unless asked
        self.post(self.haldi, self.wh, -50, 0, b, date=D0(2026, 4, 21))
        names = lambda q: [  # noqa: E731
            r["batch_no"]
            for r in self.c.get("/api/stock/reports/batch-wise-balance/" + q).json()[
                "rows"
            ]
        ]
        self.assertEqual(names(""), ["RA"])
        self.assertEqual(names("?include_zero=1"), ["RA", "RB"])
        self.assertEqual(names(f"?batch={b.id}&include_zero=1"), ["RB"])
        self.assertEqual(
            self.c.get("/api/stock/reports/batch-wise-balance/?as_of=zz").status_code,
            400,
        )

    def test_expiry_report_statuses(self):
        today = datetime.date.today()
        td = lambda n: today + datetime.timedelta(days=n)  # noqa: E731
        gone = self.mkbatch("GONE", expiry_date=td(-5))
        soon = self.mkbatch("SOON", expiry_date=td(20))
        far = self.mkbatch("FAR", expiry_date=td(400))
        none = self.mkbatch("NONE")
        empty = self.mkbatch("EMPTYB", expiry_date=td(3))
        for b in (gone, soon, far, none, empty):
            self.post(self.haldi, self.wh, 10, 10, b, date=td(-30))
        self.post(self.haldi, self.wh, -10, 0, empty, date=td(-29))
        rows = self.c.get("/api/stock/reports/expiry/").json()["rows"]
        self.assertEqual(
            [(r["batch_no"], r["status"]) for r in rows],
            [("GONE", "Expired"), ("SOON", "Expiring")],
        )
        self.assertEqual(rows[0]["days_to_expiry"], -5)
        self.assertEqual(rows[1]["stock_value"], "100.00")
        r2 = self.c.get("/api/stock/reports/expiry/?include_expired=0").json()["rows"]
        self.assertEqual([r["batch_no"] for r in r2], ["SOON"])
        r3 = self.c.get("/api/stock/reports/expiry/?within_days=10").json()["rows"]
        self.assertEqual([r["batch_no"] for r in r3], ["GONE"])
        r4 = self.c.get("/api/stock/reports/expiry/?within_days=500").json()["rows"]
        self.assertEqual([r["batch_no"] for r in r4], ["GONE", "SOON", "FAR"])
        self.assertEqual(
            self.c.get("/api/stock/reports/expiry/?within_days=-1").status_code, 400
        )


class IntegrityTests(BatchBase):
    def test_green_then_detects_corruption(self):
        a = self.mkbatch("IA", expiry_date=D0(2027, 1, 1))
        self.post(self.haldi, self.wh, 10, 10, a)
        self.post(self.plain, self.wh, 4, 2)
        self.assertEqual(batch_service.integrity_errors(), [])

        # 1. batch missing on a batch item's SLE
        StockLedgerEntry.objects.filter(batch=a).update(batch=None)
        errs = batch_service.integrity_errors()
        self.assertTrue(any("Batch missing" in e for e in errs), errs)
        self.assertTrue(
            any(
                "Batch totals" in e or "without a Bin" in e or "Batch" in e
                for e in errs
            )
        )
        StockLedgerEntry.objects.filter(batch__isnull=True, item=self.haldi).update(
            batch=a
        )

        # 2. batch on a non-batch item / wrong-item batch
        StockLedgerEntry.objects.filter(item=self.plain).update(batch=a)
        errs = batch_service.integrity_errors()
        self.assertTrue(any("not batch-tracked" in e for e in errs), errs)
        self.assertTrue(any("!= batch" in e for e in errs), errs)
        StockLedgerEntry.objects.filter(item=self.plain).update(batch=None)
        self.assertEqual(batch_service.integrity_errors(), [])

        # 3. negative batch balance
        StockLedgerEntry.objects.filter(batch=a).update(qty=D("-1"))
        self.assertTrue(
            any("Negative batch balance" in e for e in batch_service.integrity_errors())
        )
        StockLedgerEntry.objects.filter(batch=a).update(qty=D("10"))

        # 4. sum of batches != Bin
        Bin.objects.filter(item=self.haldi).update(actual_qty=D("9"))
        self.assertTrue(
            any("Batch totals" in e for e in batch_service.integrity_errors())
        )
        Bin.objects.filter(item=self.haldi).update(actual_qty=D("10"))
        self.assertEqual(batch_service.integrity_errors(), [])

        # 5. expiry-tracked item whose batch has no expiry
        Batch.objects.filter(pk=a.pk).update(expiry_date=None)
        self.assertTrue(any("no expiry" in e for e in batch_service.integrity_errors()))

    def test_expired_stock_warning(self):
        a = self.mkbatch("WARN", expiry_date=D0(2020, 1, 1))
        self.post(self.haldi, self.wh, 2, 5, a, date=D0(2019, 1, 1))
        w = batch_service.integrity_warnings(today=D0(2026, 1, 1))
        self.assertEqual(len(w), 1)
        self.assertIn("expired", w[0])
        self.assertEqual(batch_service.integrity_warnings(today=D0(2019, 6, 1)), [])


class RbacTests(BatchBase):
    def _client(self, name, labels, **flags):
        user = User.objects.create_user(name, f"{name}@t.com", "pw")
        role = Role.objects.create(name=f"role_{name}")
        UserRole.objects.create(user=user, role=role)
        for label in labels:
            RolePermission.objects.create(role=role, model_label=label, **flags)
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_unauthorised_reads_are_403(self):
        c = self._client("nobody", ["stock.UOM"], can_read=True)
        for url in (
            "/api/stock/batches/",
            f"/api/stock/batches/fefo/?item={self.haldi.id}&warehouse={self.wh.id}&qty=1",
            "/api/stock/reports/batch-wise-balance/",
            "/api/stock/reports/expiry/",
        ):
            self.assertEqual(c.get(url).status_code, 403, url)

    def test_batch_read_vs_write_and_report_gate(self):
        reader = self._client("reader", ["stock.Batch"], can_read=True)
        self.assertEqual(reader.get("/api/stock/batches/").status_code, 200)
        r = reader.post(
            "/api/stock/batches/",
            {"item": self.salt.id, "batch_no": "NOPE"},
            format="json",
        )
        self.assertEqual(r.status_code, 403)
        # reports are gated on stock.Bin, not stock.Batch
        self.assertEqual(reader.get("/api/stock/reports/expiry/").status_code, 403)
        bin_reader = self._client("binreader", ["stock.Bin"], can_read=True)
        self.assertEqual(bin_reader.get("/api/stock/reports/expiry/").status_code, 200)
        self.assertEqual(
            bin_reader.get("/api/stock/reports/batch-wise-balance/").status_code, 200
        )

    def test_seeded_roles_cover_batch(self):
        call_command("seed_defaults")
        for role, flag in (
            ("Stock Manager", "can_create"),
            ("Default Role", "can_read"),
            ("Sales Manager", "can_read"),
            ("Purchase Manager", "can_read"),
            ("System Manager", "can_delete"),
        ):
            self.assertTrue(
                RolePermission.objects.filter(
                    role__name=role, model_label="stock.Batch", **{flag: True}
                ).exists(),
                role,
            )
        self.assertFalse(
            RolePermission.objects.filter(
                role__name="Default Role", model_label="stock.Batch", can_create=True
            ).exists()
        )
