"""Quick notes: saved for later, never written to the books.

Each test that writes checks the ledgers before and after, so a note that touches
GLEntry or StockLedgerEntry would fail here.
"""

import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import GLEntry
from core.models import Company
from stock.models import StockLedgerEntry

from .models import QuickNote
from .tests import setup_test_masters

User = get_user_model()

URL = "/api/assistant/quick-notes/"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 200


def done_url(pk):
    return f"{URL}{pk}/done/"


class QuickNoteBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="notekeeper", email="notekeeper@erp.local", password="password"
        )
        self.client.force_authenticate(user=self.user)
        setup_test_masters(self)
        self.other_company = Company.objects.create(
            name="Other Co " + uuid.uuid4().hex[:8],
            abbr="OC" + uuid.uuid4().hex[:4],
            default_currency="INR",
        )

    def post_note(self, company=None, **data):
        headers = {"X-Company-ID": str((company or self.company).pk)}
        return self.client.post(URL, data, format="multipart", headers=headers)

    def snapshot(self):
        return {
            "notes": QuickNote.objects.count(),
            "gl": GLEntry.objects.count(),
            "sle": StockLedgerEntry.objects.count(),
        }


class QuickNoteRulesTests(QuickNoteBase):
    def test_database_refuses_an_empty_note(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuickNote.objects.create(
                company=self.company, created_by=self.user, text="", attachment=""
            )
        self.assertEqual(QuickNote.objects.count(), 0)

    def test_database_refuses_an_unknown_status(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuickNote.objects.create(
                company=self.company,
                created_by=self.user,
                text="Haldi bill",
                status="archived",
            )

    def test_database_refuses_done_without_a_time(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuickNote.objects.create(
                company=self.company,
                created_by=self.user,
                text="Haldi bill",
                status="done",
                done_at=None,
            )

    def test_api_refuses_an_empty_or_blank_note(self):
        before = self.snapshot()
        for text in ["", "   "]:
            with self.subTest(text=text):
                resp = self.post_note(text=text)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("text", resp.data)
        self.assertEqual(self.snapshot(), before)


class QuickNoteCreateTests(QuickNoteBase):
    def test_text_note_is_saved_open_for_the_active_company(self):
        before = self.snapshot()
        resp = self.post_note(text="Gupta bill, haldi 10 kg")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "open")
        self.assertIsNone(resp.data["done_at"])
        self.assertFalse(resp.data["has_attachment"])
        note = QuickNote.objects.get(pk=resp.data["id"])
        self.assertEqual(note.company, self.company)
        self.assertEqual(note.created_by, self.user)
        # A note writes nothing to the books.
        self.assertEqual(self.snapshot()["notes"], before["notes"] + 1)
        self.assertEqual(self.snapshot()["gl"], before["gl"])
        self.assertEqual(self.snapshot()["sle"], before["sle"])

    def test_photo_note_stores_the_file_and_hides_its_path(self):
        resp = self.post_note(
            attachment=SimpleUploadedFile("bill.jpg", JPEG, "image/jpeg")
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["has_attachment"])
        self.assertNotIn("attachment", resp.data)
        note = QuickNote.objects.get(pk=resp.data["id"])
        self.assertTrue(note.attachment.name.startswith("quick_notes/"))
        self.assertTrue(note.attachment.name.endswith(".jpg"))
        with note.attachment.open("rb") as fh:
            self.assertEqual(fh.read(), JPEG)

    def test_voice_clip_is_accepted(self):
        resp = self.post_note(
            attachment=SimpleUploadedFile("note.webm", WEBM, "audio/webm")
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_failed_uploads_are_refused_and_write_nothing(self):
        cases = [
            ("note.txt", b"plain words"),
            ("sheet.csv", b"item,qty\nhaldi,10\n"),
            ("note.webm", b"not audio at all"),
            ("note.jpg", b""),
        ]
        before = self.snapshot()
        for name, data in cases:
            with self.subTest(name=name):
                resp = self.post_note(attachment=SimpleUploadedFile(name, data))
                self.assertEqual(resp.status_code, 400)
                self.assertIn("attachment", resp.data)
        self.assertEqual(self.snapshot(), before)

    def test_file_over_the_bill_limit_is_refused(self):
        before = self.snapshot()
        with mock.patch("assistant.quick_notes.BILL_MAX_BYTES", 100):
            resp = self.post_note(attachment=SimpleUploadedFile("bill.jpg", JPEG))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("attachment", resp.data)
        self.assertEqual(self.snapshot(), before)


class QuickNoteListTests(QuickNoteBase):
    def test_open_notes_come_first_then_newest_first(self):
        old_open = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="old open"
        )
        new_open = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="new open"
        )
        done = QuickNote.objects.create(
            company=self.company,
            created_by=self.user,
            text="done one",
            status="done",
            done_at=timezone.now(),
        )
        # Created in order above, but make the timestamps unambiguous.
        QuickNote.objects.filter(pk=old_open.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        QuickNote.objects.filter(pk=new_open.pk).update(created_at=timezone.now())
        QuickNote.objects.filter(pk=done.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )

        resp = self.client.get(URL, headers={"X-Company-ID": str(self.company.pk)})
        self.assertEqual(resp.status_code, 200)
        ids = [row["id"] for row in resp.data["results"]]
        self.assertEqual(ids, [new_open.pk, old_open.pk, done.pk])

    def test_list_only_shows_the_active_companys_notes(self):
        mine = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="mine"
        )
        theirs = QuickNote.objects.create(
            company=self.other_company, created_by=self.user, text="theirs"
        )

        here = self.client.get(URL, headers={"X-Company-ID": str(self.company.pk)})
        there = self.client.get(
            URL, headers={"X-Company-ID": str(self.other_company.pk)}
        )
        self.assertEqual([r["id"] for r in here.data["results"]], [mine.pk])
        self.assertEqual([r["id"] for r in there.data["results"]], [theirs.pk])

    def test_a_note_of_another_company_cannot_be_marked_done(self):
        theirs = QuickNote.objects.create(
            company=self.other_company, created_by=self.user, text="theirs"
        )
        resp = self.client.post(
            done_url(theirs.pk), headers={"X-Company-ID": str(self.company.pk)}
        )
        self.assertEqual(resp.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "open")

    def test_anonymous_user_is_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))


class QuickNoteDoneTests(QuickNoteBase):
    def test_done_marks_the_note_done_with_a_time(self):
        note = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="Gupta bill"
        )
        before = self.snapshot()
        resp = self.client.post(
            done_url(note.pk), headers={"X-Company-ID": str(self.company.pk)}
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "done")
        self.assertIsNotNone(resp.data["done_at"])
        note.refresh_from_db()
        self.assertEqual(note.status, "done")
        self.assertIsNotNone(note.done_at)
        self.assertEqual(self.snapshot(), before)

    def test_done_twice_keeps_the_first_time(self):
        note = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="Gupta bill"
        )
        headers = {"X-Company-ID": str(self.company.pk)}
        self.client.post(done_url(note.pk), headers=headers)
        note.refresh_from_db()
        first = note.done_at

        resp = self.client.post(done_url(note.pk), headers=headers)
        self.assertEqual(resp.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.done_at, first)

    def test_done_on_a_missing_note_is_a_404(self):
        resp = self.client.post(
            done_url(999999), headers={"X-Company-ID": str(self.company.pk)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_there_is_no_update_or_delete(self):
        note = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="Gupta bill"
        )
        headers = {"X-Company-ID": str(self.company.pk)}
        resp = self.client.patch(
            f"{URL}{note.pk}/", {"text": "changed"}, format="json", headers=headers
        )
        self.assertIn(resp.status_code, (404, 405))
        resp = self.client.delete(f"{URL}{note.pk}/", headers=headers)
        self.assertIn(resp.status_code, (404, 405))
        note.refresh_from_db()
        self.assertEqual(note.text, "Gupta bill")


def file_url(pk):
    return f"{URL}{pk}/file/"


class QuickNoteFileTests(QuickNoteBase):
    def make_photo_note(self, company):
        return QuickNote.objects.create(
            company=company,
            created_by=self.user,
            attachment=SimpleUploadedFile("bill.jpg", JPEG, "image/jpeg"),
        )

    def test_owner_company_gets_the_bytes_inline(self):
        note = self.make_photo_note(self.company)
        resp = self.client.get(
            file_url(note.pk), headers={"X-Company-ID": str(self.company.pk)}
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), JPEG)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertTrue(resp["Content-Disposition"].startswith("inline"))
        self.assertEqual(resp["Cache-Control"], "private, no-store")

    def test_another_company_gets_404(self):
        note = self.make_photo_note(self.company)
        resp = self.client.get(
            file_url(note.pk), headers={"X-Company-ID": str(self.other_company.pk)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_note_without_a_file_is_404(self):
        note = QuickNote.objects.create(
            company=self.company, created_by=self.user, text="Gupta bill"
        )
        resp = self.client.get(
            file_url(note.pk), headers={"X-Company-ID": str(self.company.pk)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_file_request_writes_nothing(self):
        note = self.make_photo_note(self.company)
        before = self.snapshot()
        resp = self.client.get(
            file_url(note.pk), headers={"X-Company-ID": str(self.company.pk)}
        )
        self.assertEqual(resp.status_code, 200)
        resp.close()
        self.assertEqual(self.snapshot(), before)
