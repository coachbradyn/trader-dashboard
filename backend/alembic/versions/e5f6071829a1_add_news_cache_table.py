"""add news_cache table

Revision ID: e5f6071829a1
Revises: d4e5f6071829
Create Date: 2026-03-27 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6071829a1'
down_revision: Union[str, None] = 'd4e5f6071829'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'news_cache' not in existing_tables:
        op.create_table(
            'news_cache',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('alpaca_id', sa.String(50), nullable=False),
            sa.Column('headline', sa.Text(), nullable=False),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('source', sa.String(100), nullable=True),
            sa.Column('tickers', sa.JSON(), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('url', sa.String(500), nullable=True),
            sa.Column('sentiment_score', sa.Float(), nullable=True),
            sa.Column('fetched_at', sa.DateTime(), default=sa.func.now()),
        )
        op.create_index('ix_news_cache_alpaca_id', 'news_cache', ['alpaca_id'], unique=True)
        op.create_index('ix_news_cache_published_at', 'news_cache', ['published_at'])
        op.create_index('ix_news_cache_fetched_at', 'news_cache', ['fetched_at'])


def downgrade() -> None:
    op.drop_table('news_cache')
