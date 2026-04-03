import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class ScreenerAnalysis(Base):
    __tablename__ = "screener_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    picks: Mapped[dict | None] = mapped_column(JSON)  # Array of trade ideas
    market_context: Mapped[dict | None] = mapped_column(JSON)  # Sector heat, catalysts, noise ratio
    alerts_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(default=lambda: utcnow(), index=True)
