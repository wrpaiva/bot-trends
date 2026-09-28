# src/infrastructure/config.py
"""
Fonte única de configuração (TIE-8).

Nenhum outro módulo lê `os.environ`: `src/tests/test_config.py` falha se isso
voltar. Variável nova entra aqui, com o mesmo nome do `.env`.
"""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.domain.scoring import HybridWeights, ScoreWeights


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Logging (TIE-13): LOG_FORMAT "json" (padrão) ou "text"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Mongo
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "trends"

    # Redis (broker do Celery e storage do rate limit da API)
    REDIS_URL: str = "redis://localhost:6379/0"

    # API — obrigatória para a API subir (validada no lifespan)
    API_KEY: str | None = None
    # Origens separadas por vírgula; use `cors_origins` para a lista
    CORS_ORIGINS: str = "http://localhost:5173"

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    # Alertas de saúde do sistema (TIE-39), num chat separado do de tendências.
    # Sem TELEGRAM_SYSTEM_CHAT_ID os problemas só vão para o log.
    TELEGRAM_SYSTEM_CHAT_ID: str | None = None
    HEALTH_ALERT_COOLDOWN_HOURS: int = Field(default=6, ge=1)
    HEALTH_LLM_FALLBACK_MAX: float = Field(default=0.5, ge=0, le=1)
    HEALTH_LLM_MIN_SAMPLE: int = Field(default=10, ge=1)
    HEALTH_QUEUE_MAX: int = Field(default=100, ge=1)
    HEALTH_BACKUP_MAX_AGE_HOURS: int = Field(default=26, ge=1)

    # Alertas (TIE-31): score mínimo e intervalo entre alertas do mesmo produto
    ALERT_THRESHOLD: float = 85.0
    ALERT_COOLDOWN_HOURS: int = 24

    # Mercado Livre
    ML_BASE_URL: str = "https://api.mercadolibre.com"
    ML_SITE_ID: str = "MLB"
    # Teto de requisições por minuto de cada collector (0 desliga)
    ML_MAX_REQUESTS_PER_MINUTE: int = 60
    # OAuth (TIE-41): a API do ML deixou de ser pública. Credenciais do app
    # criado em developers.mercadolivre.com.br; o REDIRECT_URI tem de ser
    # idêntico ao cadastrado no app. O token em si mora no Mongo (`ml_oauth`),
    # obtido uma vez com `python -m apps.ml_auth.main` e renovado sozinho.
    ML_CLIENT_ID: str | None = None
    ML_CLIENT_SECRET: str | None = None
    ML_REDIRECT_URI: str | None = None
    ML_AUTH_URL: str = "https://auth.mercadolivre.com.br/authorization"

    # Bootstrap de categorias
    BOOTSTRAP_FETCH_ML_CATEGORIES: bool = False
    BOOTSTRAP_AUTO_ENABLE: bool = False

    # Apify
    APIFY_TOKEN: str | None = None
    APIFY_ACTOR_ID: str = "clockworks~tiktok-scraper"
    TIKTOK_HASHTAGS: str = "fyp"
    # Volume da coleta: cada vídeo consome crédito da Apify
    TIKTOK_RESULTS_PER_HASHTAG: int = 10
    TIKTOK_INTERVAL_MIN: int = 120
    APIFY_MAX_REQUESTS_PER_MINUTE: int = 30

    # Circuit breaker por serviço externo (TIE-37): abre após N falhas
    # consecutivas; depois do cooldown, deixa passar uma tentativa de teste.
    BREAKER_FAILURE_THRESHOLD: int = Field(default=5, ge=1)
    BREAKER_COOLDOWN_MIN: int = Field(default=30, ge=1)

    # Pesos do score (TIE-22). Os 6 numéricos somam 1.0; NUMERIC + LLM somam 1.0.
    # Validados na subida: peso errado derruba o processo, não a análise.
    SCORE_W_RANK: float = 0.20
    SCORE_W_REVIEWS: float = 0.15
    SCORE_W_SOCIAL: float = 0.25
    SCORE_W_VIEWS: float = 0.15
    SCORE_W_ENGAGEMENT: float = 0.15
    SCORE_W_PRICE_STABILITY: float = 0.10
    SCORE_W_NUMERIC: float = 0.6
    SCORE_W_LLM: float = 0.4
    # TIE-21: "percentile" compara views/engajamento dentro da categoria;
    # "absolute" volta às faixas fixas. Categoria com menos que MIN_GROUP
    # produtos usa o pool global; pool global pequeno demais → absoluto.
    SCORE_NORMALIZATION: Literal["percentile", "absolute"] = "percentile"
    # Motor de tendência: vídeo mais velho que isso não é tendência e fica fora
    # da análise; o piso de idade evita que vídeo de minutos exploda views/h
    TREND_MAX_AGE_DAYS: int = Field(default=30, ge=1)
    TREND_MIN_AGE_HOURS: float = Field(default=6.0, gt=0)
    SCORE_PERCENTILE_MIN_GROUP: int = Field(default=5, ge=2)
    # TIE-18: vídeo do TikTok sem sinal de venda (loja, "link na bio", preço...)
    # fica fora da análise. false volta a pontuar tudo.
    TREND_REQUIRE_COMMERCIAL: bool = True

    # LLM
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "gpt-4.1-mini"
    # TIE-25: horas que uma análise do LLM é reaproveitada para as mesmas
    # métricas de entrada. 0 desliga o cache.
    LLM_CACHE_TTL_HOURS: int = Field(default=6, ge=0)

    @model_validator(mode="after")
    def _valida_pesos(self) -> "Settings":
        self.score_weights()
        self.hybrid_weights()
        return self

    def score_weights(self) -> ScoreWeights:
        return ScoreWeights(
            rank=self.SCORE_W_RANK,
            reviews=self.SCORE_W_REVIEWS,
            social=self.SCORE_W_SOCIAL,
            views=self.SCORE_W_VIEWS,
            engagement=self.SCORE_W_ENGAGEMENT,
            price_stability=self.SCORE_W_PRICE_STABILITY,
        )

    def hybrid_weights(self) -> HybridWeights:
        return HybridWeights(numeric=self.SCORE_W_NUMERIC, llm=self.SCORE_W_LLM)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def missing(self, *names: str) -> list[str]:
        """Nomes, dentre `names`, sem valor (None ou só espaços)."""
        return [n for n in names if not str(getattr(self, n) or "").strip()]


settings = Settings()
