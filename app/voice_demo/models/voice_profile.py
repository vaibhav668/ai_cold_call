import uuid
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class VoiceProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "voice_profiles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    avatar: Mapped[str] = mapped_column(String(255), nullable=True)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    supported_languages: Mapped[str] = mapped_column(Text, nullable=False)  # Comma-separated languages (e.g., "English,Hindi,Telugu")
    voice_provider: Mapped[str] = mapped_column(String(50), default="melotts", nullable=False)
    voice_configuration: Mapped[str] = mapped_column(Text, nullable=True)  # JSON configuration mapping speaker attributes
    preview_audio: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # "active", "inactive"
