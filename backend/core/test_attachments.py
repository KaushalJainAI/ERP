"""Attachments (proof documents): upload/list/download, validation, RBAC, immutability.

The parent used for the lifecycle rules is ``core.DummySubmittableDoc`` (a real
``SubmittableDocument``); master behaviour is covered with ``selling.Customer``.
"""

import hashlib
import io
import shutil
import tempfile
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import override_settings
from rest_framework.test import APITestCase

from core import attachments as att_rules
from core.models import Attachment, AuditLog, Company, DocStatus, DummySubmittableDoc
from selling.models import Customer, CustomerGroup
from users.models import Role, RolePermission, User, UserRole

DOC = "core.DummySubmittableDoc"
URL = "/api/core/attachments/"

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
CSV = b"date,amount\n2026-01-01,100\n"


def make_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


def upload(name: str, data: bytes, content_type: str = "application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class AttachmentBase(APITestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix="erp-attach-test-")
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    def make_user(self, name, **flags):
        user = User.objects.create_user(
            username=name, email=f"{name}@t.com", password="Pass123!"
        )
        role = Role.objects.create(name=f"role_{name}")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(role=role, model_label=DOC, **flags)
        return user

    def setUp(self):
        self.clerk = self.make_user("clerk", can_read=True, can_write=True)
        self.manager = self.make_user(
            "mgr", can_read=True, can_write=True, can_delete=True
        )
        self.viewer = self.make_user("viewer", can_read=True)
        self.outsider = User.objects.create_user(
            username="outsider", email="o@t.com", password="Pass123!"
        )
        self.doc = DummySubmittableDoc.objects.create(name="DUM-1", title="Bill")

    def post(self, user, f, doc=None, **extra):
        self.client.force_authenticate(user)
        doc = doc or self.doc
        data = {"model": DOC, "object_id": str(doc.pk), "file": f, **extra}
        return self.client.post(URL, data, format="multipart")

    def lst(self, user, doc=None):
        self.client.force_authenticate(user)
        doc = doc or self.doc
        return self.client.get(URL, {"model": DOC, "object_id": str(doc.pk)})


class UploadListDownloadTests(AttachmentBase):
    def test_upload_list_download_roundtrip(self):
        res = self.post(
            self.clerk, upload("bill.pdf", PDF), description="Supplier bill"
        )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["original_filename"], "bill.pdf")
        self.assertEqual(body["content_type"], "application/pdf")
        self.assertEqual(body["size"], len(PDF))
        self.assertEqual(body["sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(body["uploaded_by"], self.clerk.id)
        self.assertTrue(body["can_delete"])

        att = Attachment.objects.get()
        self.assertRegex(
            att.file.name, r"^attachments/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.pdf$"
        )
        self.assertEqual(att.description, "Supplier bill")

        listed = self.lst(self.viewer)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([r["id"] for r in listed.json()["results"]], [att.id])
        self.assertFalse(listed.json()["can_upload"])
        self.assertFalse(listed.json()["results"][0]["can_delete"])

        dl = self.client.get(f"{URL}{att.id}/download/")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(b"".join(dl.streaming_content), PDF)
        self.assertEqual(dl["Content-Type"], "application/pdf")
        self.assertIn("attachment", dl["Content-Disposition"])
        self.assertEqual(dl["X-Content-Type-Options"], "nosniff")

        inline = self.client.get(f"{URL}{att.id}/download/", {"inline": "1"})
        self.assertTrue(inline["Content-Disposition"].startswith("inline"))
        list(inline.streaming_content)

    def test_all_allowed_types_are_accepted(self):
        files = {
            "a.pdf": PDF,
            "b.jpg": JPEG,
            "c.jpeg": JPEG + b"1",
            "d.png": PNG,
            "e.webp": WEBP,
            "f.heic": HEIC,
            "g.xlsx": make_xlsx(),
            "h.csv": CSV,
        }
        for name, data in files.items():
            res = self.post(self.clerk, upload(name, data))
            self.assertEqual(res.status_code, 201, f"{name}: {res.content}")

    def test_content_type_is_sniffed_not_client_sent(self):
        res = self.post(self.clerk, upload("scan.png", PNG, content_type="text/html"))
        self.assertEqual(res.json()["content_type"], "image/png")

    def test_inline_is_refused_for_non_previewable_types(self):
        self.post(self.clerk, upload("x.csv", CSV))
        att = Attachment.objects.get()
        resp = self.client.get(f"{URL}{att.id}/download/", {"inline": "1"})
        self.assertTrue(resp["Content-Disposition"].startswith("attachment"))
        list(resp.streaming_content)

    def test_endpoint_resolves_to_model_for_the_generic_form(self):
        self.assertEqual(
            att_rules.resolve_parent_label(endpoint="/selling/sales-invoices/"),
            "selling.SalesInvoice",
        )
        self.assertEqual(
            att_rules.resolve_parent_label(endpoint="stock/items/"), "stock.Item"
        )
        with self.assertRaises(att_rules.AttachmentError):
            att_rules.resolve_parent_label(endpoint="/accounts/gl-entries/")

    def test_masters_can_carry_attachments_via_endpoint(self):
        company = Company.objects.create(name="A Co", abbr="AC")
        grp = CustomerGroup.objects.create(name="G", is_group=True)
        cust = Customer.objects.create(name="Cust", company=company, customer_group=grp)
        role = Role.objects.create(name="cust_role")
        user = User.objects.create_user(username="cu", email="cu@t.com", password="x")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role, model_label="selling.Customer", can_read=True, can_write=True
        )
        self.client.force_authenticate(user)
        res = self.client.post(
            URL,
            {
                "endpoint": "/selling/customers/",
                "object_id": str(cust.pk),
                "file": upload("kyc.jpg", JPEG),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["model_label"], "selling.Customer")
        listed = self.client.get(
            URL, {"endpoint": "/selling/customers/", "object_id": str(cust.pk)}
        )
        self.assertEqual(len(listed.json()["results"]), 1)

    def test_unknown_or_disallowed_parent_is_refused(self):
        self.client.force_authenticate(self.clerk)
        for model in (
            "accounts.GLEntry",
            "core.AuditLog",
            "nope.Nothing",
            "core.Attachment",
        ):
            res = self.client.get(URL, {"model": model, "object_id": "1"})
            self.assertEqual(res.status_code, 400, model)
            self.assertEqual(res.json()["code"], "attachment_parent_invalid")

    def test_missing_parent_is_404(self):
        self.client.force_authenticate(self.clerk)
        res = self.client.get(URL, {"model": DOC, "object_id": "99999"})
        self.assertEqual(res.status_code, 404)

    def test_same_file_twice_on_one_record_is_refused(self):
        self.assertEqual(self.post(self.clerk, upload("a.pdf", PDF)).status_code, 201)
        res = self.post(self.clerk, upload("renamed.pdf", PDF))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "attachment_duplicate")
        other = DummySubmittableDoc.objects.create(name="DUM-2", title="Other")
        self.assertEqual(
            self.post(self.clerk, upload("a.pdf", PDF), doc=other).status_code, 201
        )

    def test_path_in_filename_is_stripped(self):
        res = self.post(self.clerk, upload("..\\..\\etc/passwd.pdf", PDF))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["original_filename"], "passwd.pdf")
        self.assertNotIn("..", Attachment.objects.get().file.name)


class ValidationTests(AttachmentBase):
    def assert_rejected(self, f, code, status_code=400):
        res = self.post(self.clerk, f)
        self.assertEqual(res.status_code, status_code, res.content)
        self.assertEqual(res.json()["code"], code)
        self.assertEqual(Attachment.objects.count(), 0)
        return res

    def test_disallowed_extension(self):
        self.assert_rejected(
            upload("run.exe", b"MZ\x90\x00"), "attachment_type_not_allowed"
        )
        self.assert_rejected(
            upload("page.html", b"<html></html>"), "attachment_type_not_allowed"
        )
        self.assert_rejected(upload("noext", PDF), "attachment_type_not_allowed")

    def test_spoofed_extension_executable_as_pdf(self):
        self.assert_rejected(
            upload("invoice.pdf", b"MZ\x90\x00\x03\x00\x00\x00"),
            "attachment_content_mismatch",
        )

    def test_spoofed_extension_real_type_under_wrong_extension(self):
        self.assert_rejected(upload("photo.jpg", PNG), "attachment_content_mismatch")
        self.assert_rejected(upload("doc.pdf", PNG), "attachment_content_mismatch")

    def test_spoofed_client_content_type_does_not_help(self):
        self.assert_rejected(
            upload("evil.png", b"<script>alert(1)</script>", content_type="image/png"),
            "attachment_content_mismatch",
        )

    def test_plain_zip_is_not_xlsx(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "hi")
        self.assert_rejected(
            upload("sheet.xlsx", buf.getvalue()), "attachment_content_mismatch"
        )

    def test_binary_is_not_csv(self):
        self.assert_rejected(
            upload("data.csv", b"a,b\x00\x01\x02\n"), "attachment_content_mismatch"
        )

    def test_empty_file(self):
        self.assert_rejected(upload("empty.pdf", b""), "attachment_empty")

    def test_missing_file(self):
        self.client.force_authenticate(self.clerk)
        res = self.client.post(
            URL, {"model": DOC, "object_id": str(self.doc.pk)}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "attachment_missing")

    @override_settings(ATTACHMENT_MAX_BYTES=1024)
    def test_too_large(self):
        self.assert_rejected(
            upload("big.pdf", PDF + b"0" * 2000), "attachment_too_large", 413
        )

    @override_settings(ATTACHMENT_MAX_BYTES=1024)
    def test_exactly_at_limit_is_allowed(self):
        data = PDF + b"0" * (1024 - len(PDF))
        self.assertEqual(self.post(self.clerk, upload("ok.pdf", data)).status_code, 201)

    def test_default_limit_is_10_mb(self):
        self.assertEqual(att_rules.max_bytes(), 10 * 1024 * 1024)


class PermissionTests(AttachmentBase):
    def test_user_without_read_cannot_list_or_download(self):
        self.post(self.clerk, upload("a.pdf", PDF))
        att = Attachment.objects.get()
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.lst(self.outsider).status_code, 403)
        self.assertEqual(self.client.get(f"{URL}{att.id}/download/").status_code, 403)

    def test_attachment_access_follows_the_parent_model_not_other_models(self):
        other = User.objects.create_user(
            username="oth", email="oth@t.com", password="x"
        )
        role = Role.objects.create(name="cust_only")
        UserRole.objects.create(user=other, role=role)
        RolePermission.objects.create(
            role=role, model_label="selling.Customer", can_read=True
        )
        self.post(self.clerk, upload("a.pdf", PDF))
        self.assertEqual(self.lst(other).status_code, 403)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(URL, {"model": DOC, "object_id": "1"}).status_code, 401
        )

    def test_read_only_user_cannot_upload(self):
        res = self.post(self.viewer, upload("a.pdf", PDF))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_submit_permission_alone_allows_upload(self):
        user = self.make_user("submitter", can_read=True, can_submit=True)
        self.assertEqual(self.post(user, upload("a.pdf", PDF)).status_code, 201)

    def test_no_media_url_route(self):
        self.post(self.clerk, upload("a.pdf", PDF))
        att = Attachment.objects.get()
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get(f"/media/{att.file.name}").status_code, 404)


class DeleteRuleTests(AttachmentBase):
    def attach(self, user=None, name="a.pdf", data=PDF):
        res = self.post(user or self.clerk, upload(name, data))
        self.assertEqual(res.status_code, 201, res.content)
        return Attachment.objects.get(pk=res.json()["id"])

    def delete(self, user, att):
        self.client.force_authenticate(user)
        return self.client.delete(f"{URL}{att.id}/")

    def test_uploader_can_delete_on_draft(self):
        att = self.attach()
        path = att.file.path
        with self.captureOnCommitCallbacks(execute=True):
            res = self.delete(self.clerk, att)
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Attachment.objects.filter(pk=att.pk).exists())
        import os

        self.assertFalse(os.path.exists(path))

    def test_manager_can_delete_someone_elses_on_draft(self):
        att = self.attach()
        self.assertEqual(self.delete(self.manager, att).status_code, 204)

    def test_other_writer_cannot_delete_on_draft(self):
        att = self.attach()
        peer = self.make_user("peer", can_read=True, can_write=True)
        res = self.delete(peer, att)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "attachment_delete_forbidden")
        self.assertTrue(Attachment.objects.filter(pk=att.pk).exists())

    def test_reader_without_write_cannot_delete_own_after_losing_rights(self):
        att = self.attach()
        RolePermission.objects.filter(role__name="role_clerk").update(can_write=False)
        self.assertEqual(self.delete(self.clerk, att).status_code, 403)

    def test_superuser_can_delete_on_draft(self):
        att = self.attach()
        root = User.objects.create_superuser(
            username="root", email="r@t.com", password="x"
        )
        self.assertEqual(self.delete(root, att).status_code, 204)

    def _lock(self, docstatus):
        DummySubmittableDoc.objects.filter(pk=self.doc.pk).update(docstatus=docstatus)

    def test_cannot_delete_after_submit_even_manager_or_superuser(self):
        att = self.attach()
        self._lock(DocStatus.SUBMITTED)
        root = User.objects.create_superuser(
            username="root", email="r@t.com", password="x"
        )
        for user in (self.clerk, self.manager, root):
            res = self.delete(user, att)
            self.assertEqual(res.status_code, 409, user.username)
            self.assertEqual(res.json()["code"], "attachment_locked")
        self.assertTrue(Attachment.objects.filter(pk=att.pk).exists())
        self.assertFalse(self.lst(self.manager).json()["results"][0]["can_delete"])

    def test_cannot_delete_after_cancel(self):
        att = self.attach()
        self._lock(DocStatus.CANCELLED)
        self.assertEqual(self.delete(self.manager, att).status_code, 409)

    def test_can_still_add_proof_after_submit_and_cancel(self):
        self._lock(DocStatus.SUBMITTED)
        self.assertEqual(self.post(self.clerk, upload("a.pdf", PDF)).status_code, 201)
        self._lock(DocStatus.CANCELLED)
        self.assertEqual(
            self.post(self.clerk, upload("b.pdf", PDF + b"x")).status_code, 201
        )

    def test_model_layer_refuses_delete_on_submitted_parent(self):
        att = self.attach()
        self._lock(DocStatus.SUBMITTED)
        with self.assertRaises(ValidationError):
            att.delete()
        with self.assertRaises(ValidationError):
            Attachment.objects.filter(pk=att.pk).delete()
        self.assertTrue(Attachment.objects.filter(pk=att.pk).exists())

    def test_deleting_a_draft_takes_its_attachments_with_it(self):
        import os

        att = self.attach()
        path = att.file.path
        with self.captureOnCommitCallbacks(execute=True):
            self.doc.delete()
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(os.path.exists(path))
        self.assertTrue(
            AuditLog.objects.filter(
                action="attachment_delete", model_label=DOC
            ).exists()
        )


class ImmutabilityTests(AttachmentBase):
    def setUp(self):
        super().setUp()
        self.post(self.clerk, upload("a.pdf", PDF))
        self.att = Attachment.objects.get()

    def test_save_on_existing_row_is_refused(self):
        self.att.description = "tampered"
        with self.assertRaises(ValidationError):
            self.att.save()

    def test_queryset_update_is_refused(self):
        with self.assertRaises(ValidationError):
            Attachment.objects.filter(pk=self.att.pk).update(description="x")

    def test_database_trigger_blocks_raw_update(self):
        with connection.cursor() as cur:
            with self.assertRaises(Exception) as ctx:
                cur.execute("UPDATE core_attachment SET description = 'x'")
        self.assertIn("immutable", str(ctx.exception))

    def test_api_has_no_update_route(self):
        self.client.force_authenticate(self.manager)
        for method in (self.client.put, self.client.patch):
            res = method(f"{URL}{self.att.id}/", {"description": "x"}, format="json")
            self.assertEqual(res.status_code, 405)

    def test_db_constraints(self):
        base = dict(
            model_label=DOC,
            object_id="1",
            original_filename="f.pdf",
            content_type="application/pdf",
            size=10,
            sha256="a" * 64,
            uploaded_by=self.clerk,
        )
        with self.assertRaises(IntegrityError):
            Attachment.objects.create(**{**base, "size": 0, "file": "x/a.pdf"})


class AuditTests(AttachmentBase):
    def test_upload_and_delete_are_audited_on_the_parent(self):
        res = self.post(self.clerk, upload("a.pdf", PDF))
        att_id = res.json()["id"]
        add = AuditLog.objects.get(action="attachment_add")
        self.assertEqual(add.user, self.clerk)
        self.assertEqual(add.model_label, DOC)
        self.assertEqual(add.object_id, self.doc.name)
        self.assertEqual(add.changes["attachment_id"], att_id)
        self.assertEqual(add.changes["sha256"], hashlib.sha256(PDF).hexdigest())

        self.client.force_authenticate(self.clerk)
        self.assertEqual(self.client.delete(f"{URL}{att_id}/").status_code, 204)
        gone = AuditLog.objects.get(action="attachment_delete")
        self.assertEqual(gone.user, self.clerk)
        self.assertEqual(gone.changes["attachment_id"], att_id)

    def test_refused_upload_is_not_audited(self):
        self.post(self.clerk, upload("a.exe", b"MZ"))
        self.assertFalse(AuditLog.objects.filter(action="attachment_add").exists())

    def test_refused_delete_is_not_audited(self):
        self.post(self.clerk, upload("a.pdf", PDF))
        DummySubmittableDoc.objects.filter(pk=self.doc.pk).update(docstatus=1)
        att = Attachment.objects.get()
        self.client.force_authenticate(self.manager)
        self.client.delete(f"{URL}{att.id}/")
        self.assertFalse(AuditLog.objects.filter(action="attachment_delete").exists())


class IntegrityCheckTests(AttachmentBase):
    def test_clean_then_missing_file_then_tampered_file(self):
        self.post(self.clerk, upload("a.pdf", PDF))
        att = Attachment.objects.get()
        self.assertEqual(att_rules.check_integrity(), [])

        with open(att.file.path, "ab") as fh:
            fh.write(b"tamper")
        errors = att_rules.check_integrity()
        self.assertEqual(len(errors), 1)
        self.assertIn("SHA-256", errors[0])

        import os

        os.remove(att.file.path)
        self.assertIn("missing", att_rules.check_integrity()[0])
