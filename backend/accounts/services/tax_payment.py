"""Tax Payment posting and cancellation service.

Creates and submits a balanced Journal Entry for GST or TDS tax payments.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from accounts.models import Account, JournalEntry, JournalEntryLine
from core.models import DocStatus, NamingSeries
from gst.constants import GSTComponent, GSTDirection

ZERO = Decimal("0.00")


def get_gst_output_account(company, component: str) -> Account:
    acc = Account.objects.filter(
        company=company,
        gst_direction=GSTDirection.OUTPUT,
        gst_component=component,
    ).first()
    if not acc:
        raise ValidationError(
            _("No Output GST account found for %(component)s in company '%(company)s'.")
            % {"component": component, "company": company.name}
        )
    return acc


def get_gst_input_account(company, component: str) -> Account:
    acc = Account.objects.filter(
        company=company,
        gst_direction=GSTDirection.INPUT,
        gst_component=component,
    ).first()
    if not acc:
        raise ValidationError(
            _("No Input GST account found for %(component)s in company '%(company)s'.")
            % {"component": component, "company": company.name}
        )
    return acc


def get_tds_payable_account(company) -> Account:
    try:
        from hr.models import HRSettings

        settings = HRSettings.objects.filter(company=company).first()
        if settings and getattr(settings, "tds_payable_account", None):
            return settings.tds_payable_account
    except Exception:
        pass

    acc = Account.objects.filter(company=company, name__icontains="TDS Payable").first()
    if acc:
        return acc
    raise ValidationError(
        _("No TDS Payable account found for company '%(company)s'.")
        % {"company": company.name}
    )


def submit_tax_payment(tax_payment):
    """Create and submit a balanced JournalEntry for the given TaxPayment."""
    if tax_payment.tax_type == "GST":
        total_cash = tax_payment.igst_cash + tax_payment.cgst_cash + tax_payment.sgst_cash
    else:
        total_cash = tax_payment.tds_amount

    if total_cash > 0:
        if not tax_payment.paid_from:
            raise ValidationError(_("Paid From account is required when cash is paid."))
        if tax_payment.paid_from.company_id != tax_payment.company_id:
            raise ValidationError(
                _("Paid From account '%(account)s' does not belong to company '%(company)s'.")
                % {
                    "account": tax_payment.paid_from.name,
                    "company": tax_payment.company.name,
                }
            )
        if tax_payment.paid_from.is_group:
            raise ValidationError(_("Paid From account cannot be a group account."))

    je = JournalEntry.objects.create(
        name=NamingSeries.next("JV-2026-"),
        company=tax_payment.company,
        posting_date=tax_payment.posting_date,
        voucher_type=(
            "Bank Entry"
            if tax_payment.paid_from and tax_payment.paid_from.account_type == "Bank"
            else "Journal Entry"
        ),
        cheque_no=tax_payment.challan_no,
        cheque_date=tax_payment.posting_date,
        user_remark=(
            f"Tax Payment {tax_payment.tax_type} for period "
            f"{tax_payment.period.strftime('%Y-%m')} challan {tax_payment.challan_no}"
        ),
    )

    lines = []
    if tax_payment.tax_type == "GST":
        # 1. Cash part: Dr Output GST, Cr Bank/Cash
        head_cash = [
            (GSTComponent.IGST, tax_payment.igst_cash),
            (GSTComponent.CGST, tax_payment.cgst_cash),
            (GSTComponent.SGST, tax_payment.sgst_cash),
        ]
        for comp, cash in head_cash:
            if cash > 0:
                out_acc = get_gst_output_account(tax_payment.company, comp)
                lines.append(
                    JournalEntryLine(
                        journal_entry=je,
                        account=out_acc,
                        debit=cash,
                        credit=ZERO,
                    )
                )
        if total_cash > 0:
            lines.append(
                JournalEntryLine(
                    journal_entry=je,
                    account=tax_payment.paid_from,
                    debit=ZERO,
                    credit=total_cash,
                )
            )

        # 2. Credit used part: Dr Output GST, Cr Input GST
        head_credit = [
            (GSTComponent.IGST, tax_payment.igst_credit_used),
            (GSTComponent.CGST, tax_payment.cgst_credit_used),
            (GSTComponent.SGST, tax_payment.sgst_credit_used),
        ]
        for comp, cred in head_credit:
            if cred > 0:
                out_acc = get_gst_output_account(tax_payment.company, comp)
                in_acc = get_gst_input_account(tax_payment.company, comp)
                lines.append(
                    JournalEntryLine(
                        journal_entry=je,
                        account=out_acc,
                        debit=cred,
                        credit=ZERO,
                    )
                )
                lines.append(
                    JournalEntryLine(
                        journal_entry=je,
                        account=in_acc,
                        debit=ZERO,
                        credit=cred,
                    )
                )
    elif tax_payment.tax_type == "TDS":
        tds_acc = get_tds_payable_account(tax_payment.company)
        lines.append(
            JournalEntryLine(
                journal_entry=je,
                account=tds_acc,
                debit=tax_payment.tds_amount,
                credit=ZERO,
            )
        )
        lines.append(
            JournalEntryLine(
                journal_entry=je,
                account=tax_payment.paid_from,
                debit=ZERO,
                credit=tax_payment.tds_amount,
            )
        )

    JournalEntryLine.objects.bulk_create(lines)

    # Proof handling for JournalEntry
    if tax_payment.company.require_proof:
        from core.models import Attachment

        tax_attachments = Attachment.objects.filter(
            model_label="accounts.TaxPayment",
            object_id=str(tax_payment.pk),
        )
        for att in tax_attachments:
            Attachment.objects.create(
                model_label="accounts.JournalEntry",
                object_id=str(je.pk),
                file=att.file,
                original_filename=att.original_filename,
                content_type=att.content_type,
                size=att.size,
                sha256=att.sha256,
                description=att.description,
                uploaded_by=att.uploaded_by,
            )

    from accounts.models import TaxPayment

    je.submit()
    tax_payment.journal_entry = je
    TaxPayment.objects.filter(pk=tax_payment.pk).update(journal_entry=je)


def cancel_tax_payment(tax_payment):
    """Cancel the underlying JournalEntry when TaxPayment is cancelled."""
    if (
        tax_payment.journal_entry
        and tax_payment.journal_entry.docstatus == DocStatus.SUBMITTED
    ):
        tax_payment.journal_entry.cancel()
