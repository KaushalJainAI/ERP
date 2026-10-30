from django.utils.translation import gettext as _
from rest_framework import serializers

from ngu.models import NguCatalogEntry, NguEntryKind, NguLinkMethod

_NGU_FIELDS = (
    "id",
    "kind",
    "ngu_id",
    "ngu_product_id",
    "ngu_category_id",
    "category_name",
    "product_name",
    "weight",
    "unit",
    "sku",
    "price",
    "discount_price",
    "is_active",
    "components",
    "link_method",
    "last_synced_at",
)


class NguCatalogEntrySerializer(serializers.ModelSerializer):
    """Only ``item`` is writable: the NGU-side columns belong to the importer."""

    item_code = serializers.CharField(source="item.code", read_only=True, default=None)
    item_name = serializers.CharField(source="item.name", read_only=True, default=None)

    class Meta:
        model = NguCatalogEntry
        fields = _NGU_FIELDS + ("item", "item_code", "item_name")
        read_only_fields = _NGU_FIELDS + ("item_code", "item_name")

    def validate_item(self, item):
        if item is not None and self.instance.kind != NguEntryKind.VARIANT:
            raise serializers.ValidationError(
                _("Only variants can be linked to an item.")
            )
        taken = (
            item is not None
            and NguCatalogEntry.objects.filter(item=item)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if taken:
            raise serializers.ValidationError(
                _("This item is already linked to another NGU variant.")
            )
        return item

    def update(self, instance, validated_data):
        item = validated_data.get("item", instance.item)
        instance.item = item
        instance.link_method = NguLinkMethod.MANUAL if item else NguLinkMethod.NONE
        instance.save(update_fields=["item", "link_method", "modified_at"])
        return instance
