from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from accounts.models import GLEntry
from stock.models import StockLedgerEntry, Bin
from core.models import SystemHealthCheck


class Command(BaseCommand):
    help = "Verifies all system financial and stock ledger invariants (plan.md §6)."

    def handle(self, *args, **options):
        self.stdout.write("Running ERP Integrity Verification...")
        errors = []

        # 1. GL Balance Invariant
        vouchers = GLEntry.objects.filter(is_cancelled=False).values("voucher_type", "voucher_no").distinct()
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

            expected_qty = (sle_aggregate["sum_qty"] or Decimal("0.000")).quantize(Decimal("0.001"))
            expected_value = (sle_aggregate["sum_value"] or Decimal("0.00")).quantize(Decimal("0.01"))

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

        # Record Health Status
        status_str = "alert" if errors else "ok"
        details_payload = {"errors": errors} if errors else {"message": "All system invariants verified successfully."}

        SystemHealthCheck.objects.create(
            check_name="ledger_and_stock_integrity",
            status=status_str,
            details=details_payload,
        )

        if errors:
            self.stderr.write(self.style.ERROR(f"Integrity Check FAILED with {len(errors)} error(s):"))
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  - {err}"))
            raise CommandError("ERP Integrity verification failed.")

        self.stdout.write(self.style.SUCCESS("[OK] ERP System Ledger & Inventory Integrity Verified (0 Drifts)"))
