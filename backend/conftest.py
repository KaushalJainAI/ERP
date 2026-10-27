"""Shared pytest fixtures.

Posting is refused outside an open fiscal year (``FiscalYear.for_date``), so the
test database always carries a wide-open default fiscal year. Tests that need to
exercise the "no fiscal year" path opt out with
``@pytest.mark.no_default_fiscal_year``.
"""
import datetime

import pytest

DEFAULT_FY_NAME = "TEST-FY"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_default_fiscal_year: remove the default open FiscalYear for this test",
    )


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Create the default open fiscal year once, committed, in the test database.

    Committed (not per-test) so that it is visible to both pytest-style db tests
    and Django ``TestCase`` classes, whose class-level transaction starts after
    session setup.
    """
    from core.models import FiscalYear

    with django_db_blocker.unblock():
        FiscalYear.objects.get_or_create(
            name=DEFAULT_FY_NAME,
            defaults={
                "start_date": datetime.date(2000, 1, 1),
                "end_date": datetime.date(2099, 12, 31),
                "is_closed": False,
            },
        )


@pytest.fixture(autouse=True)
def _fiscal_year_opt_out(request):
    """Honour ``no_default_fiscal_year`` by deleting all fiscal years for the test
    (inside the test's transaction, so it is rolled back afterwards)."""
    if request.node.get_closest_marker("no_default_fiscal_year") is None:
        yield
        return
    request.getfixturevalue("db")
    from core.models import FiscalYear

    FiscalYear.objects.all().delete()
    yield
