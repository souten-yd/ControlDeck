"""news channel (items, actions, full-text index)

Revision ID: a3c5e7f9b1d2
Revises: b5e1c7a93d20
Create Date: 2026-11-23 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, Sequence[str], None] = "b5e1c7a93d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topics", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    with op.batch_alter_table("news_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_news_items_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_news_items_source_key"), ["source_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_news_items_published_at"), ["published_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_news_items_user_id"), ["user_id"], unique=False)
    op.create_table(
        "news_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["news_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "item_id", "action", name="uq_news_action"),
    )
    with op.batch_alter_table("news_actions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_news_actions_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_news_actions_item_id"), ["item_id"], unique=False)
    # SQLite のときだけ全文検索（FTS5）。他の DB は LIKE で探す（app/news/store.py）
    from app.news.fts import create_fts

    create_fts(op.get_bind())


def downgrade() -> None:
    from app.news.fts import drop_fts

    drop_fts(op.get_bind())
    with op.batch_alter_table("news_actions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_news_actions_item_id"))
        batch_op.drop_index(batch_op.f("ix_news_actions_user_id"))
    op.drop_table("news_actions")
    with op.batch_alter_table("news_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_news_items_user_id"))
        batch_op.drop_index(batch_op.f("ix_news_items_published_at"))
        batch_op.drop_index(batch_op.f("ix_news_items_source_key"))
        batch_op.drop_index(batch_op.f("ix_news_items_kind"))
    op.drop_table("news_items")
