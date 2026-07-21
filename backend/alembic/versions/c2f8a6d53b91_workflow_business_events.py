"""workflow business event outbox

Revision ID: c2f8a6d53b91
Revises: b7e1d94c2f60
Create Date: 2026-07-21 08:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2f8a6d53b91"
down_revision: Union[str, Sequence[str], None] = "b7e1d94c2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_business_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_name", sa.String(length=128), nullable=False),
        sa.Column("source_workflow_id", sa.Integer(), nullable=False),
        sa.Column("source_execution_id", sa.Integer(), nullable=False),
        sa.Column("source_node_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.Column("lineage_json", sa.Text(), nullable=False),
        sa.Column("hop", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_execution_id"], ["workflow_executions.id"]),
        sa.ForeignKeyConstraint(["source_workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("workflow_business_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflow_business_events_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_business_events_event_id"), ["event_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_workflow_business_events_event_name"), ["event_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_business_events_source_execution_id"), ["source_execution_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_business_events_source_workflow_id"), ["source_workflow_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_business_events_status"), ["status"], unique=False)

    op.create_table(
        "workflow_event_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_event_id", sa.Integer(), nullable=False),
        sa.Column("target_workflow_id", sa.Integer(), nullable=False),
        sa.Column("target_execution_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_event_id"], ["workflow_business_events.id"]),
        sa.ForeignKeyConstraint(["target_execution_id"], ["workflow_executions.id"]),
        sa.ForeignKeyConstraint(["target_workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_event_id", "target_workflow_id", name="uq_workflow_event_delivery_target"),
    )
    with op.batch_alter_table("workflow_event_deliveries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflow_event_deliveries_business_event_id"), ["business_event_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_event_deliveries_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_event_deliveries_target_execution_id"), ["target_execution_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflow_event_deliveries_target_workflow_id"), ["target_workflow_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflow_event_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_event_deliveries_target_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_event_deliveries_target_execution_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_event_deliveries_status"))
        batch_op.drop_index(batch_op.f("ix_workflow_event_deliveries_business_event_id"))
    op.drop_table("workflow_event_deliveries")
    with op.batch_alter_table("workflow_business_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_status"))
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_source_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_source_execution_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_event_name"))
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_event_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_business_events_created_at"))
    op.drop_table("workflow_business_events")
