"""Serializer helpers shared by the GST-aware masters (Customer, Supplier)."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from gst.validators import normalise_gstin, validate_party_gst


class PartyGSTSerializerMixin:
    """Serializer mixin for Customer / Supplier GST fields.

    * ``validate_gstin`` stores the normalised (stripped, upper-cased) GSTIN; the
      checksum is checked by the model field's ``validate_gstin_format`` validator.
    * ``validate`` applies the cross-field rules (category versus GSTIN, state
      auto-fill from the GSTIN prefix, prefix/state mismatch, Overseas -> no state)
      using the instance's current values for fields a PATCH omits.
    """

    def validate_gstin(self, value):
        return normalise_gstin(value) or None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance

        def current(name, default=None):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, default) if instance is not None else default

        gstin = current("gstin")
        category = current("gst_category", "Unregistered")
        state = current("state")
        # A PATCH that changes the GSTIN without naming a state re-derives the state
        # from the new GSTIN instead of tripping over the stale one.
        if (
            instance is not None
            and "gstin" in attrs
            and "state" not in attrs
            and normalise_gstin(attrs["gstin"]) != normalise_gstin(instance.gstin)
        ):
            state = None

        touched = {"gstin", "gst_category", "state"} & set(attrs)
        if instance is not None and not touched:
            return attrs
        try:
            gstin, state = validate_party_gst(gstin, category, state)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        attrs["gstin"] = gstin
        attrs["state"] = state
        return attrs
