from __future__ import annotations

from alembic import context

from app.database import Base, engine
import app.models  # noqa: F401  全modelをmetadataへ登録する

config = context.config
# Alembic標準env.pyのfileConfigはアプリ起動中のroot level/handlerを書き換える。
# migrationはFastAPI lifespan内でも動くため、既存logging設定をそのまま維持する。

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=engine.url.get_backend_name() == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
