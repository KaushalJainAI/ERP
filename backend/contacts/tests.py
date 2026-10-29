import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from buying.models import Supplier, SupplierGroup
from contacts.models import Address
from core.models import Company
from selling.models import Customer, CustomerGroup
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def parties():
    company = Company.objects.create(name="Addr Co", abbr="ADR", default_currency="INR")
    cg = CustomerGroup.objects.create(name="All CG")
    sg = SupplierGroup.objects.create(name="All SG")
    c = Customer.objects.create(name="Ram", company=company, customer_group=cg)
    s = Supplier.objects.create(name="Shyam", company=company, supplier_group=sg)
    return c, s


@pytest.fixture
def client():
    user = User.objects.create_superuser(username="addr_admin", password="x")
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_db_requires_exactly_one_party(parties):
    c, s = parties
    with pytest.raises(IntegrityError), transaction.atomic():
        Address.objects.create(address_title="x", address_line1="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Address.objects.create(
            address_title="x", address_line1="x", customer=c, supplier=s
        )
    Address.objects.create(address_title="x", address_line1="x", supplier=s)


def test_db_one_primary_per_party(parties):
    c, _ = parties
    Address.objects.create(
        address_title="a", address_line1="a", customer=c, is_primary_address=True
    )
    Address.objects.create(address_title="b", address_line1="b", customer=c)
    with pytest.raises(IntegrityError), transaction.atomic():
        Address.objects.create(
            address_title="c", address_line1="c", customer=c, is_primary_address=True
        )


def test_db_pincode_is_six_characters(parties):
    c, _ = parties
    with pytest.raises(IntegrityError), transaction.atomic():
        Address.objects.create(
            address_title="a", address_line1="a", customer=c, pincode="4567"
        )


def test_api_validates_party_primary_and_pincode(parties, client):
    c, s = parties
    url = "/api/contacts/addresses/"
    base = {"address_title": "Shop", "address_line1": "Main Road"}
    res = client.post(url, base, format="json")
    assert res.status_code == 400
    res = client.post(
        url, {**base, "customer": c.pk, "pincode": "045677"}, format="json"
    )
    assert res.status_code == 400 and "pincode" in res.data
    res = client.post(
        url,
        {**base, "customer": c.pk, "pincode": "456771", "is_primary_address": True},
        format="json",
    )
    assert res.status_code == 201, res.content
    res = client.post(
        url, {**base, "customer": c.pk, "is_primary_address": True}, format="json"
    )
    assert res.status_code == 400 and "is_primary_address" in res.data
    res = client.get(url, {"customer": c.pk})
    assert res.data["count"] == 1
