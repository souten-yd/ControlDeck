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
    # 使う OpenCode の系列。"v1"（opencode-ai）か "v2"（@opencode/cli）。
    # 既定は v1 のまま。v2 を導入しただけでは切り替わらない。
    "runtime": "v1",
}

# 同居する OpenCode ランタイムと、それを持つアドオン（feature）の対応。
# 導入先prefixが別なので、片方だけ導入・片方だけ削除ができる。
RUNTIME_FEATURES = {"v1": "opencode", "v2": "opencode-v2"}


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


def active_runtime(preferred: str = "") -> str:
    """実際に使う OpenCode の系列を決める。

    `preferred` は画面が名指しする系列（v1 画面／v2 画面のボタン）。空なら設定値を使う。
    名指しされた系列が無効（未導入・停止）なら、有効なもう一方へ落とす。片方だけ
    導入している利用者に「設定が v1 のままなので動かない」と言わせないため。
    どちらも無効なら希望をそのまま返し、判断は呼び出し側（active_binary）へ委ねる。
    """
    if preferred not in RUNTIME_FEATURES:
        preferred = str(get_settings().get("runtime") or "v1")
    if preferred not in RUNTIME_FEATURES:
        preferred = "v1"
    for name in [preferred, *(n for n in RUNTIME_FEATURES if n != preferred)]:
        if registry.is_enabled(RUNTIME_FEATURES[name]):
            return name
    return preferred


def active_binary(preferred: str = "") -> tuple[str, Path]:
    """(系列, 実行ファイル)。使えない場合は CodeAgentError を投げる。"""
    runtime = active_runtime(preferred)
    feature_id = RUNTIME_FEATURES[runtime]
    if not registry.is_enabled(feature_id):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    binary = registry.executable(feature_id)
    if binary is None:
        raise CodeAgentError("OpenCodeを利用できません")
    return runtime, binary


def runtime_states() -> dict[str, dict]:
    """系列ごとのアドオン状態。画面の切り替え UI が導入済みかを判断するために使う。"""
    return {name: registry.status(feature_id) for name, feature_id in RUNTIME_FEATURES.items()}


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
    if str(settings.get("runtime") or "") not in RUNTIME_FEATURES:
        raise ValueError("OpenCodeの系列はv1かv2を指定してください")
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


# OS の処理を止める道具。CodeDEV で制作する仕事に要らない。
#
# 2026-09-12、OpenCode が「古いサーバーが別ディレクトリを提供している」と判断して
# `kill 3080550` を実行した。3080550 は Control Deck 本体で、40 分止まった。同じ
# 利用者で走っている以上 signal を撃てること自体は塞げないので、撃つ道具を渡さない。
#
# 命令の先頭だけを見ても足りない。事故のときの命令は
# `kill 3080550 2>/dev/null; sleep 1; cd ... && ...` という一続きで、区切りの後ろに
# 隠れる形もある。そこで区切り文字を明示して、その直後に来る場合も拾う。
#
# 単純に `*<名前>*` としないのは、語を含むだけの path（`src/myservice`）まで拒んで
# しまうためである。区切りを挟むことで「命令として置かれたもの」に絞る。
#
# 副作用として `git commit -m "kill switch の修正"` のように文中に空白付きで含む
# ものも拒まれる。言い換えれば通るので、取りこぼすより良いと判断する。
_FORBIDDEN_COMMANDS = (
    "kill", "pkill", "killall", "skill", "fuser",
    "systemctl", "service", "shutdown", "reboot", "halt",
)
# 先頭（区切り無し）と、区切りの直後。
_COMMAND_SEPARATORS = ("", " ", ";", "&", "|", "(", "\t")


def _forbidden_commands() -> dict[str, str]:
    """止める道具を禁じる規則を組む。

    背後で server を起こしたいなら `timeout` を付けて前面で走らせる。後始末の
    ために他人の process を探して撃つ、という形にしない。
    """
    rules: dict[str, str] = {}
    for name in _FORBIDDEN_COMMANDS:
        for separator in _COMMAND_SEPARATORS:
            head = f"*{separator}" if separator else ""
            rules[f"{head}{name}"] = "deny"
            rules[f"{head}{name} *"] = "deny"
    return rules


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


def _window_of(instance: dict) -> int:
    """そのモデル設定で 1 request が使える窓の大きさ。

    鍵の名前はランタイムで違う（llama.cpp は ctx_size、Lucebox は max_ctx）。
    共有KVを切っている llama.cpp は ctx_size を slot 数へ固定配分するので、
    1 request の取り分はその割り算のあとになる。総量をそのまま宣言すると、
    宣言した窓に届く前に転送先が溢れて、後追いの圧縮に戻る。
    """
    context = 0
    for key in ("ctx_size", "max_ctx"):
        context = int(instance.get(key) or 0)
        if context > 0:
            break
    if context <= 0:
        return 0
    if not instance.get("kv_unified", True):
        context //= max(1, int(instance.get("n_parallel") or 1))
    return context


def _model_limits(model: str) -> dict[str, int] | None:
    """そのモデルの窓の大きさを OpenCode へ伝える。

    伝えないと自動圧縮が先回りでは一度も動かない。OpenCode の判定は
    `limit.context === 0` をそのまま「窓を知らない」と読んで諦める作りで、
    残るのは溢れてから畳む後追いの経路だけになる。実測（2026-09-12、919
    session）: 圧縮 38 件のうち 33 件が `overflow` 付き＝モデルが断ってからの
    後追いで、先回りは 0 件だった。そのたびに 1 往復を捨てていた。

    `model` は gateway の仮想モデル `auto` であることが多い。これは実在の
    instance ではないので alias では引けず、以前の実装はここで None を返して
    いた。転送先は request ごとに gateway が決める（起動中を優先、無ければ
    登録順）ので、候補のうち最も狭い窓に合わせる。広いほうに合わせると、狭い
    モデルへ回った回だけ溢れが戻る。

    input も併せて宣言する。OpenCode は input がある場合だけ compaction.reserved
    を見る作りで、context だけだと予備は「返答の上限」（既定 32,000）に固定される。
    """
    try:
        from app.models_mgmt import local_llm

        match = next(
            (i for i in local_llm.list_instances() if str(i.get("alias") or "") == model), None)
        if match is not None:
            context = _window_of(match)
        else:
            # gateway.resolve_instance と同じ候補集合（embedding/reranker は除く）。
            windows = [w for w in map(_window_of, local_llm.llm_instances()) if w > 0]
            context = min(windows) if windows else 0
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
    runtime: str = "v1",
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
        "permission": {
            "external_directory": _allowed_directories(),
            "bash": _forbidden_commands(),
        },
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
    if runtime == "v2":
        return _write_v2_config(safe_job_id, payload)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


# ---- OpenCode v2 向けの読み替え ----
#
# v2 は設定の形が変わっている（provider→providers、permission→permissions、
# agent→agents、npm→package、options→settings/headers、modalities→capabilities）。
# 上の payload は v1 の形のままにして、ここで機械的に読み替える。測って書いた
# コメント（窓の大きさ、圧縮の発火点、重い道具の内訳）を二重に持たないため。
#
# v2 は OPENCODE_CONFIG / OPENCODE_CONFIG_DIR / OPENCODE_CONFIG_CONTENT の
# どれも見ない（2.0.2 で実測）。job ごとに config directory を作り、
# XDG_CONFIG_HOME でそこへ向ける。利用者自身の ~/.config/opencode は読まれない。


def _v2_permissions(permission: dict) -> list[dict]:
    """v1 の tool 別 map を、v2 の (action, resource, effect) の並びへ読み替える。

    v2 は「後に一致した規則が勝つ」ので、広い規則から順に並べる。bash は shell へ
    名前が変わった。tool 名そのものを action に置ける（v2 自身が v1 の
    トップレベル `tools` をこの形へ移行する）ので、道具の出し入れもここで書ける。
    """
    renamed = {"bash": "shell"}
    rules: list[dict] = []
    for tool, entries in permission.items():
        action = renamed.get(tool, tool)
        for resource, effect in entries.items():
            rules.append({"action": action, "resource": resource, "effect": effect})
    return rules


def _v2_agents(agent: dict) -> dict:
    """v1 の agent 定義を v2 へ読み替える。

    v2 の agent は `tools` を持たない。道具の出し入れは agent ごとの permissions
    へ `{action: "<道具名のパターン>", resource: "*", effect: deny}` で書く。これは
    v2 自身が v1 のトップレベル `tools: {pattern: false}` を移行する先と同じ形である。

    実機で確認したところ、deny した道具は主エージェントの一覧そのものから消えた
    （Add-on の道具 7 個だけが見え、media_scene_* / media_job_* / sonic_* は出ない）。
    v1 の `tools: false` と同じ効きで、文脈の節約（実測 26,692 → 5,458 トークン）も
    そのまま残る。
    """
    agents: dict[str, Any] = {}
    for name, spec in agent.items():
        converted = {key: value for key, value in spec.items() if key != "tools"}
        tools = spec.get("tools") or {}
        rules = [
            {"action": pattern, "resource": "*", "effect": "allow" if allowed else "deny"}
            for pattern, allowed in tools.items()
        ]
        if rules:
            converted["permissions"] = rules
        agents[name] = converted
    return agents


# 道具の結果 1 件に許す長さ。v2 の組み込み上限で、v1 には対応する設定が無い。
#
# v2 の既定は max_lines 2,000 / max_bytes 51,200。51,200 バイトは約 14,600 トークンで、
# 窓 131,072 なら 1 件で 11% を使う。実測（262,142 トークンで打ち止めた会話）では
# 道具の結果が 48.1% を占め、内訳は bash 178k 文字・read 135k・edit 95k だった。
# 既定のままでも数件で窓が埋まる。
#
# そこで窓に対する割合で決める。入力窓の 1/16 を上限とし、v2 の既定を超えない。
# 狭いモデルへ回っても 1 件が窓を食い尽くさず、広いモデルでは既定どおりになる。
#
# 削っても情報は失われない。v2 は全文を data directory の tool-output/ へ落とし、
# その directory を読む権限を既定で開けている（実測）。必要な行は agent が grep で
# 拾える。ControlDeck が MCP bridge で手作りした形（RESULT_INLINE_LIMIT）と同じ。
TOOL_OUTPUT_DEFAULT_BYTES = 51_200
TOOL_OUTPUT_DEFAULT_LINES = 2_000
TOOL_OUTPUT_MIN_BYTES = 8_000
TOOL_OUTPUT_WINDOW_SHARE = 16
# 実測: 8,000 文字 ≒ 2,300 トークン（agent-notes.md の RESULT_INLINE_LIMIT の根拠）。
BYTES_PER_TOKEN = 3.5


def _v2_tool_output(limit: dict | None) -> dict:
    """1 件の道具の結果に許す長さ。窓が分からなければ v2 の既定に任せる。"""
    tokens = int((limit or {}).get("input") or 0)
    if tokens <= 0:
        return {"max_lines": TOOL_OUTPUT_DEFAULT_LINES, "max_bytes": TOOL_OUTPUT_DEFAULT_BYTES}
    share = int(tokens * BYTES_PER_TOKEN) // TOOL_OUTPUT_WINDOW_SHARE
    max_bytes = max(TOOL_OUTPUT_MIN_BYTES, min(TOOL_OUTPUT_DEFAULT_BYTES, share))
    return {"max_lines": TOOL_OUTPUT_DEFAULT_LINES, "max_bytes": max_bytes}


def _v2_payload(payload: dict) -> dict:
    """v1 形の runtime config を v2 形へ読み替える。"""
    provider = payload["provider"]["controldeck"]
    options = dict(provider.get("options") or {})
    headers = dict(options.pop("headers", {}) or {})
    models: dict[str, Any] = {}
    limit: dict | None = None
    for model_id, spec in provider["models"].items():
        converted = {key: value for key, value in spec.items()
                     if key not in ("attachment", "modalities")}
        modalities = spec.get("modalities") or {}
        # v2 は attachment / modalities を capabilities へ統合した。tools を明示
        # しないと道具を渡さない転送先とみなされうるので、併せて宣言する。
        converted["capabilities"] = {
            "tools": True,
            "input": list(modalities.get("input") or ["text"]),
            "output": list(modalities.get("output") or ["text"]),
        }
        models[model_id] = converted
        limit = limit or spec.get("limit")
    compaction = dict(payload.get("compaction") or {})
    v2: dict[str, Any] = {
        "$schema": payload["$schema"],
        "model": payload["model"],
        "providers": {
            "controldeck": {
                "name": provider["name"],
                # AI SDK の npm ではなく v2 同梱の OpenAI 互換 provider を使う。
                # 実行のたびに npm を取りに行かせない。
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": options,
                # headers は settings の中では送られない（2.0.2 で実測）。
                # provider 直下に置く必要がある。
                **({"headers": headers} if headers else {}),
                "models": models,
            }
        },
        # v2 は reserved を buffer へ改名し、prune は警告付きで無視する
        # （完了した道具の出力を捨てる剪定は v2 では設定項目ではない）。
        "compaction": {"auto": True, "buffer": int(compaction.get("reserved") or 0)},
        "permissions": _v2_permissions(payload.get("permission") or {}),
        "agents": _v2_agents(payload.get("agent") or {}),
        "tool_output": _v2_tool_output(limit),
        # 編集のあとに整形を通す。差分が整形だけで膨らむのを防ぎ、
        # lint との往復を 1 往復ぶん減らす。v1 に対応する設定は無い。
        "formatter": True,
    }
    # skills は paths/urls をまとめた1本の配列になった。
    skills = (payload.get("skills") or {}).get("paths") or []
    if skills:
        v2["skills"] = list(skills)
    if payload.get("instructions"):
        v2["instructions"] = list(payload["instructions"])
    if payload.get("mcp"):
        servers = {}
        for name, spec in payload["mcp"].items():
            converted = {key: value for key, value in spec.items()
                         if key not in ("enabled", "timeout")}
            # enabled は disabled へ反転、timeout は catalog/execution の組になった。
            converted["disabled"] = not bool(spec.get("enabled", True))
            timeout = int(spec.get("timeout") or 0)
            if timeout:
                converted["timeout"] = {"catalog": timeout, "execution": timeout}
            servers[name] = converted
        v2["mcp"] = {"servers": servers}
    return v2


# TUI 側の設定（keybind / theme / layout）。v2 は config directory の cli.json から読む。
#
# ControlDeck は job ごとに config directory を作り替えるので、そのままだと利用者が
# keybind を変えても次の起動で消える。系列をまたいで1枚だけ持ち、生成した directory
# からはそこへ symlink する。ControlDeck はこのファイルの中身を作った後は触らない。
#
# 既定は v1 の操作へ揃えてある。
#
# v1（1.18.30）と v2（2.0.2）の既定キーを突き合わせると、162 件中 16 件だけが変わって
# いた。そのうち「v2 が割り当てを外した／ずらした」ものを v1 の値へ戻す。plan への
# 切替（agent.cycle）が tab でなくなったのが実用上いちばん響く。
#
# tab は v2 で prompt.autocomplete.complete も使うが、この衝突は無害である。
# autocomplete 一群は候補が出ている間だけ効く文脈限定の割り当てで（next=down,
# prev=up, select=return と、通常操作と重なる key を並べていることから分かる）、
# v1 でも tab は agent_cycle と diff_switch_focus が共有していた。left / right /
# home / end / space も v1 で同じ共存をしていた組み合わせをそのまま戻している。
#
# 戻さないものが 2 つある。v2 が新機能へ割り当て直したキーで、戻すと新機能が潰れる:
#   theme.switch        <leader>t     → v2 では terminal.toggle
#   session.child.first <leader>down  → v2 では terminal.select
# この 2 つは v2 の割り当てのままにする（theme は command palette から選べる）。
CLI_CONFIG_SEED = {
    "keybinds": {
        # plan ⇔ build の切替。v1 と同じく tab / shift+tab。
        "agent.cycle": "tab",
        "agent.cycle.reverse": "shift+tab",
        # v2 が割り当てを外した diff viewer の操作。
        "diff.collapse": "left",
        "diff.expand": "right",
        "diff.expand_all": "E",
        "diff.switch_focus": "tab",
        "diff.toggle": "enter,space",
        # 同じく v2 が外した入力欄の先頭／末尾移動。
        "input.buffer.end": "end",
        "input.buffer.home": "home",
    }
}


def cli_config_path() -> Path:
    """利用者が編集する TUI 設定。無ければ既定で1度だけ作る。"""
    path = _integration_dir() / "cli.json"
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(CLI_CONFIG_SEED, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    return path


def _link_cli_config(config_dir: Path) -> None:
    """生成した config directory から、共有の cli.json を見えるようにする。

    TUI 自身が設定画面から書き換えて symlink を実体ファイルへ置き換えた場合は、
    そちらを尊重して触らない（利用者がその session で変えた結果である）。
    """
    link = config_dir / "cli.json"
    try:
        shared = cli_config_path()
        if link.is_symlink():
            if link.readlink() == shared:
                return
            link.unlink()
        elif link.exists():
            return
        link.symlink_to(shared)
    except OSError:  # noqa: BLE001 - 設定の都合で session を止めない
        logger.exception("cli.jsonを共有設定へ繋げられませんでした")


def _write_v2_config(safe_job_id: str, payload: dict) -> Path:
    """v2 用の config directory を作り、そのパス（XDG_CONFIG_HOME 相当）を返す。"""
    root = _integration_dir() / f"runtime-config-v2-{safe_job_id}"
    if not root.resolve().is_relative_to(_integration_dir()):
        raise CodeAgentError("runtime config pathがintegration directory外です")
    config_dir = root / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(config_dir, 0o700)
    path = config_dir / "opencode.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(_v2_payload(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    _link_cli_config(config_dir)
    return root


def _runtime_env(runtime: str, config: Path) -> dict[str, str]:
    """runtime config を読ませるための環境変数。"""
    return {"XDG_CONFIG_HOME": str(config)} if runtime == "v2" else {"OPENCODE_CONFIG": str(config)}


def _discard_runtime_config(runtime: str, config: Path) -> None:
    """job 別の runtime config を消す。v2 は directory なのでまとめて消す。"""
    if runtime == "v2":
        shutil.rmtree(config, ignore_errors=True)
    else:
        config.unlink(missing_ok=True)


def _run_argv(runtime: str, binary: Path, message: str, model: str, project: Path) -> list[str]:
    """`opencode run` の argv。系列ごとの差はここだけに閉じる。

    v2 は `--dir` を持たず、作業ディレクトリで対象を決める（呼び出し側が
    systemd-run の --working-directory で渡している）。既定では常駐の
    background service へ繋ぎに行くため、`--standalone` で job 専用のサーバーに
    する。常駐を増やさず、job ごとの config が確実に効く。
    """
    argv = [str(binary), "run", message, "--format", "json", "--auto",
            "--model", f"controldeck/{model}"]
    return argv + (["--standalone"] if runtime == "v2" else ["--dir", str(project)])


def _event_error(event: dict) -> str:
    """error イベントから表に出してよい要約を取る。

    v1 は error.name、v2 は error.type / error.message を返す。どちらも
    prompt や credential を含めない短い識別子だけを拾う。
    """
    error = event.get("error")
    if not isinstance(error, dict):
        return "provider error"
    for key in ("name", "type", "message"):
        value = error.get(key)
        if isinstance(value, str) and value:
            return value[:100]
    return "provider error"


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
    runtime: str = "",
) -> tuple[str, str]:
    """対話TUIセッション用のshellコマンドを組み立てる。(command, project_dir)を返す。

    ターミナル基盤（tmux）の上でopencode TUIをそのまま動かす。設定は永続config
    （Control Deck LLM provider）を都度再生成して渡す。
    """
    import shlex

    runtime, binary = active_binary(runtime)
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
        runtime=runtime,
    )
    # v2 の root command は --model を持たない（model は config で決まる）。
    # 常駐 service へ繋がないよう --standalone を付ける。TUI を閉じたら一緒に終わる。
    argv = [str(binary)]
    argv += ["--standalone"] if runtime == "v2" else ["--model", f"controldeck/{model_id}"]
    if prompt.strip():
        argv += ["--prompt", prompt.strip()]
    argv.append(str(project))
    env = " ".join(f"{key}={shlex.quote(value)}" for key, value in _runtime_env(runtime, config).items())
    command = f"{env} exec " + " ".join(shlex.quote(a) for a in argv)
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
    if not instruction.strip():
        raise CodeAgentError("指示が空です")
    runtime, binary = active_binary()
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if systemd_run is None or systemctl is None:
        raise CodeAgentError("systemd user managerを利用できません")
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
        runtime=runtime,
    )
    # LLM endpoint（llama.cpp / Lucebox instance）はondemand hookを通らないため先に起動保証する
    from app.models_mgmt import local_llm

    await local_llm.ensure_ready_by_base_url(endpoint)
    unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
    argv = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        f"--unit={unit}", f"--working-directory={project}",
        *(f"--setenv={key}={value}" for key, value in _runtime_env(runtime, runtime_config).items()),
        *_run_argv(runtime, binary, instruction[:32_000], model_id, project),
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
                reported_error = _event_error(event)
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
        await asyncio.to_thread(_discard_runtime_config, runtime, runtime_config)
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
        runtime, binary = active_binary()
        systemd_run = shutil.which("systemd-run")
        systemctl = shutil.which("systemctl")
        if systemd_run is None or systemctl is None:
            raise CodeAgentError("systemd user managerを利用できません")
        runtime_config = await asyncio.to_thread(_runtime_config,
            job.id, endpoint, model_id, owner_user_id=job.owner_user_id,
            project_id=await asyncio.to_thread(_managed_project_id, project),
            runtime=runtime,
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
            *(f"--setenv={key}={value}" for key, value in _runtime_env(runtime, runtime_config).items()),
            *_run_argv(runtime, binary, "添付されたControl Deckの指示を実行してください。",
                       model_id, project),
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
            _discard_runtime_config(runtime, runtime_config)
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
                reported_error = _event_error(event)
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
