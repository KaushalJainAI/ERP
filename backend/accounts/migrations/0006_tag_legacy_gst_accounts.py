"""Tag the legacy bare ``CGST`` / ``SGST`` / ``IGST`` liability accounts as Output.

``seed_defaults`` used to create these three Tax accounts per company. Tagging them
keeps their names (history stays readable) and lets ``ensure_gst_accounts`` find them
by ``(company, direction, component)`` instead of creating duplicates.
"""

from django.db import migrations


def tag_legacy_accounts(apps, schema_editor):
    Account = apps.get_model("accounts", "Account")
    for component in ("CGST", "SGST", "IGST"):
        legacy = Account.objects.filter(
            name=component,
            account_type="Tax",
            root_type="Liability",
            is_group=False,
            gst_direction__isnull=True,
        )
        for acc in legacy:
            already = Account.objects.filter(
                company_id=acc.company_id,
                gst_direction="Output",
                gst_component=component,
            ).exists()
            if already:
                continue
            acc.gst_direction = "Output"
            acc.gst_component = component
            acc.save(update_fields=["gst_direction", "gst_component"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_gst_tax_masters"),
    ]

    operations = [
        migrations.RunPython(tag_legacy_accounts, migrations.RunPython.noop),
    ]
