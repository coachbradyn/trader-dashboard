"""add screener and settings tables

Revision ID: a1b2c3d4e5f6
Revises: 394815f1ac84
Create Date: 2026-03-06 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '394815f1ac84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Missing columns on existing tables ──────────────────────────

    # Portfolios: add risk fields + status
    op.add_column('portfolios', sa.Column('max_pct_per_trade', sa.Float(), nullable=True))
    op.add_column('portfolios', sa.Column('max_open_positions', sa.Integer(), nullable=True))
    op.add_column('portfolios', sa.Column('max_drawdown_pct', sa.Float(), nullable=True))
    op.add_column('portfolios', sa.Column('status', sa.String(20), nullable=False, server_default='active'))

    # Traders: add last_webhook_at
    op.add_column('traders', sa.Column('last_webhook_at', sa.DateTime(), nullable=True))

    # ── New tables ──────────────────────────────────────────────────

    # AllowlistedKey table
    op.create_table(
        'allowlisted_keys',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('api_key_hash', sa.String(255), nullable=False),
        sa.Column('label', sa.String(100), nullable=True),
        sa.Column('claimed_by_id', sa.String(36), sa.ForeignKey('traders.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # IndicatorAlert table
    op.create_table(
        'indicator_alerts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('ticker', sa.String(20), nullable=False, index=True),
        sa.Column('indicator', sa.String(50), nullable=False, index=True),
        sa.Column('value', sa.Float, nullable=False),
        sa.Column('signal', sa.String(20), nullable=False),
        sa.Column('timeframe', sa.String(10), nullable=True),
        sa.Column('metadata_extra', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now(), index=True),
    )

    # MarketSummary table
    op.create_table(
        'market_summaries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('summary_type', sa.String(20), nullable=False, index=True),
        sa.Column('scope', sa.String(20), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('tickers_analyzed', sa.JSON, nullable=True),
        sa.Column('generated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), index=True),
    )

    # ScreenerAnalysis table
    op.create_table(
        'screener_analyses',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('picks', sa.JSON, nullable=True),
        sa.Column('market_context', sa.JSON, nullable=True),
        sa.Column('alerts_analyzed', sa.Integer, nullable=False, server_default='0'),
        sa.Column('generated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table('screener_analyses')
    op.drop_table('market_summaries')
    op.drop_table('indicator_alerts')
    op.drop_table('allowlisted_keys')

    op.drop_column('traders', 'last_webhook_at')
    op.drop_column('portfolios', 'status')
    op.drop_column('portfolios', 'max_drawdown_pct')
    op.drop_column('portfolios', 'max_open_positions')
    op.drop_column('portfolios', 'max_pct_per_trade')
