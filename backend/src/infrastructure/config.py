# src/infrastructure/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Mongo
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "trends"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # Mercado Livre
    ML_BASE_URL: str = "https://api.mercadolibre.com"
    ML_SITE_ID: str = "MLB"

    # Apify
    APIFY_TOKEN: str | None = None
    APIFY_ACTOR_ID: str = "clockworks~tiktok-scraper"
    TIKTOK_HASHTAGS: str = "fyp"

    # LLM
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "gpt-4.1-mini"

settings = Settings()