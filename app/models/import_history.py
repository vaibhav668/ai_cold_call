import uuid
from sqlalchemy import String, Integer, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class ImportHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "import_history"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # 'success', 'failed', 'partial'
    total_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successfully_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_details: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # List of validation errors
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
