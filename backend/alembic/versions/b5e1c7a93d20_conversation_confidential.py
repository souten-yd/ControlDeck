"""conversation confidential flag (one-way)

Revision ID: b5e1c7a93d20
Revises: d3a8f1c5e7b2
Create Date: 2026-11-21 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e1c7a93d20"
down_revision: Union[str, Sequence[str], None] = "d3a8f1c5e7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 「この会話は秘密」を付けた時刻。None でなければ秘密の会話（外部への調査・外部の LLM を使わない。解除できない）
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("confidential_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("confidential_at")
