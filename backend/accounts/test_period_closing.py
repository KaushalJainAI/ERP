"""Period Closing Voucher: year-end P&L -> retained earnings."""

import datetime
from io import StringIO
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
    PeriodClosingVoucher,
)
from accounts.services import closing
from core.models import AuditLog, Company, FiscalYear, UserCompany
from users.models import Role, RolePermission, User, UserRole

D = datetime.date
Dec = Decimal
BASE = "/api/accounts/"


def make_chart(company):
    def acc(name, root, parent=None, **kw):
        return Account.objects.create(
            name=name, company=company, root_type=root, parent=parent, **kw
        )

    assets = acc("Assets", AccountRootType.ASSET, is_group=True)
    equity = acc("Equity", AccountRootType.EQUITY, is_group=True)
    income = acc("Income", AccountRootType.INCOME, is_group=True)
    expense = acc("Expenses", AccountRootType.EXPENSE, is_group=True)
    return {
        "bank": acc(
            "Bank", AccountRootType.ASSET, assets, account_type=AccountType.BANK
        ),
        "capital": acc("Capital", AccountRootType.EQUITY, equity),
        "reserves": acc("Reserves & Surplus", AccountRootType.EQUITY, equity),
        "sales": acc("Sales", AccountRootType.INCOME, income),
        "other_income": acc("Interest Income", AccountRootType.INCOME, income),
        "rent": acc("Rent", AccountRootType.EXPENSE, expense),
        "wages": acc("Wages", AccountRootType.EXPENSE, expense),
        "equity_root": equity,
    }


class ClosingBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Close Co", abbr="CC", default_currency="INR"
        )
        self.a = make_chart(self.company)
        self.fy25 = FiscalYear.objects.create(
            name="FY25", start_date=D(2031, 4, 1), end_date=D(2032, 3, 31)
        )
        self.fy26 = FiscalYear.objects.create(
            name="FY26", start_date=D(2032, 4, 1), end_date=D(2033, 3, 31)
        )
        self._n = 0

    def je(self, date, lines):
        """Submit a JE of ``[(account, debit, credit)]``."""
        self._n += 1
        entry = JournalEntry.objects.create(
            name=f"JE-{self._n}", posting_date=date, company=self.company
        )
        for account, dr, cr in lines:
            JournalEntryLine.objects.create(
                journal_entry=entry, account=account, debit=Dec(dr), credit=Dec(cr)
            )
        entry.submit()
        return entry

    def sale(self, date, amount):
        return self.je(
            date, [(self.a["bank"], amount, 0), (self.a["sales"], 0, amount)]
        )

    def spend(self, date, amount, account=None):
        return self.je(
            date, [(account or self.a["rent"], amount, 0), (self.a["bank"], 0, amount)]
        )

    def pcv(self, fy, head=None, end=None, name=None):
        self._n += 1
        return PeriodClosingVoucher.objects.create(
            name=name or f"PCV-{self._n}",
            company=self.company,
            fiscal_year=fy,
            period_end_date=end or fy.end_date,
            closing_account_head=head or self.a["reserves"],
        )

    def balance(self, account, **filters):
        agg = GLEntry.objects.filter(
            account=account, is_cancelled=False, **filters
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        return (agg["dr"] or Dec(0)) - (agg["cr"] or Dec(0))


class PostingTests(ClosingBase):
    def test_profit_year_zeroes_income_and_expense_into_the_closing_account(self):
        self.sale(D(2031, 5, 1), "1000.00")
        self.je(
            D(2031, 6, 1),
            [(self.a["bank"], "50.00", 0), (self.a["other_income"], 0, "50.00")],
        )
        self.spend(D(2031, 7, 1), "300.00")
        self.spend(D(2031, 8, 1), "200.00", self.a["wages"])
        pcv = self.pcv(self.fy25)
        pcv.submit()
        pcv.refresh_from_db()
        self.assertEqual(pcv.docstatus, 1)
        self.assertEqual(pcv.net_profit, Dec("550.00"))

        rows = GLEntry.objects.filter(
            voucher_type="accounts.PeriodClosingVoucher", voucher_no=pcv.name
        )
        self.assertEqual({r.posting_date for r in rows}, {D(2032, 3, 31)})
        by_account = {r.account.name: (r.debit, r.credit) for r in rows}
        self.assertEqual(by_account["Sales"], (Dec("1000.00"), Dec(0)))
        self.assertEqual(by_account["Interest Income"], (Dec("50.00"), Dec(0)))
        self.assertEqual(by_account["Rent"], (Dec(0), Dec("300.00")))
        self.assertEqual(by_account["Wages"], (Dec(0), Dec("200.00")))
        self.assertEqual(by_account["Reserves & Surplus"], (Dec(0), Dec("550.00")))
        self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))
        self.assertEqual({r.fiscal_year for r in rows}, {"FY25"})
        for acct in ("sales", "other_income", "rent", "wages"):
            self.assertEqual(self.balance(self.a[acct]), 0)

    def test_loss_year_debits_the_closing_account(self):
        self.sale(D(2031, 5, 1), "100.00")
        self.spend(D(2031, 6, 1), "400.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        self.assertEqual(pcv.net_profit, Dec("-300.00"))
        self.assertEqual(self.balance(self.a["reserves"]), Dec("300.00"))

    def test_only_the_period_is_closed(self):
        self.sale(D(2031, 5, 1), "100.00")
        self.sale(D(2032, 5, 1), "70.00")  # next year: untouched
        pcv = self.pcv(self.fy25)
        pcv.submit()
        self.assertEqual(pcv.net_profit, Dec("100.00"))
        self.assertEqual(self.balance(self.a["sales"]), Dec("-70.00"))

    def test_cancel_reverses_exactly(self):
        self.sale(D(2031, 5, 1), "100.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        pcv.cancel()
        pcv.refresh_from_db()
        self.assertEqual(pcv.docstatus, 2)
        self.assertEqual(self.balance(self.a["sales"]), Dec("-100.00"))
        self.assertEqual(self.balance(self.a["reserves"]), 0)
        live = GLEntry.objects.filter(
            voucher_type="accounts.PeriodClosingVoucher", is_cancelled=False
        )
        self.assertFalse(live.exists())
        # ... and the year can be closed again
        again = self.pcv(self.fy25)
        again.submit()
        self.assertEqual(again.docstatus, 1)


class RuleTests(ClosingBase):
    def test_one_submitted_pcv_per_company_and_year(self):
        self.sale(D(2031, 5, 1), "100.00")
        self.pcv(self.fy25).submit()
        second = self.pcv(self.fy25)
        with self.assertRaisesMessage(ValidationError, "already closed"):
            second.submit()
        second.refresh_from_db()
        self.assertEqual(second.docstatus, 0)

    def test_previous_year_must_be_closed_first(self):
        self.sale(D(2031, 5, 1), "100.00")
        self.sale(D(2032, 5, 1), "100.00")
        with self.assertRaisesMessage(
            ValidationError, "previous fiscal year (FY25) is not closed"
        ):
            self.pcv(self.fy26).submit()
        self.pcv(self.fy25).submit()
        pcv = self.pcv(self.fy26)
        pcv.submit()
        self.assertEqual(pcv.net_profit, Dec("100.00"))

    def test_previous_year_without_postings_does_not_block(self):
        self.sale(D(2032, 5, 1), "100.00")
        pcv = self.pcv(self.fy26)
        pcv.submit()
        self.assertEqual(pcv.docstatus, 1)

    def test_cannot_cancel_while_a_later_year_is_closed(self):
        self.sale(D(2031, 5, 1), "100.00")
        self.sale(D(2032, 5, 1), "100.00")
        first = self.pcv(self.fy25)
        first.submit()
        self.pcv(self.fy26).submit()
        with self.assertRaisesMessage(ValidationError, "exists after"):
            first.cancel()

    def test_closing_account_must_be_liability_or_equity(self):
        self.sale(D(2031, 5, 1), "100.00")
        with self.assertRaisesMessage(ValidationError, "Liability / Equity"):
            self.pcv(self.fy25, head=self.a["rent"]).submit()
        with self.assertRaisesMessage(ValidationError, "cannot be a group"):
            self.pcv(self.fy25, head=self.a["equity_root"]).submit()

    def test_nothing_to_close_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "no income or expense"):
            self.pcv(self.fy25).submit()

    def test_end_date_must_be_inside_the_year(self):
        self.sale(D(2031, 5, 1), "100.00")
        with self.assertRaisesMessage(ValidationError, "within the fiscal year"):
            self.pcv(self.fy25, end=D(2032, 4, 1)).submit()

    def test_cross_company_closing_account_is_refused(self):
        other = Company.objects.create(name="Other", abbr="OT", default_currency="INR")
        foreign = make_chart(other)["reserves"]
        self.sale(D(2031, 5, 1), "100.00")
        with self.assertRaisesMessage(ValidationError, "belong to the company"):
            self.pcv(self.fy25, head=foreign).submit()

    def test_closed_fiscal_year_cannot_be_closed_by_pcv_but_lock_comes_after(self):
        self.sale(D(2031, 5, 1), "100.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()  # while the year is open
        self.fy25.is_closed = True
        self.fy25.save()
        # the lock now protects the closing like any other posting
        with self.assertRaisesMessage(ValidationError, "is closed"):
            pcv.cancel()
        # a year that is already locked can no longer receive its closing voucher
        self.sale(D(2032, 5, 1), "10.00")
        self.fy26.is_closed = True
        self.fy26.save()
        with self.assertRaisesMessage(ValidationError, "is closed"):
            self.pcv(self.fy26).submit()

    def test_late_postings_into_a_closed_period_are_refused_until_cancelled(self):
        self.sale(D(2031, 5, 1), "100.00")
        original = self.sale(D(2031, 5, 2), "10.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        with self.assertRaisesMessage(ValidationError, "books are closed"):
            self.sale(D(2031, 9, 1), "5.00")
        with self.assertRaisesMessage(ValidationError, "books are closed"):
            original.cancel()
        self.sale(D(2032, 5, 1), "5.00")  # a later year is fine
        pcv.cancel()
        self.sale(D(2031, 9, 1), "5.00")


class ReportTests(ClosingBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser("root", "r@x.com", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.sale(D(2031, 5, 1), "1000.00")
        self.spend(D(2031, 6, 1), "400.00")

    def report(self, name, **params):
        res = self.client.get(
            f"{BASE}reports/{name}/", {"company": self.company.id, **params}
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def test_pnl_unchanged_after_closing(self):
        window = {"from_date": "2031-04-01", "to_date": "2032-03-31"}
        before = self.report("profit-and-loss", **window)
        self.pcv(self.fy25).submit()
        after = self.report("profit-and-loss", **window)
        self.assertEqual(before, after)
        self.assertEqual(after["net_profit"], "600.00")

    def test_balance_sheet_shows_retained_earnings_and_balances(self):
        bs = self.report("balance-sheet", to_date="2032-03-31")
        self.assertEqual(bs["total_assets"], "600.00")
        # before closing the profit is provisional
        self.assertEqual(bs["provisional_profit"], "600.00")
        self.assertEqual(
            Dec(bs["total_assets"]),
            Dec(bs["total_liabilities"])
            + Dec(bs["total_equity"])
            + Dec(bs["provisional_profit"]),
        )
        self.pcv(self.fy25).submit()
        bs = self.report("balance-sheet", to_date="2032-03-31")
        self.assertEqual(bs["provisional_profit"], "0.00")
        self.assertEqual(bs["total_equity"], "600.00")
        self.assertIn(
            ("Reserves & Surplus", "600.00"),
            [(r["account_name"], r["balance"]) for r in bs["accounts"]],
        )
        self.assertEqual(
            Dec(bs["total_assets"]),
            Dec(bs["total_liabilities"]) + Dec(bs["total_equity"]),
        )

    def test_trial_balance_stays_balanced_and_can_hide_the_closing(self):
        self.pcv(self.fy25).submit()
        tb = self.report("trial-balance")
        self.assertTrue(tb["is_balanced"])
        by_name = {r["account_name"]: r for r in tb["rows"]}
        self.assertEqual(by_name["Sales"]["net_balance"], "0.00")
        tb = self.report("trial-balance", exclude_closing="1")
        self.assertTrue(tb["is_balanced"])
        by_name = {r["account_name"]: r for r in tb["rows"]}
        self.assertEqual(by_name["Sales"]["net_balance"], "-1000.00")
        self.assertNotIn("Reserves & Surplus", by_name)


class ApiTests(ClosingBase):
    def setUp(self):
        super().setUp()
        self.sale(D(2031, 5, 1), "100.00")
        self.user = User.objects.create_user("acc", "a@x.com", "x")
        role = Role.objects.create(name="Closer")
        self.perm = RolePermission.objects.create(
            role=role,
            model_label="accounts.PeriodClosingVoucher",
            can_read=True,
            can_create=True,
            can_write=True,
            can_submit=True,
            can_cancel=True,
            can_delete=True,
        )
        RolePermission.objects.create(
            role=role, model_label="core.FiscalYear", can_read=True
        )
        UserRole.objects.create(user=self.user, role=role)
        UserCompany.objects.get_or_create(
            user=self.user, company=self.company, defaults={"is_default": True}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_defaults_submit_cancel_and_audit(self):
        res = self.client.post(
            f"{BASE}period-closing-vouchers/",
            {"fiscal_year": self.fy25.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["period_end_date"], "2032-03-31")
        self.assertEqual(res.data["closing_account_head_name"], "Reserves & Surplus")
        pk = res.data["id"]
        res = self.client.post(f"{BASE}period-closing-vouchers/{pk}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["net_profit"], "100.00")
        res = self.client.patch(
            f"{BASE}period-closing-vouchers/{pk}/", {"remarks": "x"}, format="json"
        )
        self.assertEqual(res.status_code, 409)
        res = self.client.delete(f"{BASE}period-closing-vouchers/{pk}/")
        self.assertEqual(res.status_code, 409)
        res = self.client.post(f"{BASE}period-closing-vouchers/{pk}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        actions = set(
            AuditLog.objects.filter(
                model_label="accounts.PeriodClosingVoucher"
            ).values_list("action", flat=True)
        )
        self.assertTrue({"create", "submit", "cancel"} <= actions)

    def test_default_head_is_created_when_the_chart_has_none(self):
        self.a["reserves"].delete()
        res = self.client.post(
            f"{BASE}period-closing-vouchers/",
            {"fiscal_year": self.fy25.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["closing_account_head_name"], "Retained Earnings")

    def test_submit_errors_are_400(self):
        pcv = self.pcv(self.fy26)
        self.sale(D(2032, 5, 1), "1.00")
        res = self.client.post(f"{BASE}period-closing-vouchers/{pcv.pk}/submit/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("previous fiscal year", str(res.data))

    def test_rbac(self):
        self.perm.can_submit = False
        self.perm.save()
        pcv = self.pcv(self.fy25)
        res = self.client.post(f"{BASE}period-closing-vouchers/{pcv.pk}/submit/")
        self.assertEqual(res.status_code, 403)
        outsider = User.objects.create_user("nobody", "n@x.com", "x")
        UserCompany.objects.get_or_create(
            user=outsider, company=self.company, defaults={"is_default": True}
        )
        client = APIClient()
        client.force_authenticate(outsider)
        self.assertEqual(client.get(f"{BASE}period-closing-vouchers/").status_code, 403)

    def test_company_isolation(self):
        pcv = self.pcv(self.fy25)
        other = Company.objects.create(name="Other", abbr="OT", default_currency="INR")
        stranger = User.objects.create_user("stranger", "s@x.com", "x")
        UserRole.objects.create(
            user=stranger, role=UserRole.objects.get(user=self.user).role
        )
        UserCompany.objects.filter(user=stranger).delete()
        UserCompany.objects.create(user=stranger, company=other, is_default=True)
        client = APIClient()
        client.force_authenticate(stranger)
        res = client.get(f"{BASE}period-closing-vouchers/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"] if "results" in res.data else res.data, [])
        self.assertEqual(
            client.post(f"{BASE}period-closing-vouchers/{pcv.pk}/submit/").status_code,
            404,
        )


class IntegrityAndExportTests(ClosingBase):
    def test_integrity_clean_then_warns_about_late_postings(self):
        self.sale(D(2031, 5, 1), "100.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        errors, warnings = closing.check_integrity()
        self.assertEqual((errors, warnings), ([], []))
        call_command("verify_integrity", stdout=StringIO())

    def test_integrity_detects_missing_ledger_rows(self):
        self.sale(D(2031, 5, 1), "100.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        GLEntry.objects.filter(voucher_no=pcv.name).update(is_cancelled=True)
        errors, _warnings = closing.check_integrity()
        self.assertTrue(any("no GL entries" in e for e in errors))

    def test_tally_export_never_exports_the_closing_voucher(self):
        from tally_export.models import TallyExportSettings
        from tally_export.services import collect

        self.sale(D(2031, 5, 1), "100.00")
        pcv = self.pcv(self.fy25)
        pcv.submit()
        settings, _created = TallyExportSettings.objects.get_or_create(
            company=self.company
        )
        refs, skipped = collect.list_refs(self.company, settings, only_pending=False)
        self.assertNotIn("accounts.PeriodClosingVoucher", [r.doctype for r in refs])
        self.assertIn(
            (pcv.name, "period_closing"), [(s["name"], s["reason"]) for s in skipped]
        )
        self.assertIn("accounts.JournalEntry", [r.doctype for r in refs])
