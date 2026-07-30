import uuid
from sqlalchemy import String, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class PromptTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "prompt_templates"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    language_prompt: Mapped[str] = mapped_column(Text, nullable=True)
    conversation_goals: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
