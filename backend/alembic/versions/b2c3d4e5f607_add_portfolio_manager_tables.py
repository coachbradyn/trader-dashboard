"""add portfolio manager tables

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f607'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Portfolio Actions (Action Queue) ─────────────────────────────
    op.create_table(
        'portfolio_actions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('portfolio_id', sa.String(36), sa.ForeignKey('portfolios.id'), nullable=False, index=True),
        sa.Column('ticker', sa.String(10), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('action_type', sa.String(20), nullable=False),
        sa.Column('suggested_qty', sa.Float(), nullable=True),
        sa.Column('suggested_price', sa.Float(), nullable=True),
        sa.Column('current_price', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('trigger_type', sa.String(20), nullable=False, index=True),
        sa.Column('trigger_ref', sa.String(36), nullable=True),
        sa.Column('priority_score', sa.Float(), nullable=False, server_default='0', index=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending', index=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('reject_reason', sa.Text(), nullable=True),
        sa.Column('outcome_pnl', sa.Float(), nullable=True),
        sa.Column('outcome_correct', sa.Boolean(), nullable=True),
        sa.Column('outcome_resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── Backtest Imports ─────────────────────────────────────────────
    op.create_table(
        'backtest_imports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('strategy_name', sa.String(50), nullable=False, index=True),
        sa.Column('strategy_version', sa.String(20), nullable=True),
        sa.Column('exchange', sa.String(20), nullable=True),
        sa.Column('ticker', sa.String(10), nullable=False, index=True),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('trade_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('win_rate', sa.Float(), nullable=True),
        sa.Column('profit_factor', sa.Float(), nullable=True),
        sa.Column('avg_gain_pct', sa.Float(), nullable=True),
        sa.Column('avg_loss_pct', sa.Float(), nullable=True),
        sa.Column('max_drawdown_pct', sa.Float(), nullable=True),
        sa.Column('max_adverse_excursion_pct', sa.Float(), nullable=True),
        sa.Column('avg_hold_days', sa.Float(), nullable=True),
        sa.Column('total_pnl_pct', sa.Float(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── Backtest Trades ──────────────────────────────────────────────
    op.create_table(
        'backtest_trades',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('import_id', sa.String(36), sa.ForeignKey('backtest_imports.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('trade_number', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('signal', sa.String(50), nullable=True),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('qty', sa.Float(), nullable=True),
        sa.Column('position_value', sa.Float(), nullable=True),
        sa.Column('net_pnl', sa.Float(), nullable=True),
        sa.Column('net_pnl_pct', sa.Float(), nullable=True),
        sa.Column('favorable_excursion', sa.Float(), nullable=True),
        sa.Column('favorable_excursion_pct', sa.Float(), nullable=True),
        sa.Column('adverse_excursion', sa.Float(), nullable=True),
        sa.Column('adverse_excursion_pct', sa.Float(), nullable=True),
        sa.Column('cumulative_pnl', sa.Float(), nullable=True),
        sa.Column('cumulative_pnl_pct', sa.Float(), nullable=True),
        sa.Column('trade_date', sa.DateTime(), nullable=False),
    )

    # ── Portfolio Holdings ───────────────────────────────────────────
    op.create_table(
        'portfolio_holdings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('portfolio_id', sa.String(36), sa.ForeignKey('portfolios.id'), nullable=False, index=True),
        sa.Column('trade_id', sa.String(36), sa.ForeignKey('trades.id'), nullable=True, index=True),
        sa.Column('ticker', sa.String(10), nullable=False, index=True),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('entry_date', sa.DateTime(), nullable=False),
        sa.Column('strategy_name', sa.String(50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('portfolio_holdings')
    op.drop_table('backtest_trades')
    op.drop_table('backtest_imports')
    op.drop_table('portfolio_actions')
