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
    # --- Portfolio Chat Agent ("Ask Rent Control", POST /agent/chat) ---
    # Reuses ANTHROPIC_API_KEY above: empty key disables the agent (503), same as
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
    # Sent-notification history.
    NOTIFICATION_RETENTION_DAYS: int = 365
    # Deliberately NOT swept: document_extraction_logs (scanner-quality telemetry, holds no
    # lease content) and agent_usage_logs (cost only, no PII — retention detaches them from
    # deleted conversations rather than removing them).
    # --- CPI index sources ---
    # The cached index is refreshed daily by POST /internal/run-cpi-indexing from the
    # first source that answers, tried in order. Both are keyless and free.
    #
    # CBS (Central Bureau of Statistics) is the *contractual* publisher — Israeli lease
    # escalation clauses reference the index as CBS publishes it — so it is always tried
    # first and its readings always win. CPI_INDEX_ID 120010 is the general CPI.
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

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
