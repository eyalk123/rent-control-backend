from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://localhost:5432/rent_control"
    DEFAULT_CURRENCY: str = "ILS"
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""  # e.g. your-project.appspot.com
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""  # full service account key JSON as a string
    # Comma-separated list of allowed browser origins for CORS.
    # Mobile clients are unaffected (CORS is a browser-only mechanism).
    # In Railway set e.g. "https://app.example.com,https://web.up.railway.app".
    CORS_ORIGINS: str = "http://localhost:5173"
    # Sentry error monitoring (errors only, no performance tracing). Leave empty to
    # disable: nothing is initialised and no network calls are made, which is what the
    # test suite and local development run with.
    SENTRY_DSN: str = ""
    # Tags every Sentry event so production noise stays separable. Normally left unset:
    # Railway injects the environment name and `resolve_environment()` picks it up. Set
    # this only to override that.
    ENVIRONMENT: str = ""
    # Root log level, applied by `configure_logging()` in app/main.py. INFO is the
    # useful default: the app logs one line per job summary and per Storage cleanup,
    # all of which you want in the Railway stream. Raise to WARNING to quieten it.
    LOG_LEVEL: str = "INFO"
    # Push notifications (Expo Push Service).
    # Optional access token; only required when Expo "Enhanced Security" is enabled.
    EXPO_ACCESS_TOKEN: str = ""
    # Shared secret guarding POST /internal/run-reminders (sent as the X-Cron-Secret header).
    # Leave empty to disable the endpoint (it will reject every request).
    REMINDER_CRON_SECRET: str = ""
    # Anthropic API key for the document-extraction feature (POST /extract/lease).
    # Leave empty to disable the endpoint (it will reject every request with 503).
    ANTHROPIC_API_KEY: str = ""
    # Claude model used for lease extraction. Sonnet is the cost/accuracy default;
    # switch to "claude-opus-4-8" if extraction accuracy on hard scans isn't enough.
    EXTRACTION_MODEL: str = "claude-sonnet-4-6"
    # --- Portfolio Chat Agent ("Ask RentVance", POST /agent/chat) ---
    # Reuses ANTHROPIC_API_KEY above: empty key disables the agent (503), same as
    # ── Subscriptions ────────────────────────────────────────────────────────
    # Whether plan limits are *enforced*. Off by default, and deliberately so: the
    # entitlement service computes and reports the answer either way, but nothing is
    # refused while this is false.
    #
    # It exists because the gate becomes correct long before it becomes fair. Until
    # checkout works — which is blocked on Paddle domain approval — a new account that
    # reaches three properties would be told to upgrade to a plan it cannot buy. Turning
    # this on is the launch switch, not a code change.
    ENTITLEMENT_ENFORCED: bool = False
    # HMAC secret for `POST /webhooks/revenuecat`, shown once in the RevenueCat dashboard
    # when signing is enabled. Empty makes the endpoint answer 503 rather than accepting
    # anything: an unauthenticated write path into billing state is worse than a broken
    # one, so a missing secret should be loud.
    REVENUECAT_WEBHOOK_SECRET: str = ""
    # The optional static `Authorization` header RevenueCat can be configured to send,
    # as an alternative to (or alongside) HMAC signing. Whatever string is typed into the
    # dashboard arrives verbatim — there is no `Bearer` scheme unless you type one.
    #
    # Weaker than the signature on its own: it proves the sender knew a string, not that
    # the body arrived unaltered. Supported because which of the two a RevenueCat project
    # offers varies, and a half-configured webhook is worth failing loudly over.
    #
    # Whichever of the two is configured is REQUIRED; configure both and both must pass.
    # Neither configured is a 503 — see `verify_request`.
    REVENUECAT_WEBHOOK_AUTH: str = ""

    # extraction. Model is configurable independently of EXTRACTION_MODEL.
    AGENT_MODEL: str = "claude-sonnet-4-6"
    # Cap on tokens Claude may emit per reply (cost + latency guard).
    AGENT_MAX_TOKENS: int = 2048
    # Max model<->tool round-trips per user message, so a stuck loop can't run forever.
    AGENT_MAX_TOOL_ITERS: int = 8
    # Per-owner messages allowed per calendar day (rate limit → 429 past this).
    AGENT_DAILY_MESSAGE_LIMIT: int = 50
    # Per-owner estimated spend allowed per UTC day, in USD (429 past this). Summed from
    # agent_usage_logs.estimated_cost_usd. The real "denial of wallet" guard — one message
    # can fan out several model calls, so message count alone doesn't bound spend.
    AGENT_DAILY_COST_LIMIT_USD: float = 2.0
    # App-wide estimated spend allowed per UTC day, in USD — a global kill-switch across all
    # owners. 0 disables it.
    AGENT_GLOBAL_DAILY_COST_LIMIT_USD: float = 20.0
    # Provisional cost charged to a turn the moment it starts (a "reservation"), before its
    # real cost is known. Reconciled to the actual cost when the turn finishes. Makes the
    # cost caps burst-safe: concurrent turns see each other's reservations.
    AGENT_RESERVE_COST_USD: float = 0.25
    # Most recent messages kept when replaying a conversation to the model; older
    # turns are dropped/summarized to bound context size and cost.
    AGENT_HISTORY_MAX_MESSAGES: int = 40
    # --- Retention ---
    # How long each class of data is kept before POST /internal/run-retention deletes it.
    # 0 disables that class (nothing is deleted). Enforced only when an external scheduler
    # calls the endpoint, like the reminder/CPI crons — a value alone does nothing.
    #
    # Chat conversations, by last-updated. Their messages hold tenant PII verbatim, so this
    # is the shortest window.
    AGENT_RETENTION_DAYS: int = 90
    # Deletion trace (activity_log). `label` holds names and addresses; the log's job is
    # answering "what happened months ago?", so it outlives the chats.
    ACTIVITY_LOG_RETENTION_DAYS: int = 365
    # Sent-notification history. One exception: a `cpi_rent_change` row is held past this
    # window while the renter's lease is still running, because it is the only
    # point-in-time record of the figure the owner was shown and leases outlast a year.
    NOTIFICATION_RETENTION_DAYS: int = 365
    # Which client each owner worked in, per day (owner_client_days). Counts only — no
    # PII at all — but it is owner-scoped behavioural data and a year of it answers every
    # question the table exists for, so it ages out on the same window as the activity log.
    CLIENT_USAGE_RETENTION_DAYS: int = 365
    # Deliberately NOT swept: document_extraction_logs (scanner-quality telemetry, holds no
    # lease content) and agent_usage_logs (cost only, no PII — retention detaches them from
    # deleted conversations rather than removing them).
    # --- CPI index sources ---
    # The cached index is refreshed daily by POST /internal/run-cpi-indexing from the
    # first source that answers, tried in order. Both are keyless and free.
    #
    # CBS (Central Bureau of Statistics) is the *contractual* publisher — Israeli lease
    # escalation clauses reference the index as CBS publishes it — so it is always tried
    # first and its readings always win.
    #
    # Which *series* each country's leases are linked to lives in the country table
    # (`app/countries/config.py`, `IndexSeries`), not here — it is a property of the market,
    # not of the deployment. CPI_INDEX_ID predates that and is kept as an **override**: set
    # it and Israel's series id changes, which is the escape hatch for a CBS renumbering or
    # a staging environment pointed at a test series, without a release. It is scoped to the
    # default country so it cannot repoint a second market's index at Israel's. Israel's
    # configured id is 120010, the general CPI, so leaving this unset changes nothing.
    CBS_API_BASE_URL: str = "https://api.cbs.gov.il"
    CPI_INDEX_ID: int = 120010
    # Bank of Israel republishes the identical CBS series over SDMX. Fallback only: it
    # fills gaps CBS hasn't covered and never overwrites a CBS-sourced reading. Series
    # code "CP" is "מדד המחירים לצרכן - כללי", the same series as CPI_INDEX_ID 120010.
    BOI_API_BASE_URL: str = "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2"
    BOI_CPI_SERIES_CODE: str = "CP"
    # How far behind the newest *published* month the cache may fall before the job
    # reports failure (503). Absorbs a late publication; catches a dead feed.
    CPI_MAX_STALE_MONTHS: int = 2

    # --- Legal documents ---
    # The versions this server considers current. Reported by GET /users/me/legal; NOT
    # enforced there. Each client bundles its own copy of the document text and gates on
    # its own `TERMS_VERSION` / `PRIVACY_VERSION` in `features/legal/legalContent.ts`, so
    # that nobody is ever asked to accept wording their build cannot display — a mobile
    # build waiting on app-store review would otherwise be locked out of the product for
    # a document it does not have. These two exist so the gap is measurable: compare them
    # against `legal_acceptances.version` to see who is still on the previous text.
    #
    # Revising a document means bumping it in THREE places, in this order: both copies of
    # `legalContent.ts` (web and mobile — they are manually-synced duplicates), then here.
    CURRENT_TERMS_VERSION: str = "2026-06-09"
    CURRENT_PRIVACY_VERSION: str = "2026-06-09"

    # --- Internal analytics dashboard ---
    # Comma-separated Firebase UIDs allowed to reach /admin/*. Empty (the default) disables
    # the dashboard entirely: with no one on the list, every request 404s, which is the safe
    # direction for a route that reports on the whole user base. Anyone not on the list gets
    # 404 rather than 403 — a 403 would confirm the route exists.
    ADMIN_OWNER_IDS: str = ""
    # Requests per minute per client IP for /admin/*, across both routes.
    ADMIN_RATE_LIMIT_PER_MINUTE: int = 30
    # Firebase *web* client config, injected into the dashboard page so its login box can
    # sign in with the admin's existing account rather than inventing a second credential.
    # These are public identifiers — the web app already ships them to every visitor — but
    # they are not in this repo today, so they come from the environment rather than being
    # committed. Empty means the page renders a "not configured" notice instead of a login
    # box. `authDomain` is derived from FIREBASE_PROJECT_ID.
    FIREBASE_WEB_API_KEY: str = ""
    FIREBASE_WEB_APP_ID: str = ""

    # --- Support messages (in-app "report a bug / ask a question") ---
    # Resend API key. Empty disables sending: the row is still written, but the route
    # answers 502 so the submitter is told to retry rather than believing a message
    # reached us. There is no admin screen for these, so a swallowed failure would be
    # a message nobody ever goes looking for.
    RESEND_API_KEY: str = ""
    # The From: address. Must be on a domain verified in Resend, or Resend's own
    # sandbox sender — which only delivers to the Resend account owner's address, and
    # is the right choice here because there is exactly one recipient.
    RESEND_FROM_ADDRESS: str = ""
    # Where every support message lands: the product owner's own mailbox. Empty
    # disables sending, like an empty key.
    SUPPORT_EMAIL_TO: str = ""
    # Submissions allowed per owner per rolling hour. Counted in the database rather
    # than in process memory: the API runs several Railway replicas, and an in-memory
    # counter would admit one burst per replica.
    SUPPORT_MESSAGE_HOURLY_LIMIT: int = 5
    # Cap on the combined decoded size of a submission's screenshots. Base64 inflates
    # by about a third on the wire, so this is roughly 8MB of request body.
    SUPPORT_MESSAGE_MAX_ATTACHMENT_BYTES: int = 6_000_000

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def admin_owner_ids(self) -> set[str]:
        return {uid.strip() for uid in self.ADMIN_OWNER_IDS.split(",") if uid.strip()}


settings = Settings()
