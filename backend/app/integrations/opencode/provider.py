from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from app.config import data_dir
from app.features import registry
from app.files import service as files
from app.jobs.service import Job

OPERATIONS = {"analyze", "implement", "fix", "test", "review"}
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_SETTINGS = {
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "llama3.2",
    "project_path": "",
    # ControlDeck の OpenAI 互換ゲートウェイ経由にするか。
    # 経由すると KV の受け入れ制御（混雑時の待機・枯渇時の再試行）が効く。
    "use_gateway": True,
}


class CodeAgentError(RuntimeError):
    pass


def _integration_dir() -> Path:
    root = (data_dir() / "integrations" / "opencode").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return _integration_dir() / "settings.json"


def get_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            settings.update({key: raw[key] for key in settings if key in raw})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return settings


def gateway_base_url() -> str:
    """ControlDeck ゲートウェイの base_url。判定・組み立てはgatewayへ委譲する。"""
    from app.models_mgmt import gateway

    return gateway.base_url()


def is_gateway_url(base_url: str) -> bool:
    from app.models_mgmt import gateway

    return gateway.is_gateway_url(base_url)


def gateway_client_header() -> str:
    from app.models_mgmt import gateway

    return gateway.CLIENT_HEADER


def resolve_backend_port() -> int | None:
    """OpenCode が最終的に到達する llama.cpp のポート。

    ゲートウェイ経由だと base_url は ControlDeck のポートになるため、
    そのままではアイドル判定（どのモデルが使われているか）が引けない。
    ゲートウェイの転送先まで解決して実ポートを返す。
    """
    from urllib.parse import urlsplit

    settings = get_settings()
    base_url = str(settings.get("base_url") or "")
    if is_gateway_url(base_url):
        try:
            from app.models_mgmt.gateway import resolve_endpoint

            return resolve_endpoint(str(settings.get("model") or ""))[1]
        except Exception:  # noqa: BLE001 - 解決できなければ不明として扱う
            return None
    try:
        parsed = urlsplit(base_url)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            return None
        return parsed.port
    except ValueError:
        return None


def autoconfigure(*, model: str = "") -> dict:
    """導入直後に通信できる状態へ自動設定する。

    ユーザーが base_url や APIキーを手で入れなくても使えるようにする。
    ゲートウェイのAPIキーは未発行なら発行し、OpenCode の runtime config へ渡る
    形で保存する。
    """
    from app.models_mgmt import gateway, local_llm

    target = model
    if not target:
        # 特定モデルを名指しせず、転送先の判断はゲートウェイへ任せる。停止中の別モデルを
        # 起こさずに、そのとき動いているモデルへ流れる。llama.cpp か Lucebox かも
        # ゲートウェイ側の解決に委ねる（OpenCode はモデル名しか知らなくてよい）。
        target = gateway.AUTO_MODEL if local_llm.llm_instances() else ""
    gateway.get_api_key(create=True)  # 未発行なら発行
    patch = {"base_url": gateway_base_url(), "use_gateway": True}
    if target:
        patch["model"] = target
    return save_settings(patch)


def save_settings(patch: dict) -> dict:
    settings = get_settings()
    settings.update({key: patch[key] for key in settings if key in patch})
    settings["base_url"] = str(settings["base_url"]).strip().rstrip("/")
    settings["model"] = str(settings["model"]).strip()
    if not settings["base_url"].startswith(("http://", "https://")):
        raise ValueError("LLM endpointはhttp(s) URLで指定してください")
    if not settings["model"] or len(settings["model"]) > 200:
        raise ValueError("modelを指定してください")
    project = str(settings.get("project_path") or "")
    if project:
        resolved = files.resolve(project)
        if not resolved.is_dir():
            raise ValueError("project pathはディレクトリを指定してください")
        settings["project_path"] = str(resolved)
    path = _settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return settings


def _api_key_for(base_url: str) -> str:
    """ゲートウェイ宛なら発行済みAPIキー、直結なら従来どおりダミー。"""
    if not is_gateway_url(base_url):
        return "sk-no-key"
    from app.models_mgmt import gateway

    return gateway.get_api_key(create=True) or "sk-no-key"


def _addon_asset_roots() -> list[Path]:
    """Add-on が作った成果物の置き場。

    MediaForge も SonicForge も、生成したものを feature-data の下へ置いて
    asset_id で返す。OpenCode はそのパスを読みに行くので、ここが閉じていると
    生成のたびに確認が入る。置き場の形は Add-on ごとに違うので、両方見る。

    開けるのは assets の下だけにする。feature-data ごと開けると、モデルの重みや
    実行状態まで一緒に読めてしまう。知らない形の Add-on は従来どおり確認する。
    """
    from app.config import data_dir

    base = data_dir() / "feature-data"
    if not base.is_dir():
        return []
    roots: list[Path] = []
    for feature in sorted(base.iterdir()):
        if not feature.is_dir():
            continue
        for candidate in (feature / "assets", feature / "data" / "assets"):
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def _mcp_result_dir() -> Path:
    """長すぎる Add-on tool の結果を、削る前に落としておく場所。

    削った側だけを会話へ載せる。全部を載せると、1 件で文脈を食い尽くしうる
    （実測で会話の 48.1% が道具の結果だった）。落とした先はここに置き、
    必要になったら grep で読ませる。
    """
    root = (data_dir() / "integrations" / "opencode" / "tool-results").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _allowed_directories() -> dict[str, str]:
    """確認なしで読ませてよい、プロジェクト外のディレクトリ。"""
    from app.terminals import attachments

    allowed: dict[str, str] = {}
    for root in [
        codedev_root(), attachments.store.root, _mcp_result_dir(), *_addon_asset_roots(),
    ]:
        allowed[f"{root}/*"] = "allow"
        allowed[f"{root}/**"] = "allow"
    return allowed


logger = logging.getLogger("control_deck.opencode")


def _skill_paths() -> list[str]:
    """有効なスキルの置き場。取れなければ何も渡さない。

    スキルは補助であって、これが読めないことで OpenCode が起動しないのは
    本末転倒である。
    """
    try:
        from app.skills import registry as skills

        return skills.enabled_paths()
    except Exception:  # noqa: BLE001 - スキルの都合で session を止めない
        logger.exception("有効なskillの一覧を取得できませんでした")
        return []


# 一度の返答に許す長さ。窓のうちここは常に空けておく必要がある。
#
# 8,192 で 30KB 前後の文章に相当する。コードを書く用途で足りなかった実測は無く、
# 足りなければモデルは続きを書ける。ここを広げるほど圧縮が早く来る（窓は
# 入力と出力で分け合うため）。
OPENCODE_MAX_OUTPUT_TOKENS = 8192
# 畳む作業そのものに要る余地。要約を書く一往復ぶん。
OPENCODE_COMPACTION_RESERVED = 4096


# 主の会話から外して、専門の子へ委ねる道具。
#
# OpenCode は MCP の道具名を書き換えて見せる（server 名を前に付け、記号を _ に
# する）。この server 名は下の payload["mcp"] で決めているので、前置きは既知である。
#
# 実測（道具 24 個で 26,692 トークン）:
#   3D  media.scene.* / media.job.*  15,583 tok  58%  コードを書く会話では一度も使わない
#   音  sonic.*                       5,651 tok  21%  たまに使う
#   絵  media.generate 系             5,236 tok  20%  よく使う。一発で終わることが多い
#
# 絵は主に残す。子を起こす往復のほうが、載せておく 5,236 トークンより高くつく。
# 3D と音を外すと、主は 26,692 → 5,458 トークン（-80%）になり、圧縮を挟んだ
# 直後の余裕が 13,346 → 36,590 トークンへ広がる。
#
# 子の中の往復（場面を何度も直す、音を何本も作る）は主の文脈に入らない。実測で
# 会話の 48.1% は道具の結果だったので、こちらの効きのほうが大きい。
_SUBAGENT_TOOLS = {
    "sculptor": ("controldeck_addons_media_scene_*", "controldeck_addons_media_job_*"),
    "sound": ("controldeck_addons_sonic_*",),
}


def _delegated_agents() -> dict[str, Any]:
    """主から重い道具を外し、専門の子に持たせる。

    子は自分の文脈を持つ。主へ返るのは結論だけで、途中の往復は入らない。
    子の文脈が短いことには、もう一つ効きめがある——MCP の生成は GPU を要求し、
    その度に言語モデルが降ろされる。戻すときの読み直しは文脈の長さに比例するので、
    短い子ほど安く戻る。
    """
    agents: dict[str, Any] = {}
    build_tools: dict[str, bool] = {}
    for name, patterns in _SUBAGENT_TOOLS.items():
        for pattern in patterns:
            build_tools[pattern] = False
        agents[name] = {
            "mode": "subagent",
            "description": _SUBAGENT_DESCRIPTIONS[name],
            "tools": {pattern: True for pattern in patterns},
        }
    agents["build"] = {"tools": build_tools}
    return agents


_SUBAGENT_DESCRIPTIONS = {
    "sculptor": (
        "3D の場面を作る係。場面を作る・直す・材質を貼る・書き出す。"
        "何を置いてどう見せたいかを渡すと、出来た場面の id と書き出した資産を返す。"
    ),
    "sound": (
        "音を作る係。台詞の読み上げ、効果音、環境音、音楽、書き起こし。"
        "キャラクターの声は先に作る必要があるので、声の用意もこの係が行う。"
    ),
}


def _model_limits(model: str) -> dict[str, int] | None:
    """そのモデルの窓の大きさを OpenCode へ伝える。

    伝えないと自動圧縮が一度も動かない。OpenCode は limit.context が 0 の間は
    発火しないと決めており（実測: この環境の 793 session のうち先回りの圧縮は
    0 件、頂点は 262,142 tokens で打ち止め）、provider がモデルを宣言するときに
    limit を書いていなかった。

    input も併せて宣言する。OpenCode は input がある場合だけ compaction.reserved
    を見る作りで、context だけだと予備は「返答の上限」（既定 32,000）に固定される。
    """
    try:
        from app.models_mgmt import llama

        for instance in llama.list_instances():
            if str(instance.get("alias") or "") != model:
                continue
            context = int(instance.get("ctx_size") or 0)
            if context <= 0:
                return None
            output = min(OPENCODE_MAX_OUTPUT_TOKENS, max(1024, context // 4))
            return {"context": context, "output": output, "input": context - output}
    except Exception:  # noqa: BLE001 - 窓の大きさが取れないことで session を止めない
        logger.exception("モデルの窓の大きさを取得できませんでした")
    return None


def _runtime_config(
    job_id: str,
    base_url: str,
    model: str,
    *,
    owner_user_id: int | None = None,
    project_id: str | None = None,
) -> Path:
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "", job_id)[:24]
    path = _integration_dir() / f"runtime-config-{safe_job_id}.json"
    limits = _model_limits(model)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"controldeck/{model}",
        "provider": {
            "controldeck": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Control Deck LLM",
                "options": {
                    "baseURL": base_url.rstrip("/"),
                    "apiKey": _api_key_for(base_url),
                    # ゲートウェイがOpenCodeを識別するための印。停止中のモデルを
                    # OpenCodeが起こすときは投機デコード優先で常駐させる。
                    "headers": {gateway_client_header(): "opencode"},
                },
                # 画像を送らせるには attachment と modalities の両方が要る。
                # attachment だけだと OpenCode は画像を text に落として送り、モデルは
                # 「画像入力に対応していない」と答える。実際に画像を載せるかは
                # modalities.input の image で決まる。転送先が VLM でない場合は
                # 転送先が拒否する（送る側では判断できない）。
                "models": {
                    model: {
                        "name": model,
                        "attachment": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                        **({"limit": limits} if limits else {}),
                    }
                },
            }
        },
        # 古い道具の出力を捨てる。会話も判断も壊さず、完了した tool の出力だけが
        # 対象で、直近 40,000 トークン分は残る（OpenCode 側の既定）。
        #
        # 既定は false である。切ったままだと、忘れる仕組みが一つも動かない。
        # 実測: この環境の 793 session のうち圧縮が起きたのは 19 件で、うち 17 件は
        # 「モデルが断ってから畳んだ」後追いだった。先回りの圧縮は一度も発火して
        # いない。唐揚げダンジョンの session は 262,142 tokens で打ち止めている。
        #
        # 剪定が狙うのは、その打ち止めの中身そのものである。頂点の内訳を数えると
        # 道具の結果が 48%、推論が 20%、画像は 7%、人が読む本文は 2% だった。
        # 頂点 100k を超えた 13 session で見積もると、平均 51.7% が空く。
        # 窓が尽きる手前で自動的に畳む。
        #
        # 既定は auto: true だが、limit.context が 0 の間は一度も発火しない。
        # 窓の大きさは _model_limits で宣言した。発火点は OpenCode の式で
        # limit.input - reserved になるので、ctx 65,536 ならこうなる:
        #
        #   窓        65,536
        #   返答       8,192   常に空けておく
        #   入力      57,344   = 窓 - 返答
        #   予備       4,096   畳む作業そのものの分
        #   発火      53,248   入力がここに届いたら畳む（窓の 81%）
        #
        # 畳んだ後に残す直近は OpenCode の既定に任せる（発火点の 25% を 15,000 で
        # 頭打ちにしたもの）。窓が 65,536 だった頃は道具定義 26,702 トークンに
        # 押されて畳んだ直後の余裕が 13,000 しか無く、10,000 へ絞っていた。
        # 窓が 131,072 になり、重い道具を子へ委ねて主が 5,458 トークンになった
        # いまは、既定でも次の発火まで 97,000 トークンある。残る文脈のほうが
        # 価値が高い。
        #
        # 剪定（prune）は別物で、完了した道具の出力だけを捨てる。直近 40,000
        # トークン分は守られる（OpenCode 側の固定値で、設定では変えられない）。
        "compaction": {
            "prune": True,
            "reserved": OPENCODE_COMPACTION_RESERVED,
        },
        # CodeDEV 配下は毎回聞かない。別プロジェクトを参照するだけで確認が入ると
        # 手が止まるためで、CodeDEV の外は既定どおり確認する。
        # ターミナルから送った画像の置き場も開ける。利用者が自分で送ったものであり、
        # ここが閉じているとパスを渡しても読めない。
        # `*` は階層を跨がない照合系もあるので、直下と再帰の両方を挙げておく。
        "permission": {"external_directory": _allowed_directories()},
        "agent": _delegated_agents(),
    }
    # 導入済みで有効なスキルだけ読ませる。利用者の ~/.claude や
    # ~/.config/opencode へは書かない——そこは利用者自身のもので、こちらが
    # 足したり消したりしてよい場所ではない。ControlDeck から起動した session
    # にだけ効くので、無効化はこの一覧から外すだけで済む。
    skill_paths = _skill_paths()
    if skill_paths:
        payload["skills"] = {"paths": skill_paths}
    # 文脈の使い方を書いたものを system prompt へ足す。449 トークン。
    #
    # 実測の内訳では道具の結果が 48.1% を占め、その中身は全文読みと、絞らずに
    # 受け取ったコマンドの出力だった。どちらも「読む前に当たりを付ける」だけで
    # 桁が変わる。プロジェクト側の AGENTS.md は残したまま、こちらを足す。
    notes = Path(__file__).with_name("agent-notes.md")
    if notes.exists():
        payload["instructions"] = [str(notes)]
    if owner_user_id is not None:
        from app.addons.agent_mcp import MCP_CLIENT_TIMEOUT_MS, issue_opencode_token
        from app.config import get_config

        bridge = Path(__file__).with_name("addon_mcp_bridge.py").resolve()
        token = issue_opencode_token(owner_user_id, safe_job_id, project_id=project_id)
        payload["mcp"] = {
            "controldeck_addons": {
                "type": "local",
                "command": [sys.executable, str(bridge)],
                "enabled": True,
                "timeout": MCP_CLIENT_TIMEOUT_MS,
                "environment": {
                    "CONTROL_DECK_ADDON_MCP_URL": (
                        f"http://127.0.0.1:{get_config().server.port}/api/v1/addons/agent-mcp"
                    ),
                    "CONTROL_DECK_ADDON_MCP_TOKEN": token,
                    "CONTROL_DECK_ADDON_MCP_RESULT_DIR": str(_mcp_result_dir()),
                },
            }
        }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def _managed_project_id(project: Path) -> str | None:
    try:
        relative = project.resolve(strict=True).relative_to(codedev_root().resolve())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return relative.name if len(relative.parts) == 1 and not relative.name.startswith(".") else None


def codedev_root() -> Path:
    """OpenCodeプロジェクトのルート。置き場の決定は config へ委ねる。

    Project Lab も同じ場所を見るため、両者で別々に組み立てない。
    """
    from app.config import codedev_dir

    root = codedev_dir()
    root.mkdir(exist_ok=True)
    return root


def list_projects() -> list[dict]:
    """CodeDEV配下のプロジェクト一覧（更新の新しい順）。"""
    projects = []
    for path in codedev_root().iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        projects.append({
            "name": path.name, "path": str(path),
            "git": (path / ".git").is_dir(), "modified_at": mtime,
        })
    return sorted(projects, key=lambda item: -float(item["modified_at"]))


def ensure_project(name: str) -> dict:
    """プロジェクト名からCodeDEV配下のフォルダを取得（無ければ作成 + git init）。"""
    import subprocess

    cleaned = name.strip()
    if (not cleaned or len(cleaned) > 64 or cleaned in (".", "..")
            or cleaned.startswith(".") or any(c in cleaned for c in "/\\\0")):
        raise CodeAgentError("プロジェクト名は64文字以内で、/ や先頭の . は使えません")
    path = codedev_root() / cleaned
    created = not path.exists()
    path.mkdir(exist_ok=True)
    if created and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, timeout=15)
    return {"name": cleaned, "path": str(path), "created": created,
            "git": (path / ".git").is_dir()}


def delete_project(name: str) -> dict:
    """CodeDEV配下のプロジェクトをフォルダごと削除する（配下チェック付き）。"""
    cleaned = name.strip()
    if (not cleaned or len(cleaned) > 64 or cleaned in (".", "..")
            or cleaned.startswith(".") or any(c in cleaned for c in "/\\\0")):
        raise CodeAgentError("プロジェクト名が不正です")
    root = codedev_root()
    path = (root / cleaned).resolve()
    if not path.is_relative_to(root) or path == root:
        raise CodeAgentError("CodeDEV配下のプロジェクトのみ削除できます")
    if not path.is_dir():
        raise CodeAgentError("プロジェクトが見つかりません")
    shutil.rmtree(path)
    return {"name": cleaned, "path": str(path)}


def import_project(source_path: str) -> dict:
    """CodeDEV外のフォルダをCodeDEVへコピーして取り込む（管理下は素通し）。

    依存物などの重量ディレクトリ（node_modules/.venv等）は再生成可能なため
    コピー対象から除外する。名前衝突時は -2, -3 と連番を付ける。
    """
    import shutil as _shutil

    source = files.resolve(source_path)
    if not source.is_dir():
        raise CodeAgentError("フォルダを指定してください")
    root = codedev_root()
    if source == root:
        raise CodeAgentError("CodeDEVルート自体は開けません。プロジェクトを選択してください")
    if source.is_relative_to(root):
        return {"name": source.name, "path": str(source), "imported": False}
    base = source.name or "project"
    destination = root / base
    counter = 2
    while destination.exists():
        destination = root / f"{base}-{counter}"
        counter += 1
    _shutil.copytree(
        source, destination,
        ignore=_shutil.ignore_patterns("node_modules", ".venv", "venv", "__pycache__",
                                       ".mypy_cache", ".pytest_cache"),
        symlinks=True,
    )
    return {"name": destination.name, "path": str(destination), "imported": True}


def tui_command(
    *,
    project_path: str,
    prompt: str = "",
    base_url: str = "",
    model: str = "",
    owner_user_id: int | None = None,
) -> tuple[str, str]:
    """対話TUIセッション用のshellコマンドを組み立てる。(command, project_dir)を返す。

    ターミナル基盤（tmux）の上でopencode TUIをそのまま動かす。設定は永続config
    （Control Deck LLM provider）を都度再生成して渡す。
    """
    import shlex

    if not registry.is_enabled("opencode"):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    binary = registry.executable("opencode")
    if binary is None:
        raise CodeAgentError("OpenCodeを利用できません")
    settings = get_settings()
    endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
    model_id = (model or settings["model"]).strip()
    if not endpoint.startswith(("http://", "https://")) or not model_id:
        raise CodeAgentError("LLM endpointとmodelを設定してください")
    raw_project = project_path or settings.get("project_path") or str(Path.home())
    try:
        project = files.resolve(raw_project)
    except (files.FileAccessError, FileNotFoundError) as exc:
        raise CodeAgentError(str(exc)) from exc
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    config = _runtime_config(
        f"tui-{owner_user_id or 0}", endpoint, model_id,
        owner_user_id=owner_user_id, project_id=_managed_project_id(project),
    )
    argv = [str(binary), "--model", f"controldeck/{model_id}"]
    if prompt.strip():
        argv += ["--prompt", prompt.strip()]
    argv.append(str(project))
    command = f"OPENCODE_CONFIG={shlex.quote(str(config))} exec " + " ".join(shlex.quote(a) for a in argv)
    return command, str(project)


def _prompt(operation: str, instruction: str) -> str:
    labels = {
        "analyze": "コードを読み取り、問題点と改善案を分析してください。ファイルは変更しないでください。",
        "implement": "要求を実装し、必要なテストも更新してください。",
        "fix": "不具合を再現・原因特定して修正し、回帰テストを追加してください。",
        "test": "対象をテストし、失敗があれば原因と再現手順を報告してください。",
        "review": "変更をレビューし、重大度順に具体的な指摘を報告してください。ファイルは変更しないでください。",
    }
    return f"{labels[operation]}\n\nユーザー要求:\n{instruction.strip()}"


def _extract_text(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            found.append(value["text"])
        elif isinstance(value.get("content"), str) and value.get("type") in ("message", "result"):
            found.append(value["content"])
        for child in value.values():
            found.extend(_extract_text(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_extract_text(child))
    return found


def _find_session_id(value: Any) -> str:
    """opencode JSONイベントからセッションIDを再帰探索する（継続対話用）。"""
    if isinstance(value, dict):
        for key in ("sessionID", "session_id", "sessionId"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
        for child in value.values():
            found = _find_session_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_session_id(child)
            if found:
                return found
    return ""


async def run_chat(
    job: Job, *, instruction: str, project_name: str = "", project_path: str = "",
    session_id: str = "", on_text=None, on_event=None,
) -> dict:
    """AIチャット用のheadless実行。JSONイベントを逐次読み、本文テキストを

    on_text コールバックへストリームする（Codex/Claude風のチャット内コーディング）。
    session_id 指定で前回のopencodeセッションを継続する。
    """
    if not registry.is_enabled("opencode"):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    if not instruction.strip():
        raise CodeAgentError("指示が空です")
    binary = registry.executable("opencode")
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if binary is None or systemd_run is None or systemctl is None:
        raise CodeAgentError("OpenCodeまたはsystemd user managerを利用できません")
    settings = get_settings()
    if project_name.strip():
        project = Path(ensure_project(project_name)["path"])
    elif (project_path or "").strip():
        # CodeDEV外のフォルダはコピーして取り込んでから開く
        imported = await asyncio.to_thread(import_project, project_path)
        project = Path(imported["path"])
    else:
        raw = settings.get("project_path") or ""
        if not raw:
            raise CodeAgentError("プロジェクトを指定してください")
        project = files.resolve(raw)
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    endpoint = str(settings["base_url"]).rstrip("/")
    model_id = str(settings["model"]).strip()
    runtime_config = await asyncio.to_thread(_runtime_config,
        f"chat-{job.id}", endpoint, model_id, owner_user_id=job.owner_user_id,
        project_id=await asyncio.to_thread(_managed_project_id, project),
    )
    # LLM endpoint（llama.cpp / Lucebox instance）はondemand hookを通らないため先に起動保証する
    from app.models_mgmt import local_llm

    await local_llm.ensure_ready_by_base_url(endpoint)
    unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
    argv = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        f"--unit={unit}", f"--working-directory={project}",
        f"--setenv=OPENCODE_CONFIG={runtime_config}",
        str(binary), "run", instruction[:32_000],
        "--format", "json", "--auto",
        "--model", f"controldeck/{model_id}", "--dir", str(project),
    ]
    if session_id:
        argv += ["--session", session_id]
    job.set_progress("OpenCodeを起動", 0, 1)
    events = 0
    emitted: set[str] = set()
    found_session = ""
    reported_error = ""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )

        async def drain_stderr() -> bytes:
            assert proc is not None and proc.stderr is not None
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(drain_stderr())
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            events += 1
            if on_event is not None:
                await on_event(str(event.get("type") or ""), events)
            if not found_session:
                found_session = _find_session_id(event)
            if event.get("type") == "error":
                reported_error = str(event.get("error", {}).get("name") or "provider error")[:100]
            for text in _extract_text(event):
                cleaned = text.strip()
                if not cleaned or cleaned in emitted:
                    continue
                emitted.add(cleaned)
                if on_text is not None:
                    await on_text(cleaned)
            if events % 5 == 0:
                job.set_progress("OpenCode実行中", events, 0)
        await stderr_task
        await proc.wait()
    except asyncio.CancelledError:
        stop = await asyncio.create_subprocess_exec(
            systemctl, "--user", "stop", f"{unit}.service",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await stop.wait()
        raise
    finally:
        await asyncio.to_thread(runtime_config.unlink, missing_ok=True)
    if reported_error:
        raise CodeAgentError(f"OpenCode provider error: {reported_error}")
    if proc is None or proc.returncode != 0:
        raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
    output = "\n\n".join(emitted)
    job.set_progress("完了", 1, 1)
    return {"output": output[-100_000:], "events": events,
            "session_id": found_session, "project_path": str(project)}


class OpenCodeProvider:
    async def run(
        self, job: Job, *, operation: str, project_path: str, instruction: str,
        base_url: str = "", model: str = "",
    ) -> dict:
        if not registry.is_enabled("opencode"):
            raise CodeAgentError("OpenCode featureが有効ではありません")
        if operation not in OPERATIONS:
            raise CodeAgentError("未対応のoperationです")
        if not instruction.strip() or len(instruction) > 32_000:
            raise CodeAgentError("instructionは1〜32000文字で指定してください")
        try:
            project = files.resolve(project_path)
        except (files.FileAccessError, FileNotFoundError) as exc:
            raise CodeAgentError(str(exc)) from exc
        if not project.is_dir():
            raise CodeAgentError("project pathはディレクトリを指定してください")
        settings = get_settings()
        endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
        model_id = (model or settings["model"]).strip()
        binary = registry.executable("opencode")
        systemd_run = shutil.which("systemd-run")
        systemctl = shutil.which("systemctl")
        if binary is None or systemd_run is None or systemctl is None:
            raise CodeAgentError("OpenCodeまたはsystemd user managerを利用できません")
        runtime_config = await asyncio.to_thread(_runtime_config,
            job.id, endpoint, model_id, owner_user_id=job.owner_user_id,
            project_id=await asyncio.to_thread(_managed_project_id, project),
        )
        prompt_path = (_integration_dir() / f"prompt-{job.id}.txt").resolve()
        if not prompt_path.is_relative_to(_integration_dir()):
            raise CodeAgentError("prompt pathがintegration directory外です")
        prompt_path.write_text(_prompt(operation, instruction), encoding="utf-8")
        os.chmod(prompt_path, 0o600)
        unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
        argv = [
            systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
            f"--unit={unit}", f"--working-directory={project}",
            f"--setenv=OPENCODE_CONFIG={runtime_config}",
            str(binary), "run", "添付されたControl Deckの指示を実行してください。",
            "--format", "json", "--auto",
            "--model", f"controldeck/{model_id}", "--dir", str(project),
            "--file", str(prompt_path),
        ]
        job.set_progress("OpenCodeを起動", 0, 1)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            stop = await asyncio.create_subprocess_exec(
                systemctl, "--user", "stop", f"{unit}.service",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await stop.wait()
            raise
        finally:
            prompt_path.unlink(missing_ok=True)
            runtime_config.unlink(missing_ok=True)
        if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
            raise CodeAgentError("OpenCode出力が上限を超えました")
        if proc is None or proc.returncode != 0:
            # stderrはprovider/pluginがpromptやcredentialを含める可能性があるため公開しない。
            raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
        events = []
        text_parts: list[str] = []
        reported_error = ""
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") == "error":
                reported_error = str(event.get("error", {}).get("name") or "provider error")[:100]
            text_parts.extend(_extract_text(event))
            if len(events) % 10 == 0:
                job.set_progress("OpenCode実行中", len(events), 0)
        if reported_error:
            raise CodeAgentError(f"OpenCode provider error: {reported_error}")
        output = "\n".join(dict.fromkeys(part.strip() for part in text_parts if part.strip()))
        job.set_progress("完了", 1, 1)
        return {"output": output[-100_000:], "events": len(events), "operation": operation,
                "project_path": str(project), "model": model_id}


provider = OpenCodeProvider()


# ---- OMo（oh-my-openagent）の並列設定 ----
#
# 責務を分ける:
#   OMo        「いくつの仕事を並行して進めたいか」（論理並列）
#   ControlDeck「いま GPU へ何本入れて安全か」（受付・待ち行列）
#   llama.cpp  「実際の同時実行」（slot と共有KV）
#
# したがって OMo の並列数を llama の --parallel に一致させる必要はない。
# エージェントは常に LLM を呼んでいるわけではない（grep やビルドの時間がある）ので、
# 論理並列 > slot 数 は健全なオーバーサブスクリプションになり、GPU を遊ばせにくい。
# 溢れた分はゲートウェイの受け入れ制御が待たせる。

# OMo 側の既定（v4.19.4 の schema 実測値）
OMO_DEFAULT_CONCURRENCY = 5
OMO_DEFAULT_TEAM_PARALLEL = 4


def _omo_config_path() -> Path:
    """OMo のユーザー設定。~/.omo/omo.jsonc（jsonc が無ければ omo.json）。"""
    root = Path.home() / ".omo"
    root.mkdir(parents=True, exist_ok=True)
    jsonc = root / "omo.jsonc"
    if jsonc.exists():
        return jsonc
    legacy = root / "omo.json"
    return legacy if legacy.exists() else jsonc


def omo_concurrency_for(n_parallel: int, *, gated: bool) -> tuple[int, int]:
    """(論理並列, Team同時メンバー数) を決める。

    gated=True（ゲートウェイ経由）なら、溢れた分を ControlDeck が待たせられるので
    OMo 既定のまま少し多めに走らせる。GPU を遊ばせない。
    gated=False（llama.cpp 直結）なら誰も待たせてくれないため、
    対話中のメインエージェント用に 1 本空けた保守的な値にする。
    """
    if gated:
        return OMO_DEFAULT_CONCURRENCY, OMO_DEFAULT_TEAM_PARALLEL
    safe = max(1, int(n_parallel) - 1)
    return safe, safe


def sync_omo_concurrency(n_parallel: int | None = None) -> dict:
    """OMo の task.default_concurrency / task.team.max_parallel_members を整える。

    OMo は provider_concurrency / model_concurrency を上位に持つので、
    既定値だけ書き、利用者が付けた個別上書きは残す。
    schema は .strict() なので、未知のキーを混ぜると設定ごと弾かれる点に注意。
    """
    from app.models_mgmt import local_llm

    settings = get_settings()
    gated = bool(settings.get("use_gateway")) and is_gateway_url(str(settings.get("base_url") or ""))
    if n_parallel is None:
        # 並列数は実際に転送される先の設定で決める。解決規則はゲートウェイと共有する。
        from app.models_mgmt import gateway

        try:
            alias, _ = gateway.resolve_endpoint(str(settings.get("model") or ""))
        except Exception:  # noqa: BLE001 - 未登録なら同期対象なし
            return {"updated": False, "reason": "対象モデルがありません"}
        instance = local_llm.find(alias)
        if instance is None:
            return {"updated": False, "reason": "対象モデルがありません"}
        # Lucebox は slot 分割を持たない（単一セッション + 投機デコード）ので 1 とみなす。
        n_parallel = int(instance.get("n_parallel") or 1)

    concurrency, team_parallel = omo_concurrency_for(n_parallel, gated=gated)
    path = _omo_config_path()
    try:
        raw = path.read_text(encoding="utf-8")
        config = json.loads(_strip_jsonc(raw))
        if not isinstance(config, dict):
            config = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        config = {}
    task = dict(config.get("task") or {})
    team = dict(task.get("team") or {})
    if (task.get("default_concurrency") == concurrency
            and team.get("max_parallel_members") == team_parallel):
        return {"updated": False, "concurrency": concurrency, "reason": "変更なし"}
    task["default_concurrency"] = concurrency
    team["max_parallel_members"] = team_parallel
    task["team"] = team
    config["task"] = task
    try:
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"updated": False, "reason": f"書き込めません: {exc}"}
    return {"updated": True, "concurrency": concurrency, "team_parallel": team_parallel,
            "n_parallel": n_parallel, "gated": gated, "path": str(path)}


def _strip_jsonc(text: str) -> str:
    """jsonc のコメントを落とす。利用者が手で書いた設定を壊さず読むため。"""
    import re

    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)
