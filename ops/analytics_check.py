#!/usr/bin/env python3
"""
Check the admin analytics dashboard against realistic data, on a throwaway local database.

    python ops/analytics_check.py verify    # do the owner lists match their charts?
    python ops/analytics_check.py preview   # serve the real page on http://127.0.0.1:8765/admin/dashboard

Both create a scratch database beside the one in DATABASE_URL, migrate it, seed ~40 fake
owners with properties, renters, transactions, exports, scans, assistant use, client days,
devices and subscriptions, and drop it again when done. `preview` stubs out sign-in and keeps
serving until Ctrl+C.

Why it exists: the test suite runs on SQLite and cannot execute the analytics SQL (it is
PostgreSQL-only). This is the cheap way to run it for real.

**Local only.** It refuses any DATABASE_URL whose host is not localhost, so it can never
create or drop a database on Railway.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.analytics import service as S  # noqa: E402
from app.clock import utc_now_naive  # noqa: E402
from app.config import settings  # noqa: E402

SCRATCH_DB = "analytics_check_scratch"
PORT = 8765


# ── scratch database ─────────────────────────────────────────────────────────

def _base_url() -> str:
    url = settings.DATABASE_URL
    if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("Refusing: DATABASE_URL is not a local database.")
    return url.rsplit("/", 1)[0]


def create_scratch() -> str:
    base = _base_url()
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    admin.dispose()
    url = f"{base}/{SCRATCH_DB}"
    print("migrating scratch database…")
    done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
                          env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True)
    if done.returncode:
        print(done.stdout, done.stderr, sep="\n")
        drop_scratch()
        sys.exit("migration failed")
    return url


def drop_scratch() -> None:
    admin = create_engine(f"{_base_url()}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
    admin.dispose()
    print("scratch database dropped")


# ── seed ─────────────────────────────────────────────────────────────────────

def seed(db) -> None:
    """Deterministic fake data inside the 90-day window, after DATA_FLOOR."""
    random.seed(7)
    now = utc_now_naive()

    def ins(sql, **p):
        db.execute(text(sql), p)

    countries = ["IL", "IL", "IL", "US", "US", "GB", "FR", "DE", "ES", None]
    for i in range(40):
        oid = f"owner-{i}"
        created = now - timedelta(days=random.randint(0, 85), hours=random.randint(0, 23))
        ins("INSERT INTO owners (id, email, display_name, country, created_at, granted_plan, tour_state) "
            "VALUES (:id, :e, :n, :c, :t, :g, '{}')",
            id=oid, e=f"o{i}@example.test", n=f"Owner {i}", c=random.choice(countries), t=created,
            g="tier_16_plus" if i % 9 == 0 else None)
        ins("INSERT INTO legal_acceptances (owner_id, document, version, locale, platform, accepted_at) "
            "VALUES (:o, 'terms', '1', :l, :p, :t)",
            o=oid, l=random.choice(["he", "en"]), p=random.choice(["ios", "android", "web"]), t=created)
        n_props = random.choice([0, 0, 1, 2, 2, 3, 5, 9, 17])
        for k in range(n_props):
            ins("INSERT INTO properties (owner_id, address, city, type, created_at, updated_at) "
                "VALUES (:o, 'a', 'c', 'apartment', :t, :t)", o=oid, t=created + timedelta(hours=k + 1))
        txn = ("INSERT INTO transactions (owner_id, type, date_of_payment, amount, currency_code, "
               "created_at, updated_at) VALUES (:o, 'revenue', :d, 100, 'ILS', :t, :t)")
        if n_props and random.random() < 0.7:
            ins("INSERT INTO renters (owner_id, first_name, last_name, phone, lease_years, created_at, updated_at) "
                "VALUES (:o, 'f', 'l', '0', '[]', :t, :t)", o=oid, t=created + timedelta(days=random.randint(0, 20)))
            if random.random() < 0.7:
                ins(txn, o=oid, d=now.date(), t=created + timedelta(days=random.randint(0, 40)))
                if random.random() < 0.6:
                    ins("INSERT INTO report_exports (owner_id, report_type, year, format, created_at) "
                        "VALUES (:o, 'income_expense', 2026, 'pdf', :t)",
                        o=oid, t=created + timedelta(days=random.randint(1, 30)))
        elif random.random() < 0.3:
            # Out of order: a transaction with no property, so ever_* and step_* disagree.
            ins(txn, o=oid, d=now.date(), t=created + timedelta(days=3))
        for _ in range(random.randint(0, 3)):
            ins("INSERT INTO document_extraction_logs (owner_id, kind, status, created_at, updated_at, "
                "submitted_at, forms_submitted_count, fields_given_count, fields_changed_count, field_edits) "
                "VALUES (:o, 'lease', 'success', :t, :t, :s, 0, 5, 1, :fe)",
                o=oid, t=created + timedelta(days=random.randint(0, 30)),
                s=(now if random.random() < 0.6 else None),
                fe='[{"section": "lease", "field": "rent"}]' if random.random() < 0.5 else None)
        if random.random() < 0.4:
            ins("INSERT INTO agent_conversations (id, owner_id, created_at, updated_at) VALUES (:c, :o, :t, :t)",
                c=i + 1, o=oid, t=created)
            for m in range(random.choice([1, 3, settings.AGENT_DAILY_MESSAGE_LIMIT + 10])):
                ins("INSERT INTO agent_usage_logs (owner_id, conversation_id, created_at, status, tool_calls_count) "
                    "VALUES (:o, :c, :t, 'success', 0)",
                    o=oid, c=i + 1, t=created + timedelta(days=1, minutes=m))
        for d in range(random.randint(0, 6)):
            ins("INSERT INTO owner_client_days (owner_id, day, app, platform, requests, writes, first_seen_at, "
                "last_seen_at) VALUES (:o, :d, :a, 'ios', 3, :w, :t, :t)",
                o=oid, d=(created + timedelta(days=d * 3)).date(), a=random.choice(["mobile", "web", "unknown"]),
                w=random.randint(1, 5), t=created)
        if random.random() < 0.3:
            ins("INSERT INTO device_tokens (owner_id, token, platform, created_at, last_used_at) "
                "VALUES (:o, :k, :p, :t, :t)", o=oid, k=f"tok-{i}", p=random.choice(["ios", "android"]), t=created)
        if random.random() < 0.35:
            ins("INSERT INTO subscriptions (owner_id, plan, period, status, source, price_currency, price_amount, "
                "current_period_end, grace_until, created_at, updated_at) "
                "VALUES (:o, :pl, 'monthly', :st, :src, :cur, 50, :end, :g, :t, :t)",
                o=oid, pl=random.choice(["tier_3_8", "tier_9_15", "tier_16_plus"]),
                st=random.choice(["active", "trialing", "past_due", "canceled", "expired"]),
                src=random.choice(["apple", "google", "paddle"]), cur=random.choice(["ILS", "USD"]),
                end=now + timedelta(days=random.choice([-5, 10])), g=now + timedelta(days=3), t=created)
            ins("INSERT INTO subscription_events (event_id, owner_id, event_type, occurred_at, environment, "
                "applied, created_at, payload) VALUES (:o || '-ev', :o, :ev, :t, 'PRODUCTION', 'applied', :t, '{}')",
                o=oid, ev=random.choice(["INITIAL_PURCHASE", "CANCELLATION"]), t=created + timedelta(days=2))
    db.commit()


# ── verify: every owner list against the number its chart shows ──────────────

def verify(db) -> int:
    fails: list[str] = []

    def check(label, expected, seg, country=None, **kw):
        got = S.get_owner_list(db, seg, "90d", "week", country, **kw)["total"]
        if (expected or 0) != got:
            fails.append(f"{label} [{country or 'all'}]: chart {expected} vs list {got}")

    for country in (None, "IL"):
        d = S.build_payload(db, "90d", "week", country)
        for k, v in d["funnel"].items():
            if k.startswith("step_"):
                check("funnel " + k, v, "funnel", country, value=k)
        t = d["growth"]["totals"]
        check("live owners", t["live_owners"], "all_owners", country)
        check("new", t["new_in_window"], "signups", country)
        for r in d["growth"]["signups"]:
            check("signups " + r["bucket"], r["signups"], "signups", country, bucket=r["bucket"])
        for r in d["engagement"]["active_owners"]:
            check("active " + r["bucket"], r["active_owners"], "active", country, bucket=r["bucket"])
        for r in d["engagement"]["retention"]:
            w = r["cohort_week"]
            check("cohort " + w, r["cohort_size"], "retention", country, bucket=w)
            check("week 1 " + w, r["retained_week_1"], "retention", country, bucket=w, value="week_1")
            check("week 4 " + w, r["retained_week_4"], "retention", country, bucket=w, value="week_4")
        dist = d["engagement"]["distribution"]
        for b, v in zip(dist["buckets"], dist["properties"]):
            check("band " + b, v, "property_band", country, value=b)
        acc = d["subscriptions"]["accounts"]
        for k, v in (("on_paid_plan", "paid"), ("grandfathered", "grandfathered"), ("free", "free"),
                     ("free_at_limit", "free_at_limit")):
            check("plan " + v, acc[k], "account_plan", country, value=v)
        state = d["subscriptions"]["state"]
        paying = [r for r in state if r["state"] in ("active", "canceling", "payment_failed")]
        check("paying", sum(r["subscriptions"] for r in paying), "subs_paying", country)
        for dim in ("plan", "source", "currency"):
            for val in {r[dim] for r in paying}:
                check(f"paying {dim}={val}", sum(r["subscriptions"] for r in paying if r[dim] == val),
                      "subs_paying", country, group=dim, value=val)
        for val in {r["state"] for r in state}:
            check("state " + val, sum(r["subscriptions"] for r in state if r["state"] == val),
                  "subs_any", country, group="state", value=val)
        # The seed gives each owner at most one event, so events and owners agree.
        for r in d["subscriptions"]["events"]:
            for kind in ("started", "canceled"):
                check(f"{kind} {r['bucket']}", r[kind], "subs_events", country, value=kind, bucket=r["bucket"])
        for r in d["ai"]["assistant"]:
            check("assistant " + r["bucket"], r["owners"], "assistant", country, bucket=r["bucket"])
        check("assistant cap", d["ai"]["cap_hits"]["owners_at_cap"], "assistant_cap", country)
        for r in d["platform"]["client_over_time"]:
            check(f"client {r['app']} {r['bucket']}", r["owners"], "client_app", country,
                  value=r["app"], bucket=r["bucket"])
        for r in d["platform"]["client_overlap"]:
            check("overlap " + r["segment"], r["owners"], "client_overlap", country, value=r["segment"])
        for seg, key in (("language", "locale"), ("signup_platform", "platform"), ("devices", "platform")):
            for r in d["platform"][seg]:
                check(f"{seg} {r[key]}", r["owners"], seg, country, value=r[key])
        if country is None:
            c = d["countries"]
            for r in c["comparison"]:
                for m in ("owners", "signups", "active_owners", "paying"):
                    check(f"country {r['country']} {m}", r[m], "country", value=r["country"], group=m)
            top = [r["country"] for r in sorted(c["comparison"], key=lambda r: -r["owners"])[:4]]
            check("country other", sum(r["owners"] for r in c["comparison"] if r["country"] not in top),
                  "country", group="owners", exclude=",".join(top))
            for r in c["signups"]:
                check(f"country signups {r['country']} {r['bucket']}", r["signups"], "country",
                      value=r["country"], group="signups", bucket=r["bucket"])
            for r in c["active"]:
                check(f"country active {r['country']} {r['bucket']}", r["active_owners"], "country",
                      value=r["country"], group="active_owners", bucket=r["bucket"])

    if fails:
        print(f"{len(fails)} mismatch(es):")
        for line in fails:
            print("  ", line)
        return 1
    print("every owner list matches its chart")
    return 0


# ── preview: the real page, sign-in stubbed ──────────────────────────────────

_FIREBASE_STUB = """<script>
// analytics_check.py preview: sign-in stubbed, every request goes to the local scratch data.
window.firebase = {
  initializeApp() {},
  auth: Object.assign(() => ({
    onAuthStateChanged(cb) { setTimeout(() => cb({}), 0); },
    currentUser: { getIdToken: async () => 'preview' },
    signOut: async () => {},
  }), { GoogleAuthProvider: function () { this.setCustomParameters = () => {}; } }),
};
</script>"""


def preview(Session) -> None:
    import re

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse

    from app.api.routers import admin

    app = FastAPI()

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    def page():
        html = admin._DASHBOARD_HTML.read_text(encoding="utf-8")
        html = re.sub(r'<script src="https://www\.gstatic\.com/firebasejs/[^"]+"></script>\n?', "", html)
        html = html.replace("</head>", _FIREBASE_STUB + "\n</head>")
        return html.replace("__FIREBASE_CONFIG__", '{"apiKey": "preview"}')

    app.get("/admin/assets/{name}")(admin.admin_dashboard_asset)

    @app.get("/admin/analytics")
    def analytics(range: str = "90d", granularity: str = "week", country: str | None = None):
        with Session() as db:
            return S.build_payload(db, range, granularity, country)

    @app.get("/admin/analytics/owners")
    def owners(segment: str, range: str = "90d", granularity: str = "week", country: str | None = None,
               bucket: str | None = None, value: str | None = None, group: str | None = None,
               exclude: str | None = None):
        with Session() as db:
            try:
                return S.get_owner_list(db, segment, range, granularity, country, bucket, value, group, exclude)
            except ValueError as exc:
                raise HTTPException(400, str(exc))

    print(f"serving http://127.0.0.1:{PORT}/admin/dashboard — Ctrl+C to stop")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["verify", "preview"])
    args = parser.parse_args()

    url = create_scratch()
    engine = create_engine(url)
    Session = sessionmaker(bind=engine)
    try:
        with Session() as db:
            seed(db)
        if args.mode == "verify":
            with Session() as db:
                return verify(db)
        preview(Session)
        return 0
    finally:
        engine.dispose()
        drop_scratch()


if __name__ == "__main__":
    sys.exit(main())
