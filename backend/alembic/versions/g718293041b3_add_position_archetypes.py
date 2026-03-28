"""add position archetypes

Revision ID: g718293041b3
Revises: f607182930a2
Create Date: 2026-03-28 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g718293041b3'
down_revision: Union[str, None] = 'f607182930a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("portfolio_holdings")]

    new_cols = {
        "position_type": sa.Column("position_type", sa.String(20), server_default="momentum"),
        "thesis": sa.Column("thesis", sa.Text(), nullable=True),
        "catalyst_date": sa.Column("catalyst_date", sa.Date(), nullable=True),
        "catalyst_description": sa.Column("catalyst_description", sa.String(200), nullable=True),
        "max_allocation_pct": sa.Column("max_allocation_pct", sa.Float(), nullable=True),
        "dca_enabled": sa.Column("dca_enabled", sa.Boolean(), server_default="false"),
        "dca_threshold_pct": sa.Column("dca_threshold_pct", sa.Float(), nullable=True),
        "avg_cost": sa.Column("avg_cost", sa.Float(), nullable=True),
        "total_shares": sa.Column("total_shares", sa.Float(), nullable=True),
    }
    for name, col in new_cols.items():
        if name not in cols:
            op.add_column("portfolio_holdings", col)


def downgrade() -> None:
    cols_to_remove = [
        "position_type", "thesis", "catalyst_date", "catalyst_description",
        "max_allocation_pct", "dca_enabled", "dca_threshold_pct",
        "avg_cost", "total_shares",
    ]
    for col in cols_to_remove:
        try:
            op.drop_column("portfolio_holdings", col)
        except Exception:
            pass
