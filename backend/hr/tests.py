"""HR and daily-wage payroll tests.  Run with ``pytest hr``.

Dates are fixed in April 2026 (a 30-day month, 1 April is a Wednesday) so the
monthly-salary arithmetic is exact: 30 000 / 30 = 1 000 per day.
"""

import datetime
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from core.models import AuditLog, Company, DocStatus
from hr.models import (
    Attendance,
    LeaveType,
    LeaveAllocation,
    LeaveApplication,
    AttendanceStatus,
    Employee,
    EmployeeAdvance,
    EmployeePayment,
    EmployeeWageRate,
    Holiday,
    HolidayList,
    HRSettings,
    PayType,
    SalaryComponent,
    SalarySlip,
    SystemComponent,
)
from hr.seed import HR_LABELS, HR_ROLE_MATRIX
from hr.services import integrity
from hr.services.balances import advance_ledger_balance, payable_balance
from hr.services.setup import ensure_hr_setup
from hr.services.summary import employee_summary
from hr.services.wages import compute_wages
from users.models import Role, RolePermission, User, UserRole

d = datetime.date
P, A, H, L = (
    AttendanceStatus.PRESENT,
    AttendanceStatus.ABSENT,
    AttendanceStatus.HALF_DAY,
    AttendanceStatus.PAID_LEAVE,
)
API = "/api/hr/"


class HRBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Nidhi HR", abbr="NHR")
        assets = Account.objects.create(
            company=self.company,
            name="Assets",
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        Account.objects.create(
            company=self.company,
            name="Liabilities",
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        Account.objects.create(
            company=self.company,
            name="Expenses",
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.cash = Account.objects.create(
            company=self.company,
            name="Cash Account",
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        self.bank = Account.objects.create(
            company=self.company,
            name="Bank Account",
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.cfg = ensure_hr_setup(self.company)
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="x"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self._n = 0

    # ------------------------------------------------------------- helpers
    def employee(self, label, pay_type=PayType.DAILY, **kw):
        self._n += 1
        defaults = dict(
            name=f"EMP-T{self._n}",
            first_name=label,
            company=self.company,
            date_of_joining=d(2026, 1, 1),
            pay_type=pay_type,
        )
        defaults.update(kw)
        return Employee.objects.create(**defaults)

    def rate(self, emp, from_date=d(2026, 1, 1), daily="500", monthly=None, ot="60"):
        return EmployeeWageRate.objects.create(
            employee=emp,
            from_date=from_date,
            daily_rate=D(daily) if daily is not None else None,
            monthly_salary=D(monthly) if monthly is not None else None,
            ot_hourly_rate=D(ot),
        )

    def att(self, emp, day, status=P, ot="0"):
        return Attendance.objects.create(
            employee=emp, date=day, status=status, ot_hours=D(ot)
        )

    def present(self, emp, start, end, status=P):
        day = start
        while day <= end:
            self.att(emp, day, status)
            day += datetime.timedelta(days=1)

    def advance(self, emp, amount, instalment=None, date=d(2026, 4, 1), submit=True):
        adv = EmployeeAdvance.objects.create(
            name=f"ADV-{emp.name}-{amount}-{date}",
            employee=emp,
            company=self.company,
            posting_date=date,
            amount=D(amount),
            instalment_amount=D(instalment) if instalment else None,
            paid_from=self.cash,
        )
        if submit:
            adv.submit()
        return adv

    def slip_payload(self, emp, start, end, **extra):
        return {
            "employee": emp.pk,
            "company": self.company.pk,
            "posting_date": str(end),
            "start_date": str(start),
            "end_date": str(end),
            **extra,
        }

    def make_slip(self, emp, start, end, **extra):
        r = self.api.post(
            API + "salary-slips/",
            self.slip_payload(emp, start, end, **extra),
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def submit_slip(self, slip_id, expect=200):
        r = self.api.post(f"{API}salary-slips/{slip_id}/submit/")
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def gl(self, doc, **filt):
        return GLEntry.objects.filter(
            voucher_type=f"hr.{type(doc).__name__}",
            voucher_no=doc.name,
            is_cancelled=False,
            **filt,
        )

    def verify(self):
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())


# =========================================================== wage calculation
class WageCalculationTests(HRBase):
    def test_daily_worker_with_half_day_absence_and_overtime(self):
        w = self.employee("Ramu")
        self.rate(w)
        for day, status, ot in [
            (1, P, "0"),
            (2, P, "2"),
            (3, P, "0"),
            (4, P, "0"),
            (5, H, "0"),
            (6, A, "0"),
        ]:
            self.att(w, d(2026, 4, day), status, ot)
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 7))  # day 7: no record
        self.assertEqual(slip["payment_days"], "4.50")
        self.assertEqual(slip["ot_hours"], "2.00")
        self.assertEqual(slip["gross_pay"], "2370.00")
        self.assertEqual(slip["net_pay"], "2370.00")
        rows = {r["system_key"]: r for r in slip["details"]}
        self.assertEqual(rows["wages"]["amount"], "2250.00")
        self.assertEqual(rows["overtime"]["amount"], "120.00")
        self.assertFalse(rows["wages"]["is_manual"])

    def test_paid_leave_counts_as_a_full_day(self):
        w = self.employee("Ramu")
        self.rate(w)
        self.att(w, d(2026, 4, 1), L)
        result = compute_wages(w, d(2026, 4, 1), d(2026, 4, 1))
        self.assertEqual(result.wages, D("500.00"))

    def test_monthly_salary_per_calendar_day_with_holiday_pay(self):
        hl = HolidayList.objects.create(
            name="2026", from_date=d(2026, 1, 1), to_date=d(2026, 12, 31)
        )
        Holiday.objects.create(holiday_list=hl, date=d(2026, 4, 5), weekly_off=True)
        monthly = self.employee("Mohan", PayType.MONTHLY, holiday_list=hl)
        self.rate(monthly, daily=None, monthly="30000", ot="100")
        daily = self.employee("Dinu", holiday_list=hl)
        self.rate(daily)
        for e in (monthly, daily):
            self.present(e, d(2026, 4, 1), d(2026, 4, 2))
            self.att(e, d(2026, 4, 3), P, "1.5")
            self.att(e, d(2026, 4, 4), H)
            self.att(e, d(2026, 4, 6), A)
            self.att(e, d(2026, 4, 7), L)
        m = compute_wages(monthly, d(2026, 4, 1), d(2026, 4, 7))
        # 3 present + 0.5 half + holiday (no record) 1 + absent 0 + leave 1 = 5.5
        self.assertEqual(m.payment_days, D("5.50"))
        self.assertEqual(m.wages, D("5500.00"))
        self.assertEqual(m.overtime, D("150.00"))
        dl = compute_wages(daily, d(2026, 4, 1), d(2026, 4, 7))
        self.assertEqual(dl.payment_days, D("4.50"))  # no holiday pay
        self.assertEqual(dl.wages, D("2250.00"))

    def test_rate_change_mid_range_uses_each_days_rate(self):
        w = self.employee("Ramu")
        self.rate(w, d(2026, 1, 1), daily="500", ot="60")
        self.rate(w, d(2026, 4, 4), daily="600", ot="80")
        self.present(w, d(2026, 4, 1), d(2026, 4, 6))
        self.att(w, d(2026, 4, 10), P, "2")
        r = compute_wages(w, d(2026, 4, 1), d(2026, 4, 10))
        self.assertEqual(r.wages, D("3300.00") + D("600.00"))  # 3x500 + 3x600 + 600
        self.assertEqual(r.overtime, D("160.00"))  # 2h x 80

    def test_days_outside_employment_earn_nothing(self):
        w = self.employee("Newbie", date_of_joining=d(2026, 4, 3))
        self.rate(w, d(2026, 1, 1))
        # attendance before joining cannot be entered via the API, but be robust
        Attendance.objects.create(employee=w, date=d(2026, 4, 2), status=P)
        self.att(w, d(2026, 4, 3), P)
        self.assertEqual(
            compute_wages(w, d(2026, 4, 1), d(2026, 4, 5)).wages, D("500.00")
        )

    def test_missing_rate_is_a_clear_400(self):
        w = self.employee("NoRate")
        self.att(w, d(2026, 4, 1))
        r = self.api.post(
            API + "salary-slips/",
            self.slip_payload(w, d(2026, 4, 1), d(2026, 4, 2)),
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("no wage rate", str(r.json()))
        self.assertFalse(SalarySlip.objects.exists())

    def test_wage_rate_api_requires_the_relevant_rate_and_freezes_after_a_slip(self):
        w = self.employee("Ramu")
        r = self.api.post(
            API + "wage-rates/",
            {"employee": w.pk, "from_date": "2026-01-01", "ot_hourly_rate": "10"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # Daily needs a daily_rate
        r = self.api.post(
            API + "wage-rates/",
            {
                "employee": w.pk,
                "from_date": "2026-01-01",
                "daily_rate": "450.5",
                "ot_hourly_rate": "10",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        rate_id = r.json()["id"]
        self.present(w, d(2026, 4, 1), d(2026, 4, 2))
        self.submit_slip(self.make_slip(w, d(2026, 4, 1), d(2026, 4, 2))["id"])
        # A submitted slip depends on the rate: no edits, no delete.
        r = self.api.patch(
            f"{API}wage-rates/{rate_id}/", {"daily_rate": "999"}, format="json"
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(
            self.api.delete(f"{API}wage-rates/{rate_id}/").status_code, 400
        )
        # A later-dated row is the way to change pay.
        r = self.api.post(
            API + "wage-rates/",
            {
                "employee": w.pk,
                "from_date": "2026-04-03",
                "daily_rate": "550",
                "ot_hourly_rate": "10",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)

    def test_wage_rate_constraints(self):
        w = self.employee("Ramu")
        self.rate(w, d(2026, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeWageRate.objects.bulk_create(
                [
                    EmployeeWageRate(
                        employee=w, from_date=d(2026, 1, 1), daily_rate=D("1")
                    )
                ]
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeWageRate.objects.bulk_create(
                [
                    EmployeeWageRate(
                        employee=w, from_date=d(2026, 2, 1), daily_rate=D("-1")
                    )
                ]
            )


# ============================================================== masters
class MasterTests(HRBase):
    def test_employee_create_numbers_validates_and_is_unique_on_mobile(self):
        payload = {
            "first_name": "Sita",
            "company": self.company.pk,
            "date_of_joining": "2026-02-01",
            "mobile_no": "98765 43210",
            "pan": "abcde1234f",
            "aadhaar": "1234 5678 9012",
            "ifsc_code": "sbin0001234",
        }
        r = self.api.post(API + "employees/", payload, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertRegex(body["name"], r"^EMP-\d{4}$")
        self.assertEqual(body["mobile_no"], "9876543210")
        self.assertEqual(body["pan"], "ABCDE1234F")
        self.assertEqual(body["aadhaar"], "123456789012")  # superuser sees it
        r2 = self.api.post(
            API + "employees/", {**payload, "first_name": "Gita"}, format="json"
        )
        self.assertEqual(r2.status_code, 400)
        self.assertIn("mobile_no", r2.json())
        # blank mobile numbers do not collide
        for name in ("A", "B"):
            r = self.api.post(
                API + "employees/",
                {
                    "first_name": name,
                    "company": self.company.pk,
                    "date_of_joining": "2026-02-01",
                    "mobile_no": "",
                },
                format="json",
            )
            self.assertEqual(r.status_code, 201, r.content)
            self.assertIsNone(r.json()["mobile_no"])

    def test_employee_date_and_status_rules(self):
        base = {
            "first_name": "X",
            "company": self.company.pk,
            "date_of_joining": "2026-02-01",
        }
        r = self.api.post(
            API + "employees/", {**base, "relieving_date": "2026-01-01"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        r = self.api.post(API + "employees/", {**base, "status": "Left"}, format="json")
        self.assertEqual(r.status_code, 400)
        e = self.employee("DB")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=e.pk).update(relieving_date=d(2025, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=e.pk).update(status="Left")

    def test_holiday_list_nested_write_and_unique_dates(self):
        r = self.api.post(
            API + "holiday-lists/",
            {
                "name": "FY26",
                "from_date": "2026-01-01",
                "to_date": "2026-12-31",
                "holidays": [
                    {"date": "2026-01-26", "description": "Republic Day"},
                    {"date": "2026-04-05", "weekly_off": True},
                ],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()["holidays"]), 2)
        bad = self.api.post(
            API + "holiday-lists/",
            {
                "name": "Dup",
                "from_date": "2026-01-01",
                "to_date": "2026-12-31",
                "holidays": [{"date": "2026-01-26"}, {"date": "2026-01-26"}],
            },
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        hl = HolidayList.objects.get(name="FY26")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Holiday.objects.create(holiday_list=hl, date=d(2026, 1, 26))

    def test_used_master_cannot_be_deleted(self):
        dep = self.api.post(
            API + "departments/", {"name": "Packing"}, format="json"
        ).json()
        self.employee("P", department_id=dep["id"])
        self.assertEqual(
            self.api.delete(f"{API}departments/{dep['id']}/").status_code, 409
        )

    def test_setup_is_idempotent_and_system_components_exist(self):
        before = (HRSettings.objects.count(), SalaryComponent.objects.count())
        ensure_hr_setup(self.company)
        self.assertEqual(
            before, (HRSettings.objects.count(), SalaryComponent.objects.count())
        )
        keys = set(
            SalaryComponent.objects.filter(company=self.company).values_list(
                "system_key", flat=True
            )
        )
        self.assertEqual(keys, {"wages", "overtime", "advance_recovery", "tds"})
        self.assertEqual(self.cfg.default_payment_account, self.cash)
        self.assertEqual(
            self.cfg.payroll_payable_account.root_type, AccountRootType.LIABILITY
        )

    def test_rbac_labels_resolve_to_models(self):
        from django.apps import apps

        for label in HR_LABELS:
            apps.get_model(label)
        for matrix in HR_ROLE_MATRIX.values():
            for label in matrix:
                apps.get_model(label)

    def test_settings_cannot_move_posted_accounts(self):
        w = self.employee("Ramu")
        self.rate(w)
        self.present(w, d(2026, 4, 1), d(2026, 4, 1))
        self.submit_slip(self.make_slip(w, d(2026, 4, 1), d(2026, 4, 1))["id"])
        other = Account.objects.create(
            company=self.company,
            name="Other Payable",
            root_type=AccountRootType.LIABILITY,
        )
        r = self.api.patch(
            f"{API}settings/{self.cfg.pk}/",
            {"payroll_payable_account": other.pk},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)


# ============================================================== attendance
class AttendanceTests(HRBase):
    def test_attendance_is_locked_by_a_submitted_slip_until_it_is_cancelled(self):
        w = self.employee("Ramu")
        self.rate(w)
        rec = self.att(w, d(2026, 4, 1))
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 3))
        self.submit_slip(slip["id"])

        # inside the slip: create / update / delete are all refused
        r = self.api.post(
            API + "attendance/",
            {"employee": w.pk, "date": "2026-04-02", "status": "Present"},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("locked", str(r.json()))
        r = self.api.patch(
            f"{API}attendance/{rec.pk}/", {"status": "Absent"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.delete(f"{API}attendance/{rec.pk}/").status_code, 400)
        # moving the record out of the locked range is refused too
        r = self.api.patch(
            f"{API}attendance/{rec.pk}/", {"date": "2026-04-20"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        # a day outside the slip is free
        r = self.api.post(
            API + "attendance/",
            {"employee": w.pk, "date": "2026-04-04", "status": "Present"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        with self.assertRaises(ValidationError):
            self.att(w, d(2026, 4, 3))  # the ORM is guarded as well

        # cancelling the slip unlocks it
        self.assertEqual(
            self.api.post(f"{API}salary-slips/{slip['id']}/cancel/").status_code, 200
        )
        self.assertEqual(
            self.api.patch(
                f"{API}attendance/{rec.pk}/", {"status": "Absent"}, format="json"
            ).status_code,
            200,
        )

    def test_attendance_validation(self):
        w = self.employee("Ramu", date_of_joining=d(2026, 4, 10))
        r = self.api.post(
            API + "attendance/",
            {"employee": w.pk, "date": "2026-04-01", "status": "Present"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # before joining
        r = self.api.post(
            API + "attendance/",
            {
                "employee": w.pk,
                "date": "2026-04-11",
                "status": "Absent",
                "ot_hours": "2",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # absent with overtime
        r = self.api.post(
            API + "attendance/",
            {
                "employee": w.pk,
                "date": "2026-04-11",
                "status": "Present",
                "ot_hours": "25",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        ok = {"employee": w.pk, "date": "2026-04-11", "status": "Present"}
        self.assertEqual(
            self.api.post(API + "attendance/", ok, format="json").status_code, 201
        )
        self.assertEqual(
            self.api.post(API + "attendance/", ok, format="json").status_code, 400
        )

    def test_muster_roll_get_lists_active_employees_with_attendance(self):
        a = self.employee("A")
        b = self.employee("B")
        self.employee("Gone", status="Inactive")
        self.employee("Future", date_of_joining=d(2026, 5, 1))
        self.att(a, d(2026, 4, 2), H, "1")
        r = self.api.get(API + "attendance/muster-roll/?date=2026-04-02")
        self.assertEqual(r.status_code, 200, r.content)
        rows = {row["employee_id"]: row for row in r.json()["rows"]}
        self.assertEqual(set(rows), {a.name, b.name})
        self.assertEqual(rows[a.name]["attendance"]["status"], "Half Day")
        self.assertIsNone(rows[b.name]["attendance"])
        self.assertEqual(self.api.get(API + "attendance/muster-roll/").status_code, 400)
        self.assertEqual(
            self.api.get(API + "attendance/muster-roll/?date=nope").status_code, 400
        )

    def test_muster_roll_post_upserts_all_or_nothing(self):
        a, b = self.employee("A"), self.employee("B")
        self.rate(b)
        self.att(b, d(2026, 4, 1))
        self.submit_slip(
            self.make_slip(b, d(2026, 4, 1), d(2026, 4, 3))["id"]
        )  # locks b

        def post(day, rows):
            return self.api.post(
                API + "attendance/muster-roll/",
                {"date": day, "rows": rows},
                format="json",
            )

        # b is locked on 2 April: the whole batch is rejected, a gets nothing
        r = post(
            "2026-04-02",
            [
                {"employee": a.pk, "status": "Present"},
                {"employee": b.pk, "status": "Present"},
            ],
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Attendance.objects.filter(employee=a).exists())

        # a bad row (employee unknown) rejects the batch too
        r = post(
            "2026-04-10",
            [
                {"employee": a.pk, "status": "Present"},
                {"employee": 99999, "status": "Present"},
            ],
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Attendance.objects.filter(employee=a).exists())

        # a clean batch is applied, and re-posting updates in place (upsert)
        r = post(
            "2026-04-10",
            [
                {"employee": a.pk, "status": "Present", "ot_hours": "2"},
                {"employee": b.pk, "status": "Absent"},
            ],
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Attendance.objects.filter(date=d(2026, 4, 10)).count(), 2)
        r = post("2026-04-10", [{"employee": a.pk, "status": "Half Day"}])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Attendance.objects.filter(date=d(2026, 4, 10)).count(), 2)
        self.assertEqual(
            Attendance.objects.get(employee=a, date=d(2026, 4, 10)).status, "Half Day"
        )
        # duplicates in one batch are refused
        r = post(
            "2026-04-11",
            [
                {"employee": a.pk, "status": "Present"},
                {"employee": a.pk, "status": "Absent"},
            ],
        )
        self.assertEqual(r.status_code, 400)

    def test_muster_roll_report_grid(self):
        a = self.employee("A")
        self.att(a, d(2026, 4, 1), P, "1")
        self.att(a, d(2026, 4, 2), H)
        self.att(a, d(2026, 4, 3), A)
        r = self.api.get(
            API + "attendance/muster-roll-report/?from=2026-04-01&to=2026-04-04"
        )
        self.assertEqual(r.status_code, 200, r.content)
        row = r.json()["rows"][0]
        self.assertEqual(row["days"]["2026-04-01"], "P")
        self.assertEqual(row["days"]["2026-04-02"], "H")
        self.assertEqual(row["days"]["2026-04-03"], "A")
        self.assertIsNone(row["days"]["2026-04-04"])
        self.assertEqual(
            (row["present"], row["half_day"], row["absent"], row["ot_hours"]),
            ("1", "1", "1", "1.00"),
        )
        self.assertEqual(
            self.api.get(
                API + "attendance/muster-roll-report/?from=2026-01-01&to=2026-12-31"
            ).status_code,
            400,
        )


# ============================================================== salary slips
class SalarySlipTests(HRBase):
    def worker(self, days=6):
        w = self.employee("Ramu")
        self.rate(w)
        self.present(w, d(2026, 4, 1), d(2026, 4, days))
        return w

    def test_submit_posts_balanced_gl_and_cancel_reverses_it(self):
        w = self.worker()
        slip_json = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 6))
        slip = SalarySlip.objects.get(pk=slip_json["id"])
        self.submit_slip(slip.pk)
        entries = self.gl(slip)
        self.assertEqual(sum(e.debit for e in entries), D("3000.00"))
        self.assertEqual(sum(e.credit for e in entries), D("3000.00"))
        dr = entries.get(debit__gt=0)
        self.assertEqual(dr.account, self.cfg.wage_expense_account)
        cr = entries.get(credit__gt=0)
        self.assertEqual(cr.account, self.cfg.payroll_payable_account)
        self.assertEqual((cr.party_type, cr.party), ("Employee", w.name))
        self.assertEqual(payable_balance(w), D("3000.00"))
        self.verify()

        self.assertEqual(
            self.api.post(f"{API}salary-slips/{slip.pk}/cancel/").status_code, 200
        )
        self.assertFalse(self.gl(slip).exists())
        self.assertEqual(payable_balance(w), D("0.00"))
        # original rows are kept as cancelled facts, plus the reversals
        self.assertEqual(GLEntry.objects.filter(voucher_no=slip.name).count(), 4)
        self.verify()

    def test_submitted_slip_is_immutable(self):
        w = self.worker()
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(slip["id"])
        r = self.api.patch(
            f"{API}salary-slips/{slip['id']}/", {"remarks": "x"}, format="json"
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(
            self.api.delete(f"{API}salary-slips/{slip['id']}/").status_code, 409
        )
        self.assertEqual(
            self.api.post(f"{API}salary-slips/{slip['id']}/recompute/").status_code, 400
        )

    def test_overlapping_submitted_slip_is_rejected_but_adjacent_is_fine(self):
        w = self.worker(days=12)
        first = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 6))
        overlap = self.make_slip(w, d(2026, 4, 5), d(2026, 4, 10))
        adjacent = self.make_slip(w, d(2026, 4, 7), d(2026, 4, 12))
        self.submit_slip(first["id"])
        r = self.submit_slip(overlap["id"], expect=400)
        self.assertIn(first["name"], str(r.json()))
        self.submit_slip(adjacent["id"])
        # cancelling the first frees the period again
        self.api.post(f"{API}salary-slips/{first['id']}/cancel/")
        self.submit_slip(
            overlap["id"], expect=400
        )  # still clashes with the adjacent one
        self.verify()

    def test_manual_rows_and_totals_are_server_computed(self):
        w = self.worker()
        bonus = SalaryComponent.objects.create(
            company=self.company,
            name="Bonus",
            component_type="Earning",
            account=self.cfg.wage_expense_account,
        )
        fine = SalaryComponent.objects.create(
            company=self.company,
            name="Fine",
            component_type="Deduction",
            account=self.cfg.employee_advance_account,
        )
        slip = self.make_slip(
            w,
            d(2026, 4, 1),
            d(2026, 4, 6),
            details=[
                {"component": bonus.pk, "amount": "200.00", "remarks": "Festival"},
                {"component": fine.pk, "amount": "50.00"},
            ],
            gross_pay="999999",  # ignored
        )
        self.assertEqual(slip["gross_pay"], "3200.00")
        self.assertEqual(slip["total_deduction"], "50.00")
        self.assertEqual(slip["net_pay"], "3150.00")
        self.assertTrue(
            all(r["is_manual"] for r in slip["details"] if r["system_key"] is None)
        )

        # PATCH replaces the manual rows; the generated Wages row is rebuilt even
        # if the client round-trips (or forges) it.
        wages_row = next(r for r in slip["details"] if r["system_key"] == "wages")
        r = self.api.patch(
            f"{API}salary-slips/{slip['id']}/",
            {
                "details": [
                    {"component": wages_row["component"], "amount": "1.00"},
                    {"component": bonus.pk, "amount": "100.00"},
                ]
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["gross_pay"], "3100.00")
        self.assertEqual(r.json()["total_deduction"], "0.00")

        self.submit_slip(slip["id"])
        gl = self.gl(SalarySlip.objects.get(pk=slip["id"]))
        self.assertEqual(sum(e.debit for e in gl), D("3100.00"))
        self.verify()

    def test_negative_net_cannot_be_submitted(self):
        w = self.worker(days=1)  # gross 500
        fine = SalaryComponent.objects.create(
            company=self.company,
            name="Fine",
            component_type="Deduction",
            account=self.cfg.employee_advance_account,
        )
        slip = self.make_slip(
            w,
            d(2026, 4, 1),
            d(2026, 4, 1),
            details=[{"component": fine.pk, "amount": "600"}],
        )
        self.assertEqual(slip["net_pay"], "-100.00")
        r = self.submit_slip(slip["id"], expect=400)
        self.assertIn("negative", str(r.json()))

    def test_empty_slip_cannot_be_submitted(self):
        w = self.employee("Idle")
        self.rate(w)
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 3))
        self.assertEqual(slip["gross_pay"], "0.00")
        self.submit_slip(slip["id"], expect=400)

    def test_stale_slip_must_be_recomputed_before_submit(self):
        w = self.worker(days=2)
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 3))
        self.assertEqual(slip["gross_pay"], "1000.00")
        self.att(w, d(2026, 4, 3))  # attendance entered after the slip was drafted
        r = self.submit_slip(slip["id"], expect=400)
        self.assertIn("recompute", str(r.json()))
        r = self.api.post(f"{API}salary-slips/{slip['id']}/recompute/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["gross_pay"], "1500.00")
        self.submit_slip(slip["id"])

    def test_recompute_keeps_manual_rows(self):
        w = self.worker(days=2)
        bonus = SalaryComponent.objects.create(
            company=self.company,
            name="Bonus",
            component_type="Earning",
            account=self.cfg.wage_expense_account,
        )
        slip = self.make_slip(
            w,
            d(2026, 4, 1),
            d(2026, 4, 3),
            details=[{"component": bonus.pk, "amount": "70"}],
        )
        self.att(w, d(2026, 4, 3))
        r = self.api.post(f"{API}salary-slips/{slip['id']}/recompute/").json()
        self.assertEqual(r["gross_pay"], "1570.00")

    def test_other_company_or_employee_rows_are_rejected(self):
        w = self.worker()
        other = Company.objects.create(name="Other", abbr="OTH")
        acct = Account.objects.create(
            company=other, name="X", root_type=AccountRootType.EXPENSE
        )
        foreign = SalaryComponent.objects.create(
            company=other, name="Foreign", component_type="Earning", account=acct
        )
        r = self.api.post(
            API + "salary-slips/",
            self.slip_payload(
                w,
                d(2026, 4, 1),
                d(2026, 4, 3),
                details=[{"component": foreign.pk, "amount": "5"}],
            ),
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        r = self.api.post(
            API + "salary-slips/",
            self.slip_payload(w, d(2026, 4, 3), d(2026, 4, 1)),
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # end before start
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalarySlip.objects.filter(
                pk=self.make_slip(w, d(2026, 4, 1), d(2026, 4, 3))["id"]
            ).update(end_date=d(2026, 3, 1))

    def test_amend_a_cancelled_slip(self):
        w = self.worker()
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(slip["id"])
        self.api.post(f"{API}salary-slips/{slip['id']}/cancel/")
        r = self.api.post(f"{API}salary-slips/{slip['id']}/amend/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["gross_pay"], "3000.00")
        self.submit_slip(r.json()["id"])
        self.verify()


# ======================================================= advances & payments
class AdvanceAndPaymentTests(HRBase):
    def setUp(self):
        super().setUp()
        self.w = self.employee("Ramu")
        self.rate(self.w)
        self.present(self.w, d(2026, 4, 1), d(2026, 4, 18))

    def test_advance_posts_gl_and_is_recovered_by_instalments(self):
        adv = self.advance(self.w, "1000", instalment="400")
        entries = self.gl(adv)
        dr = entries.get(debit__gt=0)
        self.assertEqual(
            (dr.account, dr.party_type, dr.party),
            (self.cfg.employee_advance_account, "Employee", self.w.name),
        )
        self.assertEqual(entries.get(credit__gt=0).account, self.cash)
        self.assertEqual(advance_ledger_balance(self.w), D("1000.00"))

        # slip 1: instalment of 400 is proposed and recovered
        s1 = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        rec = [r for r in s1["details"] if r["system_key"] == "advance_recovery"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["amount"], "400.00")
        self.assertEqual(rec[0]["advance"], adv.pk)
        self.assertFalse(rec[0]["is_manual"])
        self.assertEqual(
            (s1["gross_pay"], s1["total_deduction"], s1["net_pay"]),
            ("3000.00", "400.00", "2600.00"),
        )
        # a draft recovery does not reduce the outstanding yet
        r = self.api.get(f"{API}employee-advances/{adv.pk}/").json()
        self.assertEqual((r["outstanding"], r["status"]), ("1000.00", "Paid"))

        self.submit_slip(s1["id"])
        slip = SalarySlip.objects.get(pk=s1["id"])
        gl = self.gl(slip)
        self.assertEqual(
            gl.get(account=self.cfg.employee_advance_account).credit, D("400.00")
        )
        self.assertEqual(
            gl.get(account=self.cfg.payroll_payable_account).credit, D("2600.00")
        )
        r = self.api.get(f"{API}employee-advances/{adv.pk}/").json()
        self.assertEqual(
            (r["recovered_amount"], r["outstanding"], r["status"]),
            ("400.00", "600.00", "Partly Recovered"),
        )
        self.assertEqual(advance_ledger_balance(self.w), D("600.00"))
        self.verify()

        s2 = self.make_slip(self.w, d(2026, 4, 7), d(2026, 4, 12))
        self.assertEqual(
            next(r for r in s2["details"] if r["system_key"] == "advance_recovery")[
                "amount"
            ],
            "400.00",
        )
        self.submit_slip(s2["id"])
        s3 = self.make_slip(self.w, d(2026, 4, 13), d(2026, 4, 18))
        self.assertEqual(
            next(r for r in s3["details"] if r["system_key"] == "advance_recovery")[
                "amount"
            ],
            "200.00",
        )
        self.submit_slip(s3["id"])
        r = self.api.get(f"{API}employee-advances/{adv.pk}/").json()
        self.assertEqual((r["outstanding"], r["status"]), ("0.00", "Recovered"))
        # nothing left to propose
        self.assertFalse(
            [
                r
                for r in self.make_slip(self.w, d(2026, 4, 19), d(2026, 4, 20))[
                    "details"
                ]
                if r["system_key"] == "advance_recovery"
            ]
        )
        self.verify()

    def test_advance_without_instalment_recovers_in_full_capped_by_pay(self):
        self.advance(self.w, "5000")
        s = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 2))  # gross 1000
        rec = next(r for r in s["details"] if r["system_key"] == "advance_recovery")
        self.assertEqual(rec["amount"], "1000.00")  # capped so net >= 0
        self.assertEqual(s["net_pay"], "0.00")
        self.submit_slip(s["id"])  # a zero net slip is fine
        self.verify()

    def test_recovery_cannot_exceed_outstanding_and_row_is_editable(self):
        adv = self.advance(self.w, "300")
        s = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        rec = next(r for r in s["details"] if r["system_key"] == "advance_recovery")
        comp = rec["component"]
        r = self.api.patch(
            f"{API}salary-slips/{s['id']}/",
            {"details": [{"component": comp, "amount": "1000", "advance": adv.pk}]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["details"][-1]["is_manual"])
        self.submit_slip(s["id"], expect=400)  # 1000 > 300 outstanding
        r = self.api.patch(
            f"{API}salary-slips/{s['id']}/",
            {"details": [{"component": comp, "amount": "100", "advance": adv.pk}]},
            format="json",
        )
        self.assertEqual(r.json()["net_pay"], "2900.00")
        # recompute keeps the person-edited recovery rather than re-proposing 300
        r = self.api.post(f"{API}salary-slips/{s['id']}/recompute/").json()
        self.assertEqual(r["total_deduction"], "100.00")
        self.submit_slip(s["id"])
        self.assertEqual(
            self.api.get(f"{API}employee-advances/{adv.pk}/").json()["outstanding"],
            "200.00",
        )

    def test_recovery_rows_must_name_an_advance_of_the_same_employee(self):
        other = self.employee("Other")
        adv = self.advance(other, "300")
        comp = SalaryComponent.objects.get(
            company=self.company, system_key=SystemComponent.ADVANCE_RECOVERY
        )
        for details in (
            [{"component": comp.pk, "amount": "10"}],  # no advance
            [
                {"component": comp.pk, "amount": "10", "advance": adv.pk}
            ],  # someone else's
        ):
            r = self.api.post(
                API + "salary-slips/",
                self.slip_payload(
                    self.w, d(2026, 4, 1), d(2026, 4, 3), details=details
                ),
                format="json",
            )
            self.assertEqual(r.status_code, 400, r.content)

    def test_advance_with_recoveries_cannot_be_cancelled(self):
        adv = self.advance(self.w, "1000", instalment="400")
        s = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(s["id"])
        r = self.api.post(f"{API}employee-advances/{adv.pk}/cancel/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("recoveries", str(r.json()))
        self.api.post(f"{API}salary-slips/{s['id']}/cancel/")
        self.assertEqual(
            self.api.post(f"{API}employee-advances/{adv.pk}/cancel/").status_code, 200
        )
        self.assertFalse(self.gl(adv).exists())
        self.verify()

    def test_advance_validation(self):
        base = {
            "employee": self.w.pk,
            "company": self.company.pk,
            "posting_date": "2026-04-01",
            "amount": "1000",
            "paid_from": self.cash.pk,
        }
        self.assertEqual(
            self.api.post(
                API + "employee-advances/", {**base, "amount": "0"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.api.post(
                API + "employee-advances/",
                {**base, "instalment_amount": "2000"},
                format="json",
            ).status_code,
            400,
        )
        expense = self.cfg.wage_expense_account
        self.assertEqual(
            self.api.post(
                API + "employee-advances/",
                {**base, "paid_from": expense.pk},
                format="json",
            ).status_code,
            400,
        )
        r = self.api.post(API + "employee-advances/", base, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertRegex(r.json()["name"], r"^EMP-ADV-\d{4}$")
        self.assertEqual(r.json()["status"], "Draft")

    def _paid_slip(self, upto=6):
        s = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, upto))
        self.submit_slip(s["id"])
        return s

    def pay(self, amount, date="2026-04-10", **extra):
        return self.api.post(
            API + "employee-payments/",
            {
                "employee": self.w.pk,
                "company": self.company.pk,
                "posting_date": date,
                "amount": amount,
                "paid_from": self.cash.pk,
                "mode": "Cash",
                **extra,
            },
            format="json",
        )

    def test_payment_cannot_exceed_wages_payable(self):
        self._paid_slip()  # payable 3000
        r = self.pay("3500")
        self.assertEqual(r.status_code, 201)  # drafting is fine ...
        self.assertEqual(
            self.api.post(
                f"{API}employee-payments/{r.json()['id']}/submit/"
            ).status_code,
            400,
        )
        self.assertFalse(GLEntry.objects.filter(voucher_no=r.json()["name"]).exists())

        p1 = self.pay("1000").json()
        self.assertEqual(
            self.api.post(f"{API}employee-payments/{p1['id']}/submit/").status_code, 200
        )
        self.assertEqual(payable_balance(self.w), D("2000.00"))
        p2 = self.pay("2000.01").json()
        self.assertEqual(
            self.api.post(f"{API}employee-payments/{p2['id']}/submit/").status_code, 400
        )
        p3 = self.pay("2000").json()
        self.assertEqual(
            self.api.post(f"{API}employee-payments/{p3['id']}/submit/").status_code, 200
        )
        self.assertEqual(payable_balance(self.w), D("0.00"))
        gl = self.gl(EmployeePayment.objects.get(pk=p3["id"]))
        self.assertEqual(gl.get(debit__gt=0).account, self.cfg.payroll_payable_account)
        self.assertEqual(gl.get(credit__gt=0).account, self.cash)
        self.verify()

        # cancelling the payment restores the payable
        self.api.post(f"{API}employee-payments/{p3['id']}/cancel/")
        self.assertEqual(payable_balance(self.w), D("2000.00"))
        self.verify()

    def test_payment_with_nothing_payable_and_bad_inputs(self):
        r = self.pay("10").json()
        self.assertEqual(
            self.api.post(f"{API}employee-payments/{r['id']}/submit/").status_code, 400
        )
        self.assertEqual(self.pay("0").status_code, 400)
        self.assertEqual(self.pay("10", mode="Cheque").status_code, 400)

    def test_slip_cannot_be_cancelled_after_its_wages_were_paid(self):
        s = self._paid_slip()
        p = self.pay("3000").json()
        self.api.post(f"{API}employee-payments/{p['id']}/submit/")
        r = self.api.post(f"{API}salary-slips/{s['id']}/cancel/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("paid", str(r.json()))
        self.api.post(f"{API}employee-payments/{p['id']}/cancel/")
        self.assertEqual(
            self.api.post(f"{API}salary-slips/{s['id']}/cancel/").status_code, 200
        )

    def test_summary_endpoint_and_service(self):
        self.advance(self.w, "1000", instalment="400")
        s = self.make_slip(self.w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(s["id"])
        p = self.pay("1000", date="2026-04-08").json()
        self.api.post(f"{API}employee-payments/{p['id']}/submit/")
        r = self.api.get(
            f"{API}employees/{self.w.pk}/summary/?from=2026-04-01&to=2026-04-30"
        )
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["days_present"], 18)
        self.assertEqual(body["gross_earned"], "3000.00")
        self.assertEqual(body["total_deductions"], "400.00")
        self.assertEqual(body["net_pay"], "2600.00")
        self.assertEqual(body["total_paid"], "1000.00")
        self.assertEqual(body["wages_payable"], "1600.00")
        self.assertEqual(body["advance_outstanding"], "600.00")
        self.assertEqual(len(body["recent_slips"]), 1)
        self.assertEqual(len(body["recent_payments"]), 1)
        # the service is a pure function (portal reuse)
        again = employee_summary(self.w, d(2026, 4, 1), d(2026, 4, 30))
        self.assertEqual(again["wages_payable"], D("1600.00"))
        self.assertEqual(
            self.api.get(
                f"{API}employees/{self.w.pk}/summary/?from=2026-05-01&to=2026-04-01"
            ).status_code,
            400,
        )


# ============================================================= payroll entry
class PayrollEntryTests(HRBase):
    def setUp(self):
        super().setUp()
        self.spice = self.api.post(
            API + "departments/", {"name": "Spice"}, format="json"
        ).json()["id"]
        self.w1 = self.employee("W1", department_id=self.spice)
        self.w2 = self.employee("W2")
        self.w3 = self.employee("W3")  # never gets a wage rate
        for w in (self.w1, self.w2):
            self.rate(w)
        for w in (self.w1, self.w2, self.w3):
            self.present(w, d(2026, 4, 1), d(2026, 4, 3))

    def entry(self, **extra):
        r = self.api.post(
            API + "payroll-entries/",
            {
                "company": self.company.pk,
                "posting_date": "2026-04-03",
                "start_date": "2026-04-01",
                "end_date": "2026-04-03",
                **extra,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_get_employees_honours_filters_and_dates(self):
        e = self.entry(department=self.spice)
        r = self.api.post(f"{API}payroll-entries/{e['id']}/get-employees/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([x["employee"] for x in r.json()["employees"]], [self.w1.pk])
        e2 = self.entry()
        r = self.api.post(f"{API}payroll-entries/{e2['id']}/get-employees/")
        self.assertEqual(len(r.json()["employees"]), 3)
        # idempotent
        r = self.api.post(f"{API}payroll-entries/{e2['id']}/get-employees/")
        self.assertEqual(len(r.json()["employees"]), 3)

    def test_bulk_create_submit_pay_and_cancel(self):
        # w2 already has a submitted slip for the period -> must be skipped
        self.submit_slip(self.make_slip(self.w2, d(2026, 4, 1), d(2026, 4, 3))["id"])
        e = self.entry()
        self.api.post(f"{API}payroll-entries/{e['id']}/get-employees/")
        r = self.api.post(f"{API}payroll-entries/{e['id']}/create-slips/")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(len(body["created"]), 1)
        skipped = {s["employee"]: s["reason"] for s in body["skipped"]}
        self.assertEqual(set(skipped), {self.w2.name, self.w3.name})
        self.assertIn("Overlaps", skipped[self.w2.name])
        self.assertIn("no wage rate", skipped[self.w3.name])
        # calling it again creates nothing new for the linked row
        again = self.api.post(f"{API}payroll-entries/{e['id']}/create-slips/").json()
        self.assertEqual(again["created"], [])

        # paying before submit is refused
        self.assertEqual(
            self.api.post(f"{API}payroll-entries/{e['id']}/make-payments/").status_code,
            400,
        )

        r = self.api.post(f"{API}payroll-entries/{e['id']}/submit/")
        self.assertEqual(r.status_code, 200, r.content)
        slip = SalarySlip.objects.get(name=body["created"][0])
        self.assertEqual(slip.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(slip.payroll_entry_id, e["id"])
        self.verify()

        r = self.api.post(
            f"{API}payroll-entries/{e['id']}/make-payments/",
            {"mode": "UPI", "reference_no": "UPI123", "posting_date": "2026-04-04"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["amount"], "1500.00")
        self.assertEqual(r.json()[0]["docstatus"], 1)
        self.assertEqual(payable_balance(self.w1), D("0.00"))
        # a second run pays nothing more
        self.assertEqual(
            self.api.post(f"{API}payroll-entries/{e['id']}/make-payments/").json(), []
        )
        self.verify()

        # the entry cannot be cancelled while its wages are paid out ...
        r = self.api.post(f"{API}payroll-entries/{e['id']}/cancel/")
        self.assertEqual(r.status_code, 400)
        slip.refresh_from_db()
        self.assertEqual(slip.docstatus, DocStatus.SUBMITTED)  # all or nothing
        # ... but can once the payment is cancelled
        pay = EmployeePayment.objects.get(salary_slip=slip)
        self.assertEqual(
            self.api.post(f"{API}employee-payments/{pay.pk}/cancel/").status_code, 200
        )
        self.assertEqual(
            self.api.post(f"{API}payroll-entries/{e['id']}/cancel/").status_code, 200
        )
        slip.refresh_from_db()
        self.assertEqual(slip.docstatus, DocStatus.CANCELLED)
        self.verify()

    def test_submit_is_all_or_nothing(self):
        e = self.entry()
        self.api.post(f"{API}payroll-entries/{e['id']}/get-employees/")
        self.assertEqual(
            self.api.post(f"{API}payroll-entries/{e['id']}/create-slips/")
            .json()["created"]
            .__len__(),
            2,
        )
        # make one slip unsubmittable: its employee gets a clashing submitted slip
        bad = SalarySlip.objects.filter(
            payroll_entry_id=e["id"], employee=self.w2
        ).get()
        good = SalarySlip.objects.filter(
            payroll_entry_id=e["id"], employee=self.w1
        ).get()
        clash = self.make_slip(self.w2, d(2026, 4, 3), d(2026, 4, 5))
        self.submit_slip(clash["id"])
        r = self.api.post(f"{API}payroll-entries/{e['id']}/submit/")
        self.assertEqual(r.status_code, 400, r.content)
        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual((good.docstatus, bad.docstatus), (0, 0))
        self.assertFalse(self.gl(good).exists())

    def test_entry_without_slips_cannot_be_submitted(self):
        e = self.entry()
        self.assertEqual(
            self.api.post(f"{API}payroll-entries/{e['id']}/submit/").status_code, 400
        )

    def test_payroll_entry_validation(self):
        r = self.api.post(
            API + "payroll-entries/",
            {
                "company": self.company.pk,
                "posting_date": "2026-04-03",
                "start_date": "2026-04-03",
                "end_date": "2026-04-01",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)


# ================================================================== RBAC
class RBACTests(HRBase):
    def role_user(self, email, role_name, permissions=None):
        user = User.objects.create_user(username=email, email=email, password="x")
        role = Role.objects.create(name=role_name)
        UserRole.objects.create(user=user, role=role)
        rows = permissions if permissions is not None else HR_ROLE_MATRIX[role_name]
        for label, flags in rows.items():
            RolePermission.objects.create(
                role=role, model_label=label, **{f: True for f in flags}
            )
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_user_without_roles_is_denied_everywhere(self):
        nobody = User.objects.create_user(
            username="n", email="n@example.com", password="x"
        )
        c = APIClient()
        c.force_authenticate(nobody)
        for path in (
            "employees/",
            "attendance/",
            "salary-slips/",
            "employee-payments/",
            "wage-rates/",
            "settings/",
            "attendance/muster-roll/?date=2026-04-01",
        ):
            self.assertEqual(c.get(API + path).status_code, 403, path)
        self.assertEqual(
            c.post(API + "departments/", {"name": "X"}, format="json").status_code, 403
        )
        anon = APIClient()
        self.assertIn(anon.get(API + "employees/").status_code, (401, 403))

    def test_hr_user_can_draft_but_not_submit_or_see_rates(self):
        c = self.role_user("u@example.com", "HR User")
        w = self.employee("Ramu")
        self.rate(w)
        self.present(w, d(2026, 4, 1), d(2026, 4, 2))
        self.assertEqual(c.get(API + "employees/").status_code, 200)
        r = c.post(
            API + "salary-slips/",
            self.slip_payload(w, d(2026, 4, 1), d(2026, 4, 2)),
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        slip_id = r.json()["id"]
        self.assertEqual(
            c.post(f"{API}salary-slips/{slip_id}/submit/").status_code, 403
        )
        self.assertEqual(
            c.post(f"{API}salary-slips/{slip_id}/recompute/").status_code, 200
        )
        self.assertEqual(c.get(API + "wage-rates/").status_code, 403)
        self.assertEqual(c.get(API + "settings/").status_code, 403)
        self.assertEqual(
            c.post(API + "departments/", {"name": "X"}, format="json").status_code, 403
        )
        # muster roll: HR User may read and write attendance
        self.assertEqual(
            c.get(API + "attendance/muster-roll/?date=2026-04-01").status_code, 200
        )
        r = c.post(
            API + "attendance/muster-roll/",
            {"date": "2026-04-05", "rows": [{"employee": w.pk, "status": "Present"}]},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        # payment actions need payment rights
        entry = c.post(
            API + "payroll-entries/",
            {
                "company": self.company.pk,
                "posting_date": "2026-04-03",
                "start_date": "2026-04-01",
                "end_date": "2026-04-03",
            },
            format="json",
        ).json()
        self.assertEqual(
            c.post(f"{API}payroll-entries/{entry['id']}/make-payments/").status_code,
            403,
        )

    def test_a_create_only_role_cannot_use_the_upsert_to_overwrite(self):
        c = self.role_user(
            "c@example.com",
            "Muster Clerk",
            {"hr.Attendance": ("can_read", "can_create"), "hr.Employee": ("can_read",)},
        )
        w = self.employee("Ramu")
        r = c.post(
            API + "attendance/muster-roll/",
            {"date": "2026-04-05", "rows": [{"employee": w.pk, "status": "Present"}]},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_hr_manager_has_full_access(self):
        c = self.role_user("m@example.com", "HR Manager")
        w = self.employee("Ramu")
        self.assertEqual(
            c.post(
                API + "wage-rates/",
                {"employee": w.pk, "from_date": "2026-01-01", "daily_rate": "500"},
                format="json",
            ).status_code,
            201,
        )
        self.present(w, d(2026, 4, 1), d(2026, 4, 2))
        s = c.post(
            API + "salary-slips/",
            self.slip_payload(w, d(2026, 4, 1), d(2026, 4, 2)),
            format="json",
        ).json()
        self.assertEqual(
            c.post(f"{API}salary-slips/{s['id']}/submit/").status_code, 200
        )
        self.assertEqual(
            c.post(f"{API}salary-slips/{s['id']}/cancel/").status_code, 200
        )

    def test_aadhaar_is_masked_unless_the_caller_can_write_employees(self):
        w = self.employee("Ramu", aadhaar="123456789012")
        reader = self.role_user(
            "r@example.com", "Reader", {"hr.Employee": ("can_read",)}
        )
        body = reader.get(f"{API}employees/{w.pk}/").json()
        self.assertEqual(body["aadhaar"], "XXXX-XXXX-9012")
        self.assertEqual(
            reader.get(API + "employees/").json()["results"][0]["aadhaar"],
            "XXXX-XXXX-9012",
        )
        self.assertEqual(
            self.api.get(f"{API}employees/{w.pk}/").json()["aadhaar"], "123456789012"
        )
        manager = self.role_user("m@example.com", "HR Manager")
        self.assertEqual(
            manager.get(f"{API}employees/{w.pk}/").json()["aadhaar"], "123456789012"
        )

    def test_audit_log_never_holds_the_full_aadhaar(self):
        r = self.api.post(
            API + "employees/",
            {
                "first_name": "S",
                "company": self.company.pk,
                "date_of_joining": "2026-02-01",
                "aadhaar": "123456789012",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        log = AuditLog.objects.filter(
            model_label="hr.Employee", action="create"
        ).latest("pk")
        self.assertEqual(log.changes["aadhaar"], "XXXX-XXXX-9012")
        self.api.patch(
            f"{API}employees/{r.json()['id']}/",
            {"aadhaar": "999988887777"},
            format="json",
        )
        log = AuditLog.objects.filter(
            model_label="hr.Employee", action="update"
        ).latest("pk")
        self.assertEqual(log.changes["aadhaar"], "XXXX-XXXX-7777")

    def test_submit_and_cancel_are_audited(self):
        w = self.employee("Ramu")
        self.rate(w)
        self.att(w, d(2026, 4, 1))
        s = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 1))
        self.submit_slip(s["id"])
        self.api.post(f"{API}salary-slips/{s['id']}/cancel/")
        actions = set(
            AuditLog.objects.filter(model_label="hr.SalarySlip").values_list(
                "action", flat=True
            )
        )
        self.assertTrue({"create", "submit", "cancel"} <= actions)


# ============================================================== integrity
class IntegrityTests(HRBase):
    def test_clean_flow_is_green_and_tampering_is_detected(self):
        w = self.employee("Ramu")
        self.rate(w)
        self.present(w, d(2026, 4, 1), d(2026, 4, 6))
        self.advance(w, "1000", instalment="400")
        s = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 6))
        self.submit_slip(s["id"])
        p = self.api.post(
            API + "employee-payments/",
            {
                "employee": w.pk,
                "company": self.company.pk,
                "posting_date": "2026-04-07",
                "amount": "1000",
                "paid_from": self.bank.pk,
                "mode": "Bank",
            },
            format="json",
        ).json()
        self.api.post(f"{API}employee-payments/{p['id']}/submit/")
        errors, warnings = integrity.check()
        self.assertEqual((errors, warnings), ([], []))
        self.verify()

        # a slip total that no longer matches its rows
        SalarySlip.objects.filter(pk=s["id"]).update(net_pay=D("1.00"))
        errors, _ = integrity.check()
        self.assertTrue(any("do not match its rows" in e for e in errors), errors)
        with self.assertRaises(CommandError):
            self.verify()
        SalarySlip.objects.filter(pk=s["id"]).update(net_pay=D("2600.00"))

        # a payment whose GL went missing
        GLEntry.objects.filter(voucher_no=p["name"]).update(is_cancelled=True)
        errors, _ = integrity.check()
        self.assertTrue(any("Employee payment" in e for e in errors), errors)
        GLEntry.objects.filter(voucher_no=p["name"]).update(is_cancelled=False)

        # advance outstanding vs the ledger: a stray journal-style GL row
        adv = EmployeeAdvance.objects.get()
        GLEntry.objects.filter(voucher_no=adv.name, debit__gt=0).update(
            party="SOMEONE-ELSE"
        )
        errors, _ = integrity.check()
        self.assertTrue(errors)

    def test_repost_hr_rebuilds_draft_slips(self):
        w = self.employee("Ramu")
        self.rate(w)
        self.att(w, d(2026, 4, 1))
        s = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 2))
        SalarySlip.objects.filter(pk=s["id"]).update(
            gross_pay=D("1.00"), net_pay=D("1.00")
        )
        out = StringIO()
        call_command("repost_hr", stdout=out)
        self.assertEqual(SalarySlip.objects.get(pk=s["id"]).gross_pay, D("500.00"))
        self.assertIn("Rebuilt 1 draft", out.getvalue())

    def test_seed_defaults_creates_hr_roles_and_setup(self):
        call_command("seed_defaults", stdout=StringIO())
        call_command("seed_defaults", stdout=StringIO())  # idempotent
        self.assertTrue(Role.objects.filter(name="HR User").exists())
        manager = Role.objects.get(name="HR Manager")
        self.assertTrue(
            RolePermission.objects.get(
                role=manager, model_label="hr.SalarySlip"
            ).can_submit
        )
        self.assertFalse(
            RolePermission.objects.get(
                role=Role.objects.get(name="HR User"), model_label="hr.SalarySlip"
            ).can_submit
        )
        acme = Company.objects.get(abbr="ACME")
        cfg = HRSettings.objects.get(company=acme)
        self.assertEqual(
            cfg.payroll_payable_account.root_type, AccountRootType.LIABILITY
        )
        self.assertEqual(cfg.default_payment_account.account_type, AccountType.CASH)
        self.verify()

    def test_hr_not_set_up_gives_a_clear_error(self):
        other = Company.objects.create(name="Bare", abbr="BARE")
        e = Employee.objects.create(
            name="EMP-BARE",
            first_name="B",
            company=other,
            date_of_joining=d(2026, 1, 1),
        )
        EmployeeWageRate.objects.create(
            employee=e, from_date=d(2026, 1, 1), daily_rate=D("100")
        )
        Attendance.objects.create(employee=e, date=d(2026, 4, 1), status=P)
        r = self.api.post(
            API + "salary-slips/",
            {
                "employee": e.pk,
                "company": other.pk,
                "posting_date": "2026-04-01",
                "start_date": "2026-04-01",
                "end_date": "2026-04-01",
            },
            format="json",
            HTTP_X_COMPANY_ID=str(other.pk),
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("System salary components", str(r.json()))
        r = self.api.post(
            API + "settings/ensure-setup/",
            {"company": other.pk},
            format="json",
            HTTP_X_COMPANY_ID=str(other.pk),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(HRSettings.objects.filter(company=other).exists())


class LeaveAndReportTests(HRBase):
    def setUp(self):
        super().setUp()
        self.lt = LeaveType.objects.create(
            company=self.company,
            name="Casual Leave",
            max_days_allowed=D("12.00"),
        )

    def test_leave_type_crud_and_uniqueness(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeaveType.objects.create(
                company=self.company,
                name="Casual Leave",
            )
        other = Company.objects.create(name="Other Co", abbr="OC")
        lt_other = LeaveType.objects.create(
            company=other,
            name="Casual Leave",
        )
        self.assertEqual(lt_other.name, "Casual Leave")

    def test_leave_allocation_and_balance_check(self):
        w = self.employee("Sita")
        alloc = LeaveAllocation.objects.create(
            name="HR-LAL-2026-00001",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 1, 1),
            to_date=d(2026, 12, 31),
            total_leaves_allocated=D("5.00"),
        )
        alloc.submit()

        # Application requesting more than allocated should fail on submit
        app = LeaveApplication.objects.create(
            name="HR-LAP-2026-00001",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 4, 1),
            to_date=d(2026, 4, 10),
            total_leave_days=D("10.00"),
        )
        with self.assertRaises(ValidationError):
            app.submit()

        # Application within balance succeeds
        app.to_date = d(2026, 4, 3)
        app.total_leave_days = D("3.00")
        app.save()
        app.submit()
        self.assertEqual(app.docstatus, DocStatus.SUBMITTED)

        # Further application exceeding remaining balance fails
        app2 = LeaveApplication.objects.create(
            name="HR-LAP-2026-00002",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 4, 5),
            to_date=d(2026, 4, 8),
            total_leave_days=D("3.00"),  # only 2 left
        )
        with self.assertRaises(ValidationError):
            app2.submit()

    def test_leave_application_marks_attendance_and_reverts_on_cancel(self):
        w = self.employee("Geeta")
        alloc = LeaveAllocation.objects.create(
            name="HR-LAL-2026-00002",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 1, 1),
            to_date=d(2026, 12, 31),
            total_leaves_allocated=D("10.00"),
        )
        alloc.submit()

        app = LeaveApplication.objects.create(
            name="HR-LAP-2026-00003",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 5, 1),
            to_date=d(2026, 5, 2),
            total_leave_days=D("2.00"),
        )
        app.submit()

        # Attendance for both days should be marked as Paid Leave
        att1 = Attendance.objects.get(employee=w, date=d(2026, 5, 1))
        att2 = Attendance.objects.get(employee=w, date=d(2026, 5, 2))
        self.assertEqual(att1.status, AttendanceStatus.PAID_LEAVE)
        self.assertEqual(att2.status, AttendanceStatus.PAID_LEAVE)
        self.assertIn("HR-LAP-2026-00003", att1.remarks)

        # Cancel should revert attendance
        app.cancel()
        self.assertFalse(
            Attendance.objects.filter(employee=w, date=d(2026, 5, 1)).exists()
        )
        self.assertFalse(
            Attendance.objects.filter(employee=w, date=d(2026, 5, 2)).exists()
        )

    def test_leave_locked_by_salary_slip_cannot_be_submitted(self):
        w = self.employee("Mohan")
        self.rate(w)
        self.present(w, d(2026, 4, 1), d(2026, 4, 5))
        slip = self.make_slip(w, d(2026, 4, 1), d(2026, 4, 5))
        self.submit_slip(slip["id"])

        alloc = LeaveAllocation.objects.create(
            name="HR-LAL-2026-00003",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 1, 1),
            to_date=d(2026, 12, 31),
            total_leaves_allocated=D("10.00"),
        )
        alloc.submit()

        # Cannot submit leave covering locked date 2026-04-03
        app = LeaveApplication.objects.create(
            name="HR-LAP-2026-00004",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 4, 3),
            to_date=d(2026, 4, 4),
            total_leave_days=D("2.00"),
        )
        with self.assertRaises(ValidationError):
            app.submit()

    def test_leave_balance_report(self):
        w = self.employee("Anita")
        alloc = LeaveAllocation.objects.create(
            name="HR-LAL-2026-00004",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 1, 1),
            to_date=d(2026, 12, 31),
            total_leaves_allocated=D("8.00"),
        )
        alloc.submit()

        app = LeaveApplication.objects.create(
            name="HR-LAP-2026-00005",
            company=self.company,
            employee=w,
            leave_type=self.lt,
            from_date=d(2026, 6, 1),
            to_date=d(2026, 6, 2),
            total_leave_days=D("2.00"),
        )
        app.submit()

        r = self.api.get(
            API + "reports/leave-balance/",
            {"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(
            any(
                row["employee_name"] == "Anita" and row["balance"] == "6.00"
                for row in data["rows"]
            )
        )

    def test_salary_and_wage_register_reports(self):
        # A Monthly slip needs the income-tax rates in force (see hr/test_tds.py).
        from hr.services.tax_rates import seed_income_tax_rates

        seed_income_tax_rates()
        w_daily = self.employee("DailyWorker", pay_type=PayType.DAILY)
        self.rate(w_daily, daily="400", ot="50")
        self.present(w_daily, d(2026, 4, 1), d(2026, 4, 5))
        slip_daily = self.make_slip(w_daily, d(2026, 4, 1), d(2026, 4, 5))
        self.submit_slip(slip_daily["id"])

        w_monthly = self.employee("MonthlyStaff", pay_type=PayType.MONTHLY)
        self.rate(w_monthly, daily=None, monthly="30000", ot="100")
        self.present(w_monthly, d(2026, 4, 1), d(2026, 4, 5))
        slip_monthly = self.make_slip(w_monthly, d(2026, 4, 1), d(2026, 4, 5))
        self.submit_slip(slip_monthly["id"])

        # Salary register has both slips
        r_sal = self.api.get(
            API + "reports/salary-register/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
        )
        self.assertEqual(r_sal.status_code, 200, r_sal.content)
        sal_data = r_sal.json()
        self.assertEqual(len(sal_data["rows"]), 2)
        self.assertIn("total_net_pay", sal_data)

        # Wage register has only daily wager
        r_wage = self.api.get(
            API + "reports/wage-register/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
        )
        self.assertEqual(r_wage.status_code, 200, r_wage.content)
        wage_data = r_wage.json()
        self.assertEqual(len(wage_data["rows"]), 1)
        self.assertEqual(wage_data["rows"][0]["employee_name"], "DailyWorker")
        self.assertEqual(wage_data["rows"][0]["daily_rate"], "400.00")
        self.assertIn("total_basic_wages", wage_data)
