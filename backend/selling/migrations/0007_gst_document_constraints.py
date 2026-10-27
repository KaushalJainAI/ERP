import django.db.models.expressions
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("selling", "0006_backfill_document_totals"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='salesinvoice',
            constraint=models.CheckConstraint(check=models.Q(('grand_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '-', models.Value(Decimal('0.005')))), ('grand_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '+', models.Value(Decimal('0.005'))))), name='selling_salesinvoice_grand_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoice',
            constraint=models.CheckConstraint(check=models.Q(('rounded_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '-', models.Value(Decimal('0.005')))), ('rounded_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '+', models.Value(Decimal('0.005'))))), name='selling_salesinvoice_rounded_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoice',
            constraint=models.CheckConstraint(check=models.Q(('is_reverse_charge', True), ('is_export_with_gst', True), _negated=True), name='selling_salesinvoice_rcm_not_export'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_rate__gte', 0), ('gst_rate__lte', 100)), name='selling_salesinvoiceitem_gst_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('cgst_amount__gte', 0), ('sgst_amount__gte', 0), ('igst_amount__gte', 0)), name='selling_salesinvoiceitem_gst_amounts_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_treatment', 'Taxable'), models.Q(('cgst_amount', 0), ('sgst_amount', 0), ('igst_amount', 0)), _connector='OR'), name='selling_salesinvoiceitem_only_taxable_has_gst'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('igst_amount__gt', 0), models.Q(('cgst_amount__gt', 0), ('sgst_amount__gt', 0), _connector='OR'), _negated=True), name='selling_salesinvoiceitem_no_igst_with_cgst'),
        ),
        migrations.AddConstraint(
            model_name='salesorder',
            constraint=models.CheckConstraint(check=models.Q(('grand_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '-', models.Value(Decimal('0.005')))), ('grand_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '+', models.Value(Decimal('0.005'))))), name='selling_salesorder_grand_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='salesorder',
            constraint=models.CheckConstraint(check=models.Q(('rounded_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '-', models.Value(Decimal('0.005')))), ('rounded_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '+', models.Value(Decimal('0.005'))))), name='selling_salesorder_rounded_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='salesorder',
            constraint=models.CheckConstraint(check=models.Q(('is_reverse_charge', True), ('is_export_with_gst', True), _negated=True), name='selling_salesorder_rcm_not_export'),
        ),
        migrations.AddConstraint(
            model_name='salesorderitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_rate__gte', 0), ('gst_rate__lte', 100)), name='selling_salesorderitem_gst_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='salesorderitem',
            constraint=models.CheckConstraint(check=models.Q(('cgst_amount__gte', 0), ('sgst_amount__gte', 0), ('igst_amount__gte', 0)), name='selling_salesorderitem_gst_amounts_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='salesorderitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_treatment', 'Taxable'), models.Q(('cgst_amount', 0), ('sgst_amount', 0), ('igst_amount', 0)), _connector='OR'), name='selling_salesorderitem_only_taxable_has_gst'),
        ),
        migrations.AddConstraint(
            model_name='salesorderitem',
            constraint=models.CheckConstraint(check=models.Q(('igst_amount__gt', 0), models.Q(('cgst_amount__gt', 0), ('sgst_amount__gt', 0), _connector='OR'), _negated=True), name='selling_salesorderitem_no_igst_with_cgst'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('charge_type', 'On Net Total')), name='selling_salesinvoicetax_charge_type'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('category', 'Total')), name='selling_salesinvoicetax_category'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('add_deduct_tax__in', ['Add', 'Deduct'])), name='selling_salesinvoicetax_add_deduct'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('rate__gte', 0), ('rate__lte', 100)), name='selling_salesinvoicetax_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('tax_amount__gte', 0)), name='selling_salesinvoicetax_tax_amount_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.UniqueConstraint(fields=('sales_invoice', 'idx'), name='selling_salesinvoicetax_unique_idx'),
        ),
        migrations.AddConstraint(
            model_name='salesinvoicetax',
            constraint=models.UniqueConstraint(fields=('sales_invoice', 'account'), name='selling_salesinvoicetax_unique_account'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.CheckConstraint(check=models.Q(('charge_type', 'On Net Total')), name='selling_salesordertax_charge_type'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.CheckConstraint(check=models.Q(('category', 'Total')), name='selling_salesordertax_category'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.CheckConstraint(check=models.Q(('add_deduct_tax__in', ['Add', 'Deduct'])), name='selling_salesordertax_add_deduct'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.CheckConstraint(check=models.Q(('rate__gte', 0), ('rate__lte', 100)), name='selling_salesordertax_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.CheckConstraint(check=models.Q(('tax_amount__gte', 0)), name='selling_salesordertax_tax_amount_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.UniqueConstraint(fields=('sales_order', 'idx'), name='selling_salesordertax_unique_idx'),
        ),
        migrations.AddConstraint(
            model_name='salesordertax',
            constraint=models.UniqueConstraint(fields=('sales_order', 'account'), name='selling_salesordertax_unique_account'),
        ),
    ]
