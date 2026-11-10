"""Bulk import of items, customers and suppliers (card U-11)."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from buying.models import Supplier
from core.models import Company, UserCompany
from gst.services.seeding import ensure_item_tax_templates, ensure_states
from selling.models import Customer
from stock.models import UOM, Item
from users.models import Role, RolePermission, User, UserRole

IMPORT = "/api/core/import/"


def _csv_upload(text, name="rows.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


def _xlsx_upload(rows, name="rows.xlsx"):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile(
        name,
        out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class ImportBase(TestCase):
    def setUp(self):
        ensure_states()
        ensure_item_tax_templates()
        UOM.objects.get_or_create(name="Nos")
        self.company = Company.objects.create(name="Imports Co", abbr="IMP")
        self.admin = User.objects.create_superuser(
            username="import_admin", email="import_admin@test.com", password="pw"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.headers = {"HTTP_X_COMPANY_ID": str(self.company.pk)}

    def preview(self, kind, upload):
        return self.client.post(
            f"{IMPORT}preview/",
            {"kind": kind, "file": upload},
            format="multipart",
            **self.headers,
        )

    def confirm(self, kind, upload):
        return self.client.post(
            f"{IMPORT}confirm/",
            {"kind": kind, "file": upload},
            format="multipart",
            **self.headers,
        )


class TemplateTests(ImportBase):
    def test_template_downloads_for_each_kind(self):
        expected = {
            "items": ["name", "unit", "gst_rate", "selling_price", "hsn"],
            "customers": ["name", "phone", "gstin", "state_code"],
            "suppliers": ["name", "phone", "gstin", "state_code"],
        }
        for kind, columns in expected.items():
            res = self.client.get(f"{IMPORT}template/?kind={kind}", **self.headers)
            self.assertEqual(res.status_code, 200, res.content)
            self.assertIn("spreadsheetml", res["Content-Type"])
            self.assertIn(f"{kind}-template.xlsx", res["Content-Disposition"])
            sheet = load_workbook(io.BytesIO(res.content)).active
            self.assertEqual([c.value for c in sheet[1]], columns)

    def test_unknown_kind_is_refused(self):
        res = self.client.get(f"{IMPORT}template/?kind=bills", **self.headers)
        self.assertEqual(res.status_code, 400)


class ImportPermissionTests(ImportBase):
    def setUp(self):
        super().setUp()
        self.clerk = User.objects.create_user(
            username="clerk", email="clerk@test.com", password="pw"
        )
        UserCompany.objects.update_or_create(
            user=self.clerk,
            company=self.company,
            defaults={"is_default": True, "is_active": True},
        )
        self.client.force_authenticate(self.clerk)

    def test_refused_without_create_rights(self):
        upload = _csv_upload("name\nSharma Traders\n")
        res = self.preview("customers", upload)
        self.assertEqual(res.status_code, 403)
        res = self.confirm("customers", _csv_upload("name\nSharma Traders\n"))
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Customer.objects.filter(name="Sharma Traders").exists())
        res = self.client.get(f"{IMPORT}template/?kind=items", **self.headers)
        self.assertEqual(res.status_code, 403)

    def test_allowed_with_create_rights(self):
        role = Role.objects.create(name="Sales clerk")
        RolePermission.objects.create(
            role=role, model_label="selling.Customer", can_create=True
        )
        UserRole.objects.create(user=self.clerk, role=role)
        res = self.confirm("customers", _csv_upload("name\nSharma Traders\n"))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Customer.objects.filter(name="Sharma Traders").exists())


class ImportConfirmTests(ImportBase):
    def test_a_bad_row_imports_nothing_and_names_the_row(self):
        text = (
            "name,phone,gstin,state_code\n"
            "Acme Corp,9800000001,,27\n"
            "Bolt Supply,9800000002,,99\n"
            "Delta Stores,9800000003,,07\n"
        )
        res = self.confirm("customers", _csv_upload(text))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.data["errors"], 1)
        self.assertEqual(res.data["ready"], 2)
        bad = [r for r in res.data["rows"] if r["status"] == "error"]
        self.assertEqual(bad[0]["row"], 3)
        self.assertIn("state_code", bad[0]["message"])
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 0)

    def test_a_clean_file_imports_all_rows(self):
        text = (
            "name,phone,gstin,state_code\n"
            "Acme Corp,9800000001,,27\n"
            "Bolt Supply,9800000002,,07\n"
        )
        res = self.confirm("customers", _csv_upload(text))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["created"], 2)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 2)
        self.assertEqual(Customer.objects.get(name="Bolt Supply").state_id, "07")

    def test_a_repeat_upload_creates_no_duplicates(self):
        text = "name,unit,gst_rate,selling_price,hsn\nHaldi,Nos,5,120,0910\nJeera,Nos,5,300,0909\n"
        first = self.confirm("items", _csv_upload(text))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.data["created"], 2)
        again = self.confirm("items", _csv_upload(text))
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.data["created"], 0)
        self.assertEqual(again.data["skipped"], 2)
        self.assertEqual(Item.objects.filter(name="Haldi").count(), 1)
        self.assertEqual(Item.objects.filter(name="Jeera").count(), 1)

    def test_existing_names_are_matched_case_insensitively(self):
        self.assertEqual(
            self.confirm("customers", _csv_upload("name\nAcme Corp\n")).status_code, 200
        )
        res = self.preview("customers", _csv_upload("name\nacme corp\n"))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["skipped"], 1)
        self.assertEqual(res.data["rows"][0]["status"], "skip")
        self.assertEqual(res.data["rows"][0]["message"], "Already exists")

    def test_a_duplicate_name_in_the_file_is_an_error(self):
        res = self.preview("suppliers", _csv_upload("name\nBolt Supply\nbolt supply\n"))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["rows"][0]["status"], "ready")
        self.assertEqual(res.data["rows"][1]["status"], "error")
        self.assertIn("row 2", res.data["rows"][1]["message"])

    def test_a_customer_row_with_only_a_name_imports(self):
        res = self.confirm("customers", _csv_upload("name\nSharma Traders\n"))
        self.assertEqual(res.status_code, 200, res.content)
        customer = Customer.objects.get(name="Sharma Traders")
        self.assertEqual(customer.company, self.company)
        self.assertIsNone(customer.state)

    def test_an_item_with_gst_18_gets_the_18_percent_template(self):
        res = self.confirm("items", _csv_upload("name,gst_rate\nTea,18\n"))
        self.assertEqual(res.status_code, 200, res.content)
        item = Item.objects.get(name="Tea")
        self.assertEqual(item.item_tax_template.name, "GST 18%")

    def test_an_item_with_a_gst_rate_not_on_the_list_is_an_error(self):
        res = self.preview("items", _csv_upload("name,gst_rate\nTea,19\n"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["rows"][0]["status"], "error")
        self.assertIn("gst_rate", res.data["rows"][0]["message"])

    def test_an_unknown_unit_is_an_error_that_names_the_unit(self):
        res = self.preview("items", _csv_upload("name,unit\nTea,Bushel\n"))
        self.assertEqual(res.data["rows"][0]["status"], "error")
        self.assertIn("Bushel", res.data["rows"][0]["message"])

    def test_preview_writes_nothing(self):
        res = self.preview(
            "suppliers", _csv_upload("name,state_code\nBolt Supply,27\n")
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["ready"], 1)
        self.assertFalse(Supplier.objects.filter(name="Bolt Supply").exists())

    def test_xlsx_upload_is_read(self):
        upload = _xlsx_upload(
            [
                ["name", "phone", "gstin", "state_code"],
                ["Acme Corp", 9800000001, None, 27],
            ]
        )
        res = self.confirm("customers", upload)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["created"], 1)
        customer = Customer.objects.get(name="Acme Corp")
        self.assertEqual(customer.phone, "9800000001")
        self.assertEqual(customer.state_id, "27")

    def test_unsupported_file_type_is_refused(self):
        res = self.preview("items", SimpleUploadedFile("list.pdf", b"%PDF-1.4"))
        self.assertEqual(res.status_code, 400)

    def test_a_file_without_a_name_column_is_refused(self):
        res = self.preview("items", _csv_upload("unit\nNos\n"))
        self.assertEqual(res.status_code, 400)

    def test_missing_file_is_refused(self):
        res = self.client.post(
            f"{IMPORT}preview/", {"kind": "items"}, format="multipart", **self.headers
        )
        self.assertEqual(res.status_code, 400)
