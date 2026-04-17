"""add signal_weights to portfolio_actions

Revision ID: r829304152m4
Revises: q718293041l3
Create Date: 2026-04-17 16:00:00.000000

Bayesian Decision Learning (Phase 1): stores per-decision signal
component weights so the posterior engine can learn which signals
predict outcomes.
"""
from alembic import op
import sqlalchemy as sa

revision = "r829304152m4"
down_revision = "q718293041l3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portfolio_actions") as batch_op:
        batch_op.add_column(
            sa.Column("signal_weights", sa.JSON(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("portfolio_actions") as batch_op:
        batch_op.drop_column("signal_weights")
