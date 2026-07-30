from sqlalchemy import String, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customers"

    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    custom_variables: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # JSONB field for metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
