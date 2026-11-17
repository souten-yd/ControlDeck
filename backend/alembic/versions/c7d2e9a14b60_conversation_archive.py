"""conversation archive flag

Revision ID: c7d2e9a14b60
Revises: a19b48d207f1
Create Date: 2026-11-17 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d2e9a14b60"
down_revision: Union[str, Sequence[str], None] = "a19b48d207f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AI アシスタントの会話のアーカイブ（一覧から外すが消さない）
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("archived_at")
