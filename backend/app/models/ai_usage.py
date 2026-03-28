import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Boolean, DateTime
from app.database import Base


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = Column(String(20), nullable=False)  # "claude" or "gemini"
    function_name = Column(String(50), nullable=False)  # "signal_evaluation", "morning_briefing", etc.
    model = Column(String(100))
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    was_fallback = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
