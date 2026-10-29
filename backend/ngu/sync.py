"""Fetch the NGU catalogue and upsert it into the crosswalk (no Items touched)."""

import json
import subprocess
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ngu.models import NguCatalogEntry, NguEntryKind, NguLinkMethod
from stock.models import Item

#: One round trip: every table the catalogue needs, as a single JSON document.
CATALOG_SQL = """
select json_build_object(
  'categories', (select coalesce(json_agg(json_build_object('id', id, 'name', name)), '[]') from products_category),
  'products', (select coalesce(json_agg(json_build_object('id', id, 'name', name, 'category_id', category_id, 'is_active', is_active)), '[]') from products_product),
  'variants', (select coalesce(json_agg(json_build_object('id', id, 'product_id', product_id, 'weight', weight, 'unit', unit, 'price', price, 'discount_price', discount_price, 'sku', sku, 'is_active', is_active)), '[]') from products_productvariant),
  'combos', (select coalesce(json_agg(json_build_object('id', id, 'name', name, 'weight', weight, 'unit', unit, 'discount_price', discount_price, 'is_active', is_active)), '[]') from products_productcombo),
  'combo_items', (select coalesce(json_agg(json_build_object('combo_id', combo_id, 'variant_id', variant_id, 'quantity', quantity)), '[]') from products_productcomboitem)
)
"""


def fetch_from_dsn(dsn: str) -> dict:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(CATALOG_SQL)
        return cur.fetchone()[0]


def fetch_from_docker(container: str, db: str, user: str = "postgres") -> dict:
    out = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            user,
            "-d",
            db,
            "-At",
            "-c",
            CATALOG_SQL,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return json.loads(out)


def _dec(value):
    return None if value is None else Decimal(str(value))


def _norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


@transaction.atomic
def sync_catalog(data: dict) -> dict:
    """Upsert variants and combos by NGU id. Returns counts.

    Existing links are never changed. A not-yet-linked variant is linked only
    when its product name equals exactly one unlinked item's name (case and
    spacing aside) and that item has no other NGU variant.
    """
    now = timezone.now()
    categories = {c["id"]: c["name"] for c in data["categories"]}
    products = {p["id"]: p for p in data["products"]}
    counts = {"variants": 0, "combos": 0, "created": 0, "updated": 0, "linked": 0}

    by_name: dict[str, list[Item]] = {}
    for item in Item.objects.filter(ngu_entries__isnull=True):
        by_name.setdefault(_norm(item.name), []).append(item)

    def upsert(kind, ngu_id, fields):
        entry, created = NguCatalogEntry.objects.get_or_create(
            kind=kind, ngu_id=ngu_id, defaults={**fields, "last_synced_at": now}
        )
        if not created:
            for k, v in fields.items():
                setattr(entry, k, v)
            entry.last_synced_at = now
            entry.save()
        counts["created" if created else "updated"] += 1
        return entry

    for v in data["variants"]:
        prod = products[v["product_id"]]
        entry = upsert(
            NguEntryKind.VARIANT,
            v["id"],
            dict(
                ngu_product_id=prod["id"],
                ngu_category_id=prod["category_id"],
                category_name=categories.get(prod["category_id"], ""),
                product_name=prod["name"],
                weight=_dec(v["weight"]),
                unit=v["unit"] or "",
                sku=v["sku"] or "",
                price=_dec(v["price"]),
                discount_price=_dec(v["discount_price"]),
                is_active=bool(v["is_active"] and prod["is_active"]),
            ),
        )
        counts["variants"] += 1
        if entry.item_id is None:
            matches = by_name.get(_norm(prod["name"]), [])
            if len(matches) == 1:
                entry.item = matches[0]
                entry.link_method = NguLinkMethod.EXACT_NAME
                entry.save(update_fields=["item", "link_method", "modified_at"])
                by_name[_norm(prod["name"])] = []
                counts["linked"] += 1

    items_by_combo: dict[int, list] = {}
    for ci in data["combo_items"]:
        items_by_combo.setdefault(ci["combo_id"], []).append(
            {"variant_id": ci["variant_id"], "quantity": ci["quantity"]}
        )
    for c in data["combos"]:
        upsert(
            NguEntryKind.COMBO,
            c["id"],
            dict(
                product_name=c["name"],
                weight=_dec(c["weight"]),
                unit=c["unit"] or "",
                price=_dec(c["discount_price"]) or Decimal("0"),
                is_active=bool(c["is_active"]),
                components=items_by_combo.get(c["id"], []),
            ),
        )
        counts["combos"] += 1
    return counts
