"""HuggingFace からの GGUF 取得。

役割:
- repo 検索と、repo 内の GGUF 一覧（量子化バリアント）の提示
- 選んだファイルをモデルライブラリ（libraries.py）へダウンロード
- 完了後にランタイムへ登録（llama.cpp の instance・Sly-Radiance のモデル。任意）

huggingface_hub には依存せず httpx だけで扱う。既存の role_presets の
ダウンロード処理を一般化したもの。

安全（docs/PLAN-model-catalog.md「run3 共通バックエンド API」）:
- 通信は `web_collector/egress.py` の guarded transport で出す（名前解決 → 全アドレス検査 →
  検査済み IP へ接続。プロキシ環境変数も使わない）。redirect は自動で追わず、各段の URL を
  https か確認してから追う。metadata の問い合わせ・取得・再開の全通信が同じ道。
- 大きさ（expected_bytes）は呼び出し側が推測で渡さず、fetch_sizes=True で Job の開始時に
  HF の API から読む。目録の行に大きさの推測値は書かない。
- 途中のファイル（.part）は「停止」（pause）・サーバー停止・失敗では残し、明示的な
  「取り消し」（cancel）だけで消す（Range で続きから取れる。docs/PLAN-model-catalog.md「決めた意味」）。
"""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

import httpx

from app.security.offline import ensure_external_allowed

HF_API = "https://huggingface.co/api"
HF_RESOLVE = "https://huggingface.co"
# 分割GGUF（model-00001-of-00003.gguf）。1ファイルでは使えないのでまとめて扱う。
_SHARD_RE = re.compile(r"^(?P<stem>.+?)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)
# ダウンロード後に残しておく余白。書き込み中の他プロセスやメタデータ用。
_FREE_SPACE_RESERVE = 1 * 1024 * 1024 * 1024
# redirect を追う回数の上限（HF は CDN へ 1〜2 回で着く）。
_MAX_REDIRECTS = 5
# 偽・悪意のあるファイル名（パスの逃げ出し・URL への細工）を断る。
# `?` `#` `%` は URL へ補間すると要求先・保存名が変わる（クエリ・断片・エスケープ）。
_BAD_PATH_RE = re.compile(r"(^/|\\|\.\.|[?#%]|[\x00-\x1f\x7f])")


class HfError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    """gated repo 用のトークンがあれば付ける。無ければ匿名。"""
    from app.models_mgmt import gateway  # 設定の置き場を共用する

    token = ""
    try:
        token = str(gateway._load().get("hf_token") or "")
        if token:
            from app.security.crypto import decrypt_text

            token = decrypt_text(token)
    except Exception:  # noqa: BLE001 - トークン不備で匿名アクセスまで止めない
        token = ""
    headers = {"User-Agent": "ControlDeck/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def set_token(token: str) -> None:
    """gated repo 用トークンを保存する（空で解除）。"""
    from app.models_mgmt import gateway
    from app.security.crypto import encrypt_text

    data = gateway._load()
    if token.strip():
        data["hf_token"] = encrypt_text(token.strip())
    else:
        data.pop("hf_token", None)
    gateway._save(data)


def has_token() -> bool:
    from app.models_mgmt import gateway

    return bool(gateway._load().get("hf_token"))


def _client(timeout: float | None = 30.0, *, with_auth: bool = True) -> httpx.AsyncClient:
    """外部への出口を通した httpx クライアント。

    guarded transport（web_collector/egress.py）が接続の前に名前解決して全アドレスを検査し、
    検査済みの IP だけに接続する（DNS rebinding・内部アドレス・プロキシ経由の回避を防ぐ）。
    redirect は自動で追わない（各段を _open_stream で検査してから追う）。

    with_auth=False は、取得（redirect を自分で追う）用。トークンをクライアントの既定
    ヘッダにすると、CDN など別のホストへ移る redirect にも付いて漏れる。取得では
    `_open_stream` が最初の HF の要求にだけ付け、別ホストへ移ったら落とす。
    """
    from app.web_collector.egress import guarded_transport

    connect_only = httpx.Timeout(None if timeout is None else timeout, connect=20.0)
    return httpx.AsyncClient(
        transport=guarded_transport(), timeout=connect_only,
        follow_redirects=False, trust_env=False,
        headers=_headers() if with_auth else {"User-Agent": "ControlDeck/1.0"},
    )


async def search_models(query: str, limit: int = 20) -> list[dict]:
    """GGUF を持つ repo を検索する。"""
    ensure_external_allowed("Hugging Face のモデル検索")
    params = {"search": query, "filter": "gguf", "limit": str(limit),
              "sort": "downloads", "direction": "-1"}
    try:
        async with _client(20) as client:
            response = await client.get(f"{HF_API}/models", params=params)
    except httpx.HTTPError as exc:
        raise HfError(f"HuggingFace 検索に失敗しました: {exc}") from exc
    if response.status_code >= 400:
        raise HfError(f"HuggingFace 検索エラー ({response.status_code})")
    return [{
        "repo": item.get("id", ""),
        "downloads": item.get("downloads", 0),
        "likes": item.get("likes", 0),
        "gated": bool(item.get("gated")),
    } for item in response.json()]


async def _repo_tree(repo: str, revision: str) -> list[dict]:
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo or ""):
        raise HfError("repo は owner/name の形式で指定してください")
    if not re.fullmatch(r"[\w.\-]{1,100}", revision or ""):
        raise HfError("revision が不正です")
    ensure_external_allowed("Hugging Face のファイル一覧")
    try:
        async with _client(30) as client:
            response = await client.get(
                f"{HF_API}/models/{repo}/tree/{revision}", params={"recursive": "1"},
            )
    except httpx.HTTPError as exc:
        raise HfError(f"ファイル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HfError("認証が必要な repo です。HuggingFace トークンを設定してください")
    if response.status_code == 404:
        raise HfError("repo が見つかりません")
    if response.status_code >= 400:
        raise HfError(f"ファイル一覧の取得エラー ({response.status_code})")

    return response.json()


async def list_model_files(repo: str, revision: str = "main") -> list[dict]:
    """repo の最上位のファイル（path・size）。safetensors 形式のモデルを丸ごと取るときの一覧。

    サブフォルダは返さない（取得は同名のファイルを 1 か所へ置くため、入れ子は扱わない）。
    """
    return [{"path": str(e["path"]), "size": int(e.get("size") or 0)}
            for e in await _repo_tree(repo, revision)
            if e.get("type") == "file" and "/" not in str(e.get("path") or "/")
            and not str(e["path"]).startswith(".")]


async def list_repo_files(repo: str, revision: str = "main") -> list[dict]:
    """repo 内の GGUF を量子化バリアントとして並べる。

    分割GGUF は 1 グループにまとめ、合計サイズと必要な全ファイルを返す
    （1つだけ落としても使えないため）。
    """
    entries = await _repo_tree(repo, revision)

    groups: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") != "file":
            continue
        path = str(entry.get("path") or "")
        if not path.lower().endswith(".gguf"):
            continue
        size = int(entry.get("size") or 0)
        name = path.rsplit("/", 1)[-1]
        shard = _SHARD_RE.match(name)
        key = shard.group("stem") if shard else name
        group = groups.setdefault(key, {
            "name": key, "files": [], "size": 0,
            "sharded": bool(shard), "shard_total": int(shard.group("total")) if shard else 1,
        })
        group["files"].append(path)
        group["size"] += size
    for group in groups.values():
        group["files"].sort()
        group["complete"] = len(group["files"]) == group["shard_total"]
    return sorted(groups.values(), key=lambda g: g["name"].lower())


def _quantization_matches(path: str, quantization: str,
                          exact_folders: frozenset[str] | None = None) -> bool:
    """量子化 id がパス（サブフォルダ名またはファイル名）に含まれるかを区切り付きで判定する。

    `Q2_0` が `IQ2_0` や `Q2_00` に部分一致しないように、`-` `_` `.` `/` を境界として扱う。
    Unsloth のサブフォルダ（UD-Q4_K_XL/…）と Maya の tier（Maya-M/…）の両方に効く。

    `exact_folders` は同じ repo に在るフォルダ名の集合。選択した量子化と**正確に一致する
    フォルダが repo に在るときは、そこだけを採用する**（Maya-M を選んだときに
    Maya-M-Derisked のような別 variant まで「部分一致」で拾わないため）。正確なフォルダが
    無い場合だけ、最上位に直接置かれた GGUF のファイル名を区切り付きで照合する。
    """
    quant = (quantization or "").strip().strip("/").casefold()
    if not quant or "/" in quant or "\\" in quant or quant.startswith("."):
        return False
    segments = [(part.casefold(), part) for part in path.split("/")]
    folders = {folded for folded, _ in segments[:-1]}
    if quant in folders:
        return True                          # 正確な量子化フォルダ（最も確実）
    if exact_folders is not None and quant in exact_folders:
        return False                         # 別 variant の部分一致を拾わない
    pattern = re.compile(r"(?:^|[-_./])" + re.escape(quant) + r"(?=$|[-_./])", re.I)
    for _folded, raw in segments[:-1]:
        if pattern.search(raw):
            return True
    return bool(pattern.search(segments[-1][1]))   # 最上位に直接置かれた GGUF


async def list_quantization_files(repo: str, revision: str = "main",
                                  quantization: str = "") -> list[dict]:
    """repo 内の指定した量子化の GGUF **分割全片**をサブフォルダごと列挙する。

    同じ repo に他の量子化のフォルダが並んでいても、選んだ量子化だけを返す
    （repo 全体を落とさない）。ファイル名の実在は HF の tree API で確認し、大きさも
    ここから読む（推測しない）。
    """
    if not (quantization or "").strip():
        raise HfError("量子化が指定されていません")
    entries = await _repo_tree(repo, revision)
    gguf = [item for item in entries
            if item.get("type") == "file"
            and str(item.get("path") or "").lower().endswith(".gguf")]
    # この repo にあるフォルダ名を先に集める。選択した量子化のフォルダが在ればそれだけを取る
    # （同名の別 variant を部分一致で混ぜない）。フォルダが無いときだけファイル名で照合する。
    exact_folders = frozenset(
        part.casefold()
        for item in gguf
        for part in str(item["path"]).split("/")[:-1]
    )
    files = [{"path": str(item["path"]), "size": int(item.get("size") or 0)}
             for item in gguf
             if _quantization_matches(str(item["path"]), quantization, exact_folders)]
    files.sort(key=lambda item: item["path"])
    if not files:
        raise HfError(f"量子化 {quantization} の GGUF が repo にありません")
    _ensure_complete_shards([item["path"] for item in files])
    return files


def _ensure_complete_shards(paths: list[str]) -> None:
    """分割 GGUF の全片が揃っているか確認する（1 片だけでは起動できない）。

    片数だけでは合格させない。同じフォルダ・同じ stem・同じ total の index が 1..total を
    過不足なく満たすこと（重複 index や別フォルダの混在は「揃った」と数えない）を検証する。
    """
    groups: dict[tuple[str, str, int], set[int]] = {}
    for path in paths:
        parent, _, name = path.rpartition("/")
        match = _SHARD_RE.match(name)
        if match is None:
            continue
        key = (parent, match.group("stem"), int(match.group("total")))
        groups.setdefault(key, set()).add(int(match.group("index")))
    for (parent, stem, total), indices in groups.items():
        wanted = set(range(1, total + 1))
        if indices != wanted:
            missing = sorted(wanted - indices)
            extra = sorted(indices - wanted)
            detail = f"不足 {missing}" if missing else f"重複・不正 {extra}"
            label = f"{parent}/{stem}" if parent else stem
            raise HfError(f"分割 GGUF の一部がありません: {label}（{detail}、{total} 片必要）")


def _ensure_free_space(destination: Path, expected_bytes: int) -> None:
    """入らないものを落とし始めない（途中で詰まると中途半端なファイルが残る）。"""
    try:
        free = shutil.disk_usage(destination).free
    except OSError as exc:
        raise HfError(f"保存先を確認できません: {exc}") from exc
    if expected_bytes > max(0, free - _FREE_SPACE_RESERVE):
        raise HfError(
            f"空き容量が足りません（必要 {expected_bytes / 1e9:.1f}GB / "
            f"空き {free / 1e9:.1f}GB）"
        )


def _validate_repo(repo: str) -> None:
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo or ""):
        raise HfError("repo は owner/name の形式で指定してください")


def _validate_files(files: list[str]) -> list[str]:
    """取得対象のファイル名の検証（パスの逃げ出し・URL への細工を断る）。"""
    out: list[str] = []
    for path in files:
        path = str(path)
        if not path or _BAD_PATH_RE.search(path):
            raise HfError(f"ファイル名が不正です: {path!r}")
        out.append(path)
    return out


def _same_host(a: str, b: str) -> bool:
    return (httpx.URL(a).host or "").casefold() == (httpx.URL(b).host or "").casefold()


async def _open_stream(client: httpx.AsyncClient, url: str, headers: dict[str, str]):
    """各段の redirect を検査してから追い、最後の response を (context, response) で返す。

    呼び出し側は必ず `await cm.__aexit__(...)` で閉じる（async with と同じ）。
    https 以外の redirect 先（内部のアドレス・細工した URL）はここで断つ。接続そのものは
    guarded transport が更に検査する（二重の守り）。

    トークン（Authorization）は最初の HF のホストにだけ付ける。CDN など別ホストへ移る
    redirect では落とす（gated repo のトークンが redirect 先へ漏れないように）。
    """
    first = url
    for _ in range(_MAX_REDIRECTS):
        send = dict(headers)
        if not _same_host(url, first):
            send.pop("Authorization", None)   # 別ホスト（CDN など）へトークンを送らない
        context = client.stream("GET", url, headers=send)
        response = await context.__aenter__()
        if response.status_code not in (301, 302, 303, 307, 308):
            return context, response
        location = str(response.headers.get("location", ""))
        await context.__aexit__(None, None, None)
        try:
            target = str(httpx.URL(url).join(location))
        except httpx.InvalidURL as exc:
            raise HfError(f"リダイレクト先が不正です: {location!r}") from exc
        if httpx.URL(target).scheme != "https":
            raise HfError("リダイレクト先が https ではありません")
        url = target
    raise HfError("リダイレクトが多すぎます")


async def _download_file(job, client: httpx.AsyncClient, url: str, destination: Path,
                        *, label: str, done_bytes: int, total_bytes: int) -> int:
    """1ファイルをレジューム付きで取得する。途中のファイル（.part）はここでは作るだけ。"""
    temp = destination.with_suffix(destination.suffix + ".part")
    if temp.is_symlink() or (temp.exists() and not temp.is_file()):
        raise HfError(f"途中ファイルが書き替えられています: {temp.name}")
    if destination.exists() and (destination.is_symlink() or not destination.is_file()):
        raise HfError(f"保存先がファイルではありません: {destination.name}")
    existing = temp.stat().st_size if temp.exists() else 0
    headers = _headers()                  # 最初の HF のホストにだけ付く（_open_stream が別ホストで落とす）
    if existing:
        headers["Range"] = f"bytes={existing}-"
    mode = "ab" if existing else "wb"
    context, response = await _open_stream(client, url, headers)
    try:
        if response.status_code == 416:  # 既に全部取得済み
            temp.replace(destination)
            return destination.stat().st_size
        if response.status_code >= 400:
            raise HfError(f"ダウンロード失敗 ({response.status_code}): {label}")
        if existing and response.status_code != 206:
            # レンジ非対応なら最初から取り直す
            existing, mode = 0, "wb"
        received = existing
        with temp.open(mode) as target:
            async for chunk in response.aiter_bytes(1024 * 1024):
                target.write(chunk)
                received += len(chunk)
                job.set_progress(f"ダウンロード中: {label}", done_bytes + received, total_bytes or None)
    finally:
        await context.__aexit__(None, None, None)
    temp.replace(destination)
    return received


def _keep_partial(job) -> bool:
    """Cancel のとき途中のファイル（.part）を残すか。停止・サーバー停止なら残す。

    呼び出し側は CancelledError だけこの関数で判断し、失敗（その他の例外）は無条件で
    残す（もう一度押せば Range で続きから取れる。docs/PLAN-model-catalog.md「決めた意味」）。
    """
    from app.jobs import service as jobs_service

    return bool(getattr(job, "pause_requested", False)) or jobs_service.is_shutting_down()


async def _fetch_file_sizes(repo: str, revision: str, files: list[str],
                            listed: list[dict] | None = None) -> int:
    """Job の開始時に HF の API からファイルの大きさを読む（推測値を受け取らない）。

    repo に無いファイルはここで断る（目録と repo の食い違いを途中で失敗させない）。
    `listed` は呼び出し側が既に取った一覧（同じ問い合わせを 2 度しない）。呼び出し側から
    渡されないときは recursive な tree を使う（量子化のサブフォルダにある分割片も検証できる）。
    """
    if listed is not None:
        source = listed
    else:
        source = [item for item in await _repo_tree(repo, revision) if item.get("type") == "file"]
    available = {str(item["path"]): int(item.get("size") or 0) for item in source}
    missing = [path for path in files if path not in available]
    if missing:
        raise HfError(f"repo にファイルがありません: {', '.join(missing[:3])}")
    return sum(available[path] for path in files)


def _register_llama(register: dict, saved: list[str]) -> str:
    from app.models_mgmt import llama

    alias = str(register.get("alias") or "").strip()
    if not alias:
        return ""
    patch = {"alias": alias, "model_path": saved[0]}
    for key in ("role", "endpoint_id", "port", "ctx_size", "n_parallel"):
        if register.get(key) is not None:
            patch[key] = register[key]
    llama.save_instance(alias, patch)
    return alias


def _candidate_ports(register: dict) -> list[int]:
    """登録先のポート候補。指定が無ければ全ランタイムの使用中ポートを 1 度だけ読む。

    数 GB を取り終えてからポート衝突で失敗しないように、Sly・Radiance・Strata で同じ
    候補の選び方を使う（既存 API の既定は 8100）。
    """
    if register.get("port") is not None:
        return [int(register["port"])]
    from app.models_mgmt import local_llm

    try:
        taken = local_llm.endpoint_ports()
    except Exception:  # noqa: BLE001 - 読めないときは保存時の判定に任せる
        taken = set()
    return [port for port in range(8100, 8200) if port not in taken]


def _save_with_port(save, error_type, register: dict, **fields) -> None:
    """ポートの衝突だけ次の候補へ。別名の重複など他の理由はそのまま呼び出し側へ返す。"""
    ports = _candidate_ports(register)
    for port in ports:
        try:
            save(port=port, **fields)
        except error_type as exc:
            # 明示指定のポートは再試行しない（利用者の指定を黙って変えない）。
            if register.get("port") is not None or not getattr(exc, "port_conflict", False):
                raise
            continue
        return
    raise error_type("空いているポートがありません")


def _first_shard(paths: list[str]) -> str:
    """分割 GGUF の 00001 を選ぶ（保存 API は最初の片を要求する。順序に依存しない）。"""
    for path in paths:
        match = _SHARD_RE.match(path.rsplit("/", 1)[-1])
        if match is not None and int(match.group("index")) == 1:
            return path
    return paths[0]


def _register_sly_radiance(register: dict, saved: list[str]) -> str:
    """Sly-Radiance: 取得したフォルダ（config.json + safetensors）をモデルとして登録する。

    Radiance の .rad とは登録の形が違うので、同じ「ダウンロード＋登録」でも扱いを分ける
    （adapter ごとに実際の保存先と登録先を検証する）。
    """
    from pathlib import Path as _Path

    from app.models_mgmt import sly_radiance

    alias = str(register.get("alias") or "").strip()
    if not alias:
        return ""
    folder = _Path(saved[0]).parent
    fields: dict = {}
    for key in ("context", "kv_cache", "max_num_seqs", "quantization",
                "gpu_memory_utilization", "prefill_chunk", "reasoning_effort"):
        if register.get(key) is not None:
            fields[key] = register[key]
    # prefix cache は常にオン（切る値は受け付けない。sly_radiance が検証する）
    _save_with_port(
        lambda *, port: sly_radiance.save_instance(alias, model_path=str(folder), port=port, **fields),
        sly_radiance.SlyRadianceError, register,
    )
    return alias


def _register_radiance(register: dict, saved: list[str]) -> str:
    """Radiance: 取得した .rad ファイルをそのまま既存の登録 API へ渡す（Sly と混ぜない）。"""
    from app.models_mgmt import radiance

    alias = str(register.get("alias") or "").strip()
    if not alias:
        return ""
    fields: dict = {}
    for key in ("context",):
        if register.get(key) is not None:
            fields[key] = int(register[key])
    _save_with_port(
        lambda *, port: radiance.save_instance(alias, model_path=str(saved[0]), port=port, **fields),
        radiance.RadianceError, register,
    )
    return alias


def _register_strata(register: dict, saved: list[str]) -> str:
    """Strata: 取得した分割 GGUF の最初の片を、既存の family/model 登録へ渡す。

    既存の保存 API は model_path にフォルダではなく 00001 のファイルを要求し、残りの片も
    同じ場所に揃っていることを検証する。ランタイム自身が取る既存の経路
    （model.strata.start）はそのまま残し、ここは ControlDeck が HF から取った重みを
    登録するときだけ使う（起動は既存 API に任せる）。
    """
    from app.models_mgmt import strata_catalog, strata_gateway

    alias = str(register.get("alias") or "").strip()
    if not alias:
        return ""
    family = str(register.get("family") or "")
    model = str(register.get("model") or "").casefold()
    if not family or not model:
        raise HfError("Strata の登録には family と model が必要です")
    if not strata_catalog.is_valid(family, model):
        raise HfError(f"Strata の系列・量子化が不正です: {family}/{model}")
    fields: dict = {"model_path": _first_shard(saved)}
    if register.get("context") is not None:
        fields["context"] = f"{int(register['context']) // 1024}k"
    _save_with_port(
        lambda *, port: strata_gateway.save_instance(alias, family=family, model=model,
                                                      port=port, **fields),
        strata_gateway.StrataGatewayError, register,
    )
    return alias


def _register_dispatch(register: dict, saved: list[str]) -> str:
    """取得後の登録を runtime 別の adapter へ回す。無指定は従来どおり llama.cpp（互換）。

    Maya は取得した GGUF を Maya 本体の probe から登録する担当（#21）が受ける。
    ここでは保存 API を持たないので受け付けず、経路をエラーと契約に明記する:
    `GET /api/v1/features/maya-s/catalog` で候補を選び、
    `POST /api/v1/features/maya-s/models/register-jobs` で登録する。
    """
    runtime = str(register.get("runtime") or "llama")
    if runtime == "llama":
        return _register_llama(register, saved)
    if runtime == "radiance":
        return _register_radiance(register, saved)
    if runtime == "sly-radiance":
        return _register_sly_radiance(register, saved)
    if runtime == "strata":
        return _register_strata(register, saved)
    # Maya の登録は Maya 本体の目録（probe）から行う（#21 の担当）。ここでは受け付けない。
    raise HfError(
        f"取得後の登録に対応していないランタイムです: {runtime}。"
        "Maya は /api/v1/features/maya-s/models/register-jobs から登録してください"
    )


async def download(job, repo: str, files: list[str], *, library_id: str = "",
                   revision: str = "main", expected_bytes: int = 0,
                   fetch_sizes: bool = False, quantization: str = "",
                   register: dict | None = None) -> dict:
    """GGUF をライブラリへ取得し、必要ならランタイムへ登録する。

    fetch_sizes=True のとき、大きさは Job の開始時に HF の API から読む
    （呼び出し側の expected_bytes=0 と併せる。推測値は使わない）。
    quantization が指定され files が空なら、その量子化の分割全片だけを HF の tree から
    列挙して取得する（他量子化は落とさない）。
    """
    from app.models_mgmt import libraries

    _validate_repo(repo)
    files = _validate_files([str(path) for path in files])
    if not re.fullmatch(r"[\w.\-]{1,100}", revision or ""):
        raise HfError("revision が不正です")
    listed: list[dict] | None = None
    if fetch_sizes:
        # 「ファイル全部」は目録の行（files=[]）から始まる。この分岐が空の検査より
        # 先に来るよう、空の検査は中で行う（先に置くと files=[] が必ず断られていた）。
        ensure_external_allowed("Hugging Face のモデル取得")
        if not files:
            if quantization:
                # 目録の量子化行: サブフォルダの分割全片を列挙し、選んだ量子化だけを取る。
                listed = await list_quantization_files(repo, revision, quantization)
                files = [str(item["path"]) for item in listed]
                job.log(f"量子化 {quantization} のファイル {len(files)} 件を取得します")
            else:
                # フォルダ形式のモデル（Sly など）。最上位のファイルをここで決める
                # （list_model_files と同じ決まり）。サブフォルダは扱わない。
                listed = await list_model_files(repo, revision)
                files = [str(item["path"]) for item in listed]
                if not files:
                    raise HfError("repo に取得できるファイルがありません")
                job.log(f"repo のファイル {len(files)} 件を全部取得します")
        if quantization:
            # 明示指定された files に他量子化が混ざらないようにする（repo 全体を落とさない）。
            for path in files:
                if not _quantization_matches(path, quantization):
                    raise HfError(f"量子化 {quantization} に属さないファイルです: {path}")
            _ensure_complete_shards(files)
        if listed is None:
            # 明示指定の files でも、既存ファイルの大きさ検証に使う一覧を 1 度だけ読む。
            listed = [item for item in await _repo_tree(repo, revision)
                      if item.get("type") == "file"]
        expected_bytes = await _fetch_file_sizes(repo, revision, files, listed=listed)
        job.log(f"HF の API から大きさを確認しました: 約 {expected_bytes / 1e9:.1f}GB")
    elif not files:
        raise HfError("ダウンロードするファイルがありません")
    root = (libraries.library_path(library_id, create=True) if library_id
            else libraries.default_models_dir())
    destination_dir = root / repo.replace("/", "--")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if destination_dir.is_symlink() or not destination_dir.is_dir():
        raise HfError("保存先のフォルダを準備できません")
    if expected_bytes:
        _ensure_free_space(destination_dir, expected_bytes)

    # HF の tree を読めたときは 1 ファイルずつの大きさも持つ。既存ファイルを「取得済み」と
    # して飛ばす前に、symlink・通常ファイルでないもの・大きさの違うものを弾く（途中で
    # 壊れた残り物をそのまま使わない）。
    expected_sizes = {str(item["path"]).rsplit("/", 1)[-1]: int(item.get("size") or 0)
                      for item in (listed or [])}

    saved: list[str] = []
    pending: list[tuple[str, Path]] = []
    done = 0
    for path in files:
        target = destination_dir / path.rsplit("/", 1)[-1]
        saved.append(str(target))
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise HfError(f"保存先がファイルではありません: {target.name}")
        expected_size = expected_sizes.get(target.name)
        if target.is_file():
            actual = target.stat().st_size
            if expected_size is not None and actual != expected_size:
                job.log(f"大きさが違うため取り直します: {target.name}")
                pending.append((path, target))
            else:
                done += actual
                job.log(f"既にあります: {target.name}")
        else:
            pending.append((path, target))

    # 全部揃っていれば接続もしない（再取得も余計なセッションも避ける）
    if pending:
        ensure_external_allowed("Hugging Face のモデル取得")
        # トークンは要求ごとに付ける（クライアントの既定にすると redirect 先へ漏れる）。
        async with _client(None, with_auth=False) as client:
            for path, target in pending:
                url = f"{HF_RESOLVE}/{repo}/resolve/{revision}/{path}"
                try:
                    done += await _download_file(
                        job, client, url, target, label=target.name,
                        done_bytes=done, total_bytes=expected_bytes,
                    )
                except asyncio.CancelledError:
                    # 「取り消し」（cancel）だけが途中のファイルを消す。停止・サーバー停止は
                    # 残す（Range で続きから。docs/PLAN-model-catalog.md「決めた意味」）。
                    if not _keep_partial(job):
                        target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
                    raise
                except Exception:
                    # 失敗（通信エラー・4xx/5xx）: .part を残す。もう一度押せば続きから取れる。
                    raise

    result = {"repo": repo, "files": saved, "registered": ""}
    if register and saved:
        job.set_progress("モデルを登録中", 1, 1)
        # 保存 API は同期的な設定読み書き・ポート走査を伴う。async の中で直接呼ぶと
        # event loop を止める（AGENTS.md 9）ので threadpool へ出す。
        result["registered"] = await asyncio.to_thread(_register_dispatch, register, saved)
    job.set_progress("完了", 1, 1)
    return result
