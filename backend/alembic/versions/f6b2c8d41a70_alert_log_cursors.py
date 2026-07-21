"""durable alert log cursors

Revision ID: f6b2c8d41a70
Revises: e4f1a7b9c203
Create Date: 2026-07-21 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b2c8d41a70"
down_revision: Union[str, Sequence[str], None] = "e4f1a7b9c203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_log_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=8), nullable=False),
        sa.Column("file_identity", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("offset", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "stream", name="uq_alert_log_cursor_stream"),
    )
    with op.batch_alter_table("alert_log_cursors") as batch_op:
        batch_op.create_index(batch_op.f("ix_alert_log_cursors_rule_id"), ["rule_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("alert_log_cursors") as batch_op:
        batch_op.drop_index(batch_op.f("ix_alert_log_cursors_rule_id"))
    op.drop_table("alert_log_cursors")
