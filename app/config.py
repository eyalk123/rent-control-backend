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
    # Days to keep chat conversations before the retention job deletes them (by last-updated).
    # 0 disables retention (nothing is deleted). Enforced only when an external scheduler calls
    # POST /internal/run-agent-retention (like the reminder/CPI crons).
    AGENT_RETENTION_DAYS: int = 0
    # CBS (Central Bureau of Statistics) public price-index API, used for CPI rent
    # linkage. Keyless and free. CPI_INDEX_ID 120010 is the general Consumer Price
    # Index. Refreshed monthly by POST /internal/run-cpi-indexing.
    CBS_API_BASE_URL: str = "https://api.cbs.gov.il"
    CPI_INDEX_ID: int = 120010

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
