"""Bill photo to a pre-filled Purchase Invoice.

No test reaches a real LLM. The vision call is stubbed at ``assistant.bill_photo``;
the adapter tests patch ``urlopen`` so that nothing leaves the process.
"""

import hashlib
import json
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from buying.models import PurchaseInvoice
from core.models import Attachment
from stock.models import Item, StockLedgerEntry

from .bill_photo import (
    BillReaderUnavailable,
    BillUnreadable,
    call_vision_model,
    is_password_protected,
    parse_bill,
)
from .models import AssistantConversation, AssistantMessage, AssistantProposal
from .tests import setup_test_masters

User = get_user_model()

URL = "/api/assistant/bill-photo/"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
PDF = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>\nendobj\ntrailer<< /Root 1 0 R >>\n%%EOF"
PDF_ENCRYPTED = (
    b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>\nendobj\n"
    b"trailer<< /Root 1 0 R /Encrypt 2 0 R >>\n%%EOF"
)
VISION = "assistant.bill_photo.call_vision_model"


def reading(**overrides):
    """The model's JSON reply for a clear bill: Gupta Suppliers, 10 kg Haldi at 150."""
    data = {
        "readable": True,
        "confidence": 0.92,
        "supplier_name": "Gupta Suppliers",
        "bill_no": "GS/2041",
        "bill_date": "2026-10-20",
        "total": 1500,
        "items": [{"name": "Haldi Powder", "qty": 10, "rate": 150}],
    }
    data.update(overrides)
    return json.dumps(data)


class BillPhotoBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_superuser(
            username="billclerk", email="billclerk@erp.local", password="password"
        )
        self.client.force_authenticate(user=self.user)
        setup_test_masters(self)

    def post_bill(self, data=JPEG, name="bill.jpg", content_type="image/jpeg", **extra):
        upload = SimpleUploadedFile(name, data, content_type=content_type)
        return self.client.post(URL, {"file": upload, **extra}, format="multipart")

    def post_files(self, *files):
        """Several bill pages, as (name, data, content_type) tuples, in the `files` field."""
        uploads = [
            SimpleUploadedFile(name, data, content_type=content_type)
            for name, data, content_type in files
        ]
        return self.client.post(URL, {"files": uploads}, format="multipart")

    def snapshot(self):
        return {
            "pi": PurchaseInvoice.objects.count(),
            "gl": GLEntry.objects.count(),
            "sle": StockLedgerEntry.objects.count(),
            "proposals": AssistantProposal.objects.count(),
            "messages": AssistantMessage.objects.count(),
            "staged": Attachment.objects.filter(
                model_label="assistant.AssistantMessage"
            ).count(),
        }


class BillPhotoProposalTests(BillPhotoBase):
    def test_valid_bill_becomes_prefilled_proposal_with_photo_as_proof(self):
        before = self.snapshot()
        with mock.patch(VISION, return_value=reading()) as vision:
            resp = self.post_bill()

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["error"])
        proposal = resp.data["proposal"]
        self.assertEqual(proposal["type"], "proposal")
        self.assertEqual(proposal["target"], "buying.PurchaseInvoice")
        self.assertEqual(proposal["route"], "/buying/purchase-invoices/new")
        values = proposal["values"]
        self.assertEqual(values["supplier"], self.supplier.pk)
        self.assertEqual(values["bill_no"], "GS/2041")
        self.assertEqual(values["bill_date"], "2026-10-20")
        self.assertEqual(len(values["items"]), 1)
        self.assertEqual(values["items"][0]["item"], self.item.pk)
        self.assertEqual(values["items"][0]["qty"], "10.000")
        self.assertEqual(values["items"][0]["rate"], "150.00")
        self.assertEqual(vision.call_count, 1)
        self.assertEqual(vision.call_args.args[1], "image/jpeg")

        row = AssistantProposal.objects.get(pk=proposal["proposal_id"])
        self.assertEqual(row.message_id, resp.data["message_id"])
        self.assertEqual(row.status, "proposed")

        staged = Attachment.objects.get(
            model_label="assistant.AssistantMessage",
            object_id=str(resp.data["message_id"]),
        )
        self.assertEqual(staged.sha256, hashlib.sha256(JPEG).hexdigest())
        with staged.file.open("rb") as fh:
            self.assertEqual(fh.read(), JPEG)

        # Nothing is saved: no Purchase Invoice, no ledger rows.
        after = self.snapshot()
        self.assertEqual(after["pi"], before["pi"])
        self.assertEqual(after["gl"], before["gl"])
        self.assertEqual(after["proposals"], before["proposals"] + 1)
        self.assertEqual(after["staged"], before["staged"] + 1)

    def test_unknown_item_asks_a_question_and_guesses_nothing(self):
        before = self.snapshot()
        with mock.patch(
            VISION,
            return_value=reading(
                items=[{"name": "Kali Jeera Special", "qty": 5, "rate": 300}]
            ),
        ):
            resp = self.post_bill()

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["proposal"])
        question = resp.data["question"]
        self.assertIn("Kali Jeera Special", question["text"])
        self.assertEqual(question["options"], [])
        self.assertEqual(resp.data["reply"], question["text"])
        # The turn is recorded, but no proposal and no proof are kept.
        after = self.snapshot()
        self.assertEqual(after["pi"], before["pi"])
        self.assertEqual(after["proposals"], before["proposals"])
        self.assertEqual(after["staged"], before["staged"])
        self.assertEqual(after["messages"], before["messages"] + 2)

    def test_ambiguous_item_offers_the_matches(self):
        Item.objects.create(
            code="HALDI-WHOLE-0001",
            name="Haldi Whole",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_active=True,
        )
        with mock.patch(
            VISION, return_value=reading(items=[{"name": "Haldi", "qty": 2}])
        ):
            resp = self.post_bill()

        self.assertIsNone(resp.data["proposal"])
        self.assertGreaterEqual(len(resp.data["question"]["options"]), 2)

    def test_unknown_supplier_asks_a_question(self):
        before = self.snapshot()
        with mock.patch(
            VISION, return_value=reading(supplier_name="Zxqv Imaginary Traders")
        ):
            resp = self.post_bill()

        self.assertIsNone(resp.data["proposal"])
        self.assertIn("Zxqv Imaginary Traders", resp.data["question"]["text"])
        self.assertEqual(self.snapshot()["proposals"], before["proposals"])

    def test_bill_total_mismatch_is_a_warning_on_the_proposal(self):
        with mock.patch(VISION, return_value=reading(total=999)):
            resp = self.post_bill()
        self.assertEqual(resp.data["proposal"]["type"], "proposal")
        self.assertTrue(
            any("Bill total mismatch" in w for w in resp.data["proposal"]["warnings"])
        )


class BillPhotoFileTests(BillPhotoBase):
    def test_bad_files_are_rejected_before_any_read(self):
        cases = [
            (
                "bill.exe",
                b"MZ\x90\x00" + b"\x00" * 50,
                "image/jpeg",
                "attachment_type_not_allowed",
            ),
            (
                "bill.jpg",
                b"hello, this is not a picture",
                "image/jpeg",
                "attachment_content_mismatch",
            ),
            (
                "bill.txt",
                b"item,qty\nhaldi,10\n",
                "text/plain",
                "attachment_type_not_allowed",
            ),
            ("bill.csv", b"item,qty\nhaldi,10\n", "text/csv", "bill_type_not_allowed"),
            ("bill.jpg", b"", "image/jpeg", "attachment_empty"),
        ]
        before = self.snapshot()
        for name, data, content_type, error in cases:
            with self.subTest(name=name, error=error), mock.patch(VISION) as vision:
                resp = self.post_bill(data=data, name=name, content_type=content_type)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.data["error"], error)
                vision.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_oversized_bill_photo_is_refused(self):
        with mock.patch("assistant.views.BILL_MAX_BYTES", 10), mock.patch(
            VISION
        ) as vision:
            resp = self.post_bill()
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.data["error"], "bill_too_large")
        vision.assert_not_called()

    def test_missing_file_is_a_400(self):
        resp = self.client.post(URL, {}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "no_file")

    def test_invalid_conversation_id_is_a_400_not_a_500(self):
        with mock.patch(VISION) as vision:
            resp = self.post_bill(conversation_id="abc")
        self.assertEqual(resp.status_code, 400)
        vision.assert_not_called()


class BillPdfAndPagesTests(BillPhotoBase):
    def test_pdf_bill_becomes_purchase_invoice_proposal(self):
        before = self.snapshot()
        with mock.patch(VISION, return_value=reading()) as vision:
            resp = self.post_bill(
                data=PDF, name="bill.pdf", content_type="application/pdf"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["error"])
        self.assertEqual(resp.data["proposal"]["type"], "proposal")
        self.assertEqual(resp.data["proposal"]["target"], "buying.PurchaseInvoice")
        self.assertEqual(vision.call_args.args[0], PDF)
        self.assertEqual(vision.call_args.args[1], "application/pdf")

        staged = Attachment.objects.get(
            model_label="assistant.AssistantMessage",
            object_id=str(resp.data["message_id"]),
        )
        self.assertEqual(staged.content_type, "application/pdf")
        self.assertEqual(staged.original_filename, "bill.pdf")

        # Nothing is saved: no invoice, no ledger rows, no stock rows.
        after = self.snapshot()
        self.assertEqual(after["pi"], before["pi"])
        self.assertEqual(after["gl"], before["gl"])
        self.assertEqual(after["sle"], before["sle"])
        self.assertEqual(after["proposals"], before["proposals"] + 1)

    def test_password_protected_pdf_is_refused_before_the_model_is_called(self):
        before = self.snapshot()
        with mock.patch(VISION) as vision:
            resp = self.post_bill(
                data=PDF_ENCRYPTED, name="bill.pdf", content_type="application/pdf"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["error"], "bill_pdf_protected")
        self.assertEqual(
            resp.data["reply"],
            "This PDF is password-protected. Send it without a password or as a photo.",
        )
        self.assertIsNone(resp.data["proposal"])
        vision.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_two_images_make_one_proposal_with_both_pages_staged(self):
        before = self.snapshot()
        with mock.patch(VISION, return_value=reading()) as vision:
            resp = self.post_files(
                ("bill-1.jpg", JPEG, "image/jpeg"),
                ("bill-2.png", PNG, "image/png"),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["proposal"]["type"], "proposal")
        self.assertEqual(vision.call_count, 1)
        self.assertEqual(vision.call_args.args[1], "image/jpeg")
        self.assertEqual(vision.call_args.args[2], [(PNG, "image/png")])

        staged = Attachment.objects.filter(
            model_label="assistant.AssistantMessage",
            object_id=str(resp.data["message_id"]),
        )
        self.assertEqual(staged.count(), 2)
        after = self.snapshot()
        self.assertEqual(after["pi"], before["pi"])
        self.assertEqual(after["gl"], before["gl"])
        self.assertEqual(after["proposals"], before["proposals"] + 1)

    def test_too_many_files_is_refused_before_any_read(self):
        five = [(f"page-{i}.jpg", JPEG + bytes([i]), "image/jpeg") for i in range(5)]
        before = self.snapshot()
        with mock.patch(VISION) as vision:
            resp = self.post_files(*five)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "bill_too_many_files")
        vision.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_files_over_the_combined_limit_are_refused(self):
        before = self.snapshot()
        with mock.patch("assistant.views.BILL_MAX_BYTES", 250), mock.patch(
            "assistant.views.BILL_MAX_TOTAL_BYTES", 500
        ), mock.patch(VISION) as vision:
            resp = self.post_files(
                *[(f"page-{i}.jpg", JPEG, "image/jpeg") for i in range(3)]
            )
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.data["error"], "bill_too_large")
        vision.assert_not_called()
        self.assertEqual(self.snapshot(), before)


class PdfProtectionTests(APITestCase):
    def test_encryption_is_read_from_the_trailer_only(self):
        self.assertFalse(is_password_protected(PDF))
        self.assertTrue(is_password_protected(PDF_ENCRYPTED))
        # A cross-reference stream names the encryption dictionary the same way.
        self.assertTrue(
            is_password_protected(b"%PDF-1.5\n<< /Type /XRef /Encrypt 9 0 R >>")
        )
        # A word that only starts with the same letters is not encryption.
        self.assertFalse(is_password_protected(b"%PDF-1.4\n/Encrypted true\n%%EOF"))


class BillPhotoFailureTests(BillPhotoBase):
    def assert_friendly_failure(self, resp, error):
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["error"], error)
        self.assertIsNone(resp.data["proposal"])
        self.assertTrue(resp.data["reply"])
        self.assertNotIn("Traceback", resp.data["reply"])

    def test_model_failure_returns_a_friendly_reply_and_writes_nothing(self):
        before = self.snapshot()
        with mock.patch(VISION, side_effect=BillReaderUnavailable("timeout")):
            resp = self.post_bill()
        self.assert_friendly_failure(resp, "bill_reader_unavailable")
        self.assertEqual(self.snapshot(), before)

    def test_network_failure_inside_the_real_adapter_is_friendly(self):
        before = self.snapshot()
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"}), mock.patch(
            "assistant.bill_photo.urllib.request.urlopen",
            side_effect=TimeoutError("slow"),
        ):
            resp = self.post_bill()
        self.assert_friendly_failure(resp, "bill_reader_unavailable")
        self.assertEqual(self.snapshot(), before)

    def test_no_api_key_is_friendly_and_never_calls_the_network(self):
        keys = {"LLM_API_KEY": "", "OPENROUTER_API_KEY": "", "ANTHROPIC_API_KEY": ""}
        with mock.patch.dict("os.environ", keys), mock.patch(
            "assistant.bill_photo.urllib.request.urlopen"
        ) as urlopen:
            resp = self.post_bill()
        self.assert_friendly_failure(resp, "bill_reader_unavailable")
        urlopen.assert_not_called()

    def test_unreadable_photo_is_friendly(self):
        replies = [
            "Sorry, I cannot read this image.",
            reading(readable=False),
            reading(confidence=0.2),
            reading(items=[]),
            reading(items=[{"name": "Haldi", "qty": "many"}]),
            "",
        ]
        for reply in replies:
            with self.subTest(reply=reply[:40]), mock.patch(VISION, return_value=reply):
                resp = self.post_bill()
                self.assert_friendly_failure(resp, "bill_unreadable")

    def test_permission_denied_user_never_reaches_the_model(self):
        clerk = User.objects.create_user(
            username="plainclerk", email="plain@erp.local", password="password"
        )
        self.client.force_authenticate(user=clerk)
        before = self.snapshot()
        with mock.patch(VISION) as vision:
            resp = self.post_bill()
        self.assert_friendly_failure(resp, "permission_denied")
        vision.assert_not_called()
        self.assertEqual(self.snapshot(), before)


class BillPhotoSaveTests(BillPhotoBase):
    def setUp(self):
        super().setUp()
        liabilities = Account.objects.create(
            name="Liabilities-" + uuid.uuid4().hex[:6],
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        payable = Account.objects.create(
            name="Creditors-" + uuid.uuid4().hex[:6],
            company=self.company,
            parent=liabilities,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        purchases = Account.objects.create(
            name="Purchases-" + uuid.uuid4().hex[:6],
            company=self.company,
            root_type=AccountRootType.EXPENSE,
        )
        self.company.default_payable_account = payable
        self.company.default_expense_account = purchases
        self.company.save()

    def purchase_invoice_body(self, values):
        return {
            "supplier": values["supplier"],
            "company": values["company"],
            "posting_date": values["posting_date"],
            "bill_no": values["bill_no"],
            "bill_date": values["bill_date"],
            "items": [
                {
                    "item": line["item"],
                    "qty": line["qty"],
                    "rate": line["rate"],
                    "warehouse": line["warehouse"],
                }
                for line in values["items"]
            ],
        }

    def test_saving_the_invoice_copies_the_photo_onto_it(self):
        with mock.patch(VISION, return_value=reading()):
            proposal = self.post_bill().data["proposal"]

        resp = self.client.post(
            "/api/buying/purchase-invoices/",
            self.purchase_invoice_body(proposal["values"]),
            format="json",
            headers={"X-Assistant-Proposal": proposal["proposal_id"]},
        )
        self.assertEqual(resp.status_code, 201, resp.data)

        invoice = PurchaseInvoice.objects.get(name=resp.data["name"])
        proof = Attachment.objects.get(
            model_label="buying.PurchaseInvoice", object_id=str(invoice.pk)
        )
        self.assertEqual(proof.sha256, hashlib.sha256(JPEG).hexdigest())
        self.assertEqual(proof.original_filename, "bill.jpg")
        with proof.file.open("rb") as fh:
            self.assertEqual(fh.read(), JPEG)

        row = AssistantProposal.objects.get(pk=proposal["proposal_id"])
        self.assertEqual(row.status, "saved")

    def test_saving_a_multi_page_bill_copies_every_page_onto_it(self):
        with mock.patch(VISION, return_value=reading()):
            resp = self.post_files(
                ("bill-1.jpg", JPEG, "image/jpeg"),
                ("bill-2.pdf", PDF, "application/pdf"),
            )
        proposal = resp.data["proposal"]

        saved = self.client.post(
            "/api/buying/purchase-invoices/",
            self.purchase_invoice_body(proposal["values"]),
            format="json",
            headers={"X-Assistant-Proposal": proposal["proposal_id"]},
        )
        self.assertEqual(saved.status_code, 201, saved.data)

        invoice = PurchaseInvoice.objects.get(name=saved.data["name"])
        proofs = Attachment.objects.filter(
            model_label="buying.PurchaseInvoice", object_id=str(invoice.pk)
        )
        self.assertEqual(
            sorted(p.content_type for p in proofs),
            ["application/pdf", "image/jpeg"],
        )

    def test_failed_proof_copy_rolls_the_invoice_save_back(self):
        with mock.patch(VISION, return_value=reading()):
            proposal = self.post_bill().data["proposal"]
        before = PurchaseInvoice.objects.count()

        with mock.patch(
            "assistant.bill_photo.carry_proof", side_effect=RuntimeError("disk full")
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/buying/purchase-invoices/",
                    self.purchase_invoice_body(proposal["values"]),
                    format="json",
                    headers={"X-Assistant-Proposal": proposal["proposal_id"]},
                )
        self.assertEqual(PurchaseInvoice.objects.count(), before)

    def test_staged_photo_is_not_reachable_through_the_attachments_api(self):
        with mock.patch(VISION, return_value=reading()):
            resp = self.post_bill()
        msg_id = resp.data["message_id"]

        listed = self.client.get(
            "/api/core/attachments/",
            {"model": "assistant.AssistantMessage", "object_id": str(msg_id)},
        )
        self.assertEqual(listed.status_code, 400)

        upload = SimpleUploadedFile("extra.jpg", JPEG + b"x", content_type="image/jpeg")
        created = self.client.post(
            "/api/core/attachments/",
            {
                "model": "assistant.AssistantMessage",
                "object_id": str(msg_id),
                "file": upload,
            },
            format="multipart",
        )
        self.assertEqual(created.status_code, 400)

    def test_deleting_the_conversation_takes_the_staged_photo_with_it(self):
        with mock.patch(VISION, return_value=reading()):
            resp = self.post_bill()
        conv_id = resp.data["conversation_id"]
        self.assertEqual(
            Attachment.objects.filter(model_label="assistant.AssistantMessage").count(),
            1,
        )

        deleted = self.client.delete(f"/api/assistant/conversations/{conv_id}/")
        self.assertIn(deleted.status_code, (200, 204))
        self.assertFalse(AssistantConversation.objects.filter(pk=conv_id).exists())
        self.assertEqual(
            Attachment.objects.filter(model_label="assistant.AssistantMessage").count(),
            0,
        )


class BillReplyParsingTests(APITestCase):
    def test_fenced_json_is_read(self):
        bill = parse_bill("Here it is:\n```json\n" + reading() + "\n```")
        self.assertEqual(bill["supplier_name"], "Gupta Suppliers")
        self.assertEqual(str(bill["items"][0]["qty"]), "10.000")

    def test_strict_fields_reject_loose_values(self):
        for text in [
            reading(readable="true"),
            reading(confidence="high"),
            reading(items=[{"name": "Haldi", "qty": -1}]),
            reading(items=[{"name": "Haldi", "qty": "NaN"}]),
            reading(items=[{"name": "", "qty": 1}]),
            "[1, 2, 3]",
        ]:
            with self.subTest(text=text[:50]), self.assertRaises(BillUnreadable):
                parse_bill(text)

    def test_control_characters_and_long_names_are_cleaned(self):
        bill = parse_bill(reading(supplier_name="Gupta\x00 Suppliers\n" + "x" * 400))
        self.assertNotIn("\x00", bill["supplier_name"])
        self.assertLessEqual(len(bill["supplier_name"]), 200)


class VisionAdapterTests(APITestCase):
    def fake_response(self, body):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body).encode(
            "utf-8"
        )
        return response

    def test_adapter_returns_the_models_text(self):
        body = {"choices": [{"message": {"content": reading()}}]}
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"}), mock.patch(
            "assistant.bill_photo.urllib.request.urlopen",
            return_value=self.fake_response(body),
        ):
            text = call_vision_model(JPEG, "image/jpeg")
        self.assertEqual(text, reading())

    def test_adapter_treats_an_empty_answer_as_unavailable(self):
        body = {"choices": []}
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"}), mock.patch(
            "assistant.bill_photo.urllib.request.urlopen",
            return_value=self.fake_response(body),
        ):
            with self.assertRaises(BillReaderUnavailable):
                call_vision_model(JPEG, "image/jpeg")

    def test_pdf_and_extra_pages_are_sent_as_file_and_image_parts(self):
        body = {"choices": [{"message": {"content": reading()}}]}
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"}), mock.patch(
            "assistant.bill_photo.urllib.request.urlopen",
            return_value=self.fake_response(body),
        ) as urlopen:
            call_vision_model(PDF, "application/pdf", [(JPEG, "image/jpeg")])
        request = urlopen.call_args.args[0]
        content = json.loads(request.data.decode("utf-8"))["messages"][0]["content"]
        self.assertEqual(
            [part["type"] for part in content], ["text", "file", "image_url"]
        )
        self.assertTrue(
            content[1]["file"]["file_data"].startswith("data:application/pdf;base64,")
        )
        self.assertTrue(
            content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )
