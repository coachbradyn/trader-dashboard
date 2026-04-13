"""add entry-time regime snapshot to trades

Revision ID: m374859607h9
Revises: l263748596g8
Create Date: 2026-04-13 13:00:00.000000

Adds entry_vix, entry_spy_close, entry_spy_20ema, entry_spy_adx — all
nullable. Populated going forward by trade_processor; existing rows
stay NULL and contribute only to unconditional stats in
_compute_conditional_probability.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm374859607h9'
down_revision: Union[str, None] = 'l263748596g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    if "trades" not in existing_tables:
        return
    existing_cols = [c["name"] for c in inspector.get_columns("trades")]

    additions = [
        ("entry_vix", sa.Float()),
        ("entry_spy_close", sa.Float()),
        ("entry_spy_20ema", sa.Float()),
        ("entry_spy_adx", sa.Float()),
    ]
    for name, col_type in additions:
        if name not in existing_cols:
            op.add_column("trades", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "entry_spy_adx")
    op.drop_column("trades", "entry_spy_20ema")
    op.drop_column("trades", "entry_spy_close")
    op.drop_column("trades", "entry_vix")
