"""add ticker_fundamentals table

Revision ID: h829304152c4
Revises: g718293041b3
Create Date: 2026-03-30 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h829304152c4'
down_revision: Union[str, None] = 'g718293041b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if "ticker_fundamentals" not in tables:
        op.create_table(
            "ticker_fundamentals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("ticker", sa.String(20), nullable=False, unique=True),
            sa.Column("company_name", sa.String(200), nullable=True),
            sa.Column("sector", sa.String(100), nullable=True),
            sa.Column("industry", sa.String(200), nullable=True),
            sa.Column("market_cap", sa.Float(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("earnings_date", sa.Date(), nullable=True),
            sa.Column("earnings_time", sa.String(10), nullable=True),
            sa.Column("analyst_target_low", sa.Float(), nullable=True),
            sa.Column("analyst_target_high", sa.Float(), nullable=True),
            sa.Column("analyst_target_consensus", sa.Float(), nullable=True),
            sa.Column("analyst_rating", sa.String(30), nullable=True),
            sa.Column("analyst_count", sa.Integer(), nullable=True),
            sa.Column("eps_estimate_current", sa.Float(), nullable=True),
            sa.Column("eps_actual_last", sa.Float(), nullable=True),
            sa.Column("eps_surprise_last", sa.Float(), nullable=True),
            sa.Column("revenue_estimate_current", sa.Float(), nullable=True),
            sa.Column("revenue_actual_last", sa.Float(), nullable=True),
            sa.Column("pe_ratio", sa.Float(), nullable=True),
            sa.Column("short_interest_pct", sa.Float(), nullable=True),
            sa.Column("insider_transactions_90d", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        )
        op.create_index("ix_ticker_fundamentals_ticker", "ticker_fundamentals", ["ticker"], unique=True)
        op.create_index("ix_ticker_fundamentals_updated_at", "ticker_fundamentals", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_ticker_fundamentals_updated_at", table_name="ticker_fundamentals")
    op.drop_index("ix_ticker_fundamentals_ticker", table_name="ticker_fundamentals")
    op.drop_table("ticker_fundamentals")
