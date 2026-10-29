import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from buying.models import Supplier, SupplierGroup
from core.models import Company, UserCompany
from legacy.models import TallyItemLine, TallyRecord
from selling.models import Customer, CustomerGroup
from users.models import Role, User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def company():
    return Company.objects.create(name="Legacy Co", abbr="LGC", default_currency="INR")


@pytest.fixture
def voucher(company):
    return TallyRecord.objects.create(
        company=company,
        kind="voucher",
        company_year="010010",
        fiscal_year="2024-25",
        guid="g-1",
        voucher_type="Sales Gst",
        base_voucher_type="Sales",
        voucher_no="S/1",
        posting_date=datetime.date(2024, 5, 1),
        party="Ram Kirana",
        amount=Decimal("-1050.00"),
        erp_name="TLY-2425-000001",
        erp_links=[{"doctype": "accounts.JournalEntry", "name": "TLY-2425-000001"}],
        raw="<VOUCHER>...</VOUCHER>",
    )


def _line(company, record, **kw):
    vals = dict(
        company=company,
        record=record,
        fiscal_year="2024-25",
        posting_date=datetime.date(2024, 5, 1),
        voucher_type="Sales",
        item_name="Haldi",
        is_inward=False,
        qty=Decimal("10.000"),
        rate=Decimal("100.000000"),
        amount=Decimal("1000.00"),
    )
    vals.update(kw)
    return TallyItemLine(**vals)


def test_record_is_append_only(voucher):
    voucher.narration = "changed"
    with pytest.raises(ValidationError):
        voucher.save()
    with pytest.raises(ValidationError):
        voucher.delete()


def test_record_unique_per_year_and_kind(company, voucher):
    dup = dict(
        company=company,
        kind="voucher",
        company_year="010010",
        fiscal_year="2024-25",
        guid="g-1",
        raw="",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        TallyRecord.objects.create(**dup)
    # the same guid in the next year's folder is a different record
    TallyRecord.objects.create(**{**dup, "company_year": "200000"})


def test_item_line_rejects_negative_values(company, voucher):
    for bad in (
        {"qty": Decimal("-1")},
        {"amount": Decimal("-1")},
        {"rate": Decimal("-1")},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            _line(company, voucher, **bad).save()


def test_item_line_has_at_most_one_party(company, voucher):
    cg = CustomerGroup.objects.create(name="All CG")
    sg = SupplierGroup.objects.create(name="All SG")
    c = Customer.objects.create(name="Ram", company=company, customer_group=cg)
    s = Supplier.objects.create(name="Ram", company=company, supplier_group=sg)
    with pytest.raises(IntegrityError), transaction.atomic():
        _line(company, voucher, customer=c, supplier=s).save()
    _line(company, voucher, customer=c).save()


def _user_with_role(role_name, company):
    call_command("seed_defaults", stdout=StringIO())
    user = User.objects.create_user(username=f"u-{role_name}", password="x")
    UserRole.objects.create(user=user, role=Role.objects.get(name=role_name))
    UserCompany.objects.create(user=user, company=company, is_default=True)
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_api_lists_without_raw_and_details_with_raw(voucher, company):
    client = _user_with_role("Accounts Manager", company)
    res = client.get("/api/legacy/tally-records/", {"voucher_no": "S/1"})
    assert res.status_code == 200, res.content
    row = res.data["results"][0]
    assert "raw" not in row
    assert row["erp_name"] == "TLY-2425-000001"
    res = client.get(f"/api/legacy/tally-records/{voucher.pk}/")
    assert res.data["raw"] == "<VOUCHER>...</VOUCHER>"


def test_api_is_read_only(voucher, company):
    client = _user_with_role("System Manager", company)
    res = client.post("/api/legacy/tally-records/", {"kind": "voucher"}, format="json")
    assert res.status_code in (403, 405)
    res = client.delete(f"/api/legacy/tally-records/{voucher.pk}/")
    assert res.status_code in (403, 405)
    assert TallyRecord.objects.filter(pk=voucher.pk).exists()


def test_sales_manager_reads_item_lines_not_the_archive(company, voucher):
    _line(company, voucher).save()
    client = _user_with_role("Sales Manager", company)
    res = client.get("/api/legacy/tally-item-lines/", {"fiscal_year": "2024-25"})
    assert res.status_code == 200, res.content
    assert res.data["results"][0]["item_name"] == "Haldi"
    assert client.get("/api/legacy/tally-records/").status_code == 403
