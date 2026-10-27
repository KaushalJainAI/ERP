"""Backfill Supplier GST category / state from the GSTIN (data-only).

A party with a GSTIN becomes ``Registered Regular`` and its state is taken from the
GSTIN prefix when that is a known state code. Parties without a GSTIN keep the
default ``Unregistered``. Only untouched defaults are changed, so re-running is a
no-op. No GSTIN is validated here (legacy values may fail the checksum).
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Party = apps.get_model("buying", "Supplier")
    State = apps.get_model("core", "State")
    known = set(State.objects.values_list("code", flat=True))
    for party in Party.objects.exclude(gstin__isnull=True).exclude(gstin=""):
        changed = []
        if party.gst_category == "Unregistered":
            party.gst_category = "Registered Regular"
            changed.append("gst_category")
        prefix = party.gstin.strip()[:2]
        if party.state_id is None and prefix in known:
            party.state_id = prefix
            changed.append("state")
        if changed:
            party.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_seed_states"),
        ("buying", "0003_supplier_gst_fields"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
