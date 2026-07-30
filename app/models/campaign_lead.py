import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class CampaignLead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_leads"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # 'pending', 'ringing', 'completed', 'failed', 'retry_scheduled'
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
