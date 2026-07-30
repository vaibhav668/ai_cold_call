import uuid
from sqlalchemy import String, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class CallLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "call_logs"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    plivo_call_uuid: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)
    phone_number: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # 'initiated', 'ringing', 'in-progress', 'completed', 'failed'
    duration_seconds: Mapped[int] = mapped_column(default=0, nullable=False)
    transcript: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # List of conversational exchanges
