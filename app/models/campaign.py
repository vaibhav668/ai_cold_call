from sqlalchemy import String, Text, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'hospital' or 'real_estate'
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)  # 'draft', 'scheduled', 'active', 'paused', 'completed'
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
