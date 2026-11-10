"""Create the ``day_summary`` alert rule on existing databases (U-7 d).

``seed_defaults`` also creates it, but that command must not run on the real books
(it would recreate the demo company), so the rule arrives with ``migrate``. Idempotent;
recipients are chosen by an admin in Alert settings.
"""

from django.db import migrations


def add_rule(apps, schema_editor):
    AlertRule = apps.get_model("notifications", "AlertRule")
    AlertRule.objects.get_or_create(key="day_summary", defaults={"is_active": True})


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [migrations.RunPython(add_rule, migrations.RunPython.noop)]
