"""The tax figures refresh: what is fetched when, and what a changed or unreadable booklet
does to the stored figures."""
from datetime import date, datetime, timedelta

import pytest

from app.models.tax_value import TaxDocument, TaxValue
from app.repositories.tax_value_repository import TaxValueRepository
from app.services import il_tax_source, tax_data_service
from app.services.tax_data_service import TaxDataService

TODAY = date(2026, 10, 19)


class FakeBooklets:
    """Stands in for gov.il and the PDF reader: a file per year, read as its own text."""

    name = "il_booklet"
    law_values = staticmethod(il_tax_source.law_values)

    def __init__(self, files: dict[int, bytes]):
        self.files = files
        self.fetched: list[int] = []

    def booklet_url(self, year):
        return f"http://gov/{year}.pdf"

    def fetch(self, url):
        year = int(url.rsplit("/", 1)[1].split(".")[0])
        self.fetched.append(year)
        content = self.files.get(year)
        return ("ok", content) if content is not None else ("missing", None)

    def read_booklet(self, pdf: bytes) -> dict:
        if b"unreadable" in pdf:
            raise il_tax_source.BookletError("layout changed")
        return {"rent_exemption_ceiling_monthly": int(pdf.split(b":")[1])}


def _files(through=2026, ceiling=5654):
    return {y: f"%PDF:{ceiling}".encode() for y in range(2022, through + 1)}


@pytest.fixture
def booklets(monkeypatch):
    fake = FakeBooklets(_files())
    monkeypatch.setattr(tax_data_service, "fetch_document", fake.fetch)
    return fake


@pytest.fixture
def clock(monkeypatch):
    now = {"at": datetime(2026, 10, 19, 6, 0)}
    monkeypatch.setattr(tax_data_service, "utc_now_naive", lambda: now["at"])
    return now


def _service(db_session, booklets):
    return TaxDataService(TaxValueRepository(db_session), {"il_booklet": booklets})


def _ceiling(db_session, year):
    return TaxValueRepository(db_session).values_for("IL", year).get(
        "rent_exemption_ceiling_monthly"
    )


def test_the_first_run_loads_every_year_from_2022(db_session, booklets, clock):
    summary = _service(db_session, booklets).refresh(TODAY)

    assert summary["updated"] == [f"IL-{y}" for y in range(2022, 2027)]
    assert summary["failed"] == []
    assert all(_ceiling(db_session, y) == 5654 for y in range(2022, 2027))
    row = db_session.query(TaxValue).filter_by(tax_year=2026, key="surtax_rate_pct").one()
    assert row.source == "law" and "121ב" in row.reference
    document = db_session.query(TaxDocument).filter_by(tax_year=2026).one()
    assert document.url == "http://gov/2026.pdf" and len(document.sha256) == 64


def test_a_stored_year_is_not_fetched_again_within_the_week(db_session, booklets, clock):
    service = _service(db_session, booklets)
    service.refresh(TODAY)
    booklets.fetched.clear()

    clock["at"] += timedelta(days=6)
    assert service.refresh(TODAY + timedelta(days=6))["checked"] == 0
    assert booklets.fetched == []


def test_this_years_booklet_is_checked_weekly_and_past_years_are_not(db_session, booklets, clock):
    service = _service(db_session, booklets)
    service.refresh(TODAY)
    booklets.fetched.clear()

    clock["at"] += timedelta(days=7)
    summary = service.refresh(TODAY + timedelta(days=7))

    assert booklets.fetched == [2026]
    assert summary["updated"] == []  # same file, nothing re-read
    document = db_session.query(TaxDocument).filter_by(tax_year=2026).one()
    assert document.checked_at == clock["at"]


def test_a_republished_booklet_replaces_the_years_figures(db_session, booklets, clock):
    service = _service(db_session, booklets)
    service.refresh(TODAY)
    old_sha = db_session.query(TaxDocument).filter_by(tax_year=2026).one().sha256

    booklets.files[2026] = b"%PDF:5800"
    clock["at"] += timedelta(days=7)
    summary = service.refresh(TODAY + timedelta(days=7))

    assert summary["updated"] == ["IL-2026"]
    assert _ceiling(db_session, 2026) == 5800
    assert _ceiling(db_session, 2025) == 5654
    row = db_session.query(TaxValue).filter_by(
        tax_year=2026, key="rent_exemption_ceiling_monthly"
    ).one()
    assert row.sha256 != old_sha


def test_an_unreadable_booklet_keeps_the_old_figures_and_reports_it(
    db_session, booklets, clock, monkeypatch
):
    reported = []
    monkeypatch.setattr(
        tax_data_service.sentry_sdk, "capture_message", lambda msg, **kw: reported.append(msg)
    )
    service = _service(db_session, booklets)
    service.refresh(TODAY)

    booklets.files[2026] = b"%PDF:unreadable"
    clock["at"] += timedelta(days=7)
    summary = service.refresh(TODAY + timedelta(days=7))

    assert summary["failed"] == ["IL-2026"]
    assert reported == ["Tax booklet did not read: IL 2026"]
    assert _ceiling(db_session, 2026) == 5654


def test_a_year_not_published_yet_is_tried_again_the_next_day(db_session, monkeypatch, clock):
    booklets = FakeBooklets(_files(through=2026))
    monkeypatch.setattr(tax_data_service, "fetch_document", booklets.fetch)
    service = _service(db_session, booklets)
    january = date(2027, 1, 5)
    clock["at"] = datetime(2027, 1, 5, 6, 0)
    service.refresh(january)
    assert db_session.query(TaxDocument).filter_by(tax_year=2027).count() == 0
    # Law figures are there already; the booklet's are what the report will say is missing.
    assert "ten_percent_rate_pct" in TaxValueRepository(db_session).values_for("IL", 2027)

    booklets.files[2027] = b"%PDF:5900"
    booklets.fetched.clear()
    clock["at"] += timedelta(days=1)
    summary = service.refresh(january + timedelta(days=1))

    assert summary["updated"] == ["IL-2027"]
    assert booklets.fetched == [2027]


def test_in_january_last_years_booklet_is_still_checked(db_session, booklets, clock):
    service = _service(db_session, booklets)
    service.refresh(TODAY)
    booklets.files[2027] = b"%PDF:5654"
    booklets.fetched.clear()

    clock["at"] = datetime(2027, 1, 20, 6, 0)
    service.refresh(date(2027, 1, 20))

    assert sorted(booklets.fetched) == [2026, 2027]


def test_law_values_follow_the_code(db_session, booklets, clock, monkeypatch):
    service = _service(db_session, booklets)
    service.refresh(TODAY)
    assert TaxValueRepository(db_session).values_for("IL", 2025)["capital_surtax_rate_pct"] == 2
    assert "capital_surtax_rate_pct" not in TaxValueRepository(db_session).values_for("IL", 2024)

    changed = il_tax_source.LawValue("surtax_rate_pct", 4, "test", "http://law", first_year=2026)
    monkeypatch.setattr(
        FakeBooklets,
        "law_values",
        staticmethod(lambda year: [changed] if year == 2026 else il_tax_source.law_values(year)),
    )
    summary = service.refresh(TODAY)

    assert summary["law_values_changed"] == 1
    assert TaxValueRepository(db_session).values_for("IL", 2026)["surtax_rate_pct"] == 4
