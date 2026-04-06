"""Write-ahead log for incoming webhooks.

Every webhook is persisted as 'pending' before processing begins.
On success → 'processed'. On error → 'failed'. On startup, pending
rows are replayed to recover from mid-processing crashes.
"""

import uuid
from app.utils.utc import utcnow
from datetime import datetime

from sqlalchemy import String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending / processed / failed
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())
    processed_at: Mapped[datetime | None] = mapped_column()
