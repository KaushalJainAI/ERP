from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buying", "0009_backfill_invoice_outstanding"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="purchaseinvoice",
            constraint=models.CheckConstraint(
                check=models.Q(("outstanding_amount__gte", 0)),
                name="buying_purchaseinvoice_outstanding_gte_0",
            ),
        ),
    ]
