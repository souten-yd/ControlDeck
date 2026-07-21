"""`deck.sh database`から使う秘密値非表示のDB診断CLI。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.database.runtime import describe_database_url, normalized_database_url, read_database_env


def _candidate_url() -> str:
    raw = os.environ.get("CONTROL_DECK_CANDIDATE_DB_URL", "")
    if not raw:
        raise ValueError("PostgreSQL URLを入力してください")
    normalized = normalized_database_url(raw)
    if not normalized.startswith("postgresql+psycopg://"):
        raise ValueError("切替先はPostgreSQL URLで指定してください")
    return normalized


def _check_url(raw: str) -> None:
    url = normalized_database_url(raw)
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5}
                           if url.startswith("postgresql") else {})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    finally:
        engine.dispose()
    print(describe_database_url(url))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control-deck-database")
    sub = parser.add_subparsers(dest="command", required=True)
    env_parser = sub.add_parser("validate-env-file")
    env_parser.add_argument("path", type=Path)
    sub.add_parser("check-candidate")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-env-file":
            read_database_env(args.path)
        elif args.command == "check-candidate":
            _check_url(_candidate_url())
        else:
            from app.config import db_url

            _check_url(db_url())
    except Exception as exc:
        # URLやdriver例外はcredentialを含み得るため、詳細を標準出力／journalへ出さない。
        print(f"database check failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
