"""managed application system service scope

Revision ID: d91a2c7f4e80
Revises: c2f8a6d53b91
Create Date: 2026-07-21 12:45:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d91a2c7f4e80"
down_revision: Union[str, Sequence[str], None] = "c2f8a6d53b91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("managed_applications") as batch_op:
        batch_op.add_column(sa.Column("systemd_scope", sa.String(length=16), nullable=False, server_default="user"))
        batch_op.add_column(sa.Column("system_service_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("managed_applications") as batch_op:
        batch_op.drop_column("system_service_id")
        batch_op.drop_column("systemd_scope")
