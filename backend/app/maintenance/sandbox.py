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

import hashlib
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
GATEWAY_PORT_BASE = 18900
GATEWAY_PORT_SPAN = 100
# 箱の中の OpenCode の会話サーバー（`opencode serve`）を ControlDeck へ公開するポート（pod の -p）
SERVE_PORT_BASE = 19000
SERVE_PORT_SPAN = 100
# 開発役のモデルの順番（使い切ったら次へ。利用者 2026-11-21「GLM を使い切り、無料で高性能なものを順次」）。
# 最後はローカル LLM（ゲートウェイの中継経由）。Zen のキーが無い・箱で allow_zen が off なら opencode* は飛ばす。
NO_MODEL_MESSAGE = "使えるモデルがありません（この作業場はローカル LLM を借りない設定で、外部のモデルのキーもありません）"
DEFAULT_MODEL_CHAIN = ["opencode-go/glm-5.3", "opencode/hy4-flash-free", "opencode/deepseek-v4.1-flash-free",
                       "controldeck/auto"]
LOCAL_MODEL = "controldeck/auto"
# 作業場の機能（2026-11-22）。meta の capabilities（文字列の集合）で持ち、中継・設定・mirror-start が強制する。
#   llm: ローカル LLM を借りる / tools: ControlDeck の道具（MCP）/ mirror: ControlDeck の複製 / gpu: GPU を借りる（P4 未実装）
CAPABILITIES = ("llm", "tools", "mirror", "gpu")
ADOPT_MODES = ("pr", "branch")
TARGETS = ("app", "llm_app", "controldeck")
# 使用量の上限・残高切れ・レート制限の印。語の区切りで見る（"rate" だけだと generate・accurate に当たり、
# ふつうのエラーで誤って次のモデルへ移る）
LIMIT_PATTERN = re.compile(
    r"\b(rate[ _-]?limit(ed)?|too many requests|quota|usage limit|limit (reached|exceeded)|exceeded|insufficient"
    r"|billing|credits?|payment required|429|402)\b", re.I)


class SandboxError(RuntimeError):
    pass


def _users_with_permission(permission: str, *, name: str = "") -> list[str]:
    """有効で、その権限を持つ利用者名（name を渡したらその人だけ）。DB を読む。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User
    from app.security.deps import user_permissions

    with SessionLocal() as db:
        query = select(User).where(User.is_active.is_(True))
        if name:
            query = query.where(User.username == name)
        return sorted(u.username for u in db.execute(query).scalars().all()
                      if permission in user_permissions(u))


def resolve_owner(requested: str = "") -> str:
    """箱の持ち主（MCP のトークンが動く利用者）。指定が無ければ有効な管理者が 1 人のときだけその人。

    持ち主は箱の AI が ControlDeck の道具（Project Lab の実行など）を呼ぶときの権限になる。
    """
    if requested:
        if not _users_with_permission("workflows.run", name=requested):
            raise SandboxError(f"持ち主 {requested} は、有効で workflows.run を持つ利用者ではありません")
        return requested
    admins = _users_with_permission("settings.manage")
    if len(admins) == 1:
        return admins[0]
    if not admins:
        raise SandboxError("有効な管理者がいません。--owner <ユーザー名> で持ち主を指定してください")
    raise SandboxError(f"管理者が複数います（{', '.join(admins)}）。--owner <ユーザー名> で持ち主を指定してください")


def _owner_user_id(username: str) -> int:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username, User.is_active.is_(True))).scalar_one_or_none()
        if user is None:
            raise SandboxError(f"持ち主 {username} が見つかりません（無効になっていないか確かめてください）")
        return int(user.id)


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

    @property
    def gateway_port(self) -> int:
        return int(self.meta["gateway_port"])

    @property
    def serve_port(self) -> int:
        return int(self.meta["serve_port"])

    @property
    def owner(self) -> str:
        return str(self.meta.get("owner") or "")

    @property
    def allow_load(self) -> bool:
        return bool(self.meta.get("allow_load", False))

    @property
    def allow_zen(self) -> bool:
        return bool(self.meta.get("allow_zen", True))

    @property
    def capabilities(self) -> frozenset[str]:
        """この箱の機能。meta に無い古い箱は、元が ControlDeck 本体なら全部、それ以外は llm・tools（今までどおり）。"""
        saved = self.meta.get("capabilities")
        if isinstance(saved, list):
            return frozenset(str(x) for x in saved if x in CAPABILITIES)
        return LEGACY_CONTROLDECK_CAPS if self.is_controldeck_source else LEGACY_APP_CAPS

    @property
    def is_controldeck_source(self) -> bool:
        source = str(self.meta.get("workspace_source") or "")
        if source:
            return source == "controldeck"
        try:
            return Path(str(self.meta.get("source") or "")).resolve() == Path(__file__).resolve().parents[3]
        except OSError:
            return False

    @property
    def target(self) -> str:
        saved = self.meta.get("target")
        if saved in TARGETS:
            return str(saved)
        return "controldeck" if self.is_controldeck_source else "llm_app"

    @property
    def adopt_mode(self) -> str:
        saved = self.meta.get("adopt")
        if saved in ADOPT_MODES:
            return str(saved)
        return "pr" if self.is_controldeck_source else "branch"


LEGACY_CONTROLDECK_CAPS = frozenset({"llm", "tools", "mirror"})
LEGACY_APP_CAPS = frozenset({"llm", "tools"})


def _pick_port(key: str, base: int, span: int, what: str) -> int:
    used = set()
    for entry in root().iterdir():
        if NAME.fullmatch(entry.name) and has_meta(entry):
            try:
                used.add(int((_read_meta(entry) or _raw_legacy_meta(entry)).get(key) or 0))
            except (TypeError, ValueError):
                continue
    for port in range(base, base + span):
        if port in used:
            continue
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SandboxError(f"{what}に使えるポートがありません")


def _free_port() -> int:
    return _pick_port("port", MIRROR_PORT_BASE, MIRROR_PORT_SPAN, "ミラー")


def _free_gateway_port() -> int:
    return _pick_port("gateway_port", GATEWAY_PORT_BASE, GATEWAY_PORT_SPAN, "ゲートウェイの中継")


def _free_serve_port() -> int:
    return _pick_port("serve_port", SERVE_PORT_BASE, SERVE_PORT_SPAN, "会話サーバー")


# ---- meta（箱の設定）----
#
# 箱の pod は box.path を書ける。meta（source・git_common_dir・overlays・ポート・owner・origin）をそこへ置くと、箱の中の
# AI が書き換えて、次にホストが作る podman の引数（overlays のマウント）で ~/.ssh などを見せられる（2026-11-22 監査）。
# meta は箱の外 `<root>/.meta/<名前>.json`（ディレクトリ 0700・ファイル 0600。.relay と同じ考え）へ置く。古い箱
# （box.path/sandbox.json）は load のときに一度だけ、検査を通した分だけ移す。load のたびに検査もする。

META_DIRNAME = ".meta"
TAMPERED = "この箱の設定は改ざんの恐れがあるので使えません（作り直してください）"
# 古い箱から移すときに信用して引き継ぐ項目（これ以外、関門の結果・取り込みの記録・調べた状態などは捨てる）
_MIGRATE_KEYS = ("name", "source", "git_common_dir", "revision", "origin", "port", "gateway_port", "serve_port",
                 "allow_load", "allow_zen", "owner", "github", "created_at", "overlays", "workspace_source",
                 "last_model", "last_used", "model_chain", "capabilities", "target", "adopt")


def meta_dir() -> Path:
    path = root() / META_DIRNAME
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _meta_path(name: str) -> Path:
    return meta_dir() / f"{name}.json"


def has_meta(path: Path) -> bool:
    """箱のフォルダに設定があるか（新しい場所、または移す前の古い場所）。"""
    return (root() / META_DIRNAME / f"{path.name}.json").is_file() or (path / "sandbox.json").is_file()


def _raw_legacy_meta(path: Path) -> dict:
    try:
        data = json.loads((path / "sandbox.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_meta(path: Path) -> dict:
    """新しい場所の設定だけを読む（古い場所は load が検査して移す）。"""
    try:
        data = json.loads(_meta_path(path.name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_meta(path: Path, meta: dict) -> None:
    target = _meta_path(path.name)
    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(meta, ensure_ascii=False, indent=1))
    os.replace(tmp, target)


def overlay_pairs(top: Path, work: Path) -> list[list[str]]:
    """箱へ見せる依存（読み取り専用）の、許す組の全部。作る時も読む時もこの規則だけを使う。
    .venv は中に絶対パスを持つので元と同じ場所へ、node_modules は複製の中の同じ位置へ。"""
    return [[str(top / ".venv"), str(top / ".venv")],
            [str(top / "frontend" / "node_modules"), str(work / "frontend" / "node_modules")]]


def _tampered(reason: str) -> SandboxError:
    return SandboxError(f"{TAMPERED}（{reason}）")


def _validated(name: str, path: Path, meta: dict, *, strict: bool = True) -> dict:
    """設定を検査して、許す形だけにした写しを返す。通らなければ「改ざんの恐れ」で断る。strict=False は消す用（元のリポジトリは見ない）。"""
    meta = dict(meta)
    if meta.get("name") != name:
        raise _tampered("名前が箱と違います")
    for key, base, span in (("port", MIRROR_PORT_BASE, MIRROR_PORT_SPAN), ("gateway_port", GATEWAY_PORT_BASE, GATEWAY_PORT_SPAN),
                            ("serve_port", SERVE_PORT_BASE, SERVE_PORT_SPAN)):
        value = meta.get(key)
        if value in (None, 0) and key != "port":
            meta.pop(key, None)       # 古い箱。load が割り当てる
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not base <= value < base + span:
            raise _tampered(f"{key} が範囲外です")
    for key in ("owner", "origin", "revision", "workspace_source", "last_model"):
        if key in meta and not isinstance(meta[key], str):
            raise _tampered(f"{key} が文字列ではありません")
    if "capabilities" in meta and not (isinstance(meta["capabilities"], list)
                                       and all(x in CAPABILITIES for x in meta["capabilities"])):
        raise _tampered("capabilities が正しくありません")
    if "target" in meta and meta["target"] not in TARGETS:
        raise _tampered("target が正しくありません")
    if "adopt" in meta and meta["adopt"] not in ADOPT_MODES:
        raise _tampered("adopt が正しくありません")
    if "model_chain" in meta and not (isinstance(meta["model_chain"], list) and all(isinstance(x, str) for x in meta["model_chain"])):
        raise _tampered("model_chain が正しくありません")
    source = Path(str(meta.get("source") or "")).expanduser()
    if not strict:
        meta["overlays"] = []
        return meta
    try:
        resolved = source.resolve()
    except OSError as exc:
        raise _tampered("source を解決できません") from exc
    if not meta.get("source") or resolved.is_relative_to(root()):
        raise _tampered("source が箱の中を指しています")
    try:
        top = Path(_git("rev-parse", "--show-toplevel", cwd=resolved)).resolve()
        common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=top)).resolve()
    except (SandboxError, OSError) as exc:
        raise _tampered("元のリポジトリを確かめられません") from exc
    if top != resolved or Path(str(meta.get("git_common_dir") or "")).resolve() != common:
        raise _tampered("git_common_dir が元のリポジトリと違います")
    allowed = overlay_pairs(top, path / "work" / "repo")
    kept = []
    for item in meta.get("overlays") if isinstance(meta.get("overlays"), list) else []:
        if isinstance(item, str):
            pair = [item, item]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pair = [str(item[0]), str(item[1])]
        else:
            continue
        if pair in allowed and pair not in kept:     # 許す 2 種類（.venv・node_modules）以外の組は捨てる
            kept.append(pair)
    meta["overlays"] = kept
    return meta


def _migrate_legacy(name: str, path: Path) -> dict:
    """古い場所（box.path/sandbox.json）の設定を、検査を通った分だけ新しい場所へ移す。中身は信用しない。"""
    raw = _raw_legacy_meta(path)
    meta = _validated(name, path, {k: raw[k] for k in _MIGRATE_KEYS if k in raw})
    _save_meta(path, meta)
    (path / "sandbox.json").unlink(missing_ok=True)
    return meta


def load(name: str, *, verify: bool = True) -> Sandbox:
    path = _dir(name)
    meta = _read_meta(path)
    if meta:
        (path / "sandbox.json").unlink(missing_ok=True)      # 箱の中から置かれた古い形のファイルは使わない
        meta = _validated(name, path, meta, strict=verify)
    elif (path / "sandbox.json").is_file():
        meta = _migrate_legacy(name, path)
    else:
        raise SandboxError(f"サンドボックス {name} はありません")
    if not meta.get("gateway_port"):
        # 中継の導入前に作った箱。割り当てて保存する（以後は同じポートを使う）。
        meta["gateway_port"] = _free_gateway_port()
        _save_meta(path, meta)
    if not meta.get("serve_port"):
        # 会話サーバーの導入前に作った箱。割り当てて保存する（pod の公開ポートが増えるので、
        # pod は次の起動で作り直される。動いているコンテナがあるときは ensure_pod が作り直さない）。
        meta["serve_port"] = _free_serve_port()
        _save_meta(path, meta)
    return Sandbox(name, path, meta)


def box_owner(box: Sandbox) -> str:
    """持ち主。古い箱（owner 無し）は resolve_owner の規則で決めて保存する。"""
    if not box.owner:
        box.meta["owner"] = resolve_owner()
        _save_meta(box.path, box.meta)
    return box.owner


def create(name: str, repo: str, ref: str = "", *, github: bool = False, allow_load: bool = False,
           allow_zen: bool = True, owner: str = "", capabilities: list[str] | None = None,
           target: str = "", adopt: str = "") -> Sandbox:
    """作業ツリーを複製してサンドボックスを作る。数秒で終わる（オブジェクトは複製しない）。"""
    path = _dir(name)
    if path.exists():
        raise SandboxError(f"サンドボックス {name} は既にあります（作り直すなら destroy してから）")
    owner = resolve_owner(owner)
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
        _set_git_identity(work, name)
        if origin:
            _git("remote", "set-url", "origin", origin, cwd=work)
        meta = {
            "name": name, "source": str(top), "git_common_dir": str(common), "revision": revision,
            "origin": origin, "port": _free_port(), "gateway_port": _free_gateway_port(),
            "serve_port": _free_serve_port(),
            "allow_load": bool(allow_load), "allow_zen": bool(allow_zen), "owner": owner, "github": bool(github), "created_at": time.time(),
            # 依存はホストのものを overlay でつなぐ（箱の中で書いてもホストは変わらない）。
            # .venv は中に絶対パスを持つので元と同じ場所へ、node_modules は複製の中の同じ位置へ。
            "overlays": [pair for pair in overlay_pairs(top, work) if Path(pair[0]).exists()],
        }
        if capabilities is not None:      # 無ければ古い箱と同じ扱い（Sandbox.capabilities が元から決める）
            meta.update(capabilities=sorted(set(capabilities)), target=target, adopt=adopt)
        _save_meta(path, meta)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        _meta_path(name).unlink(missing_ok=True)
        raise
    return load(name)


def _tool_dirs() -> list[Path]:
    """箱の中から使う、ControlDeck が管理領域に入れた道具（読み取り専用）。"""
    features = _data_dir() / "features"
    # プラグインも依存を含む feature 単位で見せる。設定の file:// はホストと同じ
    # 絶対パスなので、これがないと npm 導入済みでも箱内では存在しない。
    names = ("opencode-v2", "opencode-superpowers", "opencode-adversarial-review",
             "opencode-auto-advisor", "playwright-mcp", "context7-mcp", "ast-grep")
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
                   interactive: bool = False, name: str = "", detach: bool = False,
                   env_file: Path | None = None, binds: list[tuple[Path, Path]] = (),
                   hide: list[Path] = (), pod: str = "") -> list[str]:
    """イメージを使わない箱の podman 引数。書けるのは writable だけ、他はホストを読み取り専用で見せる。

    サンドボックス（./deck.sh sandbox）と OpenCode のプロジェクトの箱が共有する。
    pod を指定すると、その pod の中で動く（userns・network は pod 側で決まる）。未指定は従来どおり host network。
    """
    podman = shutil.which("podman")
    if not podman:
        raise SandboxError("podman が必要です")
    isolation = ["--pod", pod] if pod else ["--userns=keep-id", "--network", "host"]
    argv = [podman, "run", "--rm", *isolation, "--shm-size=1g", "--security-opt", "label=disable"]
    if interactive:
        argv += ["-it"]
    if detach:
        argv += ["-d"]
    if name:
        argv += ["--name", name]
    seen: set[str] = set()
    # 空の場所で覆う（--rootfs は全部の箱で共有なので、前に開いた別のプロジェクトのつなぎ口が空フォルダで残る。
    # 2026-11-18: conv の箱から空で書けない CodeDEV/Conv が見え、OpenCode が取り違えた）。その下につなぐものは見える
    for path in hide:
        argv += ["--tmpfs", f"{path}:rw,mode=755"]

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
    # 別の場所へつなぐ書き込み可（箱ごとの /tmp など）。--rootfs の /tmp は全部の箱で共有なので、上書きする
    for source, target in binds:
        mount(source, target, "rw")
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    if env_file is not None:
        # 秘密（パスワード）は argv に載せない（ps や systemctl show から見える）。
        argv += ["--env-file", str(env_file)]
    argv += ["-w", str(workdir), "--rootfs", str(_rootfs()), *command]
    return argv


def pod_name(box: Sandbox) -> str:
    return f"{CONTAINER_PREFIX}{box.name}"


def _pod_spec(box: Sandbox) -> str:
    spec = json.dumps({"gateway_port": box.gateway_port, "ports": [box.port, box.serve_port]}, sort_keys=True)
    return hashlib.sha256(spec.encode()).hexdigest()[:16]


def _pod_running_containers(name: str) -> bool:
    """pod の中に動いているコンテナがあるか（infra は数えない）。調べられなければ「ある」とみなす（安全側）。"""
    podman = shutil.which("podman") or "podman"
    result = subprocess.run([podman, "ps", "-q", "--filter", f"pod={name}"],
                            capture_output=True, text=True, timeout=30, check=False)
    return result.returncode != 0 or bool(result.stdout.strip())


def ensure_pod(box: Sandbox, *, need_serve_port: bool = False) -> str:
    """箱ごとの pod。pod の中は互いに 127.0.0.1 で届き、ホストへは中継のポート（-T）だけ届く。

    設定（中継のポートと公開ポート）のハッシュを label に持ち、同じなら使い回し、違えば作り直す。
    ただし動いているコンテナがあるときは作り直さない（ミラーや作業中の OpenCode を落とさない）。
    今の pod を使う。会話サーバーのように新しい公開ポートが要る呼び出し（need_serve_port）だけは、
    その場合エラーにする（先に箱の中の処理を止めてもらう）。
    """
    podman = shutil.which("podman") or "podman"
    name, spec = pod_name(box), _pod_spec(box)
    found = subprocess.run([podman, "pod", "inspect", "--format", '{{ index .Labels "controldeck.spec" }}', name],
                           capture_output=True, text=True, timeout=30, check=False)
    if found.returncode == 0 and found.stdout.strip() == spec:
        return name
    if found.returncode == 0:
        if _pod_running_containers(name):
            if need_serve_port:
                raise SandboxError(
                    f"作業場 {box.name} は古い形の pod で動いています（動いているコンテナがあるので作り直しません）。"
                    "ミラーなど箱の中の処理を止めてから、もう一度開いてください")
            return name
        subprocess.run([podman, "pod", "rm", "-f", name], capture_output=True, text=True, timeout=60, check=False)
    made = subprocess.run(
        [podman, "pod", "create", "--name", name, "--userns=keep-id",
         "--network", f"pasta:-T,{box.gateway_port}", "-p", f"127.0.0.1:{box.port}:{box.port}",
         "-p", f"127.0.0.1:{box.serve_port}:{box.serve_port}",
         "--label", f"controldeck.spec={spec}"],
        capture_output=True, text=True, timeout=60, check=False)
    if made.returncode != 0:
        raise SandboxError(f"pod を作れませんでした: {made.stderr.strip()[:300]}")
    return name


def pod_remove(box: Sandbox) -> None:
    podman = shutil.which("podman") or "podman"
    subprocess.run([podman, "pod", "rm", "-f", pod_name(box)], capture_output=True, timeout=60, check=False)


def gateway_unit(box: Sandbox) -> str:
    return f"{CONTAINER_PREFIX}{box.name}-gateway"


def _gateway_healthy(port: int, *, llm: bool = True, tools: bool = True) -> bool:
    """中継が動いていて、箱の機能（llm・tools）と同じ設定で動いているか。違えば作り直す（機能を変えた・古い中継）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            data = json.loads(response.read())
            if response.status != 200:
                return False
            # MCP のトークンを持たない古い中継は動いていても作り直す（箱から道具が呼べない）。
            # llm・tools の印が無い古い中継は「全部許す」中継なので、機能が足りない箱では作り直す
            return (bool(data.get("llm", True)) == llm and bool(data.get("tools", bool(data.get("mcp")))) == tools
                    and (bool(data.get("mcp")) or not tools))
    except Exception:  # noqa: BLE001
        return False


def _upstream_url() -> str:
    from app.config import get_config

    return f"http://127.0.0.1:{int(get_config().server.port)}"


def relay_env_file(box: Sandbox) -> Path:
    """中継へ MCP のトークンを渡す環境変数ファイル。箱のフォルダの外（箱からは見えない）。"""
    return root() / ".relay" / f"{box.name}.env"


def _write_relay_env(box: Sandbox) -> Path:
    """箱の持ち主として動く MCP のトークンを作り、0600 のファイルへ書く（argv・ログには出さない）。"""
    from app.addons.agent_mcp import issue_opencode_token
    from app.maintenance.sandbox_gateway import MCP_TOKEN_ENV

    token = issue_opencode_token(_owner_user_id(box_owner(box)), f"sandbox-{box.name}")
    path = relay_env_file(box)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{MCP_TOKEN_ENV}={token}\n")
    os.chmod(path, 0o600)
    return path


def _relay_token(box: Sandbox) -> str:
    try:
        _, _, value = relay_env_file(box).read_text(encoding="utf-8").strip().partition("=")
        return value
    except OSError:
        return ""


def gateway_stop(box: Sandbox) -> None:
    systemctl = shutil.which("systemctl") or "systemctl"
    unit = f"{gateway_unit(box)}.service"
    subprocess.run([systemctl, "--user", "stop", unit], capture_output=True, timeout=30, check=False)
    subprocess.run([systemctl, "--user", "reset-failed", unit], capture_output=True, timeout=30, check=False)


def ensure_gateway(box: Sandbox) -> None:
    """箱の外（ホスト）で LLM ゲートウェイの中継を動かす。動いていて health が通れば使い回す。"""
    llm, tools = "llm" in box.capabilities, "tools" in box.capabilities
    if _gateway_healthy(box.gateway_port, llm=llm, tools=tools):
        return
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        raise SandboxError("systemd-run が必要です（ゲートウェイの中継を起こせません）")
    venv_python = Path(box.meta["source"]) / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise SandboxError("元のリポジトリに .venv がありません（./deck.sh を一度実行してください）")
    gateway_stop(box)
    env_file = _write_relay_env(box)
    backend = Path(__file__).resolve().parents[2]
    command = [str(venv_python), "-m", "app.maintenance.sandbox_gateway", "--name", box.name,
               "--port", str(box.gateway_port), "--upstream", _upstream_url()]
    # 機能の強制は中継で行う（画面で隠すだけにしない）。付けない機能のパスは 403
    if llm:
        command.append("--allow-llm")
    if tools:
        command.append("--allow-tools")
    if box.allow_load and llm:
        command.append("--allow-load")
    if box.meta.get("allow_cloud"):
        # 既定は外部のモデルを断る（箱の OpenCode はローカル LLM だけ。2026-11-21 利用者）
        command.append("--allow-cloud")
    argv = [systemd_run, "--user", "--quiet", f"--unit={gateway_unit(box)}",
            "--property=Type=exec", f"--property=WorkingDirectory={backend}",
            "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=read-only", "--property=MemoryMax=512M", "--property=TasksMax=64",
            "--property=UMask=0077", f"--property=EnvironmentFile={env_file}",
            "--setenv=PYTHONDONTWRITEBYTECODE=1", *command]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        raise SandboxError(f"ゲートウェイの中継を起こせませんでした: {(result.stderr or result.stdout).strip()[:300]}")
    for _ in range(30):
        if _gateway_healthy(box.gateway_port, llm=llm, tools=tools):
            return
        time.sleep(0.5)
    raise SandboxError("ゲートウェイの中継が起動しませんでした（journalctl --user -u "
                       f"{gateway_unit(box)} を見てください）")


def podman_argv(box: Sandbox, command: list[str], *, interactive: bool = False, name: str = "",
                detach: bool = False, env: dict[str, str] | None = None, env_file: Path | None = None,
                need_serve_port: bool = False) -> list[str]:
    pod = ensure_pod(box, need_serve_port=need_serve_port)
    ensure_gateway(box)
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
        # 箱の中から npm・npx・pip が外へ取りに行かない（外部からの取得は利用者の了承が要る。2026-11-22 実機:
        # 箱の OpenCode が `npx tsc` で npm からパッケージを取ってきた）。手元に無いものは失敗する
        "npm_config_offline": "true", "PIP_NO_INDEX": "1",
        "PATH": tool_path(),
        **({
            # 作るアプリがローカル LLM を ControlDeck 専用の書き方なしで使えるよう、OpenAI 互換の標準の変数を渡す。
            # キーは意味の無い値（本物は渡さない。中継が上流へ通すだけ）
            "OPENAI_BASE_URL": f"http://127.0.0.1:{box.gateway_port}/api/v1/llm/v1", "OPENAI_API_KEY": "sandbox",
        } if "llm" in box.capabilities else {}),
        **(env or {}),
    }
    gh = Path.home() / ".config" / "gh"
    extra: list[str] = []
    if box.meta.get("github") and (gh / "hosts.yml").is_file():
        extra = ["-v", f"{gh}:/run/gh:ro"]
        environment["GH_CONFIG_DIR"] = "/run/gh"
    # 箱ごとの /tmp（空のルートの /tmp は全部の箱で共有され、消す時が無いので溜まり続けた。2026-11-21）。
    # 箱を消すと一緒に消える
    tmp = box.path / "tmp"
    tmp.mkdir(mode=0o700, exist_ok=True)
    argv = container_argv(command, workdir=box.repo, writable=[box.path], readonly=readonly,
                          overlays=overlays, env=environment, interactive=interactive, name=name, detach=detach,
                          pod=pod, env_file=env_file, binds=[(tmp, Path("/tmp"))])
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
        f"git_apps_dir: {box.path / 'mirror' / 'apps'}\n"
        # 上流の LLM: 箱の中の中継（本番のローカルのモデルへ届く。外部のモデルは中継が断る）
        f"llm_upstream: http://127.0.0.1:{box.gateway_port}/api/v1/llm/v1\n",
        encoding="utf-8",
    )
    return config


def mirror_container(box: Sandbox) -> str:
    return f"{CONTAINER_PREFIX}{box.name}-mirror"


def mirror_start(box: Sandbox) -> dict:
    """箱の中でミラーの ControlDeck を起こす。機械全体に効く処理は CONTROL_DECK_ISOLATED で止める。"""
    if "mirror" not in box.capabilities:
        raise SandboxError("この作業場には ControlDeck の複製（mirror）の機能がありません（開発対象が ControlDeck 本体の作業場だけ）")
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


def reviewer_model_for(box: Sandbox, work_model: str) -> str | None:
    """レビュー役のモデル。作業のモデルと違うものにする（同じモデルは同じ見落としをする）。

    作業がローカルなら、順番の中の最初の外部（キーが無い・箱で allow_zen が off なら無い）。
    作業が外部ならローカル。外部が使えなければ None（設定は変えず、ローカルのまま）。
    """
    work = normalize_model(work_model)
    if "/" not in work or work.startswith("controldeck/"):
        return next((m for m in model_chain(box) if not m.startswith("controldeck/")), None)
    return LOCAL_MODEL if "llm" in box.capabilities else None


def box_roles(box: Sandbox, work_model: str, roles: dict | None = None, *, local_only: bool = False):
    """箱の OpenCode の、役割ごとのモデル（opencode/roles.py の決まりを箱の都合で適用する）。

    roles は箱の meta の roles（{plan, build, research, quick_check, final_check, evaluate}）。値は `local` /
    `controldeck/auto` / `provider/モデル`。箱で使えない指定（llm が無いのにローカル・Zen が使えないのに opencode*）は
    未設定と同じに扱う（既定へ）。最終検証の既定は、計画 → reviewer_model_for（作業と別のモデル）→ 開発と同じ。
    戻りの models は `provider/モデル` の形。
    """
    from app.integrations.opencode import roles as roles_mod

    llm = "llm" in box.capabilities
    session = opencode_model(normalize_model(work_model or "auto"))
    raw: dict[str, str] = {}
    for role, value in (roles if roles is not None else box.meta.get("roles") or {}).items():
        if role not in roles_mod.ROLES or not str(value or "").strip():
            continue
        ref = opencode_model(normalize_model(str(value).strip()))
        if roles_mod.is_local(ref) and not llm:
            continue   # ローカルを借りない箱では選べない
        if ref.startswith("opencode") and not _zen_available(box):
            continue
        raw[role] = ref
    other = reviewer_model_for(box, work_model or "auto") or ""
    return roles_mod.resolve(raw, session_model=session, first_external=opencode_model(other) if other else "",
                             local_only=local_only, local_model=LOCAL_MODEL if llm else "")


def _set_git_identity(work: Path, name: str) -> None:
    """作業ツリーの local の git の設定に作者を入れる（利用者の ~/.gitconfig は箱に見せないため）。"""
    _git("config", "user.name", f"ControlDeck Sandbox ({name})", cwd=work)
    _git("config", "user.email", "sandbox@controldeck.local", cwd=work)


DEVELOPMENT_SKILL = "controldeck-development"


PERMISSION_PLUGIN_COPY = "controldeck-permission"


def opencode_config(box: Sandbox, *, model: str = "auto", work_model: str = "",
                    chat_server: bool = False, default_model: str = "", roles: dict | None = None,
                    local_only: bool = False) -> Path:
    """箱の中の OpenCode の設定を作る。本番と同じ生成処理で作り、箱から見えないパスだけ写し替える。

    ControlDeck の MCP は、箱の中の bridge から中継経由で載せる（呼べる道具は中継が絞る）。
    トークンは中継だけが持つので、ここには入らない。
    chat_server=True は AI アシスタントの会話サーバー用（権限プラグイン・ask の規則・question の deny・todo を足す。
    プラグインは箱から見えないので箱のホームへ写す）。default_model は設定の既定のモデル（`provider/モデル`）。UI 検証の画面写真は箱の中へ撮る。指示ファイルとスキルは箱の中へ写す（本番の data は箱から見えない）。
    """
    from app.integrations.opencode import provider

    # 箱が届くのはホストの中継だけ（本番のゲートウェイへ直には届かない）。鍵も箱へ渡さない。
    base_url = f"http://127.0.0.1:{box.gateway_port}/api/v1/llm/v1"
    payload = provider._config_payload(f"sandbox-{box.name}", base_url, model)
    payload["provider"]["controldeck"]["options"]["apiKey"] = "sandbox"
    if default_model:
        payload["model"] = opencode_model(normalize_model(default_model))
    servers = dict(payload.get("mcp") or {})
    servers.pop("controldeck_addons", None)
    with_tools = "tools" in box.capabilities
    copied = box.home / ".controldeck"
    copied.mkdir(parents=True, exist_ok=True)
    bridge = copied / "addon_mcp_bridge.py"
    shutil.copyfile(Path(provider.__file__).with_name("addon_mcp_bridge.py"), bridge)
    from app.addons.agent_mcp import MCP_CLIENT_TIMEOUT_MS

    results = copied / "mcp-results"
    results.mkdir(exist_ok=True)
    if with_tools:
        servers["controldeck_addons"] = {
            "type": "local", "command": ["/usr/bin/python3", str(bridge)], "enabled": True,
            "timeout": MCP_CLIENT_TIMEOUT_MS,
            "environment": {
                "CONTROL_DECK_ADDON_MCP_URL": f"http://127.0.0.1:{box.gateway_port}/api/v1/addons/agent-mcp",
                "CONTROL_DECK_ADDON_MCP_TOKEN": "sandbox",  # 意味の無い値。本物は中継が付ける
                "CONTROL_DECK_ADDON_MCP_RESULT_DIR": str(results),
            },
        }
    payload["mcp"] = servers
    shots = box.path / "ui-verify"
    shots.mkdir(exist_ok=True)
    browser = (payload.get("mcp") or {}).get(provider.UI_VERIFY_SERVER)
    if browser:
        command = list(browser["command"])
        command[command.index("--output-dir") + 1] = str(shots)
        browser["command"] = command
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
    # 開発のスキルは、導入の有無に関わらず箱へ渡す（箱の OpenCode が開発の作法を必ず読めるように）。
    from app.skills import catalog as skill_catalog

    development = skill_catalog.bundled_source(DEVELOPMENT_SKILL)
    if development.is_dir() and not any(Path(p).name == DEVELOPMENT_SKILL for p in skills):
        target = copied / "skills" / DEVELOPMENT_SKILL
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(development, target, symlinks=False)
        skills.append(str(target))
    # 役割ごとのモデル。本番の設定の roles は箱に持ち込まず、箱の機能と model_chain で決め直す。
    # final_check は reviewer_model_for（作業と別のモデル）を既定に含む。
    resolved = box_roles(box, work_model or model, roles, local_only=local_only)
    session_ref = opencode_model(normalize_model(work_model or model))
    agents = payload.setdefault("agent", {})
    role_agents = {"plan": "plan", "build": "build", "research": "researcher", "quick_check": "quick-checker",
                   "final_check": "reviewer", "evaluate": "evaluator"}
    for name in (*role_agents.values(), "adversarial-reviewer", "constructive-reviewer"):
        if isinstance(agents.get(name), dict):
            agents[name].pop("model", None)
    for role, name in role_agents.items():
        names = [name, "adversarial-reviewer", "constructive-reviewer"] if role == "final_check" else [name]
        for target in names:
            spec = agents.get(target)
            # 全ての役割にモデルを明示する（空だと子は呼んだ側のモデルを引き継ぐ）
            if isinstance(spec, dict):
                spec["model"] = resolved.models[role]
            elif target in ("plan", "build"):
                agents[target] = {"model": resolved.models[role]}
    # 役割で選んだローカルのモデルを、箱の controldeck の一覧へ足す（無いと OpenCode が
    # 「Model unavailable: controldeck/<モデル>」で止まる。2026-11-22 全部違うモデルの実機で見つけた）
    local_models = payload["provider"]["controldeck"].setdefault("models", {})
    for value in resolved.models.values():
        if str(value).startswith("controldeck/"):
            alias = str(value).split("/", 1)[1]
            if alias and alias not in local_models:
                local_models[alias] = {
                    "name": alias, "attachment": True,
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    **({"limit": limits} if (limits := provider._model_limits(alias)) else {}),
                }
    if skills:
        payload["skills"] = {"paths": skills}
    else:
        payload.pop("skills", None)
    config_dir = box.home / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "opencode.json"
    v2 = provider._v2_payload(payload)
    if chat_server:
        plugin = copied / "plugins" / PERMISSION_PLUGIN_COPY
        shutil.rmtree(plugin, ignore_errors=True)
        shutil.copytree(provider.PERMISSION_PLUGIN, plugin, symlinks=False)
        v2 = provider._apply_chat_server(v2, plugin)
    text = json.dumps(v2, ensure_ascii=False, indent=1)
    from app.models_mgmt import gateway

    real_key = gateway.get_api_key()
    if real_key and real_key in text:
        raise SandboxError("本番のゲートウェイ鍵が箱の設定に入りました（書き出しを中止）")
    relay_token = _relay_token(box)
    if relay_token and relay_token in text:
        raise SandboxError("MCP のトークンが箱の設定に入りました（書き出しを中止）")
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def zen_env_file(box: Sandbox) -> Path | None:
    """Zen のキーを渡す --env-file（0600・箱のフォルダの外の一時ファイル）。キーが無い・箱で禁止なら None。

    使い終わったら remove_env_file(argv) で消す。argv にキーの値は出ない。
    """
    from app.integrations.opencode import zen

    key = zen.get() if box.allow_zen else ""
    if not key:
        return None
    import tempfile

    directory = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    fd, name = tempfile.mkstemp(prefix="cd-zen-", suffix=".env", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:  # mkstemp は 0600
        handle.write(f"OPENCODE_API_KEY={key}\n")
    return Path(name)


def remove_env_file(argv: list[str]) -> None:
    if "--env-file" in argv:
        Path(argv[argv.index("--env-file") + 1]).unlink(missing_ok=True)


def opencode_model(model: str) -> str:
    """`provider/モデル` はそのまま、無ければ controldeck/ を付ける。"""
    return model if "/" in model else f"controldeck/{model}"


def normalize_model(model: str) -> str:
    """`local` はローカル LLM（controldeck/auto）の別名。"""
    return LOCAL_MODEL if model == "local" else model


def _zen_available(box: Sandbox) -> bool:
    from app.integrations.opencode import zen

    return bool(box.allow_zen and zen.get())


def model_chain(box: Sandbox) -> list[str]:
    """箱で使うモデルの順番。meta の model_chain があれば既定の代わり。Zen が使えなければ opencode* を飛ばす。"""
    configured = box.meta.get("model_chain")
    chain = [normalize_model(str(m)) for m in configured] if configured else list(DEFAULT_MODEL_CHAIN)
    if not _zen_available(box):
        chain = [m for m in chain if not m.startswith("opencode")]
    if "llm" not in box.capabilities:
        # ローカル LLM を借りない作業場は、外部のモデルだけ。無ければ空（会話は「使えるモデルがありません」で断る）
        return [m for m in chain if not m.startswith("controldeck/")]
    return chain or [LOCAL_MODEL]


def is_limit_error(line: dict) -> str:
    """OpenCode の JSONL の 1 行が使用量の上限・残高切れ・レート制限の印なら理由の先頭 200 字、でなければ空。"""
    if line.get("type") != "error":
        return ""
    text = json.dumps(line, ensure_ascii=False)
    return text[:200] if LIMIT_PATTERN.search(text) else ""


def _run_streaming(argv: list[str]) -> tuple[int, str, str]:
    """argv を動かし、標準出力の JSONL をそのまま流しつつ読む。(終了コード, sessionID, 上限の理由)。"""
    session = ""
    reason = ""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    for raw in process.stdout or []:
        sys.stdout.write(raw)
        sys.stdout.flush()
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        if not session and isinstance(line.get("sessionID"), str):
            session = line["sessionID"]
        reason = reason or is_limit_error(line)
    return process.wait(), session, reason


def run_with_chain(box: Sandbox, message: str, models: list[str], session: str = "") -> int:
    """順番に実行する。上限の印が出たら次のモデルで同じ指示をやり直す（sessionID があれば続きとして）。"""
    code = 1
    for index, model in enumerate(models):
        box.meta["last_model"] = model
        _save_meta(box.path, box.meta)
        argv = opencode_argv(box, message=message, session=session, model=model, interactive=False)
        try:
            code, found, reason = _run_streaming(argv)
        finally:
            remove_env_file(argv)
        session = found or session
        if not reason or index == len(models) - 1:
            return code
        print(f"モデルを切り替えます: {model} -> {models[index + 1]}（{reason[:200]}）", file=sys.stderr)
    return code


def opencode_argv(box: Sandbox, *, message: str = "", session: str = "", model: str = "auto",
                  interactive: bool = True) -> list[str]:
    from app.features import registry

    opencode_config(box, model="auto" if "/" in model else model, work_model=model)
    binary = registry._managed_executable("opencode-v2")
    if not binary.exists():
        raise SandboxError("OpenCode v2 が導入されていません")
    if interactive:
        command = [str(binary), "--standalone", *(["--prompt", message] if message else []), str(box.repo)]
    else:
        command = [str(binary), "run", message, "--format", "json", "--auto",
                   "--model", opencode_model(model), "--standalone", *(["--session", session] if session else [])]
    env_file = zen_env_file(box)
    try:
        return podman_argv(box, command, interactive=interactive, env_file=env_file)
    except BaseException:
        if env_file:
            env_file.unlink(missing_ok=True)
        raise


def destroy(name: str) -> None:
    box = load(name, verify=False)
    mirror_stop(box)
    gateway_stop(box)
    pod_remove(box)
    target = box.path.resolve()
    if not target.is_relative_to(root()) or target == root():
        raise SandboxError("削除対象がサンドボックスの外です")
    relay_env_file(box).unlink(missing_ok=True)
    shutil.rmtree(target)
    _meta_path(name).unlink(missing_ok=True)


# ---- 箱の中で箱の git を動かす（ホストでは box.repo の中で git を動かさない）----
#
# 箱の中の AI は box.repo/.git/config とフックを書き換えられる。ホストの git をそこで動かすと、core.fsmonitor・
# credential.helper・core.sshCommand・url.*.insteadOf などで、ホストの権限で箱の中のスクリプトが動く（2026-11-22 監査）。
# 状態の確認（ブランチ・未コミット・未 push・tree）は箱の中の git に調べさせ、結果の文字だけを受け取る。

PROBE_TTL = 30
_PROBE_SCRIPT = r"""
b=$(git rev-parse --abbrev-ref HEAD) || exit 3
h=$(git rev-parse HEAD) || exit 3
s=$(git status --porcelain) || exit 3
u=$(git log --branches --not --remotes --format=%H) || exit 3
echo "branch=$b"; echo "head=$h"
echo "dirty=$(printf %s "$s" | grep -c .)"
echo "unpushed=$(printf %s "$u" | grep -c .)"
"""
_PROBE_TREE = r"""
export GIT_INDEX_FILE="/tmp/ws-probe-index.$$"
git add -A >/dev/null || exit 3
t=$(git write-tree) || exit 3
rm -f "$GIT_INDEX_FILE"
echo "tree=$t"
"""


def in_box_result(box: Sandbox, command: list[str], *, timeout: int = 120,
                  env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """箱（pod の中の使い捨てコンテナ）の作業ツリーで command を動かす。(終了コード, 標準出力, 標準エラー)。"""
    argv = podman_argv(box, command, env=env)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"箱の中の処理がタイムアウトしました（{timeout} 秒）") from exc
    return result.returncode, result.stdout, result.stderr


def in_box(box: Sandbox, command: list[str], *, timeout: int = 120) -> str:
    code, out, err = in_box_result(box, command, timeout=timeout)
    if code != 0:
        raise SandboxError(f"箱の中の {command[0]} に失敗しました: {err.strip()[:300]}")
    return out


def probe(box: Sandbox, *, fresh: bool = False, with_tree: bool = False) -> dict:
    """箱の中で調べた状態 {branch, head, dirty(件数), unpushed, tree?, unknown, at}。

    一覧で毎回箱を起こさないよう、PROBE_TTL 秒は meta に覚えた結果を返す（fresh なら必ず調べ直す）。
    調べられなければ unknown=True・dirty=1（安全側。覚えない）。
    """
    saved = _read_meta(box.path).get("probe")
    if (not fresh and isinstance(saved, dict) and time.time() - float(saved.get("at") or 0) < PROBE_TTL
            and (not with_tree or "tree" in saved)):
        return saved
    try:
        out = in_box(box, ["sh", "-c", _PROBE_SCRIPT + (_PROBE_TREE if with_tree else "")], timeout=120)
    except SandboxError:
        return {"branch": "", "head": "", "dirty": 1, "unpushed": 0, "unknown": True, "at": time.time()}
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    result = {"branch": values.get("branch", ""), "head": values.get("head", ""),
              "dirty": int(values.get("dirty", "1") or 0), "unpushed": int(values.get("unpushed", "0") or 0),
              "unknown": False, "at": time.time()}
    if with_tree:
        result["tree"] = values.get("tree", "")
    meta = _read_meta(box.path)
    meta["probe"] = result
    _save_meta(box.path, meta)
    return result


def status(box: Sandbox) -> dict:
    state = probe(box)
    return {"name": box.name, "source": box.meta["source"], "branch": state["branch"], "dirty": bool(state["dirty"]),
            "mirror": f"http://127.0.0.1:{box.port}" if _health(box.port) else "停止中",
            "github": box.meta.get("github", False), "path": str(box.path),
            "model_chain": model_chain(box), "last_model": box.meta.get("last_model", "")}


def list_all() -> list[dict]:
    return [status(load(p.name)) for p in sorted(root().iterdir())
            if p.is_dir() and NAME.fullmatch(p.name) and has_meta(p)]


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="deck.sh sandbox")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="サンドボックスを作る")
    new.add_argument("name")
    new.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    new.add_argument("--ref", default="")
    new.add_argument("--github", action="store_true", help="この PC の gh の設定を読み取り専用で見せる")
    new.add_argument("--owner", default="", help="箱の AI が ControlDeck の道具を使うときの利用者（省くと管理者が 1 人ならその人）")
    new.add_argument("--allow-load", action="store_true", help="箱の AI にモデルの読み込み（切り替え）を許す")
    new.add_argument("--no-zen", action="store_true", help="OpenCode Zen のキーを箱の OpenCode へ渡さない")
    zen_toggle = sub.add_parser("zen", help="OpenCode Zen のキーを箱の OpenCode へ渡すかを切り替える")
    zen_toggle.add_argument("name")
    zen_toggle.add_argument("state", choices=["on", "off"])
    toggle = sub.add_parser("allow-load", help="モデルの読み込みを許すかを切り替える")
    toggle.add_argument("name")
    toggle.add_argument("state", choices=["on", "off"])
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
    code.add_argument("--model", default="",
                      help="省くとモデルの順番（`models` で見る）。指定したらその 1 つだけ（例: opencode/deepseek-v4.1-flash-free、"
                           "local はローカル LLM）。--run では使用量の上限に当たると次のモデルへ切り替えて続ける。"
                           "対話（--run なし）は順番の最初の使えるモデルで開き、切り替えはしない")
    models = sub.add_parser("models", help="モデルの順番を見る・設定する（引数なしで表示、--reset で既定へ）")
    models.add_argument("name")
    models.add_argument("models", nargs="*")
    models.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            box = create(args.name, args.repo, args.ref, github=args.github, allow_load=args.allow_load,
                         allow_zen=not args.no_zen, owner=args.owner)
            print(json.dumps(status(box), ensure_ascii=False, indent=1))
        elif args.command == "allow-load":
            box = load(args.name)
            box.meta["allow_load"] = args.state == "on"
            _save_meta(box.path, box.meta)
            gateway_stop(box)  # 次に箱を使うとき、新しい設定で中継が起きる
            print(f"{args.name}: モデルの読み込みを{'許可' if box.allow_load else '禁止'}にしました")
        elif args.command == "zen":
            box = load(args.name)
            box.meta["allow_zen"] = args.state == "on"
            _save_meta(box.path, box.meta)
            print(f"{args.name}: OpenCode Zen のキーを{'渡す' if box.allow_zen else '渡さない'}ようにしました")
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
        elif args.command == "models":
            box = load(args.name)
            if args.reset:
                box.meta.pop("model_chain", None)
                _save_meta(box.path, box.meta)
            elif args.models:
                box.meta["model_chain"] = [normalize_model(m) for m in args.models]
                _save_meta(box.path, box.meta)
            print(json.dumps({"model_chain": model_chain(box), "custom": bool(box.meta.get("model_chain")),
                              "last_model": box.meta.get("last_model", "")}, ensure_ascii=False, indent=1))
        elif args.command == "opencode":
            box = load(args.name)
            if args.run:
                models = [normalize_model(args.model)] if args.model else model_chain(box)
                if not models:
                    raise SandboxError(NO_MODEL_MESSAGE)
                return run_with_chain(box, args.run, models, args.session)
            chain = model_chain(box)
            if not args.model and not chain:
                raise SandboxError(NO_MODEL_MESSAGE)
            chosen = normalize_model(args.model) if args.model else chain[0]
            argv = opencode_argv(box, message=args.run, session=args.session, model=chosen, interactive=True)
            try:
                # execv だと使い終わりの後始末ができない（env-file を消せない）ので、子として動かす
                return subprocess.run(argv, check=False).returncode
            finally:
                remove_env_file(argv)
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
