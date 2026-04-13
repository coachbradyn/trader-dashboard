"""add embedding columns to henry_memory for semantic retrieval

Revision ID: j041526374e6
Revises: i930415263d5
Create Date: 2026-04-13 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j041526374e6'
down_revision: Union[str, None] = 'i930415263d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add `embedding` (JSON) and `embedding_model` (String) columns to henry_memory.

    We use JSON here rather than pgvector so the migration works on both the
    Railway Postgres deployment and local sqlite dev DBs without requiring
    the pgvector extension. For <10k memories, in-Python cosine similarity is
    fast enough. A follow-up migration can port to pgvector with an ivfflat
    index once memory count or retrieval latency demands it.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)

    existing_tables = inspector.get_table_names()
    if "henry_memory" not in existing_tables:
        # Table doesn't exist yet — earlier migration will create it with these
        # columns via the updated model. Nothing to do.
        return

    existing_cols = [c["name"] for c in inspector.get_columns("henry_memory")]

    if "embedding" not in existing_cols:
        op.add_column(
            'henry_memory',
            sa.Column('embedding', sa.JSON(), nullable=True),
        )

    if "embedding_model" not in existing_cols:
        op.add_column(
            'henry_memory',
            sa.Column('embedding_model', sa.String(50), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('henry_memory', 'embedding_model')
    op.drop_column('henry_memory', 'embedding')
