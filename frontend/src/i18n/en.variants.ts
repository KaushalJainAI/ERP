/** Item variants (vr.*): labels for the variant fields, attributes and the create-variant page. */
export const enVariants = {
  vr: {
    actions: {
      create_variant: "Create variant",
    },
    fields: {
      abbr: "Short code",
      attribute_name: "Attribute",
      has_variants: "Has variants (sizes, packs)",
      item_code_optional: "Item code (optional)",
      template_attributes: "Varies by",
      value: "Value",
      variant_label: "Variant",
      variant_of: "Variant of",
    },
    help: {
      batch_date: "Date of this batch.",
      has_variants: "Turn on when this item comes in sizes or packs; each one is its own variant.",
      template_attributes: "The attributes the variants differ by, for example Pack size.",
      variant_code: "Leave empty to make the code from the item and the chosen values.",
    },
    page: {
      choose: "Choose…",
      create: "Create variant",
      created: "Variant {{code}} created.",
      no_attributes: "This item has no attributes to vary by.",
      not_template: "Only an item with variants can have a variant made from it.",
      pick_one: "Choose a value for every attribute.",
      title: "New variant of {{item}}",
    },
    res: {
      item_attribute: "Item attribute",
      item_attributes: "Item attributes",
    },
    values_table: "Values",
  },
};
