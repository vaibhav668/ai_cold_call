from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "AI Voice Calling Platform"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Databases
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_cold_call"
    VECTOR_DB_PROVIDER: str = "chroma"
    CHROMA_DB_PATH: str = "./data/chroma"

    # Telephony
    TELEPHONY_PROVIDER: str = "plivo"
    PLIVO_AUTH_ID: Optional[str] = None
    PLIVO_AUTH_TOKEN: Optional[str] = None
    PLIVO_PHONE_NUMBER: Optional[str] = None

    # AI
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    LLM_PROVIDER: str = "groq"
    FALLBACK_LLM_PROVIDER: str = "openrouter"
    LLM_MODEL: str = "llama-3.1-8b-instant"


    # Security & Authentication
    JWT_SECRET_KEY: str = "supersecretdevelopmentkeychangeinproduction"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

settings = Settings()
