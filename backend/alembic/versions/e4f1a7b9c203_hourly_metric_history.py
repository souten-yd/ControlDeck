"""hourly metric history

Revision ID: e4f1a7b9c203
Revises: d91a2c7f4e80
Create Date: 2026-07-21 16:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f1a7b9c203"
down_revision: Union[str, Sequence[str], None] = "d91a2c7f4e80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metrics_hour",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minute_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("memory_percent", sa.Float(), nullable=True),
        sa.Column("gpu_percent", sa.Float(), nullable=True),
        sa.Column("vram_percent", sa.Float(), nullable=True),
        sa.Column("disk_read_bps", sa.Float(), nullable=True),
        sa.Column("disk_write_bps", sa.Float(), nullable=True),
        sa.Column("net_rx_bps", sa.Float(), nullable=True),
        sa.Column("net_tx_bps", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("metrics_hour") as batch_op:
        batch_op.create_index(batch_op.f("ix_metrics_hour_timestamp"), ["timestamp"], unique=True)

    fields = (
        "cpu_percent", "memory_percent", "gpu_percent", "vram_percent",
        "disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps",
    )
    averages = ", ".join(f"AVG({field})" for field in fields)
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        hour_expression = "date_trunc('hour', timestamp)"
    else:
        # Control Deckで許可するもう一方のDBはSQLite。既存30日分も失わずhourへ移す。
        hour_expression = "strftime('%Y-%m-%d %H:00:00.000000', timestamp)"
    op.execute(
        sa.text(
            "INSERT INTO metrics_hour "
            f"(timestamp, minute_count, {', '.join(fields)}) "
            f"SELECT {hour_expression}, COUNT(*), {averages} "
            "FROM metrics_minute "
            f"GROUP BY {hour_expression}"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("metrics_hour") as batch_op:
        batch_op.drop_index(batch_op.f("ix_metrics_hour_timestamp"))
    op.drop_table("metrics_hour")
