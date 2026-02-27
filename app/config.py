from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://localhost:5432/rent_control"
    S3_BUCKET: str = "mock-bucket"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
