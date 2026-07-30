import uuid
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'pdf', 'txt'
    status: Mapped[str] = mapped_column(String(50), default="processing", nullable=False)  # 'processing', 'active', 'failed'
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
