from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_name: str = "OpsPilot API"
    environment: str = Field(default="local", validation_alias="OPSPILOT_ENV")
    log_level: str = Field(default="INFO", validation_alias="OPSPILOT_LOG_LEVEL")

    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.5-flash", validation_alias="GEMINI_MODEL")

    google_cloud_project: str | None = Field(default=None, validation_alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="us-central1", validation_alias="GOOGLE_CLOUD_LOCATION")

    github_token: str | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    github_owner: str | None = Field(default=None, validation_alias="GITHUB_OWNER")
    github_repo: str | None = Field(default=None, validation_alias="GITHUB_REPO")


@lru_cache
def get_settings() -> Settings:
    return Settings()

