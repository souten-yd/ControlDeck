"""workflow durable typed state

Revision ID: b7e1d94c2f60
Revises: a4d9c73b1e52
Create Date: 2026-07-21 08:20:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e1d94c2f60"
down_revision: Union[str, Sequence[str], None] = "a4d9c73b1e52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_state_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("state_key", sa.String(length=128), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("written_by_execution_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.ForeignKeyConstraint(["written_by_execution_id"], ["workflow_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "namespace", "state_key", name="uq_workflow_state_key"),
    )
    with op.batch_alter_table("workflow_state_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflow_state_entries_namespace"), ["namespace"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_state_entries_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_state_entries_workflow_id"), ["workflow_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_state_entries_written_by_execution_id"), ["written_by_execution_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflow_state_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_state_entries_written_by_execution_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_state_entries_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_state_entries_updated_at"))
        batch_op.drop_index(batch_op.f("ix_workflow_state_entries_namespace"))
    op.drop_table("workflow_state_entries")
