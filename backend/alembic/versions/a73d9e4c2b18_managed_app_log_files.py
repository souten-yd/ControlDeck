"""managed application log files

Revision ID: a73d9e4c2b18
Revises: f6b2c8d41a70
Create Date: 2026-07-21 17:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a73d9e4c2b18"
down_revision: Union[str, Sequence[str], None] = "f6b2c8d41a70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("managed_applications") as batch_op:
        batch_op.add_column(sa.Column("log_files_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("managed_applications") as batch_op:
        batch_op.drop_column("log_files_json")
