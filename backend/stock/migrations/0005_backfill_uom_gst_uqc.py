"""Fill ``UOM.gst_uqc`` for the six seeded UOMs (only where still blank)."""

from django.db import migrations

UOM_UQC = {
    "Nos": "NOS",
    "Kg": "KGS",
    "Meter": "MTR",
    "Litre": "LTR",
    "Box": "BOX",
    "Set": "SET",
}


def fill_uqc(apps, schema_editor):
    UOM = apps.get_model("stock", "UOM")
    for name, uqc in UOM_UQC.items():
        UOM.objects.filter(name=name, gst_uqc="").update(gst_uqc=uqc)


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0004_gst_item_and_uom_fields"),
    ]

    operations = [
        migrations.RunPython(fill_uqc, migrations.RunPython.noop),
    ]
