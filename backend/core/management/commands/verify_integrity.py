from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from accounts.models import GLEntry
from stock.models import StockLedgerEntry, Bin
from core.models import Company, SystemHealthCheck
from accounts.services import outstanding as outstanding_service
from stock.services import batches as batch_service
from gst.services import integrity as gst_integrity


class Command(BaseCommand):
    help = "Verifies all system financial and stock ledger invariants (plan.md §6)."

    def handle(self, *args, **options):
        self.stdout.write("Running ERP Integrity Verification...")
        errors = []

        # 1. GL Balance Invariant
        vouchers = (
            GLEntry.objects.filter(is_cancelled=False)
            .values("voucher_type", "voucher_no")
            .distinct()
        )
        for v in vouchers:
            v_entries = GLEntry.objects.filter(
                voucher_type=v["voucher_type"],
                voucher_no=v["voucher_no"],
                is_cancelled=False,
            )
            total_dr = sum(e.debit for e in v_entries)
            total_cr = sum(e.credit for e in v_entries)
            if total_dr != total_cr:
                errors.append(
                    f"GL Imbalance in {v['voucher_type']}:{v['voucher_no']} - Debit: {total_dr}, Credit: {total_cr}"
                )

        # 2. Stock Bin Accuracy Invariant
        for bin_obj in Bin.objects.all():
            sle_aggregate = StockLedgerEntry.objects.filter(
                item=bin_obj.item,
                warehouse=bin_obj.warehouse,
                is_cancelled=False,
            ).aggregate(
                sum_qty=Sum("qty"),
                sum_value=Sum("stock_value_difference"),
            )

            expected_qty = (sle_aggregate["sum_qty"] or Decimal("0.000")).quantize(
                Decimal("0.001")
            )
            expected_value = (sle_aggregate["sum_value"] or Decimal("0.00")).quantize(
                Decimal("0.01")
            )

            if bin_obj.actual_qty != expected_qty:
                errors.append(
                    f"Bin Qty Drift for {bin_obj.item.code} @ {bin_obj.warehouse.name}: "
                    f"Bin actual_qty ({bin_obj.actual_qty}) != SLE Sum ({expected_qty})"
                )

            if bin_obj.stock_value != expected_value:
                errors.append(
                    f"Bin Stock Value Drift for {bin_obj.item.code} @ {bin_obj.warehouse.name}: "
                    f"Bin stock_value ({bin_obj.stock_value}) != SLE Sum ({expected_value})"
                )

            # Valuation rate must stay the derived quotient stock_value / actual_qty.
            # A mismatch means the moving-average projection drifted from its own
            # facts even when qty/value happen to tie out.
            expected_rate = (
                (expected_value / expected_qty).quantize(Decimal("0.000001"))
                if expected_qty > 0
                else Decimal("0.000000")
            )
            if bin_obj.valuation_rate != expected_rate:
                errors.append(
                    f"Bin Valuation Rate Drift for {bin_obj.item.code} @ {bin_obj.warehouse.name}: "
                    f"Bin valuation_rate ({bin_obj.valuation_rate}) != stock_value/qty ({expected_rate})"
                )

        # 3. No Negative Stock Invariant
        for bin_obj in Bin.objects.all():
            if bin_obj.actual_qty < 0:
                errors.append(
                    f"Negative Stock detected for {bin_obj.item.code} @ {bin_obj.warehouse.name}: Qty {bin_obj.actual_qty}"
                )

        # 4. Perpetual Inventory Balance: the GL inventory account must equal the
        #    stock valuation (Σ Bin.stock_value) for every perpetual-inventory company.
        perpetual_errors = self._check_perpetual_inventory_balance()
        SystemHealthCheck.objects.create(
            check_name="perpetual_inventory_balance",
            status="alert" if perpetual_errors else "ok",
            details=(
                {"errors": perpetual_errors}
                if perpetual_errors
                else {
                    "message": "GL inventory matches Bin stock value for all perpetual-inventory companies."
                }
            ),
        )
        errors.extend(perpetual_errors)

        # 5. GST invariants (totals identity, tax rows vs lines, GL vs document...).
        #    Warnings (bad GSTINs, missing HSN, legacy documents) never fail the run.
        gst_errors, gst_warnings = gst_integrity.check()
        SystemHealthCheck.objects.create(
            check_name="gst_integrity",
            status="alert" if gst_errors else "ok",
            details={
                "errors": gst_errors,
                "warnings": gst_warnings,
                "message": "" if gst_errors else "GST totals, tax rows and GL agree.",
            },
        )
        errors.extend(gst_errors)
        for warning in gst_warnings:
            self.stdout.write(self.style.WARNING(f"[GST warning] {warning}"))

        # 6. Invoice outstanding / payment status: the stored projection must equal the
        #    value derived from the GL rows tagged against each invoice.
        outstanding_errors = outstanding_service.check_integrity()
        SystemHealthCheck.objects.create(
            check_name="invoice_outstanding",
            status="alert" if outstanding_errors else "ok",
            details=(
                {"errors": outstanding_errors}
                if outstanding_errors
                else {
                    "message": "Invoice outstanding amounts and statuses match the ledger."
                }
            ),
        )
        errors.extend(outstanding_errors)

        # 7. Batch invariants: batch present iff the item is batch-tracked, the
        #    batch belongs to the item, no negative batch balance and each
        #    batch-tracked item's batches sum to its Bin. Expired stock is a
        #    warning only.
        batch_errors = batch_service.integrity_errors()
        batch_warnings = batch_service.integrity_warnings()
        SystemHealthCheck.objects.create(
            check_name="batch_integrity",
            status="alert" if batch_errors else "ok",
            details={
                "errors": batch_errors,
                "warnings": batch_warnings,
                "message": "" if batch_errors else "Batch balances and ledger rows agree.",
            },
        )
        errors.extend(batch_errors)
        for warning in batch_warnings:
            self.stdout.write(self.style.WARNING(f"[batch warning] {warning}"))

        # 8. Manufacturing: each Manufacture/Repack voucher conserves value, the
        #    header completed quantity equals its finished rows, and every Work
        #    Order projection matches the ledger (design §7).
        from manufacturing.services import integrity as manufacturing_integrity

        manu_errors, manu_warnings = manufacturing_integrity.check()
        SystemHealthCheck.objects.create(
            check_name="manufacturing_integrity",
            status="alert" if manu_errors else "ok",
            details={
                "errors": manu_errors,
                "warnings": manu_warnings,
                "message": "" if manu_errors else "Manufacturing vouchers and Work Order progress agree.",
            },
        )
        errors.extend(manu_errors)
        for warning in manu_warnings:
            self.stdout.write(self.style.WARNING(f"[manufacturing warning] {warning}"))

        # 9. HR: slip totals vs rows, slip / advance / payment GL, advance
        #    outstanding vs the advance account, overlapping slips.
        from hr.services import integrity as hr_integrity

        hr_errors, hr_warnings = hr_integrity.check()
        SystemHealthCheck.objects.create(
            check_name="hr_integrity",
            status="alert" if hr_errors else "ok",
            details={
                "errors": hr_errors,
                "warnings": hr_warnings,
                "message": "" if hr_errors else "HR slips, advances and payments agree with the ledger.",
            },
        )
        errors.extend(hr_errors)
        for warning in hr_warnings:
            self.stdout.write(self.style.WARNING(f"[hr warning] {warning}"))

        # 10. Attachments: every proof file is present and matches its recorded
        #     size / SHA-256, and still has a parent record.
        from core import attachments as attachment_service

        attachment_errors = attachment_service.check_integrity()
        SystemHealthCheck.objects.create(
            check_name="attachment_integrity",
            status="alert" if attachment_errors else "ok",
            details={
                "errors": attachment_errors,
                "message": "" if attachment_errors else "All attachment files match their records.",
            },
        )
        errors.extend(attachment_errors)

        # 11. Stock Reconciliation: each submitted document's live SLEs sum to its
        #     difference amount; no live reconciliation SLE without a submitted doc.
        from stock.services import reconciliation as reconciliation_service

        reco_errors = reconciliation_service.integrity_errors()
        SystemHealthCheck.objects.create(
            check_name="stock_reconciliation_integrity",
            status="alert" if reco_errors else "ok",
            details={
                "errors": reco_errors,
                "message": "" if reco_errors else "Stock Reconciliations agree with the stock ledger.",
            },
        )
        errors.extend(reco_errors)

        # 12. Tally export log: stored files still match their SHA-256 and counts.
        from tally_export.services import integrity as tally_export_integrity

        tally_errors = tally_export_integrity.check()
        SystemHealthCheck.objects.create(
            check_name="tally_export_integrity",
            status="alert" if tally_errors else "ok",
            details={
                "errors": tally_errors,
                "message": "" if tally_errors else "Tally export files match the export log.",
            },
        )
        errors.extend(tally_errors)

        # 13. Bank reconciliation: every clearance references a submitted voucher
        #     touching its bank account; a matched statement line equals its vouchers.
        from accounts.services import bank as bank_service

        bank_errors = bank_service.check_integrity()
        SystemHealthCheck.objects.create(
            check_name="bank_reconciliation_integrity",
            status="alert" if bank_errors else "ok",
            details={
                "errors": bank_errors,
                "message": "" if bank_errors else "Bank clearances and matches agree with the ledger.",
            },
        )
        errors.extend(bank_errors)

        # 14. Period closing: each submitted Period Closing Voucher's ledger rows
        #     match what it closed. Entries posted after the close are warnings.
        from accounts.services import closing as closing_service

        pcv_errors, pcv_warnings = closing_service.check_integrity()
        SystemHealthCheck.objects.create(
            check_name="period_closing_integrity",
            status="alert" if pcv_errors else "ok",
            details={
                "errors": pcv_errors,
                "warnings": pcv_warnings,
                "message": "" if pcv_errors else "Period Closing Vouchers agree with the ledger.",
            },
        )
        errors.extend(pcv_errors)
        for warning in pcv_warnings:
            self.stdout.write(self.style.WARNING(f"[period closing warning] {warning}"))

        # 15. Payment reconciliation: each submitted entry's live GL rows equal its
        #     allocations and no payment / note is consumed beyond its balance.
        from payment_reconciliation import services as payrecon_service

        payrecon_errors = payrecon_service.integrity_errors()
        SystemHealthCheck.objects.create(
            check_name="payment_reconciliation_integrity",
            status="alert" if payrecon_errors else "ok",
            details={
                "errors": payrecon_errors,
                "message": "" if payrecon_errors else "Payment reconciliations agree with the ledger.",
            },
        )
        errors.extend(payrecon_errors)

        # Record Health Status
        status_str = "alert" if errors else "ok"
        details_payload = (
            {"errors": errors}
            if errors
            else {"message": "All system invariants verified successfully."}
        )

        SystemHealthCheck.objects.create(
            check_name="ledger_and_stock_integrity",
            status=status_str,
            details=details_payload,
        )

        if errors:
            self.stderr.write(
                self.style.ERROR(f"Integrity Check FAILED with {len(errors)} error(s):")
            )
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  - {err}"))
            raise CommandError("ERP Integrity verification failed.")

        self.stdout.write(
            self.style.SUCCESS(
                "[OK] ERP System Ledger & Inventory Integrity Verified (0 Drifts)"
            )
        )

    def _check_perpetual_inventory_balance(self):
        """GL balance per effective warehouse account equals its Bin value.

        Every warehouse posts to its own ``account`` when set (Work In Progress,
        design §14.4) and to the company default inventory account otherwise.
        Comparing per account rather than in aggregate is what catches a WIP
        warehouse posting to the wrong ledger.
        """
        from stock.models import Warehouse

        errors = []
        companies = Company.objects.filter(
            enable_perpetual_inventory=True, default_inventory_account__isnull=False
        ).select_related("default_inventory_account")
        for company in companies:
            default = company.default_inventory_account
            expected = {default.pk: Decimal("0.00")}
            warehouses = Warehouse.objects.filter(company=company).select_related(
                "account"
            )
            for warehouse in warehouses:
                account = warehouse.account or default
                value = (
                    Bin.objects.filter(warehouse=warehouse).aggregate(
                        v=Sum("stock_value")
                    )["v"]
                    or Decimal("0.00")
                )
                expected[account.pk] = expected.get(account.pk, Decimal("0.00")) + value

            for account_pk, stock_value in expected.items():
                gl = GLEntry.objects.filter(
                    account_id=account_pk, is_cancelled=False
                ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
                gl_balance = (gl["dr"] or Decimal("0.00")) - (
                    gl["cr"] or Decimal("0.00")
                )
                voucher_count = (
                    StockLedgerEntry.objects.filter(
                        warehouse__company=company,
                        warehouse__account_id=account_pk,
                        is_cancelled=False,
                    )
                    .values("voucher_type", "voucher_no")
                    .distinct()
                    .count()
                    if account_pk != default.pk
                    else StockLedgerEntry.objects.filter(
                        warehouse__company=company,
                        warehouse__account__isnull=True,
                        is_cancelled=False,
                    )
                    .values("voucher_type", "voucher_no")
                    .distinct()
                    .count()
                )
                # Each voucher may round its valuation by up to a cent.
                tolerance = Decimal("0.01") * voucher_count
                if abs(gl_balance - stock_value) > tolerance:
                    account = self._account_label(account_pk)
                    errors.append(
                        f"Perpetual Inventory Drift for {company.name}: GL balance of "
                        f"'{account}' ({gl_balance}) != Bin stock value ({stock_value}), "
                        f"tolerance {tolerance}"
                    )
        return errors

    @staticmethod
    def _account_label(account_pk):
        from accounts.models import Account

        return Account.objects.filter(pk=account_pk).values_list("name", flat=True).first()
