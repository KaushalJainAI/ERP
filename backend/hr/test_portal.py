"""Employee self-service portal tests.  Run with ``pytest hr/test_portal.py``."""

import datetime
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from core.models import AuditLog, DocStatus
from hr.models import (
    AdvanceRequest,
    AdvanceRequestStatus,
    EmployeeAdvance,
    EmployeeIssue,
    EmployeePortalAccess,
    EmployeeStatus,
    IssueStatus,
)
from hr.seed import HR_ROLE_MATRIX
from hr.services import portal as portal_service
from hr.tests import HRBase
from users.models import Role, RolePermission, User, UserRole

d = datetime.date
API = "/api/hr/"
PORTAL = API + "portal/"
GENERIC = "Invalid mobile number or PIN."


class PortalBase(HRBase):
    def setUp(self):
        super().setUp()
        cache.clear()  # throttle counters live in the cache
        self.mobile = "9876543210"
        self.w = self.employee("Ramu", mobile_no=self.mobile)
        self.rate(self.w, daily="500", ot="60")
        self.pin = "4827"
        self.new_pin = "7391"

    # ------------------------------------------------------------- helpers
    def role_client(self, name, role_name):
        user = User.objects.create_user(username=name, email=f"{name}@x.com")
        role, created = Role.objects.get_or_create(name=role_name)
        UserRole.objects.create(user=user, role=role)
        if created or not role.permissions.exists():
            for label, flags in HR_ROLE_MATRIX[role_name].items():
                RolePermission.objects.create(
                    role=role, model_label=label, **{f: True for f in flags}
                )
        client = APIClient()
        client.force_authenticate(user)
        return client

    def set_pin(self, emp=None, pin=None, client=None, expect=200):
        emp = emp or self.w
        r = (client or self.api).post(
            f"{API}employees/{emp.pk}/set-portal-pin/",
            {"pin": pin or self.pin},
            format="json",
        )
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def login(self, mobile=None, pin=None):
        return APIClient().post(
            PORTAL + "login/",
            {"mobile_no": mobile or self.mobile, "pin": pin or self.pin},
            format="json",
        )

    def portal_client(self, emp=None, change_pin=True, pin=None):
        """Enable the portal for ``emp``, log in and (by default) change the PIN."""
        emp = emp or self.w
        pin = pin or self.pin
        self.set_pin(emp, pin)
        r = self.login(emp.mobile_no, pin)
        self.assertEqual(r.status_code, 200, r.content)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + r.json()["access"])
        if change_pin:
            r = client.post(
                PORTAL + "change-pin/",
                {"current_pin": pin, "new_pin": self.new_pin},
                format="json",
            )
            self.assertEqual(r.status_code, 200, r.content)
        return client

    def submitted_slip(self, emp, start, end):
        self.present(emp, start, end)
        slip = self.make_slip(emp, start, end)
        self.submit_slip(slip["id"])
        return slip

    def other_employee(self, label="Sita", mobile="9123456780"):
        emp = self.employee(label, mobile_no=mobile)
        self.rate(emp, daily="400", ot="50")
        return emp


# ======================================================== credentials / admin
class PortalCredentialTests(PortalBase):
    def test_set_pin_creates_synthetic_role_less_user_and_audits_without_pin(self):
        r = self.set_pin()
        self.assertEqual(r.json(), {"portal_enabled": True, "pin_must_change": True})
        self.assertNotIn(self.pin, r.content.decode())
        self.w.refresh_from_db()
        user = self.w.user
        self.assertEqual(user.username, f"emp-{self.w.name.lower()}")
        self.assertEqual(user.email, f"{user.username}@portal.invalid")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(user.user_roles.exists())
        access = self.w.portal_access
        self.assertTrue(access.portal_enabled and access.pin_must_change)
        self.assertNotEqual(access.pin_hash, self.pin)
        self.assertTrue(check_password(self.pin, access.pin_hash))
        log = AuditLog.objects.get(
            action="set_portal_pin", model_label="hr.EmployeePortalAccess"
        )
        self.assertEqual(log.object_id, self.w.name)
        self.assertEqual(log.user, self.admin)
        self.assertNotIn(self.pin, str(log.changes))
        self.assertNotIn(access.pin_hash, str(log.changes))

    def test_set_pin_again_resets_lockout_and_forces_change(self):
        client = self.portal_client()
        access = EmployeePortalAccess.objects.get(employee=self.w)
        access.failed_attempts, access.locked_until = 5, timezone.now() + datetime.timedelta(minutes=9)
        access.save()
        self.assertFalse(access.pin_must_change)
        self.set_pin(pin="5926")
        access.refresh_from_db()
        self.assertEqual((access.failed_attempts, access.locked_until), (0, None))
        self.assertTrue(access.pin_must_change)
        self.assertEqual(self.login(pin="5926").status_code, 200)
        self.assertEqual(self.login(pin=self.new_pin).status_code, 401)
        self.assertEqual(User.objects.filter(username__startswith="emp-").count(), 1)
        self.assertIsNotNone(client)

    def test_requires_mobile_and_active_employee(self):
        no_mobile = self.employee("NoPhone")
        r = self.set_pin(no_mobile, expect=400)
        self.assertIn("mobile", str(r.json()).lower())
        self.assertFalse(EmployeePortalAccess.objects.exists())
        left = self.employee("Gone", mobile_no="9000000001")
        left.status = EmployeeStatus.LEFT
        left.relieving_date = d(2026, 3, 1)
        left.save()
        self.set_pin(left, expect=400)

    def test_trivial_and_malformed_pins_rejected(self):
        for pin in (
            "1111",
            "0000",
            "123456",
            "1234",
            "4321",
            "654321",
            "7890",
            "1212",
            "123123",
            "12",
            "123",
            "1234567",
            "12a4",
            "    ",
            "3210",  # tail of the mobile number
        ):
            self.set_pin(pin=pin, expect=400)
        self.assertFalse(EmployeePortalAccess.objects.exists())
        self.assertFalse(User.objects.filter(username__startswith="emp-").exists())
        for pin in ("4827", "951357", "5926"):
            self.set_pin(pin=pin)

    def test_hr_user_cannot_set_or_disable_but_manager_can(self):
        hr_user = self.role_client("hru", "HR User")
        self.set_pin(client=hr_user, expect=403)
        self.assertFalse(EmployeePortalAccess.objects.exists())
        manager = self.role_client("hrm", "HR Manager")
        self.set_pin(client=manager)
        self.assertEqual(
            hr_user.post(f"{API}employees/{self.w.pk}/disable-portal/").status_code, 403
        )
        self.assertEqual(
            manager.post(f"{API}employees/{self.w.pk}/disable-portal/").status_code, 200
        )

    def test_employee_list_exposes_portal_status_only(self):
        self.set_pin()
        row = self.api.get(f"{API}employees/{self.w.pk}/").json()
        self.assertTrue(row["portal_enabled"])
        self.assertTrue(row["portal_pin_must_change"])
        self.assertFalse(row["portal_locked"])
        self.assertNotIn("pin_hash", row)
        self.assertNotIn("pin", row)

    def test_db_constraint_enabled_requires_pin(self):
        access = EmployeePortalAccess(employee=self.w, portal_enabled=True, pin_hash="")
        with self.assertRaises(IntegrityError), transaction.atomic():
            access.save()


# ================================================================= login
class PortalLoginTests(PortalBase):
    def test_login_success_shape_and_portal_claim(self):
        self.set_pin()
        r = self.login()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(
            set(body), {"access", "refresh", "pin_must_change", "employee"}
        )
        self.assertTrue(body["pin_must_change"])
        self.assertEqual(
            body["employee"],
            {
                "name": self.w.name,
                "employee_name": "Ramu",
                "company": self.company.name,
            },
        )
        self.assertTrue(AccessToken(body["access"])["portal"])
        self.w.refresh_from_db()
        self.assertEqual(
            AccessToken(body["access"])["user_id"], self.w.user.pk
        )
        self.assertIsNotNone(
            EmployeePortalAccess.objects.get(employee=self.w).last_login_at
        )

    def test_mobile_formats_are_matched(self):
        self.set_pin()
        for typed in ("98765 43210", "+919876543210", "09876543210", "98765-43210"):
            self.assertEqual(self.login(typed).status_code, 200, typed)

    def test_every_failure_is_identical(self):
        self.set_pin()
        left = self.employee("Gone", mobile_no="9000000002")
        self.set_pin(left)
        left.status = EmployeeStatus.LEFT
        left.relieving_date = d(2026, 4, 1)
        left.save()
        disabled = self.employee("Off", mobile_no="9000000003")
        self.set_pin(disabled)
        self.api.post(f"{API}employees/{disabled.pk}/disable-portal/")
        never = self.employee("Never", mobile_no="9000000004")
        orphan = self.employee("Orphan", mobile_no="9000000005")
        self.set_pin(orphan)
        Employee_ = type(orphan)
        Employee_.objects.filter(pk=orphan.pk).update(user=None)
        cases = [
            self.login(self.mobile, "9999"),  # wrong PIN
            self.login("9111111111", self.pin),  # unknown number
            self.login("9000000002", self.pin),  # Left
            self.login("9000000003", self.pin),  # portal disabled
            self.login("9000000004", self.pin),  # no portal access row
            self.login("9000000005", self.pin),  # no linked user
        ]
        self.assertEqual(never.portal_access if False else None, None)
        for r in cases:
            self.assertEqual(r.status_code, 401, r.content)
            self.assertEqual(r.json()["detail"], GENERIC)
            self.assertEqual(r.json()["code"], "invalid_credentials")
        self.assertEqual(len({str(r.json()) for r in cases}), 1)

    def test_lockout_after_five_failures_and_unlock_after_fifteen_minutes(self):
        self.set_pin()
        for _ in range(5):
            r = self.login(pin="9999")
            self.assertEqual(r.status_code, 401)
        # locked: even the right PIN is refused, with a generic locked message
        r = self.login()
        self.assertEqual(r.status_code, 429, r.content)
        self.assertEqual(r.json()["code"], "locked")
        self.assertNotIn("PIN is", r.json()["detail"])
        self.assertEqual(
            AuditLog.objects.filter(action="portal_lockout").count(), 1
        )
        row = self.api.get(f"{API}employees/{self.w.pk}/").json()
        self.assertTrue(row["portal_locked"])
        later = timezone.now() + datetime.timedelta(minutes=14)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.login().status_code, 429)
        later = timezone.now() + datetime.timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.login().status_code, 200)
        access = EmployeePortalAccess.objects.get(employee=self.w)
        self.assertEqual((access.failed_attempts, access.locked_until), (0, None))

    def test_counter_resets_on_success_and_lock_expiry_restarts_count(self):
        self.set_pin()
        for _ in range(4):
            self.assertEqual(self.login(pin="9999").status_code, 401)
        self.assertEqual(self.login().status_code, 200)  # resets the counter
        cache.clear()  # keep the per-IP login throttle out of this test
        for _ in range(4):
            self.assertEqual(self.login(pin="9999").status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        # after a lock expires, one new failure does not re-lock
        cache.clear()
        for _ in range(5):
            self.login(pin="9999")
        later = timezone.now() + datetime.timedelta(minutes=20)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.login(pin="9999").status_code, 401)
            self.assertEqual(self.login().status_code, 200)

    def test_unknown_number_does_not_create_state(self):
        self.assertEqual(self.login("9111111111").status_code, 401)
        self.assertFalse(EmployeePortalAccess.objects.exists())

    def test_login_is_throttled(self):
        statuses = [self.login("9111111111").status_code for _ in range(12)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10], 429)

    def test_stale_token_in_header_does_not_break_login(self):
        self.set_pin()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        r = client.post(
            PORTAL + "login/",
            {"mobile_no": self.mobile, "pin": self.pin},
            format="json",
        )
        self.assertEqual(r.status_code, 200)

    def test_missing_fields_are_400(self):
        r = APIClient().post(PORTAL + "login/", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_normal_token_login_is_impossible_for_portal_user(self):
        self.set_pin()
        user = self.w.__class__.objects.get(pk=self.w.pk).user
        client = APIClient()
        for body in (
            {"username": user.username, "password": self.pin},
            {"username": user.username, "password": ""},
            {"username": user.username, "password": user.password},
            {"username": user.email, "password": self.pin},
        ):
            r = client.post("/api/auth/token/", body, format="json")
            self.assertIn(r.status_code, (400, 401), body)
            self.assertNotIn("access", r.json())
        cache.clear()

    def test_refresh_token_keeps_portal_claim(self):
        self.set_pin()
        refresh = self.login().json()["refresh"]
        r = APIClient().post("/api/auth/token/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(AccessToken(r.json()["access"])["portal"])

    def test_disable_portal_stops_existing_tokens(self):
        client = self.portal_client()
        self.assertEqual(client.get(PORTAL + "me/").status_code, 200)
        r = self.api.post(f"{API}employees/{self.w.pk}/disable-portal/")
        self.assertEqual(r.json(), {"portal_enabled": False})
        self.assertIn(client.get(PORTAL + "me/").status_code, (401, 403))
        self.assertEqual(self.login(pin=self.new_pin).status_code, 401)
        self.assertTrue(
            AuditLog.objects.filter(
                action="disable_portal", object_id=self.w.name
            ).exists()
        )
        # re-enabling works again and re-activates the same user
        self.set_pin(pin="5926")
        self.assertEqual(self.login(pin="5926").status_code, 200)
        self.assertEqual(User.objects.filter(username__startswith="emp-").count(), 1)

    def test_employee_who_left_is_denied_even_with_a_live_token(self):
        client = self.portal_client()
        self.assertEqual(client.get(PORTAL + "me/").status_code, 200)
        self.w.status = EmployeeStatus.LEFT
        self.w.relieving_date = d(2026, 5, 1)
        self.w.save()
        self.assertEqual(client.get(PORTAL + "me/").status_code, 403)
        self.assertEqual(client.get(PORTAL + "summary/").status_code, 403)


# ============================================================ PIN change gate
class PortalPinChangeTests(PortalBase):
    URLS = (
        "summary/",
        "attendance/",
        "salary-slips/",
        "payments/",
        "advances/",
        "advance-requests/",
        "issues/",
    )

    def test_gate_until_pin_changed(self):
        client = self.portal_client(change_pin=False)
        me = client.get(PORTAL + "me/")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["pin_must_change"])
        for path in self.URLS:
            r = client.get(PORTAL + path)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["code"], "pin_change_required", path)
        for path in ("advance-requests/", "issues/"):
            r = client.post(PORTAL + path, {}, format="json")
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["code"], "pin_change_required")

        r = client.post(
            PORTAL + "change-pin/",
            {"current_pin": self.pin, "new_pin": self.new_pin},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(client.get(PORTAL + "me/").json()["pin_must_change"])
        for path in self.URLS:
            self.assertEqual(client.get(PORTAL + path).status_code, 200, path)
        # the new PIN works, the old one does not, and the flag is gone at login
        self.assertEqual(self.login(pin=self.pin).status_code, 401)
        ok = self.login(pin=self.new_pin)
        self.assertEqual(ok.status_code, 200)
        self.assertFalse(ok.json()["pin_must_change"])
        self.assertTrue(
            AuditLog.objects.filter(action="portal_change_pin").exists()
        )

    def test_change_pin_validation(self):
        client = self.portal_client(change_pin=False)

        def change(cur, new):
            return client.post(
                PORTAL + "change-pin/",
                {"current_pin": cur, "new_pin": new},
                format="json",
            )

        self.assertEqual(change("0000", self.new_pin).status_code, 400)  # wrong current
        for bad in ("1111", "1234", "12", "abcd", "3210", self.pin):
            self.assertEqual(change(self.pin, bad).status_code, 400, bad)
        self.assertTrue(
            EmployeePortalAccess.objects.get(employee=self.w).pin_must_change
        )
        self.assertEqual(change(self.pin, self.new_pin).status_code, 200)

    def test_wrong_current_pin_five_times_locks(self):
        client = self.portal_client(change_pin=False)
        for _ in range(5):
            r = client.post(
                PORTAL + "change-pin/",
                {"current_pin": "0000", "new_pin": self.new_pin},
                format="json",
            )
            self.assertEqual(r.status_code, 400)
        r = client.post(
            PORTAL + "change-pin/",
            {"current_pin": self.pin, "new_pin": self.new_pin},
            format="json",
        )
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.login().status_code, 429)


# ================================================================ read access
class PortalReadTests(PortalBase):
    def test_me_has_no_pii_and_current_rates(self):
        dept = self.api.post(API + "departments/", {"name": "Spice"}, format="json").json()
        self.w.department_id = dept["id"]
        self.w.aadhaar = "123456789012"
        self.w.pan = "ABCDE1234F"
        self.w.bank_account_no = "00112233445566"
        self.w.save()
        client = self.portal_client()
        body = client.get(PORTAL + "me/").json()
        self.assertEqual(
            set(body),
            {
                "name",
                "employee_name",
                "mobile_no",
                "department",
                "designation",
                "branch",
                "date_of_joining",
                "pay_type",
                "current_daily_rate",
                "current_monthly_salary",
                "current_ot_hourly_rate",
                "pin_must_change",
            },
        )
        self.assertEqual(body["name"], self.w.name)
        self.assertEqual(body["department"], "Spice")
        self.assertIsNone(body["designation"])
        self.assertEqual(body["current_daily_rate"], "500.00")
        self.assertIsNone(body["current_monthly_salary"])
        self.assertEqual(body["current_ot_hourly_rate"], "60.00")
        self.assertEqual(body["pay_type"], "Daily")
        text = str(body)
        for secret in ("123456789012", "ABCDE1234F", "00112233445566", "pin_hash"):
            self.assertNotIn(secret, text)

    def test_summary_matches_admin_summary(self):
        self.advance(self.w, "1000", instalment="400")
        self.present(self.w, d(2026, 4, 1), d(2026, 4, 6))
        slip = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(slip["id"])
        client = self.portal_client()
        q = "?from=2026-04-01&to=2026-04-30"
        mine = client.get(PORTAL + "summary/" + q)
        self.assertEqual(mine.status_code, 200, mine.content)
        admin = self.api.get(f"{API}employees/{self.w.pk}/summary/{q}").json()
        self.assertEqual(mine.json(), admin)
        self.assertEqual(mine.json()["days_present"], 6)
        self.assertEqual(mine.json()["advance_outstanding"], "600.00")
        # default range is the current month
        today = datetime.date.today()
        default = client.get(PORTAL + "summary/").json()
        self.assertEqual(default["from_date"], str(today.replace(day=1)))
        self.assertEqual(default["to_date"], str(today))
        self.assertEqual(
            client.get(PORTAL + "summary/?from=2026-05-01&to=2026-04-01").status_code,
            400,
        )
        self.assertEqual(client.get(PORTAL + "summary/?from=garbage").status_code, 400)

    def test_attendance_list_and_range(self):
        self.att(self.w, d(2026, 4, 1), "Present", ot="2")
        self.att(self.w, d(2026, 4, 2), "Absent")
        self.att(self.w, d(2026, 5, 3), "Half Day")
        client = self.portal_client()
        rows = client.get(PORTAL + "attendance/?from=2026-04-01&to=2026-04-30").json()
        self.assertEqual(
            rows,
            [
                {"date": "2026-04-01", "status": "Present", "ot_hours": "2.00", "remarks": ""},
                {"date": "2026-04-02", "status": "Absent", "ot_hours": "0.00", "remarks": ""},
            ],
        )
        self.assertEqual(
            client.get(PORTAL + "attendance/?from=2025-01-01&to=2026-12-31").status_code,
            400,
        )

    def test_salary_slips_list_detail_and_payments(self):
        self.advance(self.w, "1000", instalment="400")
        self.present(self.w, d(2026, 4, 1), d(2026, 4, 6))
        self.att(self.w, d(2026, 4, 7), "Present", ot="2")
        draft = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 7))
        client = self.portal_client()
        self.assertEqual(client.get(PORTAL + "salary-slips/").json(), [])  # draft hidden
        self.assertEqual(
            client.get(PORTAL + f"salary-slips/{draft['name']}/").status_code, 404
        )
        self.submit_slip(draft["id"])
        listing = client.get(PORTAL + "salary-slips/").json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(
            set(listing[0]),
            {
                "name",
                "posting_date",
                "start_date",
                "end_date",
                "payment_days",
                "ot_hours",
                "gross_pay",
                "total_deduction",
                "net_pay",
            },
        )
        detail = client.get(PORTAL + f"salary-slips/{draft['name']}/").json()
        self.assertEqual(detail["gross_pay"], "3620.00")  # 7 x 500 + 2h x 60
        self.assertEqual(detail["total_deduction"], "400.00")
        self.assertEqual(detail["net_pay"], "3220.00")
        self.assertEqual(detail["ot_hours"], "2.00")
        rows = {r["component"]: r for r in detail["rows"]}
        self.assertEqual(rows["Wages"]["type"], "Earning")
        self.assertEqual(rows["Wages"]["amount"], "3500.00")
        self.assertEqual(rows["Advance Recovery"]["type"], "Deduction")
        self.assertEqual(
            set(detail["rows"][0]), {"component", "type", "amount", "remarks"}
        )
        # a cancelled slip disappears
        self.api.post(f"{API}salary-slips/{draft['id']}/cancel/")
        self.assertEqual(client.get(PORTAL + "salary-slips/").json(), [])

    def test_payments_and_advances(self):
        self.advance(self.w, "1000", instalment="400")
        self.advance(self.w, "700", date=d(2026, 4, 2), submit=False)  # draft: hidden
        self.present(self.w, d(2026, 4, 1), d(2026, 4, 6))
        slip = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(slip["id"])
        pay = self.api.post(
            API + "employee-payments/",
            {
                "employee": self.w.pk,
                "company": self.company.pk,
                "posting_date": "2026-04-08",
                "amount": "1000.00",
                "paid_from": self.cash.pk,
                "mode": "UPI",
                "reference_no": "UTR123",
            },
            format="json",
        ).json()
        self.api.post(f"{API}employee-payments/{pay['id']}/submit/")
        client = self.portal_client()
        pays = client.get(PORTAL + "payments/").json()
        self.assertEqual(
            pays,
            [
                {
                    "name": pay["name"],
                    "posting_date": "2026-04-08",
                    "amount": "1000.00",
                    "mode": "UPI",
                    "reference_no": "UTR123",
                }
            ],
        )
        advs = client.get(PORTAL + "advances/").json()
        self.assertEqual(len(advs), 1)
        self.assertEqual(
            set(advs[0]),
            {
                "name",
                "posting_date",
                "amount",
                "recovered_amount",
                "outstanding",
                "status",
                "purpose",
            },
        )
        self.assertEqual(advs[0]["amount"], "1000.00")
        self.assertEqual(advs[0]["recovered_amount"], "400.00")
        self.assertEqual(advs[0]["outstanding"], "600.00")
        self.assertEqual(advs[0]["status"], "Partly Recovered")


# ====================================================================== RBAC
class PortalIsolationTests(PortalBase):
    def test_portal_user_is_denied_on_every_business_route(self):
        client = self.portal_client()
        for path in (
            API + "employees/",
            API + f"employees/{self.w.pk}/",
            API + f"employees/{self.w.pk}/summary/",
            API + "attendance/",
            API + "salary-slips/",
            API + "employee-payments/",
            API + "employee-advances/",
            API + "wage-rates/",
            API + "settings/",
            API + "advance-requests/",
            API + "employee-issues/",
            API + "portal-overview/",
            "/api/auth/users/",
            "/api/auth/roles/",
            "/api/accounts/accounts/",
        ):
            r = client.get(path)
            self.assertIn(r.status_code, (403, 404), path)
        self.assertEqual(
            client.post(API + "departments/", {"name": "X"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            client.post(f"{API}employees/{self.w.pk}/set-portal-pin/", {"pin": "5926"}, format="json").status_code,
            403,
        )

    def test_business_users_and_anonymous_are_denied_on_portal_routes(self):
        for path in ("me/", "summary/", "salary-slips/", "advance-requests/", "issues/"):
            self.assertIn(APIClient().get(PORTAL + path).status_code, (401, 403))
            self.assertEqual(self.api.get(PORTAL + path).status_code, 403, path)
            hr = self.role_client(f"hr{path[:3]}", "HR Manager")
            self.assertEqual(hr.get(PORTAL + path).status_code, 403, path)

    def test_employee_a_never_sees_employee_b(self):
        a, b = self.w, self.other_employee()
        slip_a = self.submitted_slip(a, d(2026, 4, 1), d(2026, 4, 3))
        slip_b = self.submitted_slip(b, d(2026, 4, 1), d(2026, 4, 5))
        pay_b = self.api.post(
            API + "employee-payments/",
            {
                "employee": b.pk,
                "company": self.company.pk,
                "posting_date": "2026-04-08",
                "amount": "100.00",
                "paid_from": self.cash.pk,
            },
            format="json",
        ).json()
        self.api.post(f"{API}employee-payments/{pay_b['id']}/submit/")
        adv_b = self.advance(b, "900")
        self.att(b, d(2026, 5, 1), "Present")
        req_b = AdvanceRequest.objects.create(
            employee=b, company=self.company, amount=D("250"), reason="b"
        )
        issue_b = EmployeeIssue.objects.create(
            employee=b, company=self.company, category="Other", message="b issue"
        )
        client_a = self.portal_client(a)

        names = [s["name"] for s in client_a.get(PORTAL + "salary-slips/").json()]
        self.assertEqual(names, [slip_a["name"]])
        # guessing B's slip name / id gives nothing
        self.assertEqual(
            client_a.get(PORTAL + f"salary-slips/{slip_b['name']}/").status_code, 404
        )
        self.assertEqual(client_a.get(PORTAL + "payments/").json(), [])
        self.assertEqual(client_a.get(PORTAL + "advances/").json(), [])
        self.assertEqual(client_a.get(PORTAL + "advance-requests/").json(), [])
        self.assertEqual(client_a.get(PORTAL + "issues/").json(), [])
        self.assertEqual(
            client_a.get(PORTAL + "attendance/?from=2026-05-01&to=2026-05-31").json(),
            [],
        )
        # parameters naming another employee are ignored
        for q in (f"?employee={b.pk}", f"?employee={b.name}", f"?employee_id={b.pk}"):
            self.assertEqual(
                [s["name"] for s in client_a.get(PORTAL + "salary-slips/" + q).json()],
                [slip_a["name"]],
            )
            summary = client_a.get(PORTAL + "summary/" + q).json()
            self.assertEqual(summary["employee"], a.name)
            self.assertEqual(client_a.get(PORTAL + "me/" + q).json()["name"], a.name)
        # cannot withdraw B's request, even by id
        r = client_a.post(PORTAL + f"advance-requests/{req_b.pk}/withdraw/")
        self.assertEqual(r.status_code, 404)
        req_b.refresh_from_db()
        self.assertEqual(req_b.status, AdvanceRequestStatus.PENDING)
        # an "employee" in the body is ignored on create
        r = client_a.post(
            PORTAL + "advance-requests/",
            {"amount": "100", "reason": "mine", "employee": b.pk},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(AdvanceRequest.objects.get(pk=r.json()["id"]).employee, a)
        r = client_a.post(
            PORTAL + "issues/",
            {"category": "Other", "message": "mine", "employee": b.pk},
            format="json",
        )
        self.assertEqual(EmployeeIssue.objects.get(pk=r.json()["id"]).employee, a)
        self.assertEqual(EmployeeIssue.objects.filter(employee=b).count(), 1)
        self.assertIsNotNone((issue_b, adv_b))


# =========================================================== advance requests
class PortalAdvanceRequestTests(PortalBase):
    def request(self, client, amount="500", reason="Medical"):
        return client.post(
            PORTAL + "advance-requests/",
            {"amount": amount, "reason": reason},
            format="json",
        )

    def test_full_flow_request_approve_draft_submit_appears_in_portal(self):
        client = self.portal_client()
        r = self.request(client, "500", "Medical")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["status"], "Pending")
        self.assertEqual(body["amount"], "500.00")
        self.assertIsNone(body["advance"])
        req_id = body["id"]
        self.assertEqual(len(client.get(PORTAL + "advance-requests/").json()), 1)

        # one pending request per employee
        dup = self.request(client, "100", "again")
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(AdvanceRequest.objects.count(), 1)

        # admin sees it
        self.assertEqual(self.api.get(API + "portal-overview/").json(), {
            "pending_advance_requests": 1, "open_issues": 0})
        lst = self.api.get(API + "advance-requests/?status=Pending").json()["results"]
        self.assertEqual(lst[0]["id"], req_id)
        self.assertEqual(lst[0]["employee_code"], self.w.name)
        self.assertEqual(
            len(self.api.get(API + f"advance-requests/?employee={self.w.pk}").json()["results"]), 1
        )

        r = self.api.post(
            f"{API}advance-requests/{req_id}/approve/",
            {"amount": "450", "instalment_amount": "150", "posting_date": "2026-04-10"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        approved = r.json()
        self.assertEqual(approved["status"], "Approved")
        self.assertEqual(approved["reviewed_by"], self.admin.pk)
        advance = EmployeeAdvance.objects.get(name=approved["advance_name"])
        self.assertEqual(advance.docstatus, DocStatus.DRAFT)
        self.assertEqual(
            (advance.amount, advance.instalment_amount, advance.employee, advance.purpose),
            (D("450.00"), D("150.00"), self.w, "Medical"),
        )
        self.assertEqual(advance.paid_from, self.cfg.default_payment_account)
        self.assertEqual(advance.posting_date, d(2026, 4, 10))
        self.assertEqual(self.gl(advance).count(), 0)  # nothing posted yet
        # the employee sees it approved but not yet paid out
        mine = client.get(PORTAL + "advance-requests/").json()[0]
        self.assertEqual(mine["status"], "Approved")
        self.assertEqual(mine["advance"], advance.name)
        self.assertEqual(mine["approved_amount"], "450.00")
        self.assertEqual(client.get(PORTAL + "advances/").json(), [])
        self.assertEqual(
            self.api.get(API + "portal-overview/").json()["pending_advance_requests"], 0
        )

        # admin pays it out by submitting the draft
        r = self.api.post(f"{API}employee-advances/{advance.pk}/submit/")
        self.assertEqual(r.status_code, 200, r.content)
        advs = client.get(PORTAL + "advances/").json()
        self.assertEqual(
            [(a["name"], a["amount"], a["outstanding"], a["status"]) for a in advs],
            [(advance.name, "450.00", "450.00", "Paid")],
        )
        self.verify()
        actions = set(
            AuditLog.objects.filter(model_label="hr.AdvanceRequest").values_list(
                "action", flat=True
            )
        )
        self.assertEqual(actions, {"create", "approve"})

        # approved => a new request is allowed again
        self.assertEqual(self.request(client, "100", "later").status_code, 201)
        # a non-pending request cannot be decided twice
        again = self.api.post(f"{API}advance-requests/{req_id}/approve/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(EmployeeAdvance.objects.count(), 1)

    def test_approve_defaults_to_requested_amount_and_validates(self):
        client = self.portal_client()
        req_id = self.request(client, "300").json()["id"]
        bad = self.api.post(
            f"{API}advance-requests/{req_id}/approve/",
            {"instalment_amount": "999"},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        # the advance account is an asset, so use the payable account instead
        bad = self.api.post(
            f"{API}advance-requests/{req_id}/approve/",
            {"paid_from": self.cfg.payroll_payable_account.pk},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(AdvanceRequest.objects.get(pk=req_id).status, "Pending")
        self.assertEqual(EmployeeAdvance.objects.count(), 0)
        ok = self.api.post(f"{API}advance-requests/{req_id}/approve/", {}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(EmployeeAdvance.objects.get().amount, D("300.00"))

    def test_reject_and_withdraw(self):
        client = self.portal_client()
        req_id = self.request(client).json()["id"]
        self.assertEqual(
            self.api.post(f"{API}advance-requests/{req_id}/reject/", {}, format="json").status_code,
            400,
        )
        r = self.api.post(
            f"{API}advance-requests/{req_id}/reject/",
            {"response": "Too soon after the last one"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "Rejected")
        mine = client.get(PORTAL + "advance-requests/").json()[0]
        self.assertEqual(mine["status"], "Rejected")
        self.assertEqual(mine["response"], "Too soon after the last one")
        # a rejected request cannot be withdrawn or approved
        self.assertEqual(
            client.post(PORTAL + f"advance-requests/{req_id}/withdraw/").status_code, 400
        )
        self.assertEqual(
            self.api.post(f"{API}advance-requests/{req_id}/approve/", {}, format="json").status_code,
            400,
        )
        # new request, withdrawn by the employee
        new_id = self.request(client, "200", "Festival").json()["id"]
        w = client.post(PORTAL + f"advance-requests/{new_id}/withdraw/")
        self.assertEqual(w.status_code, 200, w.content)
        self.assertEqual(w.json()["status"], "Withdrawn")
        self.assertEqual(
            client.post(PORTAL + f"advance-requests/{new_id}/withdraw/").status_code, 400
        )
        self.assertEqual(
            self.api.post(f"{API}advance-requests/{new_id}/approve/", {}, format="json").status_code,
            400,
        )
        self.assertEqual(self.request(client, "50", "ok").status_code, 201)

    def test_request_validation(self):
        client = self.portal_client()
        for body in (
            {"amount": "0", "reason": "x"},
            {"amount": "-5", "reason": "x"},
            {"amount": "abc", "reason": "x"},
            {"amount": "100"},
            {"amount": "100", "reason": ""},
            {"reason": "x"},
        ):
            self.assertEqual(
                client.post(PORTAL + "advance-requests/", body, format="json").status_code,
                400,
                body,
            )
        self.assertFalse(AdvanceRequest.objects.exists())

    def test_hr_user_reads_but_cannot_decide(self):
        client = self.portal_client()
        req_id = self.request(client).json()["id"]
        hr_user = self.role_client("hru", "HR User")
        self.assertEqual(hr_user.get(API + "advance-requests/").status_code, 200)
        self.assertEqual(hr_user.get(f"{API}advance-requests/{req_id}/").status_code, 200)
        self.assertEqual(
            hr_user.post(f"{API}advance-requests/{req_id}/approve/", {}, format="json").status_code, 403
        )
        self.assertEqual(
            hr_user.post(f"{API}advance-requests/{req_id}/reject/", {"response": "no"}, format="json").status_code, 403
        )
        self.assertEqual(hr_user.delete(f"{API}advance-requests/{req_id}/").status_code, 403)
        manager = self.role_client("hrm", "HR Manager")
        self.assertEqual(
            manager.post(f"{API}advance-requests/{req_id}/approve/", {}, format="json").status_code, 200
        )

    def test_db_constraints(self):
        base = dict(employee=self.w, company=self.company, amount=D("10"), reason="r")
        AdvanceRequest.objects.create(**base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceRequest.objects.create(**base)  # second Pending
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceRequest.objects.create(**{**base, "amount": D("0"), "status": "Withdrawn"})
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceRequest.objects.create(
                **{**base, "status": "Approved", "reviewed_at": timezone.now()}
            )  # Approved without an advance
        adv = self.advance(self.w, "10", submit=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceRequest.objects.create(**{**base, "status": "Withdrawn", "advance": adv})
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceRequest.objects.create(
                **{**base, "status": "Rejected"}
            )  # decision without reviewed_at
        AdvanceRequest.objects.create(
            **{**base, "status": "Approved", "advance": adv, "reviewed_at": timezone.now()}
        )
        from django.db.models.deletion import ProtectedError

        with self.assertRaises(ProtectedError):
            adv.delete()


# ===================================================================== issues
class PortalIssueTests(PortalBase):
    def test_create_resolve_flow(self):
        client = self.portal_client()
        r = client.post(
            PORTAL + "issues/",
            {
                "category": "Attendance",
                "reference_date": "2026-04-03",
                "message": "I was present on 3 April",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        issue = r.json()
        self.assertEqual(
            set(issue),
            {
                "id",
                "category",
                "reference_date",
                "reference_doc",
                "message",
                "status",
                "response",
                "created_at",
                "resolved_at",
            },
        )
        self.assertEqual(issue["status"], "Open")
        self.assertEqual(issue["reference_date"], "2026-04-03")
        slip_issue = client.post(
            PORTAL + "issues/",
            {"category": "Salary Slip", "reference_doc": "SAL-0001", "message": "Wrong total"},
            format="json",
        )
        self.assertEqual(slip_issue.status_code, 201)
        self.assertEqual(slip_issue.json()["reference_doc"], "SAL-0001")
        self.assertEqual(len(client.get(PORTAL + "issues/").json()), 2)

        self.assertEqual(self.api.get(API + "portal-overview/").json()["open_issues"], 2)
        rows = self.api.get(API + "employee-issues/?status=Open&category=Attendance").json()["results"]
        self.assertEqual([x["id"] for x in rows], [issue["id"]])
        self.assertEqual(rows[0]["employee_code"], self.w.name)

        hr_user = self.role_client("hru", "HR User")  # may resolve
        url = f"{API}employee-issues/{issue['id']}/resolve/"
        self.assertEqual(hr_user.post(url, {"status": "Resolved"}, format="json").status_code, 400)
        self.assertEqual(hr_user.post(url, {"status": "Open", "response": "x"}, format="json").status_code, 400)
        self.assertEqual(hr_user.post(url, {"status": "Bogus", "response": "x"}, format="json").status_code, 400)
        r = hr_user.post(url, {"status": "Resolved", "response": "Fixed, thanks"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "Resolved")
        self.assertIsNotNone(r.json()["resolved_at"])
        # closed issues cannot be closed again
        self.assertEqual(hr_user.post(url, {"status": "Rejected", "response": "x"}, format="json").status_code, 400)
        mine = {i["id"]: i for i in client.get(PORTAL + "issues/").json()}
        self.assertEqual(mine[issue["id"]]["status"], "Resolved")
        self.assertEqual(mine[issue["id"]]["response"], "Fixed, thanks")
        r = self.api.post(
            f"{API}employee-issues/{slip_issue.json()['id']}/resolve/",
            {"status": "Rejected", "response": "The slip is correct"},
            format="json",
        )
        self.assertEqual(r.json()["status"], "Rejected")
        self.assertEqual(self.api.get(API + "portal-overview/").json()["open_issues"], 0)
        self.assertTrue(
            AuditLog.objects.filter(model_label="hr.EmployeeIssue", action="resolve").count() == 2
        )

    def test_validation_and_limits(self):
        client = self.portal_client()
        for body in (
            {"category": "Nope", "message": "x"},
            {"category": "Other", "message": ""},
            {"category": "Other", "message": "   "},
            {"category": "Other"},
            {"message": "x"},
            {"category": "Other", "message": "x", "reference_date": "yesterday"},
        ):
            self.assertEqual(client.post(PORTAL + "issues/", body, format="json").status_code, 400, body)
        from hr.services.portal_requests import MAX_OPEN_ISSUES

        for i in range(MAX_OPEN_ISSUES):
            self.assertEqual(
                client.post(PORTAL + "issues/", {"category": "Other", "message": f"m{i}"}, format="json").status_code,
                201,
            )
        self.assertEqual(
            client.post(PORTAL + "issues/", {"category": "Other", "message": "one more"}, format="json").status_code,
            400,
        )

    def test_admin_permissions(self):
        client = self.portal_client()
        issue_id = client.post(
            PORTAL + "issues/", {"category": "Other", "message": "help"}, format="json"
        ).json()["id"]
        nobody = APIClient()
        nobody.force_authenticate(User.objects.create_user(username="n", email="n@x.com"))
        self.assertEqual(nobody.get(API + "employee-issues/").status_code, 403)
        self.assertEqual(nobody.get(API + "portal-overview/").status_code, 403)
        self.assertEqual(
            nobody.post(f"{API}employee-issues/{issue_id}/resolve/", {"status": "Resolved", "response": "x"}, format="json").status_code,
            403,
        )
        hr_user = self.role_client("hru", "HR User")
        self.assertEqual(hr_user.get(API + "portal-overview/").status_code, 200)
        self.assertEqual(hr_user.delete(f"{API}employee-issues/{issue_id}/").status_code, 403)
        self.assertEqual(
            hr_user.post(API + "employee-issues/", {"message": "x"}, format="json").status_code, 403
        )

    def test_db_constraints(self):
        base = dict(employee=self.w, company=self.company, category="Other", message="m")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeIssue.objects.create(**{**base, "message": ""})
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeIssue.objects.create(**{**base, "status": "Resolved"})  # no resolved_at
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeIssue.objects.create(**{**base, "resolved_at": timezone.now()})  # Open but closed
        EmployeeIssue.objects.create(**{**base, "status": IssueStatus.RESOLVED, "resolved_at": timezone.now()})


class PortalSeedTests(PortalBase):
    def test_seed_matrix(self):
        from hr.seed import HR_LABELS

        for label in ("hr.EmployeePortalAccess", "hr.AdvanceRequest", "hr.EmployeeIssue"):
            self.assertIn(label, HR_LABELS)
            self.assertIn(label, HR_ROLE_MATRIX["HR Manager"])
        user = HR_ROLE_MATRIX["HR User"]
        self.assertNotIn("hr.EmployeePortalAccess", user)
        self.assertEqual(user["hr.AdvanceRequest"], ("can_read",))
        self.assertIn("can_write", user["hr.EmployeeIssue"])

    def test_pin_validation_function(self):
        portal_service.validate_pin("4827")
        with self.assertRaises(Exception):
            portal_service.validate_pin("1234")
