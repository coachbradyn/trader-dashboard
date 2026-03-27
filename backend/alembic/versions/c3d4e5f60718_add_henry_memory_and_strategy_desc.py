"""add henry memory table and strategy_description to traders

Revision ID: c3d4e5f60718
Revises: b2c3d4e5f607
Create Date: 2026-03-26 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f60718'
down_revision: Union[str, None] = 'b2c3d4e5f607'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add strategy_description to traders table (safe if already exists)
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_cols = [c["name"] for c in inspector.get_columns("traders")]
    if "strategy_description" not in existing_cols:
        op.add_column('traders', sa.Column('strategy_description', sa.Text(), nullable=True))

    # Create henry_memory table (safe if already exists)
    existing_tables = inspector.get_table_names()
    if "henry_memory" not in existing_tables:
        op.create_table(
            'henry_memory',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('memory_type', sa.String(30), nullable=False, index=True),
            sa.Column('strategy_id', sa.String(50), nullable=True, index=True),
            sa.Column('ticker', sa.String(10), nullable=True, index=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('importance', sa.Integer(), default=5),
            sa.Column('reference_count', sa.Integer(), default=0),
            sa.Column('validated', sa.Boolean(), nullable=True),
            sa.Column('source', sa.String(30), default='system'),
            sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table('henry_memory')
    op.drop_column('traders', 'strategy_description')
