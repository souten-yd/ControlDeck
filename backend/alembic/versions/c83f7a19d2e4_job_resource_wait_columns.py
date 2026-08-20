"""add nullable resource wait columns to jobs

Revision ID: c83f7a19d2e4
Revises: b41f7d90c655
Create Date: 2026-08-21 00:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c83f7a19d2e4"
down_revision: Union[str, Sequence[str], None] = "b41f7d90c655"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("phase", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("wait_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("wait_reason")
        batch_op.drop_column("phase")
