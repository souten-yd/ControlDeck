"""開発用の軽量サンドボックス（`./deck.sh sandbox ...`）。

AI（OpenCode）に ControlDeck や Add-on を開発させるときの作業場所。本番の ControlDeck は
そのままにし、ここで直し、ここのミラー（別ポートの ControlDeck）で確かめ、GitHub の
プレリリースまで出す。本番へは利用者が正式 Release に格上げし、更新機能で入れる。

イメージは作らない。podman の空のルートに、ホストの /usr・/etc・/opt と道具のフォルダを
読み取り専用でつなぐ（実測: 起動 0.07 秒、Chrome・node・python・gh・git はホストのものが
そのまま動く）。書けるのはサンドボックスのフォルダだけで、依存（.venv・node_modules）は
overlay でつなぐので、箱の中で pip / npm を使ってもホストは変わらない。本番の data と
gh の token は見えない（GitHub 連携を許したときだけ gh の設定を読み取り専用で見せる）。

作業ツリーは `git clone --shared`。オブジェクトは元のリポジトリを読み取り専用で参照し、
コミットやブランチは箱の中にだけ残る。push 先は元のリポジトリの origin（GitHub）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MIRROR_PORT_BASE = 18800
MIRROR_PORT_SPAN = 100
CONTAINER_PREFIX = "cd-sandbox-"


class SandboxError(RuntimeError):
    pass


def _data_dir() -> Path:
    from app.config import data_dir

    return data_dir()


def root() -> Path:
    path = (_data_dir() / "sandboxes").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dir(name: str) -> Path:
    if not NAME.fullmatch(name):
        raise SandboxError("名前は英小文字で始まる英小文字・数字・ハイフン（32 文字まで）にしてください")
    return root() / name


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise SandboxError(f"git {' '.join(args[:2])} に失敗しました: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


@dataclass
class Sandbox:
    name: str
    path: Path
    meta: dict

    @property
    def repo(self) -> Path:
        return self.path / "work" / "repo"

    @property
    def home(self) -> Path:
        return self.path / "home"

    @property
    def port(self) -> int:
        return int(self.meta["port"])


def _free_port() -> int:
    used = {int(_read_meta(p).get("port") or 0) for p in root().iterdir() if (p / "sandbox.json").is_file()}
    for port in range(MIRROR_PORT_BASE, MIRROR_PORT_BASE + MIRROR_PORT_SPAN):
        if port in used:
            continue
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SandboxError("ミラーに使えるポートがありません")


def _read_meta(path: Path) -> dict:
    try:
        return json.loads((path / "sandbox.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load(name: str) -> Sandbox:
    path = _dir(name)
    meta = _read_meta(path)
    if not meta:
        raise SandboxError(f"サンドボックス {name} はありません")
    return Sandbox(name, path, meta)


def create(name: str, repo: str, ref: str = "", *, github: bool = False) -> Sandbox:
    """作業ツリーを複製してサンドボックスを作る。数秒で終わる（オブジェクトは複製しない）。"""
    path = _dir(name)
    if path.exists():
        raise SandboxError(f"サンドボックス {name} は既にあります（作り直すなら destroy してから）")
    source = Path(repo).expanduser().resolve()
    top = Path(_git("rev-parse", "--show-toplevel", cwd=source))
    common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=top))
    revision = _git("rev-parse", ref or "HEAD", cwd=top)
    origin = ""
    try:
        origin = _git("remote", "get-url", "origin", cwd=top)
    except SandboxError:
        pass
    path.mkdir(parents=True)
    try:
        for sub in ("work", "home", "mirror"):
            (path / sub).mkdir()
        os.chmod(path, 0o700)
        work = path / "work" / "repo"
        _git("clone", "-q", "--shared", "--no-checkout", str(top), str(work))
        _git("checkout", "-q", "-B", f"sandbox/{name}", revision, cwd=work)
        if origin:
            _git("remote", "set-url", "origin", origin, cwd=work)
        meta = {
            "name": name, "source": str(top), "git_common_dir": str(common), "revision": revision,
            "origin": origin, "port": _free_port(), "github": bool(github), "created_at": time.time(),
            # 依存はホストのものを overlay でつなぐ（箱の中で書いてもホストは変わらない）。
            # .venv は中に絶対パスを持つので元と同じ場所へ、node_modules は複製の中の同じ位置へ。
            "overlays": [
                [str(source_path), str(target)]
                for source_path, target in (
                    (top / ".venv", top / ".venv"),
                    (top / "frontend" / "node_modules", work / "frontend" / "node_modules"),
                ) if source_path.exists()
            ],
        }
        (path / "sandbox.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return load(name)


def _tool_dirs() -> list[Path]:
    """箱の中から使う、ControlDeck が管理領域に入れた道具（読み取り専用）。"""
    features = _data_dir() / "features"
    names = ("opencode-v2", "playwright-mcp", "context7-mcp", "ast-grep")
    dirs = [features / n for n in names if (features / n).is_dir()]
    browsers = _data_dir() / "cache" / "ms-playwright"
    if browsers.is_dir():
        dirs.append(browsers)
    return dirs


def _rootfs() -> Path:
    """空のルート。/bin などは /usr への symlink（Ubuntu の merged-usr と同じ形）。"""
    base = root() / ".rootfs"
    if not (base / "usr").is_dir():
        for sub in ("usr", "etc", "opt", "proc", "sys", "dev", "tmp", "run", "var/tmp", "home", "data1tb"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        for link in ("bin", "lib", "lib64", "sbin"):
            target = base / link
            if not target.exists() and not target.is_symlink():
                target.symlink_to(f"usr/{link}")
    return base


BASE_READONLY = (Path("/usr"), Path("/etc"), Path("/opt"))


def tool_path() -> str:
    """箱の中の PATH。管理領域の道具（OpenCode・ast-grep など）を先に置く。"""
    return ":".join([*(str(p / "node_modules" / ".bin") for p in _tool_dirs() if (p / "node_modules" / ".bin").is_dir()),
                     "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin"])


def container_argv(command: list[str], *, workdir: Path, writable: list[Path], readonly: list[Path] = (),
                   overlays: list[tuple[Path, Path]] = (), env: dict[str, str] | None = None,
                   interactive: bool = False, name: str = "", detach: bool = False) -> list[str]:
    """イメージを使わない箱の podman 引数。書けるのは writable だけ、他はホストを読み取り専用で見せる。

    サンドボックス（./deck.sh sandbox）と OpenCode のプロジェクトの箱が共有する。
    """
    podman = shutil.which("podman")
    if not podman:
        raise SandboxError("podman が必要です")
    argv = [podman, "run", "--rm", "--userns=keep-id", "--network", "host", "--shm-size=1g",
            "--security-opt", "label=disable"]
    if interactive:
        argv += ["-it"]
    if detach:
        argv += ["-d"]
    if name:
        argv += ["--name", name]
    seen: set[str] = set()

    def mount(source: Path, target: Path, mode: str) -> None:
        key = str(target)
        if key in seen or not source.exists():
            return
        seen.add(key)
        argv.extend(["-v", f"{source}:{target}:{mode}"])

    for path in [*BASE_READONLY, *readonly, *_tool_dirs()]:
        mount(path, path, "ro")
    for source, target in overlays:
        mount(source, target, "O")
    for path in writable:
        mount(path, path, "rw")
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    argv += ["-w", str(workdir), "--rootfs", str(_rootfs()), *command]
    return argv


def podman_argv(box: Sandbox, command: list[str], *, interactive: bool = False, name: str = "",
                detach: bool = False, env: dict[str, str] | None = None) -> list[str]:
    readonly = [Path(box.meta["git_common_dir"])]
    overlays = []
    for entry in box.meta.get("overlays") or []:
        source_path, target = (entry, entry) if isinstance(entry, str) else entry
        overlays.append((Path(source_path), Path(target)))
    environment = {
        "HOME": str(box.home), "XDG_CONFIG_HOME": str(box.home / ".config"),
        "XDG_DATA_HOME": str(box.home / ".local/share"), "XDG_STATE_HOME": str(box.home / ".local/state"),
        "XDG_CACHE_HOME": str(box.home / ".cache"), "CONTROL_DECK_SANDBOX": box.name,
        "PLAYWRIGHT_BROWSERS_PATH": str(_data_dir() / "cache" / "ms-playwright"),
        "PATH": tool_path(),
        **(env or {}),
    }
    gh = Path.home() / ".config" / "gh"
    extra: list[str] = []
    if box.meta.get("github") and (gh / "hosts.yml").is_file():
        extra = ["-v", f"{gh}:/run/gh:ro"]
        environment["GH_CONFIG_DIR"] = "/run/gh"
    argv = container_argv(command, workdir=box.repo, writable=[box.path], readonly=readonly,
                          overlays=overlays, env=environment, interactive=interactive, name=name, detach=detach)
    if extra:
        index = argv.index("--rootfs")
        argv[index:index] = extra
    return argv


def mirror_config(box: Sandbox) -> Path:
    """ミラーの ControlDeck の設定。data はサンドボックスの中、ポートは固有。"""
    config = box.path / "mirror" / "config.yaml"
    config.write_text(
        f"server:\n  host: 127.0.0.1\n  port: {box.port}\n"
        f"data_dir: {box.path / 'mirror' / 'data'}\n"
        f"codedev_dir: {box.path / 'mirror' / 'CodeDEV'}\n"
        f"git_apps_dir: {box.path / 'mirror' / 'apps'}\n",
        encoding="utf-8",
    )
    return config


def mirror_container(box: Sandbox) -> str:
    return f"{CONTAINER_PREFIX}{box.name}-mirror"


def mirror_start(box: Sandbox) -> dict:
    """箱の中でミラーの ControlDeck を起こす。機械全体に効く処理は CONTROL_DECK_ISOLATED で止める。"""
    mirror_stop(box)
    config = mirror_config(box)
    venv_python = Path(box.meta["source"]) / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise SandboxError("元のリポジトリに .venv がありません（./deck.sh を一度実行してください）")
    env = {"CONTROL_DECK_CONFIG": str(config), "CONTROL_DECK_ISOLATED": "1"}
    script = f"cd {box.repo}/backend && exec {venv_python} -m app.server >> {box.path}/mirror/server.log 2>&1"
    argv = podman_argv(box, ["/bin/bash", "-c", script], name=mirror_container(box), detach=True, env=env)
    subprocess.run(argv, capture_output=True, text=True, timeout=60, check=True)
    for _ in range(60):
        if _health(box.port):
            break
        time.sleep(1)
    else:
        tail = (box.path / "mirror" / "server.log").read_text(encoding="utf-8", errors="replace")[-1500:]
        raise SandboxError(f"ミラーが起動しませんでした:\n{tail}")
    password_file = box.path / "mirror" / "admin-password"
    if not password_file.exists():
        password = secrets.token_urlsafe(18)
        create = podman_argv(box, ["/bin/bash", "-c",
                                   f"cd {box.repo}/backend && {venv_python} -m app.cli create-admin sandbox-admin --password-stdin"],
                             env=env)
        subprocess.run([*create[:2], "-i", *create[2:]], input=password + "\n", capture_output=True,
                       text=True, timeout=120, check=True)
        password_file.write_text(password, encoding="utf-8")
        os.chmod(password_file, 0o600)
    return {"url": f"http://127.0.0.1:{box.port}", "user": "sandbox-admin", "password_file": str(password_file)}


def mirror_stop(box: Sandbox) -> None:
    podman = shutil.which("podman") or "podman"
    subprocess.run([podman, "rm", "-f", mirror_container(box)], capture_output=True, timeout=60, check=False)


def _health(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def opencode_config(box: Sandbox, *, model: str = "auto") -> Path:
    """箱の中の OpenCode の設定を作る。本番と同じ生成処理で作り、箱から見えないパスだけ写し替える。

    Add-on の MCP bridge は載せない（本番の Add-on を箱から動かさない）。UI 検証の画面写真は
    箱の中へ撮る。指示ファイルとスキルは箱の中へ写す（本番の data は箱から見えない）。
    """
    from app.integrations.opencode import provider

    settings = provider.get_settings()
    base_url = provider.gateway_base_url() if settings.get("use_gateway", True) else str(settings["base_url"])
    payload = provider._config_payload(f"sandbox-{box.name}", base_url, model)
    servers = dict(payload.get("mcp") or {})
    servers.pop("controldeck_addons", None)
    if servers:
        payload["mcp"] = servers
    else:
        payload.pop("mcp", None)
    shots = box.path / "ui-verify"
    shots.mkdir(exist_ok=True)
    browser = (payload.get("mcp") or {}).get(provider.UI_VERIFY_SERVER)
    if browser:
        command = list(browser["command"])
        command[command.index("--output-dir") + 1] = str(shots)
        browser["command"] = command
    copied = box.home / ".controldeck"
    copied.mkdir(parents=True, exist_ok=True)
    instructions = []
    for item in payload.get("instructions") or []:
        source = Path(item)
        if source.is_file():
            shutil.copyfile(source, copied / source.name)
            instructions.append(str(copied / source.name))
    payload["instructions"] = instructions
    skills = []
    for item in (payload.get("skills") or {}).get("paths") or []:
        source = Path(item)
        if source.is_dir():
            target = copied / "skills" / source.name
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target, symlinks=False)
            skills.append(str(target))
    if skills:
        payload["skills"] = {"paths": skills}
    else:
        payload.pop("skills", None)
    config_dir = box.home / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "opencode.json"
    path.write_text(json.dumps(provider._v2_payload(payload), ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def opencode_argv(box: Sandbox, *, message: str = "", session: str = "", model: str = "auto",
                  interactive: bool = True) -> list[str]:
    from app.features import registry

    opencode_config(box, model=model)
    binary = registry._managed_executable("opencode-v2")
    if not binary.exists():
        raise SandboxError("OpenCode v2 が導入されていません")
    if interactive:
        command = [str(binary), "--standalone", *(["--prompt", message] if message else []), str(box.repo)]
    else:
        command = [str(binary), "run", message, "--format", "json", "--auto",
                   "--model", f"controldeck/{model}", "--standalone", *(["--session", session] if session else [])]
    return podman_argv(box, command, interactive=interactive)


def destroy(name: str) -> None:
    box = load(name)
    mirror_stop(box)
    target = box.path.resolve()
    if not target.is_relative_to(root()) or target == root():
        raise SandboxError("削除対象がサンドボックスの外です")
    shutil.rmtree(target)


def status(box: Sandbox) -> dict:
    branch = ""
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=box.repo)
        dirty = bool(_git("status", "--porcelain", cwd=box.repo))
    except SandboxError:
        dirty = False
    return {"name": box.name, "source": box.meta["source"], "branch": branch, "dirty": dirty,
            "mirror": f"http://127.0.0.1:{box.port}" if _health(box.port) else "停止中",
            "github": box.meta.get("github", False), "path": str(box.path)}


def list_all() -> list[dict]:
    return [status(load(p.name)) for p in sorted(root().iterdir())
            if p.is_dir() and NAME.fullmatch(p.name) and (p / "sandbox.json").is_file()]


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="deck.sh sandbox")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="サンドボックスを作る")
    new.add_argument("name")
    new.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    new.add_argument("--ref", default="")
    new.add_argument("--github", action="store_true", help="この PC の gh の設定を読み取り専用で見せる")
    for command in ("shell", "destroy", "status", "mirror-start", "mirror-stop"):
        sub.add_parser(command).add_argument("name")
    run = sub.add_parser("exec", help="箱の中でコマンドを実行する")
    run.add_argument("name")
    run.add_argument("cmd", nargs=argparse.REMAINDER)
    sub.add_parser("list")
    code = sub.add_parser("opencode", help="箱の中で OpenCode を開く（--run で 1 回だけ実行し JSON を出す）")
    code.add_argument("name")
    code.add_argument("--run", default="")
    code.add_argument("--session", default="")
    code.add_argument("--model", default="auto")
    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            box = create(args.name, args.repo, args.ref, github=args.github)
            print(json.dumps(status(box), ensure_ascii=False, indent=1))
        elif args.command == "list":
            print(json.dumps(list_all(), ensure_ascii=False, indent=1))
        elif args.command == "status":
            print(json.dumps(status(load(args.name)), ensure_ascii=False, indent=1))
        elif args.command == "destroy":
            destroy(args.name)
            print(f"サンドボックス {args.name} を削除しました")
        elif args.command == "mirror-start":
            print(json.dumps(mirror_start(load(args.name)), ensure_ascii=False, indent=1))
        elif args.command == "mirror-stop":
            mirror_stop(load(args.name))
        elif args.command == "shell":
            os.execv(shutil.which("podman") or "podman",
                     podman_argv(load(args.name), ["/bin/bash", "-l"], interactive=True))
        elif args.command == "opencode":
            box = load(args.name)
            argv = opencode_argv(box, message=args.run, session=args.session, model=args.model,
                                 interactive=not args.run)
            if args.run:
                return subprocess.run(argv, check=False).returncode
            os.execv(argv[0], argv)
        elif args.command == "exec":
            command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
            result = subprocess.run(podman_argv(load(args.name), command or ["true"]), check=False)
            return result.returncode
    except SandboxError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
