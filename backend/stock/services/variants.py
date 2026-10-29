"""Item variants (ERPNext ``controllers/item_variant.py``).

A *template* item (``has_variants``) declares which attributes its variants vary
by; ``create_variant`` makes one concrete stock item per attribute combination,
copying the template's fields and building the code from the template code plus
the value abbreviations ("GARAM-500"). The combination is unique per template at
the database level (``Item.variant_signature``).
"""

from typing import Dict, Iterable, List, Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from stock.models import Item, ItemAttribute, ItemAttributeValue, ItemVariantAttribute

#: Template fields copied onto a new variant (ERPNext ``copy_attributes_to_variant``).
COPIED_FIELDS = (
    "description",
    "item_group_id",
    "stock_uom_id",
    "valuation_method",
    "standard_rate",
    "is_active",
    "gst_hsn_code",
    "is_nil_exempt",
    "is_non_gst",
    "item_tax_template_id",
    "has_batch_no",
    "has_expiry_date",
    "shelf_life_in_days",
)


def assert_not_template(item: Item) -> None:
    """Refuse a template item anywhere stock or a document line is involved."""
    if item.has_variants:
        raise ValidationError(
            _(
                "Item '%(code)s' is a template with variants: select one of its "
                "variants instead."
            )
            % {"code": item.code}
        )


def signature(selections: Dict[int, int]) -> str:
    """Canonical text of an attribute combination ("attr_id:value_id|...")."""
    return "|".join(f"{a}:{v}" for a, v in sorted(selections.items()))


def variant_attribute_rows(item: Item) -> List[ItemVariantAttribute]:
    return list(item.attributes.select_related("attribute", "value").order_by("pk"))


def variant_label(item: Item) -> str:
    """"500g, Hot" for a variant, '' for any other item."""
    if not item.variant_of_id:
        return ""
    return ", ".join(r.value.value for r in variant_attribute_rows(item) if r.value_id)


def template_attributes(template: Item) -> List[ItemAttribute]:
    return [r.attribute for r in variant_attribute_rows(template)]


def _clean_selections(template: Item, raw) -> Dict[int, ItemAttributeValue]:
    """Validate ``{attribute_id: value_id}`` against the template."""
    if not template.has_variants:
        raise ValidationError(
            _("Item '%(code)s' is not a template item.") % {"code": template.code}
        )
    declared = {r.attribute_id: r.attribute for r in variant_attribute_rows(template)}
    if not declared:
        raise ValidationError(
            _("Template '%(code)s' has no attributes; add them first.")
            % {"code": template.code}
        )
    if not raw:
        raise ValidationError(_("Select at least one attribute value."))
    chosen: Dict[int, ItemAttributeValue] = {}
    for attribute_id, value_id in dict(raw).items():
        try:
            attribute_id, value_id = int(attribute_id), int(value_id)
        except (TypeError, ValueError):
            raise ValidationError(_("Attribute and value must be ids."))
        attribute = declared.get(attribute_id)
        if attribute is None:
            raise ValidationError(
                _("Attribute #%(id)s is not used by template '%(code)s'.")
                % {"id": attribute_id, "code": template.code}
            )
        if attribute.disabled:
            raise ValidationError(
                _("Attribute '%(name)s' is disabled.") % {"name": attribute.name}
            )
        value = ItemAttributeValue.objects.filter(
            pk=value_id, attribute_id=attribute_id
        ).first()
        if value is None:
            raise ValidationError(
                _("Value #%(id)s is not a value of attribute '%(name)s'.")
                % {"id": value_id, "name": attribute.name}
            )
        chosen[attribute_id] = value
    return chosen


def create_variant(
    template: Item,
    selections,
    *,
    item_code: Optional[str] = None,
    item_name: Optional[str] = None,
) -> Item:
    """Create the variant of ``template`` for ``{attribute_id: value_id}``."""
    chosen = _clean_selections(template, selections)
    sig = signature({a: v.pk for a, v in chosen.items()})
    existing = Item.objects.filter(variant_of=template, variant_signature=sig).first()
    if existing is not None:
        raise ValidationError(
            _("A variant with these attributes already exists: '%(code)s'.")
            % {"code": existing.code}
        )

    # Declared (template) order, so codes read the same however the client sent them.
    order = [r.attribute_id for r in variant_attribute_rows(template)]
    ordered = [chosen[a] for a in order if a in chosen]
    code = item_code or "-".join([template.code] + [v.abbr for v in ordered])
    name = item_name or f"{template.name} - {' / '.join(v.value for v in ordered)}"
    if len(code) > Item._meta.get_field("code").max_length:
        raise ValidationError(_("Variant code '%(code)s' is too long.") % {"code": code})
    if len(name) > Item._meta.get_field("name").max_length:
        raise ValidationError(_("Variant name is too long."))
    if Item.objects.filter(code=code).exists():
        raise ValidationError(_("Item '%(code)s' already exists.") % {"code": code})

    fields = {f: getattr(template, f) for f in COPIED_FIELDS}
    try:
        with transaction.atomic():
            variant = Item.objects.create(
                code=code,
                name=name,
                # The template itself is never a stock item (DB CHECK); its variants are.
                is_stock_item=True,
                variant_of=template,
                variant_signature=sig,
                **fields,
            )
            for attribute_id in order:
                if attribute_id in chosen:
                    ItemVariantAttribute.objects.create(
                        item=variant,
                        attribute_id=attribute_id,
                        value=chosen[attribute_id],
                    )
    except IntegrityError:
        raise ValidationError(
            _("A variant with these attributes already exists.")
        )
    return variant


def set_template_attributes(template: Item, attribute_ids: Iterable[int]) -> None:
    """Replace the attributes a template declares (rows without a value). Refused
    once the template has variants (ERPNext: attributes of a used template are
    frozen)."""
    ids = list(dict.fromkeys(int(a) for a in attribute_ids))
    current = list(
        template.attributes.order_by("pk").values_list("attribute_id", flat=True)
    )
    if ids == current:
        return
    if template.variants.exists():
        raise ValidationError(
            _("Template '%(code)s' already has variants; its attributes cannot change.")
            % {"code": template.code}
        )
    found = ItemAttribute.objects.filter(pk__in=ids).count()
    if found != len(ids):
        raise ValidationError(_("Unknown attribute."))
    with transaction.atomic():
        template.attributes.all().delete()
        for attribute_id in ids:
            ItemVariantAttribute.objects.create(
                item=template, attribute_id=attribute_id, value=None
            )
