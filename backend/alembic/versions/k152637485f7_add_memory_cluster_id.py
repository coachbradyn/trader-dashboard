"""add cluster_id to henry_memory for gaussian mixture retrieval

Revision ID: k152637485f7
Revises: j041526374e6
Create Date: 2026-04-13 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k152637485f7'
down_revision: Union[str, None] = 'j041526374e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add `cluster_id` (Integer) to henry_memory. Populated by the periodic
    GMM fit in henry_stats_engine._compute_memory_clusters. Retrieval uses
    this to boost memories in the same gaussian cluster as the query.

    Null when unclustered (new memories before the next fit run, or fewer
    than the minimum memory count for clustering).
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)

    existing_tables = inspector.get_table_names()
    if "henry_memory" not in existing_tables:
        return

    existing_cols = [c["name"] for c in inspector.get_columns("henry_memory")]
    if "cluster_id" not in existing_cols:
        op.add_column(
            'henry_memory',
            sa.Column('cluster_id', sa.Integer(), nullable=True),
        )
        # Index helps the retrieval path filter candidates by cluster quickly
        # once we grow past a few thousand memories.
        op.create_index(
            'ix_henry_memory_cluster_id',
            'henry_memory',
            ['cluster_id'],
        )


def downgrade() -> None:
    op.drop_index('ix_henry_memory_cluster_id', table_name='henry_memory')
    op.drop_column('henry_memory', 'cluster_id')
