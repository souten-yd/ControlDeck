"""workflow durable queue

Revision ID: f310a4c29d7b
Revises: e2689f3f0c28
Create Date: 2026-07-21 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f310a4c29d7b"
down_revision: Union[str, Sequence[str], None] = "e2689f3f0c28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_queue_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.Column("enqueued_by_execution_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enqueued_by_execution_id"], ["workflow_executions.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "queue_name", "sequence", name="uq_workflow_queue_sequence"),
    )
    with op.batch_alter_table("workflow_queue_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflow_queue_items_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_queue_items_enqueued_by_execution_id"), ["enqueued_by_execution_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_queue_items_queue_name"), ["queue_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_queue_items_workflow_id"), ["workflow_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflow_queue_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_queue_items_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_queue_items_queue_name"))
        batch_op.drop_index(batch_op.f("ix_workflow_queue_items_enqueued_by_execution_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_queue_items_created_at"))
    op.drop_table("workflow_queue_items")
