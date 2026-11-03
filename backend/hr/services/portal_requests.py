"""Advance requests and issues raised from the employee portal, and the admin
decisions on them.

An approved advance request creates a **draft** ``EmployeeAdvance`` (the admin
submits it to pay out); the request itself posts nothing.  Every state change is
atomic and audited.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.mixins import record_audit_log
from core.models import NamingSeries
from hr.models import (
    AdvanceRequest,
    AdvanceRequestStatus,
    Employee,
    EmployeeAdvance,
    EmployeeIssue,
    EmployeeStatus,
    IssueStatus,
)
from hr.services import advances as advance_service
from hr.services.setup import get_settings

ZERO = Decimal("0.00")
#: An employee cannot flood the admin with open issues.
MAX_OPEN_ISSUES = 20
ADVANCE_NAME_PREFIX = "EMP-ADV-"


# ------------------------------------------------------- advance requests (portal)
def create_advance_request(employee, amount, reason, user):
    if amount is None or amount <= 0:
        raise ValidationError({"amount": _("Amount must be greater than zero.")})
    try:
        with transaction.atomic():
            # Serialise with this employee's other requests so the friendly error
            # below is the normal path; the partial UNIQUE index is the guarantee.
            Employee.objects.select_for_update().get(pk=employee.pk)
            if AdvanceRequest.objects.filter(
                employee=employee, status=AdvanceRequestStatus.PENDING
            ).exists():
                raise ValidationError(_("You already have a pending advance request."))
            req = AdvanceRequest.objects.create(
                employee=employee,
                company=employee.company,
                amount=amount,
                reason=reason,
                created_by=user,
            )
            record_audit_log(
                user,
                "create",
                req,
                changes={"amount": str(amount), "reason": reason},
            )
    except IntegrityError:
        raise ValidationError(_("You already have a pending advance request."))
    return req


def withdraw_advance_request(req, user):
    with transaction.atomic():
        req = AdvanceRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != AdvanceRequestStatus.PENDING:
            raise ValidationError(_("Only a pending request can be withdrawn."))
        req.status = AdvanceRequestStatus.WITHDRAWN
        req.modified_by = user
        req.save(update_fields=["status", "modified_by", "modified_at"])
        record_audit_log(user, "withdraw", req)
    return req


# ------------------------------------------------------- advance requests (admin)
def approve_advance_request(
    req,
    user,
    amount=None,
    paid_from=None,
    instalment_amount=None,
    posting_date=None,
    response="",
):
    """Create the draft ``EmployeeAdvance`` and mark the request Approved."""
    with transaction.atomic():
        req = AdvanceRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != AdvanceRequestStatus.PENDING:
            raise ValidationError(_("Only a pending request can be approved."))
        # Same lock the other HR postings take, so approving cannot interleave
        # with a slip submission for this employee.
        employee = Employee.objects.select_for_update().get(pk=req.employee_id)
        if employee.status != EmployeeStatus.ACTIVE:
            raise ValidationError(_("The employee is not active."))
        company = req.company
        settings_row = get_settings(company)  # clear error when HR is not set up
        paid_from = paid_from or settings_row.default_payment_account
        advance_service.validate_paid_from(paid_from, company)
        amount = req.amount if amount is None else amount
        if amount <= 0:
            raise ValidationError({"amount": _("Amount must be greater than zero.")})
        if instalment_amount is not None and not (0 < instalment_amount <= amount):
            raise ValidationError(
                {
                    "instalment_amount": _(
                        "Instalment must be greater than zero and at most the amount."
                    )
                }
            )
        advance = EmployeeAdvance.objects.create(
            name=NamingSeries.next(ADVANCE_NAME_PREFIX),
            employee=employee,
            company=company,
            posting_date=posting_date or datetime.date.today(),
            amount=amount,
            purpose=req.reason,
            instalment_amount=instalment_amount,
            paid_from=paid_from,
            created_by=user,
        )
        req.status = AdvanceRequestStatus.APPROVED
        req.advance = advance
        req.response = response
        req.reviewed_by = user
        req.reviewed_at = timezone.now()
        req.modified_by = user
        req.save()
        record_audit_log(user, "create", advance)
        record_audit_log(
            user,
            "approve",
            req,
            changes={
                "requested_amount": str(req.amount),
                "approved_amount": str(amount),
                "advance": advance.name,
            },
        )
    return req


def reject_advance_request(req, user, response):
    response = (response or "").strip()
    if not response:
        raise ValidationError({"response": _("Give the employee a reason.")})
    with transaction.atomic():
        req = AdvanceRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != AdvanceRequestStatus.PENDING:
            raise ValidationError(_("Only a pending request can be rejected."))
        req.status = AdvanceRequestStatus.REJECTED
        req.response = response
        req.reviewed_by = user
        req.reviewed_at = timezone.now()
        req.modified_by = user
        req.save()
        record_audit_log(user, "reject", req, changes={"response": response})
    return req


# ------------------------------------------------------------- issues (portal)
def create_issue(
    employee, category, message, user, reference_date=None, reference_doc=None
):
    message = (message or "").strip()
    if not message:
        raise ValidationError({"message": _("Describe the problem.")})
    with transaction.atomic():
        Employee.objects.select_for_update().get(pk=employee.pk)
        if (
            EmployeeIssue.objects.filter(
                employee=employee, status=IssueStatus.OPEN
            ).count()
            >= MAX_OPEN_ISSUES
        ):
            raise ValidationError(
                _("You have too many open issues; wait for a reply first.")
            )
        issue = EmployeeIssue.objects.create(
            employee=employee,
            company=employee.company,
            category=category,
            reference_date=reference_date,
            reference_doc=reference_doc or None,
            message=message,
            created_by=user,
        )
        record_audit_log(
            user, "create", issue, changes={"category": category, "message": message}
        )
    return issue


# -------------------------------------------------------------- issues (admin)
def resolve_issue(issue, user, status, response):
    if status not in (IssueStatus.RESOLVED, IssueStatus.REJECTED):
        raise ValidationError({"status": _("Choose Resolved or Rejected.")})
    response = (response or "").strip()
    if not response:
        raise ValidationError({"response": _("Write a reply to the employee.")})
    with transaction.atomic():
        issue = EmployeeIssue.objects.select_for_update().get(pk=issue.pk)
        if issue.status != IssueStatus.OPEN:
            raise ValidationError(_("This issue is already closed."))
        issue.status = status
        issue.response = response
        issue.resolved_by = user
        issue.resolved_at = timezone.now()
        issue.modified_by = user
        issue.save()
        record_audit_log(
            user, "resolve", issue, changes={"status": status, "response": response}
        )
    return issue
