from django.utils.translation import gettext as _
from rest_framework import serializers

from contacts.models import Address


class AddressSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        source="customer.name", read_only=True, default=None
    )
    supplier_name = serializers.CharField(
        source="supplier.name", read_only=True, default=None
    )
    state_name = serializers.CharField(
        source="state.name", read_only=True, default=None
    )

    class Meta:
        model = Address
        fields = (
            "id",
            "address_title",
            "address_type",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "state_name",
            "pincode",
            "country",
            "gstin",
            "email",
            "phone",
            "is_primary_address",
            "is_shipping_address",
            "is_active",
            "customer",
            "customer_name",
            "supplier",
            "supplier_name",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")

    def validate_gstin(self, value):
        return value.strip().upper() if value else None

    def validate(self, attrs):
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        customer, supplier = current("customer"), current("supplier")
        if bool(customer) == bool(supplier):
            raise serializers.ValidationError(
                _("Link the address to exactly one customer or one supplier.")
            )
        if current("is_primary_address"):
            field, party = (
                ("customer", customer) if customer else ("supplier", supplier)
            )
            others = Address.objects.filter(**{field: party}, is_primary_address=True)
            if self.instance:
                others = others.exclude(pk=self.instance.pk)
            if others.exists():
                raise serializers.ValidationError(
                    {
                        "is_primary_address": _(
                            "This party already has a primary address."
                        )
                    }
                )
        return attrs
