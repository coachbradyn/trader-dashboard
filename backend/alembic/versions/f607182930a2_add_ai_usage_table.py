"""add ai_usage table

Revision ID: f607182930a2
Revises: e5f6071829a1
Create Date: 2026-03-28 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f607182930a2'
down_revision: Union[str, None] = 'e5f6071829a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_usage',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('function_name', sa.String(50), nullable=False),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('was_fallback', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_ai_usage_created_at', 'ai_usage', ['created_at'])
    op.create_index('ix_ai_usage_provider', 'ai_usage', ['provider'])


def downgrade() -> None:
    op.drop_index('ix_ai_usage_provider', table_name='ai_usage')
    op.drop_index('ix_ai_usage_created_at', table_name='ai_usage')
    op.drop_table('ai_usage')
