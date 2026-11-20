"""push receipts

Revision ID: d3a8f1c5e7b2
Revises: c7d2e9f4a1b6
Create Date: 2026-11-21 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3a8f1c5e7b2"
down_revision: Union[str, Sequence[str], None] = "c7d2e9f4a1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("push_subscriptions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_receipt_error", sa.String(length=200), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("push_subscriptions", schema=None) as batch_op:
        batch_op.drop_column("last_receipt_error")
        batch_op.drop_column("last_received_at")
