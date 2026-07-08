from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_transcribe_model: str = Field(
        default="gpt-4o-mini-transcribe",
        alias="OPENAI_TRANSCRIBE_MODEL",
    )
    openai_fact_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_FACT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    semantic_search_limit: int = Field(default=5, alias="SEMANTIC_SEARCH_LIMIT")
    semantic_search_min_score: float = Field(
        default=0.25,
        alias="SEMANTIC_SEARCH_MIN_SCORE",
    )
    app_timezone: str = Field(default="Europe/Berlin", alias="APP_TIMEZONE")
    data_dir: Path = Field(default=Path("/app/data"), alias="DATA_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    allowed_telegram_user_ids_raw: str = Field(
        default="",
        alias="ALLOWED_TELEGRAM_USER_IDS",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_telegram_user_ids(self) -> set[int]:
        try:
            return {
                int(item.strip())
                for item in self.allowed_telegram_user_ids_raw.split(",")
                if item.strip()
            }
        except ValueError as error:
            raise ValueError(
                "ALLOWED_TELEGRAM_USER_IDS must contain comma-separated integers"
            ) from error

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)
