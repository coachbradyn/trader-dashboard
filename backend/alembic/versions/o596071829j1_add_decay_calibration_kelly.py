"""add decay/calibration/kelly fields (Phase 6: Systems 7/8/9)

Revision ID: o596071829j1
Revises: n485960718i0
Create Date: 2026-04-13 15:00:00.000000

henry_memory:
  - last_retrieved_at (DateTime, nullable) — System 7 decay bookkeeping
  - retrieval_count (Integer, default 0) — System 7

portfolio_actions:
  - kelly_f_base (Float, nullable) — System 9 audit trail
  - kelly_f_effective (Float, nullable) — System 9
  - injected_memory_ids (JSON, nullable) — System 7 outcome linkage
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'o596071829j1'
down_revision: Union[str, None] = 'n485960718i0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if "henry_memory" in tables:
        existing = [c["name"] for c in inspector.get_columns("henry_memory")]
        if "last_retrieved_at" not in existing:
            op.add_column("henry_memory", sa.Column("last_retrieved_at", sa.DateTime(), nullable=True))
        if "retrieval_count" not in existing:
            op.add_column("henry_memory", sa.Column("retrieval_count", sa.Integer(), nullable=False, server_default="0"))
        # Promote importance from INTEGER → FLOAT so System 7's small
        # outcome nudges (+0.3 / -0.15) and decay multiplier (×0.85) can
        # drift without rounding artifacts. Postgres performs this in
        # place; existing integer values cast cleanly to floats.
        cols_by_name = {c["name"]: c for c in inspector.get_columns("henry_memory")}
        imp_col = cols_by_name.get("importance")
        if imp_col is not None:
            type_str = str(imp_col.get("type", "")).lower()
            if "integer" in type_str or "int" in type_str:
                op.alter_column(
                    "henry_memory",
                    "importance",
                    type_=sa.Float(),
                    existing_type=sa.Integer(),
                    existing_nullable=True,
                    postgresql_using="importance::float",
                )

    if "portfolio_actions" in tables:
        existing = [c["name"] for c in inspector.get_columns("portfolio_actions")]
        if "kelly_f_base" not in existing:
            op.add_column("portfolio_actions", sa.Column("kelly_f_base", sa.Float(), nullable=True))
        if "kelly_f_effective" not in existing:
            op.add_column("portfolio_actions", sa.Column("kelly_f_effective", sa.Float(), nullable=True))
        if "injected_memory_ids" not in existing:
            op.add_column("portfolio_actions", sa.Column("injected_memory_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("portfolio_actions", "injected_memory_ids")
    op.drop_column("portfolio_actions", "kelly_f_effective")
    op.drop_column("portfolio_actions", "kelly_f_base")
    op.drop_column("henry_memory", "retrieval_count")
    op.drop_column("henry_memory", "last_retrieved_at")
