"""PostgreSQL backup/restoreをcredential非表示の固定argvで実行する。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import db_url
from app.database.runtime import validate_database_url

_QUERY_ENV = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def _connection_env() -> tuple[dict[str, str], str]:
    url = validate_database_url(db_url())
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("PostgreSQL設定時だけ使用できます")
    env = os.environ.copy()
    values = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
    }
    for key, value in values.items():
        if value is not None:
            env[key] = value
        else:
            env.pop(key, None)
    for key, env_key in _QUERY_ENV.items():
        value = url.query.get(key)
        if value is not None:
            env[env_key] = str(value)
        else:
            env.pop(env_key, None)
    return env, str(url.database)


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"{name}が見つかりません")
    return str(Path(executable).resolve(strict=True))


def dump(output: Path) -> None:
    target = output.expanduser().resolve(strict=False)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(target, flags, 0o600)
    try:
        env, database = _connection_env()
        result = subprocess.run(
            [_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--no-password",
             f"--file=/proc/self/fd/{fd}", database],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(fd,),
            timeout=3600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("pg_dumpに失敗しました")
        os.fsync(fd)
    except Exception:
        os.close(fd)
        target.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)


def restore(source: Path) -> None:
    candidate = source.expanduser()
    path = candidate.parent.resolve(strict=True) / candidate.name
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not Path(f"/proc/self/fd/{fd}").is_file():
            raise RuntimeError("PostgreSQL dumpが通常fileではありません")
        env, database = _connection_env()
        result = subprocess.run(
            [_tool("pg_restore"), "--clean", "--if-exists", "--exit-on-error", "--no-owner",
             "--no-privileges", "--no-password", f"--dbname={database}", f"/proc/self/fd/{fd}"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(fd,),
            timeout=3600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("pg_restoreに失敗しました")
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 2 or args[0] not in {"dump", "restore"}:
        print("usage: pg_tools <dump|restore> <path>", file=sys.stderr)
        return 2
    try:
        (dump if args[0] == "dump" else restore)(Path(args[1]))
    except Exception as exc:
        print(f"PostgreSQL {args[0]} failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
