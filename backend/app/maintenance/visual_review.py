"""画面の見た目の関門。撮影の画像（PNG）を、画像を読めるローカルのモデルに見せて問題を JSON で返させる。

    ./deck.sh ui-review <png...> [--spec 説明] [--max-images N]

目的: OpenCode（GLM は画像を読めない）が作った画面を、Claude が目で見なくても仕組みで確かめる。
- 送り先は本番の LLM ゲートウェイ（loopback）。モデルは「画像を読めるローカルのモデル」だけ
  （`gateway.instance_sees_images` と同じ判定。cloud: は使わない。撮影に未公開の内容が写りうるため）。
  無ければ「未実施」（不合格にはしない）。要求には機密の印（x-control-deck-confidential）を付け、
  万一 cloud: に流れてもゲートウェイが断る二重の守りにする。
- 1 回に送る画像は 4 枚まで。Pillow を使わない（依存を足さない）ので縮めず、大きすぎる画像は飛ばして記録する。
- 答えは `{"issues":[{"severity","where","what","fix"}],"summary"}`。読めない・形が違う答えは失敗。
終了コード: 問題なし/注意だけ 0、high あり 1、未実施 0、失敗（答えが読めない・通信できない）2。
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

IMAGES_PER_CALL = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_IMAGES = 16
SEVERITIES = ("high", "medium", "low")
NO_MODEL_MESSAGE = "画像を読めるローカルのモデルがありません"
GATEWAY_ENV = "CONTROL_DECK_GATEWAY_URL"
DEFAULT_GATEWAY = "http://127.0.0.1:8765/api/v1/llm/v1"
NO_RELAY_MESSAGE = "この作業場はローカル LLM を借りないので、画像を読めるモデルへ届きません（借りる作業場で流れます）"
TIMEOUT_SECONDS = 600.0

CRITERIA = """見る観点（ControlDeck の UI/UX ガイドラインから）:
1. 文字が切れる・重なる・はみ出す。ボタンや枠から文字が出ている。
2. 横にはみ出す（横スクロールが出そう・右端が切れている）。
3. 押せる大きさ（タッチ対象が小さすぎる・近すぎる）。
4. ライト/ダークで読めない色（背景と文字のコントラストが低い、色だけで状態を示している）。
5. 空白の偏り（不自然に広い・詰まりすぎ、片側だけ空いている）。
6. 英語の混在など日本語の文言の問題（画面の文言は日本語。固有名詞・コード以外の英語は問題）。
7. エラー表示の分かりやすさ（原因と次の操作が簡潔に分かるか。スタックトレースを出していないか）。
赤は破壊的操作と重大エラーだけに使う。"""

FORMAT = """答えは次の JSON だけを返す（前後に文章を付けない）:
{"issues":[{"severity":"high|medium|low","where":"どの画像のどの部分","what":"何が問題か","fix":"どう直すか"}],"summary":"全体の所見を 1〜2 文"}
severity: high=使えない・読めない・操作できない、medium=見づらい・直した方がよい、low=細かい好み。
問題が無ければ issues は空の配列にする。見えないものを推測で挙げない。日本語で書く。"""


class ReviewError(Exception):
    """答えが読めない・通信できない。関門では不合格でも未実施でもなく「失敗」。"""


@dataclass
class ReviewResult:
    status: str                       # "ok" | "skipped"（未実施）
    message: str = ""
    model: str = ""
    issues: list[dict] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    skipped_images: list[str] = field(default_factory=list)   # 大きすぎる・読めない画像
    reviewed: int = 0

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i["severity"] == severity)


# ---------------------------------------------------------------- 判定（純粋な関数）
def pick_model(entries: list[dict], *, allow_load: bool = True) -> str | None:
    """画像を読めるローカルのモデルの id。cloud:・外部・auto は使わない。

    起動中のものを優先し、無ければ起動していないものも選ぶ（ゲートウェイが呼ばれたときにロードする。
    ほかの呼び出しと同じ動き。2026-11-22 利用者「アンロードしていても呼ばれたらロードして答える方式は変えない」）。
    ロードさせたくないときだけ allow_load=False（`--no-load`）。
    entries は `/v1/models` の項目（id・runtime・external・vision・loaded）。
    """
    candidates = []
    for entry in entries:
        model = str(entry.get("id") or "")
        if not model or model == "auto" or model.startswith("cloud:"):
            continue
        if entry.get("external") or str(entry.get("runtime") or "") in ("cloud", "auto"):
            continue
        if entry.get("vision") is True:
            candidates.append(entry)
    if not candidates:
        return None
    loaded = [c for c in candidates if c.get("loaded")]
    if loaded:
        return str(loaded[0]["id"])
    return str(candidates[0]["id"]) if allow_load else None


def parse_answer(text: str) -> tuple[list[dict], str]:
    """モデルの答えを (issues, summary) に検める。形が違えば ReviewError。"""
    body = (text or "").strip()
    body = re.sub(r"<think>.*?</think>", "", body, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", body, flags=re.S)
    if fenced:
        body = fenced.group(1).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ReviewError("答えに JSON がありません")
    try:
        data = json.loads(body[start:end + 1])
    except ValueError as exc:
        raise ReviewError(f"答えが JSON として読めません: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ReviewError("答えに issues の配列がありません")
    issues = []
    for raw in data["issues"]:
        if not isinstance(raw, dict):
            raise ReviewError("issues の要素がオブジェクトではありません")
        severity = str(raw.get("severity") or "").strip().lower()
        if severity not in SEVERITIES:
            raise ReviewError(f"severity が high/medium/low ではありません: {severity!r}")
        what = str(raw.get("what") or "").strip()
        if not what:
            raise ReviewError("issues の what が空です")
        issues.append({"severity": severity, "where": str(raw.get("where") or "").strip(),
                       "what": what, "fix": str(raw.get("fix") or "").strip()})
    summary = data.get("summary")
    if not isinstance(summary, str):
        raise ReviewError("答えに summary の文字列がありません")
    return issues, summary.strip()


def build_messages(names: list[str], images: list[bytes], spec: str) -> list[dict]:
    intro = ("あなたは UI の目視レビュー担当です。添付の画面の撮影を見て、見た目の問題を挙げてください。\n"
             f"画像は順に: {', '.join(f'{i + 1}={n}' for i, n in enumerate(names))}\n")
    if spec.strip():
        intro += f"画面の説明: {spec.strip()}\n"
    content: list[dict] = [{"type": "text", "text": f"{intro}\n{CRITERIA}\n\n{FORMAT}"}]
    for data in images:
        url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


def prepare_images(paths: list[Path], max_bytes: int = MAX_IMAGE_BYTES) -> tuple[list[tuple[str, bytes]], list[str]]:
    """(送る画像, 飛ばした理由)。縮めない（依存を足さない）。大きすぎる・読めない・PNG でないものは飛ばす。"""
    ready: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            skipped.append(f"{path.name}: 読めません（{exc.strerror or exc}）")
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            skipped.append(f"{path.name}: PNG ではありません")
        elif len(data) > max_bytes:
            skipped.append(f"{path.name}: 大きすぎます（{len(data) // 1024} KB > {max_bytes // 1024} KB。縮めないので飛ばしました）")
        else:
            ready.append((path.name, data))
    return ready, skipped


# ---------------------------------------------------------------- ゲートウェイとの通信
def _headers() -> dict[str, str]:
    return {"x-requested-with": "ControlDeck", "x-control-deck-client": "visual-review",
            "x-control-deck-confidential": "1"}


def _gateway() -> str:
    # 箱の中（開発の作業場）は本番のゲートウェイへ届かず、届くのは中継だけ（箱の環境の OPENAI_BASE_URL。
    # ローカル LLM を借りる作業場で入る）。2026-11-22: 作業場の関門が「ゲートウェイへ届きません」で不合格になり、
    # 画面を変えた作業を取り込めなかった。明示の CONTROL_DECK_GATEWAY_URL → 中継 → 本番の既定、の順で使う。
    explicit = os.environ.get(GATEWAY_ENV, "").strip()
    relay = os.environ.get("OPENAI_BASE_URL", "").strip() if os.environ.get("CONTROL_DECK_SANDBOX") else ""
    return (explicit or relay or DEFAULT_GATEWAY).rstrip("/")


def list_entries(client) -> list[dict]:
    """ゲートウェイのモデル一覧。vision の項目がまだ無い古いゲートウェイなら、同じ判定（instance_sees_images）を
    手元の登録から補う。"""
    response = client.get(f"{_gateway()}/models", headers=_headers())
    response.raise_for_status()
    entries = [dict(e) for e in response.json().get("data", [])]
    if any("vision" in e for e in entries):
        return entries
    try:
        from app.models_mgmt import gateway, local_llm

        by_alias = {str(i.get("alias")): i for i in local_llm.llm_instances()}
    except Exception:  # noqa: BLE001 - 登録を読めなければ、画像を読めるとは言えない（未実施になる）
        return entries
    for entry in entries:
        instance = by_alias.get(str(entry.get("id")))
        if instance is not None:
            entry["vision"] = gateway.instance_sees_images(instance)
            entry["loaded"] = bool(instance.get("loaded"))
    return entries


def ask(client, model: str, names: list[str], images: list[bytes], spec: str) -> str:
    body = {"model": model, "messages": build_messages(names, images, spec), "temperature": 0.2,
            "max_tokens": 3000, "stream": False}
    response = client.post(f"{_gateway()}/chat/completions", json=body, headers=_headers())
    if response.status_code >= 400:
        raise ReviewError(f"ゲートウェイが {response.status_code} を返しました: {response.text[:200]}")
    try:
        return str(response.json()["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ReviewError(f"ゲートウェイの応答の形が違います: {exc}") from exc


def review(paths: list[Path], spec: str = "", *, client=None, max_images: int = DEFAULT_MAX_IMAGES,
           allow_load: bool = True) -> ReviewResult:
    """画像を見させる。モデルが無ければ未実施。答えが読めなければ ReviewError。"""
    import httpx

    if (client is None and os.environ.get("CONTROL_DECK_SANDBOX") and not os.environ.get(GATEWAY_ENV)
            and not os.environ.get("OPENAI_BASE_URL")):
        # ローカル LLM を借りない作業場: 画像を読めるモデルへ届く口が無い（不合格にはしない）
        return ReviewResult("skipped", NO_RELAY_MESSAGE)
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        try:
            model = pick_model(list_entries(client), allow_load=allow_load)
        except httpx.HTTPError as exc:
            raise ReviewError(f"ゲートウェイへ届きません: {exc}") from exc
        if model is None:
            return ReviewResult("skipped", NO_MODEL_MESSAGE)
        ready, skipped = prepare_images(paths)
        if len(ready) > max_images:
            skipped += [f"{n}: 上限 {max_images} 枚を超えたので見ていません" for n, _ in ready[max_images:]]
            ready = ready[:max_images]
        result = ReviewResult("ok", model=model, skipped_images=skipped, reviewed=len(ready))
        for i in range(0, len(ready), IMAGES_PER_CALL):
            batch = ready[i:i + IMAGES_PER_CALL]
            try:
                text = ask(client, model, [n for n, _ in batch], [d for _, d in batch], spec)
            except httpx.HTTPError as exc:
                raise ReviewError(f"ゲートウェイへ届きません: {exc}") from exc
            issues, summary = parse_answer(text)
            for issue in issues:
                issue["images"] = [n for n, _ in batch]
            result.issues += issues
            result.summaries.append(summary)
        return result
    finally:
        if owned:
            client.close()


def render(result: ReviewResult) -> str:
    if result.status == "skipped":
        return f"[未実施] {result.message}"
    lines = [f"モデル: {result.model}（{result.reviewed} 枚を見ました）"]
    for sev in SEVERITIES:
        for issue in (i for i in result.issues if i["severity"] == sev):
            lines.append(f"[{sev}] {issue['where']}: {issue['what']} → {issue['fix']}")
    if not result.issues:
        lines.append("問題は挙がりませんでした")
    lines += [f"所見: {s}" for s in result.summaries if s]
    lines += [f"飛ばした画像: {s}" for s in result.skipped_images]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    paths: list[Path] = []
    spec, max_images, allow_load = "", DEFAULT_MAX_IMAGES, True
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--spec" and args:
            spec = args.pop(0)
        elif arg == "--max-images" and args and args[0].isdigit():
            max_images = int(args.pop(0))
        elif arg == "--no-load":
            allow_load = False
        elif arg.startswith("--"):
            print("使い方: ./deck.sh ui-review <png...> [--spec 説明] [--max-images N] [--no-load]", file=sys.stderr)
            return 2
        else:
            paths.append(Path(arg))
    if not paths:
        print("使い方: ./deck.sh ui-review <png...> [--spec 説明] [--max-images N] [--no-load]", file=sys.stderr)
        return 2
    try:
        result = review(paths, spec, max_images=max_images, allow_load=allow_load)
    except ReviewError as exc:
        print(f"[失敗] {exc}", file=sys.stderr)
        return 2
    print(render(result))
    return 1 if result.count("high") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
