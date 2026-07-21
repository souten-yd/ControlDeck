"""workflow durable cache

Revision ID: a4d9c73b1e52
Revises: f310a4c29d7b
Create Date: 2026-07-21 08:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4d9c73b1e52"
down_revision: Union[str, Sequence[str], None] = "f310a4c29d7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_cache_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.Column("written_by_execution_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.ForeignKeyConstraint(["written_by_execution_id"], ["workflow_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "namespace", "cache_key", name="uq_workflow_cache_key"),
    )
    with op.batch_alter_table("workflow_cache_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflow_cache_entries_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_cache_entries_namespace"), ["namespace"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_cache_entries_workflow_id"), ["workflow_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_cache_entries_written_by_execution_id"), ["written_by_execution_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflow_cache_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_cache_entries_written_by_execution_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_cache_entries_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_cache_entries_namespace"))
        batch_op.drop_index(batch_op.f("ix_workflow_cache_entries_expires_at"))
    op.drop_table("workflow_cache_entries")
