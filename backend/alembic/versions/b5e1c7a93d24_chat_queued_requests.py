"""chat queued requests (送信待ちの要望)

Revision ID: b5e1c7a93d24
Revises: c7d2e9a14b60
Create Date: 2026-11-18 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e1c7a93d24"
down_revision: Union[str, Sequence[str], None] = "c7d2e9a14b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_queued_requests",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("conversation_id", sa.String(length=32), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("message_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_queued_requests_conversation_id", "chat_queued_requests", ["conversation_id"])
    op.create_index("ix_chat_queued_requests_status", "chat_queued_requests", ["status"])
    op.create_index("ix_chat_queued_requests_created_at", "chat_queued_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_queued_requests_created_at", table_name="chat_queued_requests")
    op.drop_index("ix_chat_queued_requests_status", table_name="chat_queued_requests")
    op.drop_index("ix_chat_queued_requests_conversation_id", table_name="chat_queued_requests")
    op.drop_table("chat_queued_requests")
