"""drop legacy application builder tables

C#／ASP.NET生成系（Application Builder）の削除に伴い、残っていた3テーブルを落とす。
application_projects.workflow_id が workflows.id を参照しているため、行が残っていると
そのWorkflowを削除できない（SQLiteのFOREIGN KEY constraint failed）。

Revision ID: b41f7d90c655
Revises: a73d9e4c2b18
Create Date: 2026-08-13 07:40:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b41f7d90c655"
down_revision: Union[str, Sequence[str], None] = "a73d9e4c2b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 参照している側から順に落とす。
LEGACY_TABLES = ("application_build_artifacts", "application_builds", "application_projects")


def upgrade() -> None:
    # offline（--sql）ではDBを覗けないため、baselineに含まれる前提でそのまま落とす。
    # as_sql は --sql（offline）生成時にTrue。実DBを覗けないので存在確認は省く。
    existing = None if op.get_context().as_sql else set(sa.inspect(op.get_bind()).get_table_names())
    for table in LEGACY_TABLES:
        if existing is None or table in existing:
            op.drop_table(table)


def downgrade() -> None:
    # 生成系ごと削除済みのため、schemaだけ戻しても意味がない。
    raise NotImplementedError("旧Application Builderのテーブルは復元しません")
