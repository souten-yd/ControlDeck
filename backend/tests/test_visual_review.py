"""画面の見た目の関門（app.maintenance.visual_review と dev-check の「画面の見た目」）。

目的: (1) モデルは「画像を読めるローカルのモデル」だけが選ばれ、cloud: や外部は選ばれず、無ければ未実施になる
(2) モデルの答えを形で検め、読めない答えは失敗にする (3) dev-check の項目が、high で不合格・medium で注意・
撮影なしで不合格・frontend を変えていなければ未実施・モデルが無ければ未実施になる。LLM はモックで、外へは出ない。
根拠: 2026-11-22 利用者の依頼。OpenCode（GLM は画像を読めない）が作った画面を、Claude が目で見なくても
仕組みで確かめる。画面の撮影には未公開の内容が写りうるので外部のモデルへ送ってはならない。
"""
import json

import httpx
import pytest

from app.maintenance import dev_check as dc
from app.maintenance import visual_review as vr

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
GOOD = {"issues": [{"severity": "medium", "where": "上部", "what": "文字が小さい", "fix": "14px に"}], "summary": "概ね良い"}


def _client(models, answers, seen=None):
    """/models と /chat/completions を返すモック。answers は順に返す答えの文字列。"""
    queue = list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": models})
        body = json.loads(request.content)
        if seen is not None:
            seen.append((body, dict(request.headers)))
        return httpx.Response(200, json={"choices": [{"message": {"content": queue.pop(0)}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _png(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.png"
        p.write_bytes(PNG)
        paths.append(p)
    return paths


def test_pick_model_only_local_vision_and_never_cloud():
    entries = [
        {"id": "auto", "runtime": "auto"},
        {"id": "cloud:big", "runtime": "cloud", "external": True, "vision": True},
        {"id": "text-only", "runtime": "llama.cpp", "vision": False},
        {"id": "vl-a", "runtime": "llama.cpp", "vision": True, "loaded": False},
        {"id": "vl-b", "runtime": "strata", "vision": True, "loaded": True},
    ]
    assert vr.pick_model(entries) == "vl-b"          # 起動中を優先
    # 起動していないものも選ぶ（呼ばれたらロードして答える、ほかの呼び出しと同じ動き）。--no-load のときだけ選ばない
    assert vr.pick_model(entries[:-1]) == "vl-a"
    assert vr.pick_model(entries[:-1], allow_load=False) is None
    assert vr.pick_model(entries[:3]) is None         # cloud と画像を読めないものだけ
    assert vr.pick_model([{"id": "cloud:x", "vision": True}]) is None


def test_review_skips_without_vision_model(tmp_path):
    client = _client([{"id": "t", "runtime": "llama.cpp", "vision": False}], [])
    result = vr.review(_png(tmp_path, 1), client=client)
    assert result.status == "skipped" and result.message == vr.NO_MODEL_MESSAGE


def test_review_sends_max_four_images_per_call_with_confidential_mark(tmp_path):
    seen: list = []
    client = _client([{"id": "vl", "runtime": "llama.cpp", "vision": True, "loaded": True}], [json.dumps(GOOD)] * 2, seen)
    result = vr.review(_png(tmp_path, 5), "設定画面", client=client)
    assert result.status == "ok" and result.reviewed == 5 and result.count("medium") == 2
    counts = [sum(1 for p in b["messages"][0]["content"] if p["type"] == "image_url") for b, _ in seen]
    assert counts == [4, 1]
    assert all(b["model"] == "vl" for b, _ in seen)
    assert seen[0][1]["x-control-deck-confidential"] == "1"
    text = seen[0][0]["messages"][0]["content"][0]["text"]
    assert "設定画面" in text and "はみ出す" in text and "押せる大きさ" in text and "英語" in text


def test_prepare_images_skips_too_large_and_non_png(tmp_path):
    big, bad = tmp_path / "big.png", tmp_path / "bad.png"
    big.write_bytes(PNG + b"y" * 100)
    bad.write_bytes(b"GIF89a")
    ready, skipped = vr.prepare_images([big, bad, tmp_path / "none.png"], max_bytes=50)
    assert ready == [] and len(skipped) == 3
    assert "大きすぎます" in skipped[0] and "PNG ではありません" in skipped[1] and "読めません" in skipped[2]


@pytest.mark.parametrize("text", [
    "JSON ではない", '{"issues": "x", "summary": ""}', '{"issues": [], "summary": 3}',
    '{"issues": [{"severity": "critical", "what": "a"}], "summary": ""}',
    '{"issues": [{"severity": "high", "what": ""}], "summary": ""}', '{"issues": [1], "summary": ""}',
])
def test_parse_answer_rejects_malformed(text):
    with pytest.raises(vr.ReviewError):
        vr.parse_answer(text)


def test_parse_answer_accepts_fenced_and_thinking():
    issues, summary = vr.parse_answer("<think>x</think>```json\n" + json.dumps(GOOD) + "\n```")
    assert issues[0]["severity"] == "medium" and summary == "概ね良い"
    assert vr.parse_answer('{"issues": [], "summary": "問題なし"}') == ([], "問題なし")


def test_review_unreadable_answer_is_failure(tmp_path):
    client = _client([{"id": "vl", "runtime": "llama.cpp", "vision": True, "loaded": True}], ["読めない"])
    with pytest.raises(vr.ReviewError):
        vr.review(_png(tmp_path, 1), client=client)


# ---------------------------------------------------------------- dev-check の項目
def _res(*severities):
    r = vr.ReviewResult("ok", model="vl", reviewed=2)
    r.issues = [{"severity": s, "where": "w", "what": "w-what", "fix": "w-fix"} for s in severities]
    return r


FE = ["frontend/src/App.tsx"]


def test_check_visual_not_run_for_backend_only_change():
    r = dc.check_visual(["backend/app/x.py"], [], 0.0, None)
    assert r.status == dc.SKIP


def test_check_visual_fails_without_screenshots():
    r = dc.check_visual(FE, [], 100.0, None)
    assert r.status == dc.NG and "撮影がありません（E2E で撮ってください）" in r.message
    stale = dc.check_visual(FE, [("frontend/test-results/a.png", 50.0)], 100.0, None)
    assert stale.status == dc.NG and "古い" in stale.message


def test_check_visual_high_fails_medium_warns():
    shots = [("frontend/test-results/a.png", 200.0)]
    assert dc.check_visual(FE, shots, 100.0, _res("high", "low")).status == dc.NG
    warn = dc.check_visual(FE, shots, 100.0, _res("medium"))
    assert warn.status == dc.OK and "注意" in warn.message
    assert dc.check_visual(FE, shots, 100.0, _res("low")).status == dc.OK


def test_check_visual_no_model_is_skipped_not_failed():
    shots = [("frontend/test-results/a.png", 200.0)]
    r = dc.check_visual(FE, shots, 100.0, vr.ReviewResult("skipped", vr.NO_MODEL_MESSAGE))
    assert r.status == dc.SKIP and "画像を読めるローカルのモデルがありません" in r.message


def test_check_visual_skip_option_and_error():
    shots = [("frontend/test-results/a.png", 200.0)]
    assert dc.check_visual(FE, [], 100.0, None, skip=True).status == dc.SKIP
    assert dc.check_visual(FE, shots, 100.0, None, error="答えが読めません").status == dc.NG
    text, passed = dc.render_report([dc.check_visual(FE, [], 100.0, None, skip=True)])
    assert "--skip-visual" in text and passed
