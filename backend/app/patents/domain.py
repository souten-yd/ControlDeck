"""特許の業務の中身（日本の実務を基本）: 段階・期限の目安・請求項の構成要件分解・AI 支援の指示文。

ResearchDataHub / PatentBuddy（qnaplibrary/patents.py, 2026-11-19 main b4f7039）から統合のため移植した。
期限は特許法の一般的な運用に沿った「目安」であり法的助言ではない。正式な期限は特許庁の通知・弁理士に確認する。
保存（SQLite）と検索は app.patents.store / app.knowledge が受け持つので、ここには持たない。
"""
import datetime as dt
import re

from app.knowledge import textproc

STAGES = [
    {"id": "disclosure", "label": "発明発掘・記録", "goal": "発明の課題・構成・効果・実施例を記録（発明提案書）",
     "ai": ["interview", "idea"]},
    {"id": "prior_art", "label": "先行技術調査", "goal": "検索式（キーワード・同義語・IPC/FI/Fターム）で先行文献を収集し X/Y/A 分類",
     "ai": ["search_plan", "novelty"]},
    {"id": "patentability", "label": "特許性評価", "goal": "新規性（29条1項）・進歩性（29条2項）・記載要件の見込み",
     "ai": ["novelty", "inventive_step"]},
    {"id": "strategy", "label": "権利化方針", "goal": "国内/PCT/パリ優先、早期審査、権利範囲と回避設計",
     "ai": ["strategy"]},
    {"id": "drafting", "label": "明細書・請求項作成", "goal": "独立項・従属項の階層、実施形態、図面、要約",
     "ai": ["claim_draft", "spec_outline"]},
    {"id": "filing", "label": "出願", "goal": "願書・明細書・請求の範囲・要約書・図面を提出（出願日の確定）",
     "ai": ["filing_check"]},
    {"id": "publication", "label": "公開（18か月）", "goal": "出願公開。必要なら補償金請求の警告",
     "ai": []},
    {"id": "exam_request", "label": "審査請求（3年以内）", "goal": "出願日から3年以内に審査請求",
     "ai": []},
    {"id": "office_action", "label": "拒絶理由対応", "goal": "意見書・手続補正書（応答期間内）",
     "ai": ["oa_response"]},
    {"id": "allowance", "label": "特許査定", "goal": "査定謄本送達から30日以内に第1〜3年分の特許料納付",
     "ai": []},
    {"id": "registered", "label": "設定登録・維持", "goal": "特許証、年金管理、侵害監視",
     "ai": ["infringement", "fto"]},
]
STAGE_IDS = [s["id"] for s in STAGES]

EVENT_KINDS = {
    "priority": "優先日（パリ/国内優先）", "filing": "出願日", "publication": "公開日",
    "exam_request": "審査請求日", "oa_dispatch": "拒絶理由通知 発送日", "oa_response": "意見書/補正書 提出日",
    "allowance_served": "特許査定謄本 送達日", "fee_paid": "特許料納付日", "registration": "設定登録日",
    "note": "メモ",
}

REF_CATEGORIES = {"X": "単独で新規性/進歩性を否定し得る", "Y": "他文献との組合せで進歩性を否定し得る",
                  "A": "技術水準を示す参考", "P": "優先日後公開", "E": "先願（拡大先願）"}


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _add_months(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.date(year, month, min(d.day, last))


def deadlines(events, foreign_resident=False, today=None):
    """Compute reminder deadlines from recorded events (list of {kind, date})."""
    today = today or dt.date.today()
    latest = {}
    for e in events:
        d = _date(e.get("date"))
        if d and (e["kind"] not in latest or d > latest[e["kind"]]):
            latest[e["kind"]] = d
    out = []

    def add(key, label, due, basis):
        out.append({"key": key, "label": label, "due": due.isoformat(), "basis": basis,
                    "days_left": (due - today).days})

    filing, priority = latest.get("filing"), latest.get("priority")
    base = priority or filing
    if priority and not filing:
        add("priority_filing", "優先権主張を伴う出願期限（優先日から12か月）", _add_months(priority, 12), "パリ条約4条C / 特許法41条")
    if filing:
        if base:
            add("publication", "出願公開の予定（優先日/出願日から18か月）", _add_months(base, 18), "特許法64条")
            add("pct", "PCT/パリ優先での外国出願期限（優先日から12か月）", _add_months(base, 12), "パリ条約4条C")
        if "exam_request" not in latest:
            add("exam_request", "審査請求期限（出願日から3年）", _add_months(filing, 36), "特許法48条の3")
    if "oa_dispatch" in latest and ("oa_response" not in latest or latest["oa_response"] < latest["oa_dispatch"]):
        days = 90 if foreign_resident else 60
        add("oa_response", "拒絶理由通知への応答期限（発送日から%d日）" % days,
            latest["oa_dispatch"] + dt.timedelta(days=days), "特許法50条・審査基準")
    if "allowance_served" in latest and "fee_paid" not in latest:
        add("fee", "特許料（第1〜3年分）納付期限（査定謄本送達から30日）",
            latest["allowance_served"] + dt.timedelta(days=30), "特許法108条1項")
    if "registration" in latest and filing:
        # Annual fees are counted from the filing date; years up to registration are paid at registration.
        year = 4
        while _add_months(filing, 12 * (year - 1)) <= latest["registration"] and year < 21:
            year += 1
        add("annuity", "第%d年分の特許料納付期限（目安）" % year, _add_months(filing, 12 * (year - 1)),
            "特許法108条2項（前年以前に納付。正確な期限は特許庁の通知で確認）")
    out.sort(key=lambda x: x["due"])
    return out


_SPLIT = re.compile(r"(?<=[、，,;；])\s*|(?<=と)、|\n")


def claim_elements(claim_text):
    """Heuristic decomposition of one claim into elements (構成要件 A, B, C...)."""
    text = re.sub(r"\s+", " ", claim_text or "").strip()
    if not text:
        return []
    # Japanese style: "...を備え、..." / "...であって、" preamble split
    preamble, body = "", text
    m = re.search(r"(であって、|において、|comprising:?|which comprises:?)", text)
    if m and m.start() < 200:
        preamble, body = text[:m.end()], text[m.end():]
    parts = [p.strip(" 、,;；") for p in re.split(r"(?<=[、；;])\s*", body) if p.strip(" 、,;；")]
    merged = []
    for p in parts:
        if merged and len(p) < 3:
            merged[-1] += "、" + p
        else:
            merged.append(p)
    closing = None
    if merged and re.match(r"^(を|から)(備え|有し|有する|含む|含み|なる|具備)", merged[-1]):
        closing = merged.pop()
    labels = [chr(ord("A") + i) if i < 26 else "Z%d" % i for i in range(len(merged))]
    out = []
    if preamble:
        out.append({"label": "前提", "text": preamble})
    out.extend({"label": l, "text": t} for l, t in zip(labels, merged))
    if closing:
        out.append({"label": "結び", "text": closing})
    return out


def keywords(text):
    """Very small keyword extractor: long CJK runs and ascii words, deduped, longest first."""
    text = textproc.normalize(text)
    stop = {"する", "される", "および", "または", "ための", "ことを", "であって", "において", "前記", "所定", "備える",
            "the", "and", "for", "with", "that", "said", "wherein", "comprising", "which"}
    cands = re.findall(r"[゠-ヿ一-鿿]{2,}|[a-z][a-z0-9\-]{3,}", text)
    seen, out = set(), []
    for c in sorted(cands, key=len, reverse=True):
        c = c[:12]
        if c in stop or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


LAW_TASKS = ("novelty", "inventive_step", "claim_draft", "spec_outline", "filing_check", "oa_response", "infringement", "fto", "strategy")

SYSTEM_PROMPT = ("あなたは日本の特許実務に精通したアシスタントです。特許法・特許審査基準に沿って、根拠（参照文献の番号や条文）を示しながら"
                 "具体的に回答してください。推測は推測と明記し、最終判断は弁理士の確認が必要であることを忘れないでください。"
                 "出力は Markdown。")

PROJECT_TEMPLATE = """# 案件
- 発明の名称: {title}
- 現在の段階: {stage}
- 技術分野: {field}
- 課題: {problem}
- 解決手段: {solution}
- 効果: {effect}
- 実施例: {embodiment}

# 請求項（最新版）
{claims}

# 登録済み先行文献
{refs}"""

PROMPTS = {
    "interview": {"label": "発明ヒアリング", "prompt": "発明者に追加で確認すべき質問を、課題・構成・作用・効果・実施例・変形例・数値範囲・"
                  "従来技術との差の観点で10〜15問作成してください。各質問に『なぜ必要か』を1行添えてください。"},
    "idea": {"label": "新規発明の創出", "prompt": "この発明を起点に、特許になり得る派生アイデアを7件提案してください。"
             "上位概念化、別用途、構成の置換・省略・組合せ、製造方法、システム化、ソフトウェア化の観点を使い、"
             "参照文献との差分（新規性の見込み）を各1〜2行で示してください。"},
    "search_plan": {"label": "検索式の設計", "prompt": "先行技術調査の検索戦略を作成してください。(1)構成要素ごとのキーワードと同義語・"
                    "英語表現、(2)推定される IPC / FI / Fターム候補と理由、(3)J-PlatPat・Espacenet・Google Patents 用の検索式例、"
                    "(4)本ライブラリで追加取得すべき文献の観点。"},
    "novelty": {"label": "新規性チェック", "prompt": "請求項1の構成要件を A,B,C… に分け、参照文献ごとに各構成要件の開示有無（○/△/×）を表で示し、"
                "新規性（特許法29条1項）を否定し得る文献があるか評価してください。根拠箇所を [番号] で引用。"},
    "inventive_step": {"label": "進歩性評価", "prompt": "審査基準の進歩性の判断手法（主引用発明の認定→一致点・相違点→副引用発明・周知技術→"
                       "動機付け（技術分野の関連性・課題の共通性・作用機能の共通性・示唆）→阻害要因・有利な効果）に沿って評価し、"
                       "反論材料と、進歩性を高める補正案を3つ示してください。"},
    "strategy": {"label": "権利化方針", "prompt": "国内のみ／パリ優先／PCT、早期審査・スーパー早期審査の適否、分割出願の余地、"
                 "権利範囲（上位概念・下位概念）と回避設計への強さについて、メリット・デメリットと推奨を示してください。"},
    "claim_draft": {"label": "請求項ドラフト", "prompt": "参照文献との差異を確保した独立請求項（物・方法・システムなど複数カテゴリ）と、"
                    "段階的に限定する従属請求項を日本語の特許請求の範囲の形式（【請求項１】…）で作成してください。"
                    "各請求項の狙い（どの先行文献を回避するか）を最後に箇条書きで。"},
    "spec_outline": {"label": "明細書の骨子", "prompt": "明細書の骨子（【技術分野】【背景技術】【先行技術文献】【発明の概要】【発明が解決しようとする課題】"
                     "【課題を解決するための手段】【発明の効果】【図面の簡単な説明】【発明を実施するための形態】）を作成し、"
                     "サポート要件・実施可能要件（36条）を満たすために必要な実施例・データを列挙してください。"},
    "filing_check": {"label": "出願前チェック", "prompt": "出願前チェックリスト（様式・記載要件・用語の統一・請求項と明細書の対応・"
                     "図面符号・新規性喪失の例外（30条）の要否・優先権主張・発明者/出願人情報）で不足を指摘してください。"},
    "oa_response": {"label": "拒絶理由への応答案", "prompt": "追加指示に貼り付けた拒絶理由通知の内容を整理し、(1)拒絶理由の要点、"
                    "(2)反論の論点、(3)補正案（新規事項追加にならない根拠となる明細書箇所付き）、(4)意見書の骨子を作成してください。"},
    "infringement": {"label": "侵害調査（クレームチャート）", "prompt": "請求項の各構成要件と、参照文献（対象製品・他社公報）の対応を表にし、"
                     "文言侵害・均等論（5要件）の観点で侵害の可能性を評価してください。不足情報も列挙。"},
    "fto": {"label": "FTO（実施可否）調査", "prompt": "本発明を実施する場合に障害となり得る他社特許を参照文献から抽出し、"
            "権利の存続・地域・請求項の範囲の観点でリスク（高/中/低）と回避設計案を示してください。"},
}
