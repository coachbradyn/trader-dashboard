"""add options trading: options_trades table + portfolio options columns

Revision ID: q718293041l3
Revises: p607182930k2
Create Date: 2026-04-14 12:00:00.000000

Foundation for options trading.  Adds:
  - options_trades table (one row per leg; spread_group_id ties legs
    of a multi-leg strategy together).
  - portfolios.options_level (int, default 0)
  - portfolios.max_options_risk, .max_options_daily_trades,
    .options_allocation_pct (null = use global default from henry_cache).

All ops are idempotent so re-running on an already-partially-migrated DB
is safe.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'q718293041l3'
down_revision: Union[str, None] = 'p607182930k2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # ── options_trades table ─────────────────────────────────────────
    if "options_trades" not in tables:
        op.create_table(
            "options_trades",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "portfolio_id",
                sa.String(36),
                sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("ticker", sa.String(20), nullable=False, index=True),
            sa.Column("option_symbol", sa.String(40), nullable=False, index=True),
            sa.Column("option_type", sa.String(4), nullable=False),
            sa.Column("strike", sa.Float, nullable=False),
            sa.Column("expiration", sa.Date, nullable=False, index=True),
            sa.Column("direction", sa.String(5), nullable=False),
            sa.Column("quantity", sa.Integer, nullable=False),
            sa.Column("entry_premium", sa.Float, nullable=False),
            sa.Column("entry_time", sa.DateTime, nullable=False),
            sa.Column("underlying_price_at_entry", sa.Float),
            sa.Column("greeks_at_entry", sa.JSON),
            sa.Column("iv_at_entry", sa.Float),
            sa.Column("current_premium", sa.Float),
            sa.Column("greeks_current", sa.JSON),
            sa.Column("exit_premium", sa.Float),
            sa.Column("exit_time", sa.DateTime),
            sa.Column("pnl_dollars", sa.Float),
            sa.Column("pnl_percent", sa.Float),
            sa.Column("status", sa.String(10), nullable=False, default="open", index=True),
            sa.Column("strategy_type", sa.String(30), nullable=False, index=True),
            sa.Column("spread_group_id", sa.String(36), index=True),
            sa.Column("alpaca_order_id", sa.String(64)),
            sa.Column("notes", sa.Text),
            sa.Column("created_at", sa.DateTime),
        )
        op.create_index(
            "ix_options_trades_portfolio_status",
            "options_trades",
            ["portfolio_id", "status"],
        )
        op.create_index(
            "ix_options_trades_expiration_status",
            "options_trades",
            ["expiration", "status"],
        )

    # ── portfolios.options_* columns ─────────────────────────────────
    portfolio_cols = [c["name"] for c in inspector.get_columns("portfolios")]
    if "options_level" not in portfolio_cols:
        op.add_column(
            "portfolios",
            sa.Column("options_level", sa.Integer, nullable=False, server_default="0"),
        )
    if "max_options_risk" not in portfolio_cols:
        op.add_column(
            "portfolios",
            sa.Column("max_options_risk", sa.Float, nullable=True),
        )
    if "max_options_daily_trades" not in portfolio_cols:
        op.add_column(
            "portfolios",
            sa.Column("max_options_daily_trades", sa.Integer, nullable=True),
        )
    if "options_allocation_pct" not in portfolio_cols:
        op.add_column(
            "portfolios",
            sa.Column(
                "options_allocation_pct",
                sa.Float,
                nullable=False,
                server_default="0.20",
            ),
        )


def downgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)

    portfolio_cols = [c["name"] for c in inspector.get_columns("portfolios")]
    for col in (
        "options_allocation_pct",
        "max_options_daily_trades",
        "max_options_risk",
        "options_level",
    ):
        if col in portfolio_cols:
            op.drop_column("portfolios", col)

    if "options_trades" in inspector.get_table_names():
        op.drop_index("ix_options_trades_expiration_status", table_name="options_trades")
        op.drop_index("ix_options_trades_portfolio_status", table_name="options_trades")
        op.drop_table("options_trades")
