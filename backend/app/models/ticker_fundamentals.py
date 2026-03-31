"""
Ticker Fundamentals
===================
Cached financial data from FMP (Financial Modeling Prep) for watchlist tickers.
One row per ticker, upserted on each refresh. No historical data — just current state.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, Integer, Float, Date, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TickerFundamentals(Base):
    __tablename__ = "ticker_fundamentals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(200))
    market_cap: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)

    # Earnings
    earnings_date: Mapped[date | None] = mapped_column(Date)
    earnings_time: Mapped[str | None] = mapped_column(String(10))  # "bmo" or "amc"

    # Analyst data
    analyst_target_low: Mapped[float | None] = mapped_column(Float)
    analyst_target_high: Mapped[float | None] = mapped_column(Float)
    analyst_target_consensus: Mapped[float | None] = mapped_column(Float)
    analyst_rating: Mapped[str | None] = mapped_column(String(30))
    analyst_count: Mapped[int | None] = mapped_column(Integer)

    # EPS
    eps_estimate_current: Mapped[float | None] = mapped_column(Float)
    eps_actual_last: Mapped[float | None] = mapped_column(Float)
    eps_surprise_last: Mapped[float | None] = mapped_column(Float)

    # Revenue
    revenue_estimate_current: Mapped[float | None] = mapped_column(Float)
    revenue_actual_last: Mapped[float | None] = mapped_column(Float)

    # Valuation / short interest
    pe_ratio: Mapped[float | None] = mapped_column(Float)
    short_interest_pct: Mapped[float | None] = mapped_column(Float)

    # Insider activity
    insider_transactions_90d: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_ticker_fundamentals_ticker", "ticker", unique=True),
    )
