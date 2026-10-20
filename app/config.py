"""Settings, read from environment variables / .env (never hard-code secrets)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    # OpenAI: a small model reads messages, a stronger one writes replies
    openai_api_key: str = ''
    parse_model: str = 'gpt-5.4-nano'
    reply_model: str = 'gpt-5.4-mini'
    llm_timeout_seconds: float = 20.0
    reasoning_effort: str = 'none'  # fastest; 'low' if replies need more care

    # The Django store backend
    backend_url: str = 'http://127.0.0.1:8000/api/'
    backend_service_key: str = Field('', description='Shared with Django as NEGOTIATOR_SERVICE_KEY')

    # Price passes: signed proof of an agreed price, checked by Django at add-to-bag
    price_pass_secret: str = Field('', description='Shared with Django as PRICE_PASS_SECRET')
    price_pass_ttl_seconds: int = 30 * 60   # short-lived; the chat fetches a fresh one while the deal stands
    deal_hold_hours: int = 24               # an agreed price is honoured this long (reopen the chat → same deal)

    # Sessions
    redis_url: str = ''  # empty = in-memory (single process, dev only)
    session_ttl_seconds: int = 60 * 60
    max_turns: int = 14
    max_firm_offers: int = 3

    # HTTP
    cors_origins: str = 'http://localhost:3100,http://127.0.0.1:3100'
    # Also allow any local dev server (any port). Set CORS_ORIGIN_REGEX= (empty) in production.
    cors_origin_regex: str = r'^http://(localhost|127\.0\.0\.1)(:\d+)?$'
    rate_limit_per_minute: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(',') if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
