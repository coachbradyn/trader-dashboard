import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class AllowlistedKey(Base):
    __tablename__ = "allowlisted_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100))
    claimed_by_id: Mapped[str | None] = mapped_column(ForeignKey("traders.id"))
    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    claimed_by: Mapped["Trader | None"] = relationship()
