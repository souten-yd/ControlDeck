"""LLM を使う各機能の「入れる量」を、使うモデルのコンテキスト長から決める。

2026-11-18 利用者の依頼「コンテキスト長（512K〜1M を想定）を考慮した値に、Deep Research・検索・調査・ニュース・
RAG・チャット・コード開発のすべてで自動で設定できるように。Web 等で調べて適切な量を評価してから」。
ニュースは独立した機能が無く、「最新」「ニュース」は調査（Web 検索）へ回るので、検索の量に含まれる。

根拠（公開の研究と実装の調査、2026-11-18）:
* 長いコンテキストは宣言どおりには使えない。RULER（NVIDIA）は 32K 以上を宣言する 17 モデルのうち、
  32K を実際に扱えたのは半分と報告。NoLiMa（ICML 2025）は字面の手がかりが無いと 32K で 13 モデル中 11 が
  短い入力の半分の性能に落ちると報告。Chroma の Context Rot（2025）も、課題を変えずに長さだけ伸ばすと
  不安定になると報告。→ 窓が 1M でも「根拠の本文」は窓に比例させ続けず、上限（EVIDENCE_CAP_TOKENS）で止める。
* RAG は取り出す数を増やすほど良くなるわけではない（増やした分は邪魔になる）。実務の目安は 3〜8 件＋再順位付け。
  → 件数は窓で 4〜8 件、本文の総量は窓の 8%・上限 16K トークン（毎回の発言にかかるので、読み込みの実測から）。
* コーディングエージェントの自動圧縮は窓の 80〜90% で発火する実装が多く、早め（50〜80%）を勧める声も多い。
  → 会話の圧縮は既存の compaction（auto: 窓に応じて 70〜85%、上限 256K）に任せる。ここでは引き継ぎの量だけ決める。
* 出力の分は入力と別に必ず空けておく（OUTPUT_SHARE）。

トークンと文字数の換算は CHARS_PER_TOKEN（日本語と英語の混在で控えめに 3）。既存の chat_compaction と同じ。
窓が分からないとき（外部の API・未登録）は DEFAULT_WINDOW として扱い、従来の固定値とほぼ同じになる。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

CHARS_PER_TOKEN = 3
DEFAULT_WINDOW = 131_072
# 根拠の本文の上限。窓が広くても、これより先は精度が落ち、読み込み（prefill）が長くなるだけ
EVIDENCE_CAP_TOKENS = 131_072
OUTPUT_SHARE = 0.10


@dataclass(frozen=True)
class Budget:
    window: int                 # 1 回の要求で使える窓（トークン）
    known: bool                 # 窓が分かっていたか
    output_tokens: int          # 回答に空けておく分
    # Deep Research: 根拠の本文（文字）・レポートの長さ（トークン）・集める出典の数。exhaustive は「徹底」
    research_evidence_chars: int
    research_report_tokens: int
    research_max_sources: int
    exhaustive_evidence_chars: int
    exhaustive_report_tokens: int
    # Web 検索: 回答の根拠にする検索結果の件数（抜粋だけを読む）
    search_pages: int
    # 調査（標準）: 検索の回数の上限と、1 件の抜粋の文字数
    investigate_calls: int
    snippet_chars: int
    # RAG: 取り出す件数と本文の総量（文字）
    rag_top_k: int
    rag_chars: int
    # チャットから OpenCode への引き継ぎ（文字）
    handoff_chars: int

    def as_dict(self) -> dict:
        return asdict(self)


def _clamp(value: float, low: float, high: float) -> int:
    return int(max(low, min(high, value)))


def for_window(window: int) -> Budget:
    known = window > 0
    w = window if known else DEFAULT_WINDOW
    output = _clamp(w * OUTPUT_SHARE, 4_096, 32_768)
    evidence_tokens = _clamp(w * 0.25, 8_000, EVIDENCE_CAP_TOKENS)
    # レポートは従来の既定（32,768）を下限に、広い窓では伸ばす。窓の半分を超えない
    report = min(_clamp(max(32_768, w * 0.06), 8_192, 65_536), max(1_024, w // 2))
    # 根拠の分＋出力の分が窓を超えないように（短い窓）
    evidence_tokens = min(evidence_tokens, max(4_000, w - output - report // 2 - 4_000))
    # 徹底: 根拠は 2 倍まで、ただし上限の 1.5 倍と「窓 − レポート」を超えない
    exhaustive_report = min(report * 2, 131_072, max(1_024, w // 2))
    exhaustive_evidence = min(evidence_tokens * 2, int(EVIDENCE_CAP_TOKENS * 1.5),
                              max(2_000, w - exhaustive_report - 2_000))
    # RAG は毎回の発言にかかる。R9700 の実測（qwen-flash-next、2026-11-18）で読み込みは約 3,100 トークン/秒、
    # 32K トークンで 10 秒。毎回待たせないよう 16K（約 5 秒）で止める
    rag_tokens = _clamp(w * 0.08, 2_000, 16_384)
    rag_top_k = 4 if w <= 32_768 else 6 if w <= 131_072 else 8
    search_pages = 5 if w <= 32_768 else 8 if w <= 131_072 else 10
    handoff_tokens = _clamp(w * 0.15, 4_000, 98_304)
    return Budget(
        window=w, known=known, output_tokens=output,
        research_evidence_chars=evidence_tokens * CHARS_PER_TOKEN,
        research_report_tokens=report,
        research_max_sources=24 if w <= 32_768 else 36 if w <= 131_072 else 48,
        exhaustive_evidence_chars=exhaustive_evidence * CHARS_PER_TOKEN,
        exhaustive_report_tokens=exhaustive_report,
        search_pages=search_pages,
        investigate_calls=6 if w <= 32_768 else 8 if w <= 131_072 else 12,
        snippet_chars=500 if w <= 32_768 else 700 if w <= 131_072 else 1_200,
        rag_top_k=rag_top_k,
        rag_chars=rag_tokens * CHARS_PER_TOKEN,
        handoff_chars=handoff_tokens * CHARS_PER_TOKEN,
    )


def for_model(base_url: str, model: str) -> Budget:
    """管理下のモデルなら、その 1 要求の窓から（共有 KV を切った llama.cpp は slot で割った後）。"""
    from app.models_mgmt import compaction

    try:
        window = compaction.window_for(base_url, model)
    except Exception:  # noqa: BLE001 - 窓が分からなくても機能は止めない（既定の窓で）
        window = 0
    return for_window(window)
