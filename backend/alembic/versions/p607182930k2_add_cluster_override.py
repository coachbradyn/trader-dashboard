"""add cluster_id_override to henry_memory (carryover #32)

Revision ID: p607182930k2
Revises: o596071829j1
Create Date: 2026-04-13 16:00:00.000000

Single nullable integer column on henry_memory. When set, the
retrieval + viz code treats it as the authoritative cluster
assignment ahead of the GMM-computed cluster_id.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p607182930k2'
down_revision: Union[str, None] = 'o596071829j1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if "henry_memory" not in inspector.get_table_names():
        return
    existing = [c["name"] for c in inspector.get_columns("henry_memory")]
    if "cluster_id_override" not in existing:
        op.add_column(
            "henry_memory",
            sa.Column("cluster_id_override", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("henry_memory", "cluster_id_override")
