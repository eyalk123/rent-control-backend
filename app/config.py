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
    # Most recent messages kept when replaying a conversation to the model; older
    # turns are dropped/summarized to bound context size and cost.
    AGENT_HISTORY_MAX_MESSAGES: int = 40
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
