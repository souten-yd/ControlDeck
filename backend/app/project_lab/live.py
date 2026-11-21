"""見張り役（ライブ確認）: run の systemd ユニットの中で command の代わりに動く。

    python -m app.project_lab.live --spec <json のファイル>

保存のたびに（debounce でまとめて）build → command を回す。子のプロセスは同じユニットの中に置く
（preview のポート確認 `web_preview_ready` がユニットのプロセスの木を見るため）。
状態は `<project>/.controldeck/live/<run_id>.jsonl` に 1 行ずつ追記する:
    {ts, phase: idle|building|running|exited|failed, changed: [最大 20 件の相対パス], exit_code, error_tail: 最後の 4KB}
子と build の出力はそのまま標準出力（= journal）にも流す。

mode
- restart: 変わったら子を止め、build してから command を起動し直す（Web サーバー）
- rerun: 変わったら子を止め、build してから command を最後まで流し直す（スクリプト・テスト・C++）
（native は ControlDeck 側が見張り役を起こさない）

このモジュールは ControlDeck 本体（DB・設定）を import しない。unit の中で軽く起動するため。
`.controldeck/`・`.git/`・`node_modules/`・`__pycache__/`・`build/` は設定にかかわらず見張らない
（状態ファイル自身の書き込みで回り続けないため）。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALWAYS_IGNORED = frozenset({".controldeck", ".git", "node_modules", "__pycache__", "build"})
MAX_CHANGED = 20
TAIL_BYTES = 4096
STOP_GRACE_SECONDS = 5.0
POLL_SECONDS = 0.1
# service.SENSITIVE_TEXT と同じ。状態ファイルは AI が読むので、秘密らしい値は伏せる。
_SENSITIVE = re.compile(
    r"(?im)(\b(?:authorization|password|passwd|token|secret|api[_-]?token|api[_-]?key)\b[ \t]*[:=][ \t]*)([^\s,;&]+)"
)


def glob_regex(pattern: str) -> re.Pattern[str]:
    """相対 glob を正規表現へ。`**` は深さをまたぎ、`*` `?` は `/` をまたがない。
    `/` を含まない pattern はどの深さにも当たる。ディレクトリに当たれば中のファイルにも当たる。"""
    text = pattern.replace("\\", "/")
    anywhere = "/" not in text.rstrip("/")
    if text.endswith("/**"):          # `docs/**` は `docs` 自身（ディレクトリの作成）にも当てる
        text = text[:-3]
    text = text.rstrip("/") or text
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if text.startswith("**", i):
            i += 2
            if text[i:i + 1] == "/":
                i += 1
                out.append("(?:.*/)?")
            else:
                out.append(".*")
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    body = "".join(out)
    if anywhere:
        body = "(?:.*/)?" + body
    return re.compile(f"^{body}(?:/.*)?$")


class Matcher:
    def __init__(self, root: Path, paths: list[str], ignore: list[str]) -> None:
        self.root = root
        self.paths = [glob_regex(item) for item in (paths or ["**/*"])]
        self.ignore = [glob_regex(item) for item in ignore]

    def relative(self, path: str) -> str | None:
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return None

    def accepts(self, path: str) -> bool:
        rel = self.relative(path)
        if rel is None or rel == ".":
            return False
        if ALWAYS_IGNORED.intersection(rel.split("/")):
            return False
        if any(item.match(rel) for item in self.ignore):
            return False
        return any(item.match(rel) for item in self.paths)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    sys.stdout.write(f"[live] {message}\n")
    sys.stdout.flush()


class _Running:
    """起こした 1 つの子（build か command）。出力を journal へ流しつつ、最後の 4KB を控える。"""

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        self.tail: collections.deque[bytes] = collections.deque()
        self._tail_size = 0
        self.proc = subprocess.Popen(  # noqa: S603 - argv 配列のみ。shell は使わない
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        out = sys.stdout.buffer
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            try:
                out.write(chunk)
                out.flush()
            except (OSError, ValueError):
                pass
            self.tail.append(chunk)
            self._tail_size += len(chunk)
            while self._tail_size - len(self.tail[0]) >= TAIL_BYTES:
                self._tail_size -= len(self.tail.popleft())

    def error_tail(self) -> str:
        self._reader.join(timeout=2)
        raw = b"".join(self.tail)[-TAIL_BYTES:]
        return _SENSITIVE.sub(r"\1***", raw.decode("utf-8", errors="replace"))

    def stop(self) -> None:
        """プロセスグループへ SIGTERM、待って SIGKILL。"""
        if self.proc.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                self.proc.wait()
        else:
            # 親が終わっても孫が残ることがある
            self._signal(signal.SIGKILL)
        self._reader.join(timeout=2)

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


class Supervisor:
    def __init__(self, spec: dict[str, Any], stop_event: threading.Event | None = None) -> None:
        self.spec = spec
        self.root = Path(spec["project"]).resolve()
        self.cwd = str(spec.get("cwd") or self.root)
        self.command: list[str] = list(spec["command"])
        self.build: list[str] = list(spec.get("build") or [])
        self.mode: str = spec.get("mode", "rerun")
        self.state_path = Path(spec["state_file"])
        self.debounce_ms = int(spec.get("debounce_ms", 400))
        self.matcher = Matcher(self.root, list(spec.get("paths") or []), list(spec.get("ignore") or []))
        self.stop_event = stop_event or threading.Event()
        self.events: queue.Queue[list[str]] = queue.Queue()
        self.exit_code = 0
        env = os.environ.copy()
        if "child_pythonpath" in spec:
            # 見張り役のために unit へ入れた PYTHONPATH を子へ渡さない（`app` の名前が衝突する）
            if spec["child_pythonpath"] is None:
                env.pop("PYTHONPATH", None)
            else:
                env["PYTHONPATH"] = str(spec["child_pythonpath"])
        self.env = env

    # 状態
    def emit(self, phase: str, *, changed: list[str] | None = None, exit_code: int | None = None,
             error_tail: str = "") -> None:
        record = {
            "ts": _now(), "phase": phase, "changed": (changed or [])[:MAX_CHANGED],
            "exit_code": exit_code, "error_tail": error_tail[-TAIL_BYTES:],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _log(f"{phase}" + (f" changed={','.join(record['changed'])}" if record["changed"] else "")
             + (f" exit={exit_code}" if exit_code is not None else ""))

    # 見張り
    def _watch_loop(self) -> None:
        import watchfiles

        try:
            for changes in watchfiles.watch(
                self.root, watch_filter=lambda _change, path: self.matcher.accepts(path),
                debounce=self.debounce_ms, step=50, stop_event=self.stop_event, rust_timeout=500,
                yield_on_timeout=False, raise_interrupt=False, ignore_permission_denied=True,
            ):
                rels = sorted({rel for _, path in changes if (rel := self.matcher.relative(path))})
                if rels:
                    self.events.put(rels)
        except Exception as exc:  # noqa: BLE001 - inotify の上限などで見張れない
            self.emit("failed", error_tail=f"ファイルの見張りを始められません: {type(exc).__name__}: {exc}")
            self.exit_code = 1
            self.stop_event.set()

    def _drain(self, first: list[str] | None = None) -> list[str]:
        merged = set(first or [])
        while True:
            try:
                merged.update(self.events.get_nowait())
            except queue.Empty:
                return sorted(merged)

    def _wait_change(self) -> list[str] | None:
        """次の変更まで待つ。止められたら None。"""
        while not self.stop_event.is_set():
            try:
                return self._drain(self.events.get(timeout=POLL_SECONDS))
            except queue.Empty:
                continue
        return None

    def _wait_child(self, child: _Running) -> tuple[str, Any]:
        while True:
            code = child.proc.poll()
            if code is not None:
                return "exit", code
            if self.stop_event.is_set():
                return "stop", None
            try:
                return "change", self._drain(self.events.get(timeout=POLL_SECONDS))
            except queue.Empty:
                continue

    def _start(self, argv: list[str]) -> _Running | str:
        try:
            return _Running(argv, self.cwd, self.env)
        except OSError as exc:
            return f"{argv[0]} を起動できません: {type(exc).__name__}: {exc}"

    def _cycle(self, changed: list[str]) -> list[str] | None:
        """1 回分（build → command）。変更で中断したらその変更を返す。止められたら None。
        build が失敗したとき・子が終わったときは、次の変更まで待ってから返す。"""
        if self.build:
            self.emit("building", changed=changed)
            builder = self._start(self.build)
            if isinstance(builder, str):
                self.emit("failed", changed=changed, error_tail=builder)
                return self._wait_change()
            kind, value = self._wait_child(builder)
            if kind != "exit":
                builder.stop()
                return value if kind == "change" else None
            if value != 0:
                self.emit("failed", changed=changed, exit_code=value, error_tail=builder.error_tail())
                return self._wait_change()
            builder.stop()  # 孫が残っていれば片付ける
        child = self._start(self.command)
        if isinstance(child, str):
            self.emit("failed", changed=changed, error_tail=child)
            return self._wait_change()
        self.emit("running", changed=changed)
        kind, value = self._wait_child(child)
        if kind == "exit":
            # どの変更の後の結果かを AI が読めるように、終わりの記録にも変わったファイルを残す
            self.emit("exited", changed=changed, exit_code=value, error_tail=child.error_tail() if value else "")
            return self._wait_change()
        child.stop()
        return value if kind == "change" else None

    def run(self) -> int:
        self.emit("idle")
        watcher = threading.Thread(target=self._watch_loop, daemon=True)
        watcher.start()
        changed: list[str] | None = []
        try:
            while changed is not None and not self.stop_event.is_set():
                changed = self._cycle(changed)
        finally:
            # 見張りの Rust 側が動いたまま終わると、終了時に落ちる（実測: segfault）。止めてから終わる
            self.stop_event.set()
            watcher.join(timeout=5)
        return self.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project Lab の見張り役")
    parser.add_argument("--spec", required=True, help="指示の JSON ファイル")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    supervisor = Supervisor(spec)
    # 親（シェルなど）が止めた信号を引き継いでいても、止める信号は受ける
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: supervisor.stop_event.set())
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
