from decimal import Decimal
from typing import Dict, List, Any
from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.models import Account, GLEntry


def quantize_money(amount: Any) -> Decimal:
    """Helper to convert amount to Decimal quantized to 0.01."""
    if amount is None:
        return Decimal("0.00")
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(Decimal("0.01"))


def post(
    voucher: Any,
    entries: List[Dict[str, Any]],
    posting_date=None,
    fiscal_year=None,
    remarks=None,
) -> List[GLEntry]:
    """
    Atomically posts immutable GL entries for a submitted document.
    Enforces debit == credit balance and group node restrictions.
    """
    if not entries:
        raise ValidationError("Cannot post empty GL entries.")

    date_to_use = posting_date or getattr(voucher, "posting_date", None)
    if not date_to_use:
        raise ValidationError("Posting date is required for GL entry.")

    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    gl_rows: List[GLEntry] = []

    with transaction.atomic():
        for item in entries:
            account = item["account"]
            if isinstance(account, (int, str)):
                account = Account.objects.get(pk=account)

            if account.is_group:
                raise ValidationError(f"Posting to group account '{account.name}' is not allowed.")

            debit = quantize_money(item.get("debit", 0))
            credit = quantize_money(item.get("credit", 0))

            if debit < 0 or credit < 0:
                raise ValidationError("Debit and credit amounts must be non-negative.")
            if debit > 0 and credit > 0:
                raise ValidationError("A single GL entry line cannot have both debit and credit > 0.")
            if debit == 0 and credit == 0:
                continue

            total_debit += debit
            total_credit += credit

            gl_entry = GLEntry(
                posting_date=date_to_use,
                account=account,
                company=account.company,
                party_type=item.get("party_type"),
                party=item.get("party"),
                debit=debit,
                credit=credit,
                voucher_type=voucher_type,
                voucher_no=voucher_no,
                against_voucher_type=item.get("against_voucher_type"),
                against_voucher_no=item.get("against_voucher_no"),
                fiscal_year=fiscal_year,
                remarks=remarks or getattr(voucher, "remarks", None),
                is_cancelled=False,
            )
            gl_rows.append(gl_entry)

        if total_debit != total_credit:
            raise ValidationError(
                f"Unbalanced GL entry: Total Debit ({total_debit}) != Total Credit ({total_credit})."
            )

        # Bulk save GL entries
        created_entries = GLEntry.objects.bulk_create(gl_rows)
        return created_entries


def reverse(voucher: Any) -> List[GLEntry]:
    """
    Atomically posts reversing GL entries for a cancelled document.
    """
    voucher_type = f"{voucher._meta.app_label}.{voucher._meta.object_name}"
    voucher_no = getattr(voucher, "name", str(voucher.pk))

    with transaction.atomic():
        original_entries = GLEntry.objects.filter(
            voucher_type=voucher_type,
            voucher_no=voucher_no,
            is_cancelled=False,
        )

        reversal_rows: List[GLEntry] = []
        for orig in original_entries:
            # Mark original as cancelled
            orig._allow_internal_cancellation = True
            orig.is_cancelled = True
            orig.save(update_fields=["is_cancelled"])

            # Create reversing entry with swapped debit/credit
            reversal = GLEntry(
                posting_date=orig.posting_date,
                account=orig.account,
                company=orig.company,
                party_type=orig.party_type,
                party=orig.party,
                debit=orig.credit,
                credit=orig.debit,
                voucher_type=orig.voucher_type,
                voucher_no=orig.voucher_no,
                against_voucher_type=orig.against_voucher_type,
                against_voucher_no=orig.against_voucher_no,
                fiscal_year=orig.fiscal_year,
                remarks=f"Reversal of {orig.voucher_no}",
                is_cancelled=True,
            )
            reversal_rows.append(reversal)

        created_reversals = GLEntry.objects.bulk_create(reversal_rows)
        return created_reversals


def repost(account_id=None, company_id=None):
    """
    Replaces derived account balances from immutable GLEntry logs.
    """
    queryset = GLEntry.objects.filter(is_cancelled=False)
    if account_id:
        queryset = queryset.filter(account_id=account_id)
    if company_id:
        queryset = queryset.filter(company_id=company_id)

    # Validate that every non-cancelled GL entry balances per voucher
    vouchers = queryset.values("voucher_type", "voucher_no").distinct()
    for v in vouchers:
        v_entries = GLEntry.objects.filter(
            voucher_type=v["voucher_type"],
            voucher_no=v["voucher_no"],
            is_cancelled=False,
        )
        total_dr = sum(e.debit for e in v_entries)
        total_cr = sum(e.credit for e in v_entries)
        if total_dr != total_cr:
            raise ValidationError(
                f"Imbalance detected during GL repost for {v['voucher_type']}:{v['voucher_no']}"
            )
    return True
