"""Rules shared by the Customer and Supplier serializers (S-8, U-2).

``PartyRecordSerializerMixin`` must come *before* ``PartyGSTSerializerMixin`` in a
serializer's bases: it runs its checks after the GST mixin has derived ``state``
from the GSTIN.
"""

from django.db.models import Value
from django.db.models.functions import Lower
from django.utils.translation import gettext as _
from rest_framework import serializers

from buying.models import Supplier
from core.models import GSTCategory
from selling.models import Customer

#: Registered categories: the party must have a state (place of supply).
REGISTERED_CATEGORIES = (
    GSTCategory.REGISTERED_REGULAR,
    GSTCategory.REGISTERED_COMPOSITION,
)

#: Names of the root groups seeded by ``seed_defaults``.
ROOT_CUSTOMER_GROUP = "All Customer Groups"
ROOT_SUPPLIER_GROUP = "All Supplier Groups"

GST_FIELDS = {"gst_category", "gstin", "state"}


def gstin_twins(party):
    """Other active parties of the same company with the same GSTIN (branches share
    one). Customers and suppliers are both checked."""
    if not party.gstin:
        return []
    twins = []
    for model in (Customer, Supplier):
        qs = model.objects.filter(
            company_id=party.company_id, gstin=party.gstin, is_active=True
        )
        if model is type(party):
            qs = qs.exclude(pk=party.pk)
        twins.extend(qs.order_by("pk"))
    return twins


class PartyRecordSerializerMixin:
    """Name uniqueness, registered-party state, default group and GSTIN warnings."""

    party_model = None
    group_field = None  # "customer_group" / "supplier_group"
    group_model = None
    root_group_name = None
    name_taken_message = None  # gettext_noop("... %(name)s ...")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        if instance is None or {"name", "company"} & set(attrs):
            self._check_name(attrs)
        if instance is None or GST_FIELDS & set(attrs):
            self._check_registered_state(attrs)
        return attrs

    def _check_name(self, attrs):
        """One party per name per company, ignoring case (the DB constraint is
        ``Lower("name")``; this gives the user a translated message instead)."""
        instance = self.instance
        name = attrs.get("name", getattr(instance, "name", None))
        company = attrs.get("company", getattr(instance, "company", None))
        if not name or company is None:
            return
        clash = (
            self.party_model.objects.filter(company=company)
            .annotate(lower_name=Lower("name"))
            .filter(lower_name=Lower(Value(name)))
        )
        if instance is not None:
            clash = clash.exclude(pk=instance.pk)
        existing = clash.first()
        if existing is not None:
            raise serializers.ValidationError(
                {"name": _(self.name_taken_message) % {"name": existing.name}}
            )

    def _check_registered_state(self, attrs):
        """A registered party must have a state. It is derived from the GSTIN prefix
        by the GST mixin; refuse only when that still leaves it blank."""
        instance = self.instance
        category = attrs.get(
            "gst_category",
            getattr(instance, "gst_category", GSTCategory.UNREGISTERED),
        )
        if category not in REGISTERED_CATEGORIES:
            return
        state = attrs["state"] if "state" in attrs else getattr(instance, "state", None)
        if state is None:
            raise serializers.ValidationError(
                {
                    "state": _(
                        "A registered party needs a state. Enter it, or use a GSTIN "
                        "whose first two digits are a state code."
                    )
                }
            )

    def create(self, validated_data):
        if not validated_data.get(self.group_field):
            validated_data[self.group_field] = self.group_model.objects.get_or_create(
                name=self.root_group_name, defaults={"is_group": True}
            )[0]
        party = super().create(validated_data)
        party._gstin_warnings = [
            _("Already saved as %(name)s") % {"name": twin.name}
            for twin in gstin_twins(party)
        ]
        return party

    def get_warnings(self, obj):
        return getattr(obj, "_gstin_warnings", [])
