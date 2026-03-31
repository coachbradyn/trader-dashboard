"""expand ticker_fundamentals and add fmp_cache table

Revision ID: i930415263d5
Revises: h829304152c4
Create Date: 2026-03-31 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i930415263d5'
down_revision: Union[str, None] = 'h829304152c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # ── Create fmp_cache table ──────────────────────────────────────────
    if "fmp_cache" not in tables:
        op.create_table(
            "fmp_cache",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("endpoint", sa.String(200), nullable=False),
            sa.Column("params_hash", sa.String(64), nullable=False),
            sa.Column("response_data", sa.JSON(), nullable=True),
            sa.Column("cached_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
            sa.Column("cache_tier", sa.String(20), nullable=False, server_default="daily"),
        )
        op.create_index("ix_fmp_cache_endpoint", "fmp_cache", ["endpoint"])
        op.create_index("ix_fmp_cache_params_hash", "fmp_cache", ["params_hash"])
        op.create_index("ix_fmp_cache_endpoint_params", "fmp_cache", ["endpoint", "params_hash"], unique=True)

    # ── Add new columns to ticker_fundamentals ──────────────────────────
    if "ticker_fundamentals" in tables:
        existing_cols = [c["name"] for c in inspector.get_columns("ticker_fundamentals")]

        new_columns = {
            "beta": sa.Column("beta", sa.Float(), nullable=True),
            "forward_pe": sa.Column("forward_pe", sa.Float(), nullable=True),
            "profit_margin": sa.Column("profit_margin", sa.Float(), nullable=True),
            "roe": sa.Column("roe", sa.Float(), nullable=True),
            "debt_to_equity": sa.Column("debt_to_equity", sa.Float(), nullable=True),
            "revenue_growth_yoy": sa.Column("revenue_growth_yoy", sa.Float(), nullable=True),
            "dcf_value": sa.Column("dcf_value", sa.Float(), nullable=True),
            "dcf_diff_pct": sa.Column("dcf_diff_pct", sa.Float(), nullable=True),
            "dividend_yield": sa.Column("dividend_yield", sa.Float(), nullable=True),
            "insider_net_90d": sa.Column("insider_net_90d", sa.Float(), nullable=True),
            "institutional_ownership_pct": sa.Column("institutional_ownership_pct", sa.Float(), nullable=True),
            "company_description": sa.Column("company_description", sa.Text(), nullable=True),
        }

        for col_name, col_def in new_columns.items():
            if col_name not in existing_cols:
                op.add_column("ticker_fundamentals", col_def)


def downgrade() -> None:
    # Remove new columns from ticker_fundamentals
    new_cols = [
        "beta", "forward_pe", "profit_margin", "roe", "debt_to_equity",
        "revenue_growth_yoy", "dcf_value", "dcf_diff_pct", "dividend_yield",
        "insider_net_90d", "institutional_ownership_pct", "company_description",
    ]
    for col_name in new_cols:
        try:
            op.drop_column("ticker_fundamentals", col_name)
        except Exception:
            pass

    # Drop fmp_cache table
    try:
        op.drop_index("ix_fmp_cache_endpoint_params", table_name="fmp_cache")
        op.drop_index("ix_fmp_cache_params_hash", table_name="fmp_cache")
        op.drop_index("ix_fmp_cache_endpoint", table_name="fmp_cache")
        op.drop_table("fmp_cache")
    except Exception:
        pass
