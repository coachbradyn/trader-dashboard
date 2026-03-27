"""add henry_context and henry_stats tables

Revision ID: d4e5f6071829
Revises: c3d4e5f60718
Create Date: 2026-03-26 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6071829'
down_revision: Union[str, None] = 'c3d4e5f60718'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # Create henry_context table (safe if already exists)
    if 'henry_context' not in existing_tables:
        op.create_table(
            'henry_context',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('strategy', sa.String(50), nullable=True),
        sa.Column('portfolio_id', sa.String(36), sa.ForeignKey('portfolios.id'), nullable=True),
        sa.Column('context_type', sa.String(30), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Integer(), nullable=True),
        sa.Column('action_id', sa.String(36), sa.ForeignKey('portfolio_actions.id'), nullable=True),
        sa.Column('trade_id', sa.String(36), sa.ForeignKey('trades.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
    )
        op.create_index('ix_henry_context_ticker', 'henry_context', ['ticker'])
        op.create_index('ix_henry_context_strategy', 'henry_context', ['strategy'])
        op.create_index('ix_henry_context_context_type', 'henry_context', ['context_type'])
        op.create_index('ix_henry_context_created_at', 'henry_context', ['created_at'])
        op.create_index('ix_henry_context_ticker_strategy_created', 'henry_context', ['ticker', 'strategy', 'created_at'])

    # Create henry_stats table (safe if already exists)
    if 'henry_stats' not in existing_tables:
        op.create_table(
            'henry_stats',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('stat_type', sa.String(50), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('strategy', sa.String(50), nullable=True),
        sa.Column('portfolio_id', sa.String(36), sa.ForeignKey('portfolios.id'), nullable=True),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('period_days', sa.Integer(), default=30),
        sa.Column('computed_at', sa.DateTime(), default=sa.func.now()),
    )
        op.create_index('ix_henry_stats_stat_type', 'henry_stats', ['stat_type'])
        op.create_index('ix_henry_stats_ticker', 'henry_stats', ['ticker'])
        op.create_index('ix_henry_stats_strategy', 'henry_stats', ['strategy'])
        op.create_index('ix_henry_stats_computed_at', 'henry_stats', ['computed_at'])
        op.create_index('ix_henry_stats_type_ticker_strategy', 'henry_stats', ['stat_type', 'ticker', 'strategy'])


def downgrade() -> None:
    op.drop_table('henry_stats')
    op.drop_table('henry_context')
