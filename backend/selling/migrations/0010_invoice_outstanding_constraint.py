from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("selling", "0009_backfill_invoice_outstanding"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.CheckConstraint(
                check=models.Q(("outstanding_amount__gte", 0)),
                name="selling_salesinvoice_outstanding_gte_0",
            ),
        ),
    ]
