"""add cluster_silhouette to henry_memory

Revision ID: l263748596g8
Revises: k152637485f7
Create Date: 2026-04-13 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l263748596g8'
down_revision: Union[str, None] = 'k152637485f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    if "henry_memory" not in existing_tables:
        return
    existing_cols = [c["name"] for c in inspector.get_columns("henry_memory")]
    if "cluster_silhouette" not in existing_cols:
        op.add_column(
            'henry_memory',
            sa.Column('cluster_silhouette', sa.Float(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('henry_memory', 'cluster_silhouette')
