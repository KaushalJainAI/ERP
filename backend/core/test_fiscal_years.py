import datetime

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from core.management.commands.verify_integrity import Command as VerifyCommand
from core.models import FiscalYear


def _fy(name, start, end):
    return FiscalYear.objects.create(
        name=name, start_date=start, end_date=end, is_closed=False
    )


@pytest.mark.no_default_fiscal_year
@pytest.mark.django_db
class TestFiscalYearEnsure:
    def test_date_covered_by_existing_year_creates_nothing(self):
        _fy("2026-27", datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        fy = FiscalYear.ensure_for_date(datetime.date(2027, 3, 15))
        assert fy.name == "2026-27"
        assert FiscalYear.objects.count() == 1

    def test_april_creates_next_year(self):
        _fy("2026-27", datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        fy = FiscalYear.ensure_for_date(datetime.date(2027, 4, 2))
        assert fy.name == "2027-28"
        assert fy.start_date == datetime.date(2027, 4, 1)
        assert fy.end_date == datetime.date(2028, 3, 31)
        assert fy.is_closed is False
        assert FiscalYear.objects.count() == 2

    def test_second_run_is_idempotent(self):
        _fy("2026-27", datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        first = FiscalYear.ensure_for_date(datetime.date(2027, 4, 2))
        second = FiscalYear.ensure_for_date(datetime.date(2027, 4, 2))
        assert second.pk == first.pk
        assert FiscalYear.objects.count() == 2

    def test_date_before_earliest_year_creates_nothing(self):
        _fy("2026-27", datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        assert FiscalYear.ensure_for_date(datetime.date(2025, 6, 1)) is None
        assert FiscalYear.objects.count() == 1

    def test_existing_name_that_does_not_cover_date_creates_nothing(self):
        # "2027-28" exists but only up to September; a December date is not covered
        # and the name is taken, so nothing is created.
        _fy("2027-28", datetime.date(2027, 4, 1), datetime.date(2027, 9, 30))
        assert FiscalYear.ensure_for_date(datetime.date(2027, 12, 1)) is None
        assert FiscalYear.objects.count() == 1

    def test_would_overlap_existing_year_creates_nothing(self):
        _fy("2026-27", datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        _fy("ODD", datetime.date(2027, 10, 1), datetime.date(2028, 9, 30))
        assert FiscalYear.ensure_for_date(datetime.date(2027, 6, 1)) is None
        assert FiscalYear.objects.count() == 2

    def test_clean_refuses_overlap(self):
        _fy("2027-28", datetime.date(2027, 4, 1), datetime.date(2028, 3, 31))
        clash = FiscalYear(
            name="CLASH",
            start_date=datetime.date(2027, 6, 1),
            end_date=datetime.date(2028, 5, 31),
        )
        with pytest.raises(ValidationError):
            clash.clean()

    def test_clean_allows_adjacent_year(self):
        _fy("2027-28", datetime.date(2027, 4, 1), datetime.date(2028, 3, 31))
        nxt = FiscalYear(
            name="2028-29",
            start_date=datetime.date(2028, 4, 1),
            end_date=datetime.date(2029, 3, 31),
        )
        nxt.clean()

    def test_verify_integrity_reports_overlap(self):
        _fy("A", datetime.date(2027, 4, 1), datetime.date(2028, 3, 31))
        _fy("B", datetime.date(2027, 10, 1), datetime.date(2028, 9, 30))
        errors, warnings = VerifyCommand()._check_fiscal_years()
        assert errors == []
        overlaps = [w for w in warnings if "overlap" in w]
        assert len(overlaps) == 1
        assert "A" in overlaps[0] and "B" in overlaps[0]

    def test_command_creates_years_for_today_and_thirty_days_ahead(self):
        call_command("ensure_fiscal_years")
        today = timezone.localdate()
        for day in (today, today + datetime.timedelta(days=30)):
            assert FiscalYear.objects.filter(
                start_date__lte=day, end_date__gte=day
            ).exists()
        count = FiscalYear.objects.count()
        call_command("ensure_fiscal_years")
        assert FiscalYear.objects.count() == count
