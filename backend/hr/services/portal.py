"""Employee portal credentials: PIN rules, the synthetic login user, login with
lockout, PIN change and the admin set / disable operations.

Workers log in with **mobile number + PIN** (no email, no password).  Only a
Django password hash of the PIN is stored (``EmployeePortalAccess.pin_hash``).
To obtain a JWT every portal employee is linked to a synthetic, role-less
``users.User`` whose password is *unusable*, so the normal ``/api/auth/token/``
login can never work for it and RBAC denies every business route.

Login failures never reveal whether a mobile number exists: every failure is
the same generic message, and an unknown number costs the same hashing time as
a wrong PIN.  After :data:`MAX_FAILED_ATTEMPTS` consecutive wrong PINs the access
is locked for :data:`LOCK_MINUTES`.
"""

import datetime
import re

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AuditLog
from hr.models import Employee, EmployeePortalAccess, EmployeeStatus
from users.models import User

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
PIN_RE = re.compile(r"^[0-9]{4,6}$")

#: Outcomes of :func:`authenticate` (the view maps them onto HTTP responses).
OK = "ok"
INVALID = "invalid"
LOCKED = "locked"

# A real PBKDF2 hash used to burn the same time when the number is unknown or the
# portal is disabled, so response time does not reveal which case it was.
_DUMMY_HASH = make_password("0000-dummy")


# ------------------------------------------------------------------ PIN rules
def validate_pin(pin, mobile_no=None):
    """Raise ``ValidationError`` unless ``pin`` is 4-6 digits and not trivial."""
    if not isinstance(pin, str) or not PIN_RE.match(pin):
        raise ValidationError(_("The PIN must be 4 to 6 digits."))
    if len(set(pin)) == 1:
        raise ValidationError(_("This PIN is too easy to guess."))
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if steps == {1} or steps == {9}:  # 1234 / 123456 / 4321 / 7890 style runs
        raise ValidationError(_("This PIN is too easy to guess."))
    for size in range(2, len(pin) // 2 + 1):  # 1212 / 123123 style repeats
        if len(pin) % size == 0 and pin == pin[:size] * (len(pin) // size):
            raise ValidationError(_("This PIN is too easy to guess."))
    digits = re.sub(r"\D", "", mobile_no or "")
    if digits and digits.endswith(pin):
        raise ValidationError(_("The PIN must not be the end of the mobile number."))


# ------------------------------------------------------------ synthetic user
def portal_username(employee):
    return f"emp-{employee.name.lower()}"


def ensure_portal_user(employee):
    """The role-less, unusable-password login user linked to ``employee``.

    Created on first use, re-activated if an earlier ``disable`` switched it off.
    A user that is linked but looks like a real account (staff, superuser, has
    roles or a usable password) is refused rather than silently reused.
    """
    user = employee.user
    if user is not None:
        if (
            user.is_staff
            or user.is_superuser
            or user.has_usable_password()
            or user.user_roles.exists()
        ):
            raise ValidationError(
                _("This employee is linked to a regular user account.")
            )
    else:
        username = portal_username(employee)
        email = f"{username}@portal.invalid"
        if (
            User.objects.filter(username=username).exists()
            or User.objects.filter(email=email).exists()
        ):
            raise ValidationError(_("A login user for this employee already exists."))
        user = User(
            username=username,
            email=email,
            first_name=employee.first_name[:150],
            last_name=employee.last_name[:150],
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        employee.user = user
        employee.save(update_fields=["user", "modified_at"])
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active", "modified_at"])
    return user


# ------------------------------------------------------------- admin actions
def _audit(user, action, employee, changes):
    AuditLog.objects.create(
        user=user,
        action=action,
        model_label="hr.EmployeePortalAccess",
        object_id=employee.name,
        changes=changes,
    )


def set_portal_pin(employee, pin, by_user):
    """Enable the portal with a fresh PIN the employee must change on first login."""
    if not employee.mobile_no:
        raise ValidationError(
            _("Set the employee's mobile number before enabling the portal.")
        )
    if employee.status != EmployeeStatus.ACTIVE:
        raise ValidationError(_("Only an active employee can use the portal."))
    validate_pin(pin, employee.mobile_no)
    with transaction.atomic():
        employee = (
            Employee.objects.select_for_update(of=("self",))
            .select_related("user")
            .get(pk=employee.pk)
        )
        ensure_portal_user(employee)
        (
            access,
            _created,
        ) = EmployeePortalAccess.objects.select_for_update().get_or_create(
            employee=employee, defaults={"pin_hash": make_password(pin)}
        )
        access.pin_hash = make_password(pin)
        access.portal_enabled = True
        access.pin_must_change = True
        access.pin_set_at = timezone.now()
        access.pin_set_by = by_user
        access.failed_attempts = 0
        access.locked_until = None
        access.save()
        # The PIN itself is never written to the audit log.
        _audit(
            by_user,
            "set_portal_pin",
            employee,
            {"portal_enabled": True, "pin_must_change": True},
        )
    return access


def disable_portal(employee, by_user):
    """Switch the portal off; the linked user is deactivated so tokens stop at once."""
    with transaction.atomic():
        employee = (
            Employee.objects.select_for_update(of=("self",))
            .select_related("user")
            .get(pk=employee.pk)
        )
        access = (
            EmployeePortalAccess.objects.select_for_update()
            .filter(employee=employee)
            .first()
        )
        if access is not None and access.portal_enabled:
            access.portal_enabled = False
            access.save(update_fields=["portal_enabled", "modified_at"])
        if employee.user_id and employee.user.is_active:
            employee.user.is_active = False
            employee.user.save(update_fields=["is_active", "modified_at"])
        _audit(by_user, "disable_portal", employee, {"portal_enabled": False})
    return access


# --------------------------------------------------------------------- login
def find_employee_by_mobile(mobile_no):
    """Match the typed number against ``Employee.mobile_no`` (spaces / dashes
    ignored; with or without the +91 / 0 prefix of a 10-digit number)."""
    raw = re.sub(r"[\s\-]", "", mobile_no or "")
    candidates = [raw]
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 10:
        last10 = digits[-10:]
        candidates += [digits, "+" + digits, last10, "+91" + last10, "91" + last10]
    seen = []
    for value in candidates:
        if value and value not in seen:
            seen.append(value)
    return Employee.objects.filter(mobile_no__in=seen).select_related("user").first()


def _eligible(employee, access):
    return (
        employee is not None
        and access is not None
        and access.portal_enabled
        and employee.status == EmployeeStatus.ACTIVE
        and employee.user_id is not None
        and employee.user.is_active
    )


def _register_failure(access, user=None):
    """Count a wrong PIN; lock on reaching the limit. The caller saves ``access``."""
    access.failed_attempts += 1
    if access.failed_attempts >= MAX_FAILED_ATTEMPTS:
        access.locked_until = timezone.now() + datetime.timedelta(minutes=LOCK_MINUTES)
        AuditLog.objects.create(
            user=user,
            action="portal_lockout",
            model_label="hr.EmployeePortalAccess",
            object_id=access.employee.name,
            changes={"failed_attempts": access.failed_attempts},
        )


def _is_locked(access):
    if access.locked_until is None:
        return False
    if timezone.now() < access.locked_until:
        return True
    # The lock has run out: start counting afresh.
    access.locked_until = None
    access.failed_attempts = 0
    return False


def authenticate(mobile_no, pin):
    """Check mobile + PIN. Returns ``(outcome, employee)``; never raises for a
    bad login (a raise inside the transaction would roll the counter back)."""
    employee = find_employee_by_mobile(mobile_no)
    with transaction.atomic():
        access = None
        if employee is not None:
            access = (
                EmployeePortalAccess.objects.select_for_update()
                .select_related("employee")
                .filter(employee=employee)
                .first()
            )
        if not _eligible(employee, access):
            check_password(pin, _DUMMY_HASH)
            return INVALID, None
        if _is_locked(access):
            return LOCKED, None
        if check_password(pin, access.pin_hash):
            access.failed_attempts = 0
            access.locked_until = None
            access.last_login_at = timezone.now()
            access.save(
                update_fields=[
                    "failed_attempts",
                    "locked_until",
                    "last_login_at",
                    "modified_at",
                ]
            )
            return OK, employee
        _register_failure(access, employee.user)
        access.save(update_fields=["failed_attempts", "locked_until", "modified_at"])
        return INVALID, None


def issue_tokens(user):
    """A JWT pair for the portal user, tagged with the ``portal`` claim."""
    refresh = RefreshToken.for_user(user)
    refresh["portal"] = True
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def change_pin(employee, access, current_pin, new_pin):
    """Employee changes their own PIN. Returns OK / INVALID (wrong current PIN) /
    LOCKED; an unacceptable new PIN raises ``ValidationError``."""
    validate_pin(new_pin, employee.mobile_no)
    with transaction.atomic():
        access = EmployeePortalAccess.objects.select_for_update().get(pk=access.pk)
        if _is_locked(access):
            return LOCKED
        if not check_password(current_pin, access.pin_hash):
            _register_failure(access, employee.user)
            access.save(
                update_fields=["failed_attempts", "locked_until", "modified_at"]
            )
            return INVALID
        if current_pin == new_pin:
            raise ValidationError(_("The new PIN must differ from the current PIN."))
        access.pin_hash = make_password(new_pin)
        access.pin_must_change = False
        access.pin_set_at = timezone.now()
        access.failed_attempts = 0
        access.locked_until = None
        access.save()
        AuditLog.objects.create(
            user=employee.user,
            action="portal_change_pin",
            model_label="hr.EmployeePortalAccess",
            object_id=employee.name,
            changes={"pin_must_change": False},
        )
    return OK
