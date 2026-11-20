"""push subscriptions

Revision ID: c7d2e9f4a1b6
Revises: b5e1c7a93d24
Create Date: 2026-11-21 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d2e9f4a1b6"
down_revision: Union[str, Sequence[str], None] = "b5e1c7a93d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint_digest", sa.String(length=64), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_digest"),
    )
    with op.batch_alter_table("push_subscriptions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_push_subscriptions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("push_subscriptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_push_subscriptions_user_id"))
    op.drop_table("push_subscriptions")
