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

    # Speech AI providers
    STT_PROVIDER: str = "faster_whisper"
    WHISPER_MODEL: str = "large-v3-turbo"
    VAD_PROVIDER: str = "silero"
    TTS_PROVIDER: str = "melotts"
    EMBEDDING_PROVIDER: str = "bge_m3"
    EMBEDDING_MODEL: str = "BAAI/bge-m3"


    # Security & Authentication
    JWT_SECRET_KEY: str = "supersecretdevelopmentkeychangeinproduction"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @classmethod
    def validate_critical_settings(cls, values: dict) -> dict:
        db_url = values.get("DATABASE_URL")
        if not db_url or db_url.strip() == "":
            raise ValueError("DATABASE_URL environment variable is missing or empty. Application startup aborted.")
        return values

settings = Settings()
# Custom manual check to trigger clear error message on init
if not settings.DATABASE_URL or settings.DATABASE_URL.strip() == "":
    raise ValueError("DATABASE_URL environment variable is missing or empty. Application startup aborted.")


def check_low_memory() -> bool:
    """
    Unified check to determine if the system is running in a memory-constrained
    environment (e.g. Render 512MB container, cgroups limit, or low host RAM).
    """
    import os
    # 1. Explicit environment overrides or platform indicators
    if os.environ.get("LOW_MEMORY_DEPLOYMENT", "true").lower() == "true":
        return True
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID") or os.environ.get("RENDER_INSTANCE_ID"):
        return True

    # 2. Check cgroups memory limit (accurate for container limits like Render)
    for path in ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory.high"]:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    val = f.read().strip()
                    if val and val.isdigit():
                        limit = int(val)
                        if limit < 1024 * 1024 * 1024:  # < 1GB limit
                            return True
        except Exception:
            pass

    # 3. Check psutil as fallback
    try:
        import psutil
        if psutil.virtual_memory().total < 1024 * 1024 * 1024:
            return True
    except Exception:
        pass

    return False

