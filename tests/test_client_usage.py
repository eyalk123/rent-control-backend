"""Which client each owner works in: the X-Client-* headers, the accumulator, the flush.

The test this file exists for is
``test_writes_count_real_work_not_housekeeping``. Everything else guards the plumbing;
that one guards the metric. If the write counter ever starts counting the calls a client
fires automatically on launch, every five-second app open looks like an evening's work and
the "where does the work happen" question becomes unanswerable — which is the only reason
``owner_client_days`` exists.
"""
from datetime import date, timedelta

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.models.owner_client_day import OwnerClientDay
from app.services import client_usage_service
from app.services.client_usage_service import ClientUsageRecorder, recorder
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property

WEB = {"X-Client-App": "web", "X-Client-Platform": "web", "X-Client-Version": "1.4.2"}
IOS = {"X-Client-App": "mobile", "X-Client-Platform": "ios", "X-Client-Version": "1.1.0"}
# The Expo web preview: the mobile app running in a browser. `app` stays "mobile" — if it
# ever reports itself as the web app, this table measures the opposite of the truth.
PREVIEW = {"X-Client-App": "mobile", "X-Client-Platform": "web", "X-Client-Version": "1.1.0"}


@pytest.fixture(autouse=True)
def clean_recorder():
    """The recorder is a process-wide singleton, so it carries whatever the rest of the
    suite happened to record. Empty it either side of each test."""
    recorder._counts.clear()
    yield
    recorder._counts.clear()


def _rows(db_session) -> list[OwnerClientDay]:
    db_session.expire_all()
    return list(
        db_session.scalars(select(OwnerClientDay).order_by(OwnerClientDay.app, OwnerClientDay.platform))
    )


# --- Capture ------------------------------------------------------------------------

def test_a_request_with_headers_becomes_one_row(client, db_session):
    assert client.get("/properties", headers=WEB).status_code == 200

    recorder.flush(db_session)

    rows = _rows(db_session)
    assert len(rows) == 1
    assert (rows[0].owner_id, rows[0].app, rows[0].platform) == (OWNER_A, "web", "web")
    assert rows[0].app_version == "1.4.2"
    assert rows[0].requests == 1


def test_many_requests_collapse_into_one_row_with_a_count(client, db_session):
    """The whole point of the accumulator: twenty calls, one row, ``requests = 20`` —
    not twenty rows, and not a presence flag that says the same thing as one call."""
    for _ in range(20):
        client.get("/properties", headers=WEB)

    recorder.flush(db_session)

    rows = _rows(db_session)
    assert len(rows) == 1
    assert rows[0].requests == 20


def test_two_clients_on_the_same_day_are_two_rows(client, db_session):
    client.get("/properties", headers=WEB)
    client.get("/properties", headers=IOS)
    client.get("/properties", headers=IOS)

    recorder.flush(db_session)

    rows = _rows(db_session)
    assert [(r.app, r.platform, r.requests) for r in rows] == [
        ("mobile", "ios", 2),
        ("web", "web", 1),
    ]


def test_the_expo_web_preview_stays_mobile(client, db_session):
    """The mobile app in a browser must not merge with the real web app — that inversion
    would produce the opposite of the answer this table exists to give."""
    client.get("/properties", headers=PREVIEW)
    client.get("/properties", headers=WEB)

    recorder.flush(db_session)

    assert [(r.app, r.platform) for r in _rows(db_session)] == [
        ("mobile", "web"),
        ("web", "web"),
    ]


def test_each_owner_gets_their_own_row(client_factory, db_session):
    client_factory(OWNER_A).get("/properties", headers=WEB)
    client_factory(OWNER_B).get("/properties", headers=WEB)

    recorder.flush(db_session)

    assert {r.owner_id for r in _rows(db_session)} == {OWNER_A, OWNER_B}


# --- The allowlist ------------------------------------------------------------------

def test_writes_count_real_work_not_housekeeping(client, db_session):
    """This is the test that protects the metric.

    `POST /device-tokens` (push registration) and `PATCH /users/me/tour-state`
    (onboarding) fire on app open with no user involvement. If they counted as writes,
    opening the app would be indistinguishable from using it.
    """
    prop = make_property(db_session)

    real_work = client.post(
        "/transactions/revenue",
        json={"property_id": prop.id, "amount": 6000, "month_for": "2026-05-01"},
        headers=WEB,
    )
    assert real_work.status_code == 201

    assert client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[a]", "platform": "ios"},
        headers=WEB,
    ).status_code in (200, 201)
    assert client.patch(
        "/users/me/tour-state",
        json={"tours_seen": {"first-run": "2026-08-24T10:00:00"}},
        headers=WEB,
    ).status_code == 200

    recorder.flush(db_session)

    row = _rows(db_session)[0]
    assert row.requests == 3
    assert row.writes == 1


def test_a_report_export_counts_as_a_write_even_though_it_is_a_get(client, db_session):
    """Proves the allowlist beats a method-based rule in the other direction too: both
    exports are GETs that generate a file and write a ReportExport row."""
    make_property(db_session)

    assert client.get(
        "/reports/income-expense", params={"year": 2025}, headers=WEB
    ).status_code == 200

    recorder.flush(db_session)

    row = _rows(db_session)[0]
    assert (row.requests, row.writes) == (1, 1)


def test_reading_is_not_a_write(client, db_session):
    client.get("/properties", headers=WEB)
    client.get("/notifications", headers=WEB)
    client.get("/reports/history", headers=WEB)

    recorder.flush(db_session)

    row = _rows(db_session)[0]
    assert (row.requests, row.writes) == (3, 0)


def test_a_rejected_write_is_attention_not_work(client, db_session):
    """A 404 attempted something and produced nothing. It still counts as a request."""
    assert client.post(
        "/transactions/revenue",
        json={"property_id": 999, "amount": 100, "month_for": "2026-05-01"},
        headers=WEB,
    ).status_code == 404

    recorder.flush(db_session)

    row = _rows(db_session)[0]
    assert (row.requests, row.writes) == (1, 0)


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/transactions/revenue", True),
        ("PATCH", "/renters/3", True),
        ("DELETE", "/properties/7", True),
        ("POST", "/agent/chat", True),
        ("POST", "/extract/lease", True),
        ("GET", "/reports/expense-log", True),
        ("DELETE", "/reports/history/4", True),
        ("POST", "/device-tokens", False),
        ("POST", "/users/me/legal", False),
        ("PATCH", "/users/me/country", False),
        ("PATCH", "/notifications/2/read", False),
        ("GET", "/reports/history", False),
        ("GET", "/properties", False),
        ("DELETE", "/agent/conversations/1", False),
        # Segment-aware: a future /properties-archive must not inherit /properties.
        ("POST", "/properties-archive", False),
    ],
)
def test_the_allowlist(method, path, expected):
    assert client_usage_service.is_write(method, path) is expected


# --- Untrusted headers --------------------------------------------------------------

@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Client-App": "desktop", "X-Client-Platform": "windows"},
        {"X-Client-App": "", "X-Client-Platform": ""},
    ],
    ids=["missing", "unrecognised", "empty"],
)
def test_unknown_headers_record_unknown_and_the_request_still_works(
    client, db_session, headers
):
    assert client.get("/properties", headers=headers).status_code == 200

    recorder.flush(db_session)

    row = _rows(db_session)[0]
    assert (row.app, row.platform) == ("unknown", "unknown")
    assert row.app_version is None


def test_a_hostile_version_string_is_not_stored_verbatim(client, db_session):
    client.get(
        "/properties",
        headers={**WEB, "X-Client-Version": "x" * 400 + " <script>"},
    )

    recorder.flush(db_session)

    assert _rows(db_session)[0].app_version is None


def test_header_values_are_case_insensitive(client, db_session):
    client.get("/properties", headers={"X-Client-App": "WEB", "X-Client-Platform": "Web"})

    recorder.flush(db_session)

    assert (_rows(db_session)[0].app, _rows(db_session)[0].platform) == ("web", "web")


# --- Flushing -----------------------------------------------------------------------

def test_two_flushes_accumulate_rather_than_overwrite(client, db_session):
    client.get("/properties", headers=WEB)
    recorder.flush(db_session)
    client.get("/properties", headers=WEB)
    client.get("/properties", headers=WEB)
    recorder.flush(db_session)

    rows = _rows(db_session)
    assert len(rows) == 1
    assert rows[0].requests == 3


def test_two_workers_flushing_the_same_row_sum_instead_of_clobbering(client, db_session):
    """Railway may run more than one worker, each with its own accumulator. A plain SET —
    or a DO NOTHING — would silently drop one of them."""
    day = date(2026, 9, 14)
    workers = [ClientUsageRecorder(), ClientUsageRecorder()]
    for worker, requests in zip(workers, (5, 7)):
        with freeze_time(day):
            for _ in range(requests):
                worker.record(
                    owner_id=OWNER_A,
                    method="GET",
                    path="/properties",
                    app="web",
                    platform="web",
                    app_version="1.4.2",
                    counted_as_write=False,
                )
    for worker in workers:
        worker.flush(db_session)

    rows = _rows(db_session)
    assert len(rows) == 1
    assert rows[0].requests == 12


def test_a_flush_across_midnight_files_each_request_under_its_own_day(db_session):
    """The day is part of the key and is resolved when the request happens. Deriving it at
    flush time would misfile everything in the window that crosses midnight UTC."""
    def hit(when):
        with freeze_time(when):
            recorder.record(
                owner_id=OWNER_A,
                method="GET",
                path="/properties",
                app="web",
                platform="web",
                app_version=None,
                counted_as_write=False,
            )

    hit("2026-09-14 23:59:50")
    hit("2026-09-15 00:00:10")
    hit("2026-09-15 00:00:20")

    # One flush, well after both days — the rows must still split.
    with freeze_time("2026-09-15 00:01:00"):
        recorder.flush(db_session)

    rows = sorted(_rows(db_session), key=lambda r: r.day)
    assert [(r.day, r.requests) for r in rows] == [
        (date(2026, 9, 14), 1),
        (date(2026, 9, 15), 2),
    ]


def test_an_empty_flush_writes_nothing(db_session):
    assert recorder.flush(db_session) == 0
    assert _rows(db_session) == []


def test_a_broken_flush_breaks_no_request_and_loses_no_counts(client, db_session, monkeypatch):
    client.get("/properties", headers=WEB)

    def explode(*_args, **_kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(client_usage_service, "_upsert", explode)
    assert recorder.flush(db_session) == 0

    # The request that came in while the database was broken still succeeded...
    assert client.get("/properties", headers=WEB).status_code == 200
    # ...and neither its count nor the retained one was lost.
    monkeypatch.undo()
    recorder.flush(db_session)
    assert _rows(db_session)[0].requests == 2


def test_stale_days_are_dropped_rather_than_retried_forever(db_session, monkeypatch):
    """A failed flush keeps its counts, but not indefinitely — an unbounded dict in a
    long-lived process is a slow leak."""
    stale = date.today() - timedelta(days=client_usage_service.STALE_DAYS + 5)
    with freeze_time(stale):
        recorder.record(
            owner_id=OWNER_A, method="GET", path="/properties",
            app="web", platform="web", app_version=None, counted_as_write=False,
        )

    monkeypatch.setattr(
        client_usage_service, "_upsert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    recorder.flush(db_session)

    assert recorder._counts == {}


def test_going_over_the_key_cap_asks_for_an_early_flush(monkeypatch):
    monkeypatch.setattr(client_usage_service, "MAX_KEYS", 2)
    fresh = ClientUsageRecorder()
    for owner in ("a", "b", "c"):
        fresh.record(
            owner_id=owner, method="GET", path="/properties",
            app="web", platform="web", app_version=None, counted_as_write=False,
        )
    assert fresh._flush_now.is_set()


def test_a_version_is_not_blanked_by_a_request_that_omits_it(client, db_session):
    client.get("/properties", headers=WEB)
    recorder.flush(db_session)
    client.get("/properties", headers={"X-Client-App": "web", "X-Client-Platform": "web"})
    recorder.flush(db_session)

    assert _rows(db_session)[0].app_version == "1.4.2"


# --- Lifecycle ----------------------------------------------------------------------

def test_unauthenticated_traffic_records_nothing(client, db_session):
    assert client.get("/health", headers=WEB).status_code == 200

    recorder.flush(db_session)

    assert _rows(db_session) == []


def test_the_rows_are_deleted_with_the_account(client, db_session):
    client.get("/properties", headers=WEB)
    recorder.flush(db_session)
    assert len(_rows(db_session)) == 1

    assert client.delete("/users/me").status_code == 200

    # The DELETE itself was recorded in memory; drop it so the assertion is about what
    # deletion erased, not about what arrived afterwards.
    recorder._counts.clear()
    assert _rows(db_session) == []


# --- CORS ---------------------------------------------------------------------------

def test_the_new_headers_survive_a_browser_preflight(client):
    """The most likely way this feature breaks production: the browser preflights the
    three new headers, and a CORS config that does not allow them fails every web request
    outright, with nothing in the server log to say why."""
    from app.config import settings

    origin = settings.cors_origins_list[0]
    resp = client.options(
        "/properties",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type,"
            "x-client-app,x-client-platform,x-client-version",
        },
    )

    assert resp.status_code == 200
    allowed = resp.headers["access-control-allow-headers"].lower()
    for header in client_usage_service.CLIENT_HEADERS:
        assert header.lower() in allowed
