"""
Central application configuration.
Reads values from environment variables / .env file via pydantic-settings.
"""
from urllib.parse import quote_plus
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "SFMS Backend"
    APP_ENV: str = "development"
    DEBUG: bool = True
    # Set false after running the one-time backfill command on upgraded databases.
    RUN_STARTUP_USER_BACKFILL: bool = True
    API_V1_PREFIX: str = "/api"

    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Soft layer switch: when false, AI routes are not mounted. Core finance
    # (auth, transactions, accounts, analytics) keeps running without AI.
    AI_ENABLED: bool = True

    # Only free-tier cloud providers or local Ollama are supported by the AI bulk flow.
    # Keys live in the backend environment, never in frontend/Vite variables.
    AI_PROVIDER: str = "groq"  # groq | gemini | ollama
    AI_API_KEY: str = ""       # Groq or Gemini key (ignored for Ollama)
    AI_MODEL: str = ""         # Empty => provider's default model
    AI_OLLAMA_BASE_URL: str = "http://127.0.0.1:11434/v1"
    # Optional SECOND free provider, used only when the first is rate-limited or down.
    AI_FALLBACK_PROVIDER: str = ""   # groq | gemini | ollama | empty (disabled)
    AI_FALLBACK_API_KEY: str = ""
    AI_FALLBACK_MODEL: str = ""      # Empty => that provider's default model
    # AI-only options. No other SFMS modules depend on these settings.
    AI_REVIEW_INCOMPLETE_PRIMARY: bool = True  # Ask Gemini to complete clearly missing source entries
    AI_REPHRASE_QUESTIONS: bool = False  # Optional extra LLM request per session; off by default

    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 15

    # Soft layer switch for the admin panel API (/admin/*). When false the routes are not mounted.
    ADMIN_PANEL_ENABLED: bool = True
    # Optional Reply-To header for emails admins send from the contact-message inbox.
    CONTACT_REPLY_TO: str = ""

    CORS_ORIGINS: str = "http://localhost:3000"

    # Email verification SMTP settings.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_FROM_NAME: str = "SFMS"
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False

    # Public backend origin used to build verification links sent by email.
    # Example in production: https://api.example.com
    PUBLIC_BACKEND_URL: str = "http://localhost:8000"
    # Public frontend origin used for browser-facing verification links.
    # Example in production: https://app.example.com
    PUBLIC_FRONTEND_URL: str = "http://localhost:5173"

    # Local profile-image storage. These are additive and do not change any
    # existing API route. MEDIA_URL is mounted by app/main.py.
    MEDIA_ROOT: str = "media"
    MEDIA_URL: str = "/media"
    MAX_PROFILE_IMAGE_MB: int = 1

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def DATABASE_URL(self) -> str:
        password = quote_plus(self.DB_PASSWORD)

        return (
            f"postgresql+psycopg2://"
            f"{self.DB_USER}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?sslmode=require"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [
            o.strip()
            for o in self.CORS_ORIGINS.split(",")
            if o.strip()
        ]


settings = Settings()
