from django.db import migrations
from django.db.models import F


def backfill_totals(apps, schema_editor):
    """Existing documents keep zero tax: net = rounded = grand_total.

    ``gst_category`` stays "" (the legacy / pre-GST marker); lines keep their
    defaults (Taxable, zero GST columns, empty item_tax_rate).
    """
    for name in ['SalesOrder', 'SalesInvoice']:
        Model = apps.get_model("selling", name)
        Model.objects.update(
            net_total=F("grand_total"),
            total_taxes_and_charges=0,
            rounding_adjustment=0,
            rounded_total=F("grand_total"),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("selling", "0005_gst_document_schema"),
    ]

    operations = [
        migrations.RunPython(backfill_totals, migrations.RunPython.noop),
    ]
