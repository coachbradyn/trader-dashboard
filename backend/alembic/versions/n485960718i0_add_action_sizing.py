"""add position sizing fields to portfolio_actions

Revision ID: n485960718i0
Revises: m374859607h9
Create Date: 2026-04-13 14:00:00.000000

Adds recommended_shares / recommended_dollar_amount /
recommended_pct_of_equity / sizing_method to portfolio_actions.
All nullable so existing rows survive intact.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n485960718i0'
down_revision: Union[str, None] = 'm374859607h9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if "portfolio_actions" not in inspector.get_table_names():
        return
    existing = [c["name"] for c in inspector.get_columns("portfolio_actions")]

    additions = [
        ("recommended_shares", sa.Float()),
        ("recommended_dollar_amount", sa.Float()),
        ("recommended_pct_of_equity", sa.Float()),
        ("sizing_method", sa.String(30)),
    ]
    for name, col_type in additions:
        if name not in existing:
            op.add_column("portfolio_actions", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    op.drop_column("portfolio_actions", "sizing_method")
    op.drop_column("portfolio_actions", "recommended_pct_of_equity")
    op.drop_column("portfolio_actions", "recommended_dollar_amount")
    op.drop_column("portfolio_actions", "recommended_shares")
