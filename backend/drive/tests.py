"""Organisation drive API: uploads, folders, private files and sharing."""

import io
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from core.models import Company, UserCompany
from drive.models import DriveFile, Folder
from users.models import User

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20


def make_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


class DriveBase(APITestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix="erp-drive-test-")
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    def setUp(self):
        self.company = Company.objects.create(name="Drive Co", abbr="DRV")
        # A second active company stops the "single company" signal from
        # auto-adding every new user (the outsider must stay a non-member).
        Company.objects.create(name="Other Co", abbr="OTH")
        self.owner = User.objects.create_user(
            username="owner", email="owner@t.com", password="Pass123!"
        )
        self.member = User.objects.create_user(
            username="member", email="member@t.com", password="Pass123!"
        )
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@t.com", password="Pass123!"
        )
        UserCompany.objects.get_or_create(
            user=self.owner, company=self.company, defaults={"is_default": True}
        )
        UserCompany.objects.get_or_create(user=self.member, company=self.company)

    def auth(self, user):
        self.client.force_authenticate(user)

    def upload(self, name="note.pdf", data=PDF, visibility="company", **extra):
        return self.client.post(
            "/api/drive/files/",
            {"file": SimpleUploadedFile(name, data), "visibility": visibility, **extra},
            format="multipart",
        )


class DriveUploadTests(DriveBase):
    def test_upload_list_download(self):
        self.auth(self.owner)
        res = self.upload()
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["original_filename"], "note.pdf")
        self.assertEqual(body["content_type"], "application/pdf")
        self.assertEqual(body["size"], len(PDF))
        self.assertTrue(body["can_edit"])
        self.assertTrue(body["can_delete"])
        stored = DriveFile.objects.get()
        self.assertRegex(stored.file.name, rf"^drive/{self.company.pk}/[0-9a-f]{{32}}\.pdf$")

        self.auth(self.member)
        listed = self.client.get("/api/drive/files/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["id"] for row in listed.json()["results"]], [body["id"]])

        downloaded = self.client.get(f"/api/drive/files/{body['id']}/download/")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), PDF)
        self.assertIn("attachment", downloaded["Content-Disposition"])

    def test_docx_upload_is_sniffed(self):
        self.auth(self.owner)
        res = self.upload("letter.docx", make_docx())
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(
            res.json()["content_type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    def test_invalid_type_and_content_are_refused(self):
        self.auth(self.owner)
        bad_ext = self.upload("script.exe", b"MZ")
        self.assertEqual(bad_ext.status_code, 400)
        self.assertEqual(bad_ext.json()["code"], "drive_type_not_allowed")

        mismatched = self.upload("fake.pdf", PNG)
        self.assertEqual(mismatched.status_code, 400)
        self.assertEqual(mismatched.json()["code"], "drive_content_mismatch")

    def test_non_member_is_denied(self):
        self.auth(self.outsider)
        res = self.upload()
        self.assertEqual(res.status_code, 403)


class DriveSharingTests(DriveBase):
    def test_private_file_is_hidden_until_shared(self):
        self.auth(self.owner)
        res = self.upload(visibility="private")
        self.assertEqual(res.status_code, 201, res.content)
        file_id = res.json()["id"]

        self.auth(self.member)
        self.assertEqual(self.client.get("/api/drive/files/").json()["count"], 0)
        self.assertEqual(
            self.client.get(f"/api/drive/files/{file_id}/").status_code, 404
        )

        self.auth(self.owner)
        shared = self.client.post(
            f"/api/drive/files/{file_id}/share/",
            {"user_ids": [self.member.pk]},
            format="json",
        )
        self.assertEqual(shared.status_code, 200, shared.content)
        self.assertEqual(shared.json()["shared_with"][0]["user"], self.member.pk)

        self.auth(self.member)
        listed = self.client.get("/api/drive/files/").json()
        self.assertEqual(listed["count"], 1)
        self.assertTrue(listed["results"][0]["shared_with_me"])
        self.assertFalse(listed["results"][0]["can_delete"])

    def test_share_rejects_outsider(self):
        self.auth(self.owner)
        file_id = self.upload(visibility="private").json()["id"]
        res = self.client.post(
            f"/api/drive/files/{file_id}/share/",
            {"user_ids": [self.outsider.pk]},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_only_uploader_can_delete(self):
        self.auth(self.owner)
        file_id = self.upload().json()["id"]
        self.auth(self.member)
        self.assertEqual(
            self.client.delete(f"/api/drive/files/{file_id}/").status_code, 403
        )
        self.auth(self.owner)
        self.assertEqual(
            self.client.delete(f"/api/drive/files/{file_id}/").status_code, 204
        )
        self.assertEqual(DriveFile.objects.count(), 0)

    def test_write_share_can_rename_but_not_change_visibility(self):
        self.auth(self.owner)
        file_id = self.upload(visibility="private").json()["id"]
        shared = self.client.post(
            f"/api/drive/files/{file_id}/share/",
            {"user_ids": [self.member.pk], "can_write": True},
            format="json",
        )
        self.assertEqual(shared.status_code, 200, shared.content)

        self.auth(self.member)
        renamed = self.client.patch(
            f"/api/drive/files/{file_id}/", {"name": "renamed.pdf"}, format="json"
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        exposed = self.client.patch(
            f"/api/drive/files/{file_id}/", {"visibility": "company"}, format="json"
        )
        self.assertEqual(exposed.status_code, 403, exposed.content)


class DriveFolderTests(DriveBase):
    def test_folder_crud_and_company_documents_folder(self):
        self.auth(self.owner)
        created = self.client.post(
            "/api/drive/folders/", {"name": "Policies"}, format="json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        folder_id = created.json()["id"]

        duplicate = self.client.post(
            "/api/drive/folders/", {"name": "Policies"}, format="json"
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.content)

        child = self.client.post(
            "/api/drive/folders/",
            {"name": "HR", "parent": folder_id},
            format="json",
        )
        self.assertEqual(child.status_code, 201, child.content)
        self.assertEqual(child.json()["path"][0]["name"], "Policies")

        root = self.client.get("/api/drive/folders/", {"parent": "root"})
        self.assertEqual(root.status_code, 200)
        names = [row["name"] for row in root.json()]
        self.assertIn("Policies", names)
        self.assertNotIn("HR", names)

        docs = self.client.get("/api/drive/folders/company-documents/")
        self.assertEqual(docs.status_code, 200, docs.content)
        self.assertTrue(docs.json()["folder"]["is_system"])
        self.assertEqual(docs.json()["folder"]["name"], "Company documents")

    def test_system_folder_and_folder_with_files_cannot_be_deleted(self):
        self.auth(self.owner)
        docs = self.client.get("/api/drive/folders/company-documents/").json()["folder"]
        self.assertEqual(
            self.client.delete(f"/api/drive/folders/{docs['id']}/").status_code, 403
        )

        folder = Folder.objects.create(
            company=self.company,
            name="Projects",
            created_by=self.owner,
        )
        self.client.post(
            "/api/drive/files/",
            {
                "file": SimpleUploadedFile("x.pdf", PDF),
                "folder": folder.pk,
            },
            format="multipart",
        )
        self.assertEqual(
            self.client.delete(f"/api/drive/folders/{folder.pk}/").status_code, 400
        )

    def test_drive_members_lists_company_members_only(self):
        self.auth(self.owner)
        res = self.client.get("/api/drive/members/")
        self.assertEqual(res.status_code, 200, res.content)
        ids = {row["id"] for row in res.json()["results"]}
        self.assertEqual(ids, {self.owner.pk, self.member.pk})

    def test_folder_parent_must_be_in_active_company(self):
        other = Company.objects.get(abbr="OTH")
        other_folder = Folder.objects.create(company=other, name="Other root")
        self.auth(self.owner)
        res = self.client.post(
            "/api/drive/folders/",
            {"name": "Cross-company", "parent": other_folder.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
