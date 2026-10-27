"""Seed ``core.State`` (idempotent) and backfill Company GST category / state.

Data-only migration (DDL is in 0007). The Company backfill follows the design: a
company with a GSTIN becomes ``Registered Regular`` and its state is taken from the
GSTIN prefix when that prefix is a known state code.
"""

from django.db import migrations

from core.gst_states import STATES


def seed_states(apps, schema_editor):
    State = apps.get_model("core", "State")
    for code, name, abbr, is_ut, is_active in STATES:
        State.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "abbr": abbr,
                "is_union_territory": is_ut,
                "is_active": is_active,
            },
        )

    Company = apps.get_model("core", "Company")
    known = set(State.objects.values_list("code", flat=True))
    for company in Company.objects.exclude(gstin__isnull=True).exclude(gstin=""):
        changed = []
        if company.gst_category == "Unregistered":
            company.gst_category = "Registered Regular"
            changed.append("gst_category")
        prefix = company.gstin.strip()[:2]
        if company.state_id is None and prefix in known:
            company.state_id = prefix
            changed.append("state")
        if changed:
            company.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_gst_state_and_company_fields"),
    ]

    operations = [
        migrations.RunPython(seed_states, migrations.RunPython.noop),
    ]
