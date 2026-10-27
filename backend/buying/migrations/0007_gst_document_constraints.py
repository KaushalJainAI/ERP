import django.db.models.expressions
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buying", "0006_backfill_document_totals"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='purchaseinvoice',
            constraint=models.CheckConstraint(check=models.Q(('grand_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '-', models.Value(Decimal('0.005')))), ('grand_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '+', models.Value(Decimal('0.005'))))), name='buying_purchaseinvoice_grand_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoice',
            constraint=models.CheckConstraint(check=models.Q(('rounded_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '-', models.Value(Decimal('0.005')))), ('rounded_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '+', models.Value(Decimal('0.005'))))), name='buying_purchaseinvoice_rounded_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoice',
            constraint=models.CheckConstraint(check=models.Q(('is_reverse_charge', True), ('is_export_with_gst', True), _negated=True), name='buying_purchaseinvoice_rcm_not_export'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_rate__gte', 0), ('gst_rate__lte', 100)), name='buying_purchaseinvoiceitem_gst_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('cgst_amount__gte', 0), ('sgst_amount__gte', 0), ('igst_amount__gte', 0)), name='buying_purchaseinvoiceitem_gst_amounts_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_treatment', 'Taxable'), models.Q(('cgst_amount', 0), ('sgst_amount', 0), ('igst_amount', 0)), _connector='OR'), name='buying_purchaseinvoiceitem_only_taxable_has_gst'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoiceitem',
            constraint=models.CheckConstraint(check=models.Q(('igst_amount__gt', 0), models.Q(('cgst_amount__gt', 0), ('sgst_amount__gt', 0), _connector='OR'), _negated=True), name='buying_purchaseinvoiceitem_no_igst_with_cgst'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.CheckConstraint(check=models.Q(('grand_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '-', models.Value(Decimal('0.005')))), ('grand_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('net_total'), '+', models.F('total_taxes_and_charges')), '+', models.Value(Decimal('0.005'))))), name='buying_purchaseorder_grand_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.CheckConstraint(check=models.Q(('rounded_total__gt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '-', models.Value(Decimal('0.005')))), ('rounded_total__lt', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('grand_total'), '+', models.F('rounding_adjustment')), '+', models.Value(Decimal('0.005'))))), name='buying_purchaseorder_rounded_total_identity'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.CheckConstraint(check=models.Q(('is_reverse_charge', True), ('is_export_with_gst', True), _negated=True), name='buying_purchaseorder_rcm_not_export'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_rate__gte', 0), ('gst_rate__lte', 100)), name='buying_purchaseorderitem_gst_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderitem',
            constraint=models.CheckConstraint(check=models.Q(('cgst_amount__gte', 0), ('sgst_amount__gte', 0), ('igst_amount__gte', 0)), name='buying_purchaseorderitem_gst_amounts_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderitem',
            constraint=models.CheckConstraint(check=models.Q(('gst_treatment', 'Taxable'), models.Q(('cgst_amount', 0), ('sgst_amount', 0), ('igst_amount', 0)), _connector='OR'), name='buying_purchaseorderitem_only_taxable_has_gst'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderitem',
            constraint=models.CheckConstraint(check=models.Q(('igst_amount__gt', 0), models.Q(('cgst_amount__gt', 0), ('sgst_amount__gt', 0), _connector='OR'), _negated=True), name='buying_purchaseorderitem_no_igst_with_cgst'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('charge_type', 'On Net Total')), name='buying_purchaseinvoicetax_charge_type'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('category', 'Total')), name='buying_purchaseinvoicetax_category'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('add_deduct_tax__in', ['Add', 'Deduct'])), name='buying_purchaseinvoicetax_add_deduct'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('rate__gte', 0), ('rate__lte', 100)), name='buying_purchaseinvoicetax_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.CheckConstraint(check=models.Q(('tax_amount__gte', 0)), name='buying_purchaseinvoicetax_tax_amount_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.UniqueConstraint(fields=('purchase_invoice', 'idx'), name='buying_purchaseinvoicetax_unique_idx'),
        ),
        migrations.AddConstraint(
            model_name='purchaseinvoicetax',
            constraint=models.UniqueConstraint(fields=('purchase_invoice', 'account'), name='buying_purchaseinvoicetax_unique_account'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.CheckConstraint(check=models.Q(('charge_type', 'On Net Total')), name='buying_purchaseordertax_charge_type'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.CheckConstraint(check=models.Q(('category', 'Total')), name='buying_purchaseordertax_category'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.CheckConstraint(check=models.Q(('add_deduct_tax__in', ['Add', 'Deduct'])), name='buying_purchaseordertax_add_deduct'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.CheckConstraint(check=models.Q(('rate__gte', 0), ('rate__lte', 100)), name='buying_purchaseordertax_rate_0_100'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.CheckConstraint(check=models.Q(('tax_amount__gte', 0)), name='buying_purchaseordertax_tax_amount_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.UniqueConstraint(fields=('purchase_order', 'idx'), name='buying_purchaseordertax_unique_idx'),
        ),
        migrations.AddConstraint(
            model_name='purchaseordertax',
            constraint=models.UniqueConstraint(fields=('purchase_order', 'account'), name='buying_purchaseordertax_unique_account'),
        ),
    ]
