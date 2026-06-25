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

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
