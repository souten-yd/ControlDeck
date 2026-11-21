# 計画: 検索基盤の強化（Web 収集エンジン・特許の調査）

2026-11-21 開始。調べた事実と採否の理由は `research-web-collector.md`、進み具合と続け方は `HANDOFF-web-collector.md`（進捗が動いたら必ず更新）。

## 0. 利用者の指示（原文の要旨）

- 「特許について、テーマや入力された文言に基づいて、データを抽出して、関連特許の調査結果を都度出力する機能」→ Claude の案（調査を Patent に足す）を「提案通りに進めて」。
- 「特許庁などからデータをダウンロードする API の設定はどこ？ 見当たらない」。
- 「これらを利用した検索基盤の強化を実施して。必要なコードやプラグインは、ControlDeck の設定の『機能と拡張』に導入ボタンと更新ボタンを付けて、個別管理・一括導入・更新ができるようにする」。
- 「ControlDeck 次世代Web収集エンジン 設計案」（Firecrawl を使わない完全ローカルの収集。Trafilatura・Playwright・Crawlee・SearXNG）を参考に、**さらに良い方法を調べて採用**し、計画と引き継ぎ資料をプロジェクトに残してから開発する。

## 1. 決めたこと（設計案からの変更点）

| 項目 | 設計案 | 採用 | 理由 |
|---|---|---|---|
| 本文抽出 | Trafilatura | Trafilatura（**別プロセス・機能の専用 venv**、無ければ従来の抽出） | 信頼できない HTML を lxml で解析するので隔離。本体の venv を汚さない。機能と拡張で導入・更新 |
| Trafilatura の取得・巡回機能 | 小規模なら単独で | **使わない** | 自分で urllib3 で通信し、SSRF 対策の外に出る |
| HTTP の安全性 | 公開 IP の検査・リダイレクトの再検査 | **解決した IP にそのまま接続する**ネットワークバックエンド（httpcore） | 検査と接続で別に名前解決すると DNS rebinding で抜けられる（CVE-2026-55391 と同型） |
| ブラウザの安全性 | ワーカー＋ネットワーク出口の制御 | **ControlDeck の中継（認証付き CONNECT プロキシ）に全通信を通す** | `page.route` は Service Worker の一部を通せない。中継なら iframe・worker・サブリソースもすべて検査できる |
| サイト巡回 | Crawlee for Python | **自前（SQLite の待ち行列）＋ Protego（robots.txt）** | Crawlee は通信・保存を自前で持ち、安全策を通す差し替えが多い。運用の独立した報告も少ない |
| 特許庁 | — | JPO 特許情報取得 API を取得先に追加（番号で引く。トークン取得の URL・ID・パスワードを設定） | JPO には言葉で探す API が無い。発見は EPO OPS・OpenAlex・ナレッジ |

## 2. 構成

```
backend/app/web_collector/
  egress.py      解決 → 検査 → その IP に接続（httpcore のバックエンド）。拒否の範囲・ポート・大きさ・時間の上限
  fetch.py       collect(url) → Page（設計案 §7 の応答の形）。リダイレクトは各段で検査。PDF は pypdf
  extract.py     抽出の窓口。専用 venv の worker（Trafilatura）へ HTML を渡す。無ければ BeautifulSoup
  worker.py      専用 venv の Python で動く抽出の子プロセス（標準入出力の JSON、RLIMIT 付き）
  browser.py     （W2）中継プロキシ＋使い捨ての Chromium。本文が足りないときだけ
  crawl.py       （W3）SQLite の待ち行列・sitemap・RSS・リンク・robots（Protego）・停止/再開
  router.py      /api/v1/web-collector（取得の試し・巡回の Job・状態）
```

呼び出し元（すべて同じ `fetch.collect` を使う）: Deep Research の `_page_text`、Workflow の `web.scrape`・`web.browser`、`knowledge/sources.py` の取得、OpenCode の MCP（`control_deck.research`）、Patent の調査。

応答の形（設計案 §7 に従う）: `url, final_url, canonical_url, status, fetch_method(http|data_api|browser), title, content_type, markdown, text, language, links, tables, content_sha256, fetched_at, warnings, extractor`。
`status` は `success / blocked / timeout / empty / security_rejected / http_error / too_large / robots_denied / captcha`。

## 3. 機能と拡張（Settings → 機能と拡張 → オプション機能）

- 新しい分類 **「検索と収集」**。中に:
  - **Web 本文抽出（Trafilatura）**: 種類 `python-lib`（専用 venv に版を固定して導入、更新で最新版）。
  - **robots.txt 解析（Protego）**: 同上（W3 で使う）。
  - 公開データの取得先（EPO・USPTO・JPO・OpenAlex）の**鍵の設定への入口**（いまは Patent →「公報」→「取得先の設定」にしかない）。
- **SearXNG（Web 検索エンジン）**: 既存の `scripts/setup-searxng.sh` を導入・更新に使う（種類 `managed-script`）。
  - **Web 収集のブラウザ**: **この PC の Google Chrome を使い、別のブラウザは入れない**（種類 `system-browser`、2026-11-21 利用者「二重になっていないか」）。毎回まっさらの一時プロファイル。
- 各分類の未導入の機能にチェックを付け、「選んだものを導入」で 1 つの Job でまとめて入れる（2026-11-21 利用者の指示）。
- 各機能に「導入」「更新」（pip・npm の機能にも個別の更新ボタンを付ける。最新版の有無は PyPI / npm の情報で確かめる）。
- 分類ごとに「まとめて導入」（未導入のものを 1 つの Job で順に入れる）。既存の「一括更新」はそのまま。

## 4. 段階（PR の単位）

| 段階 | 内容 | 完了の条件 |
|---|---|---|
| **W1** ☑ | `egress`・`fetch`・`extract`（worker）。`_page_text`・`web.scrape`・`sources.py` を共通化。機能と拡張（分類・python-lib・個別の更新・まとめて導入・取得先の入口）。JPO の取得先 | 旧動作との比較テスト（同じ HTML で本文が欠けない）。SSRF の試験（内部 IP・リダイレクト・DNS rebinding・ポート）。Trafilatura 無しでも動く。E2E（設定の画面） |
| **W2** | ブラウザ（中継プロキシ・使い捨てプロファイル・資源の遮断・自動の切り替え・初期 JSON の利用）。`web.browser` を共通化 | ブラウザの通信（iframe・worker・リダイレクト）が内部へ届かない試験。JS のページの取得率・時間・RAM を記録 |
| **W3** | 巡回（SQLite の待ち行列・sitemap・RSS・リンク・Protego・停止/再開/キャンセル・重複）、進み具合の画面、ナレッジへの明示の登録 | 停止→再起動→再開。403・429・robots 拒否・CAPTCHA を区別 |
| **P1** | Patent の「調査」: テーマ/文言 → 検索語の案（ローカル LLM）→ **外へ送る語の確認** → 取得（EPO・USPTO・OpenAlex・Web）→ ナレッジと合わせて関連順 → 上位を全文で判定 → 調査結果（一覧・比較表・傾向・少ない観点）を保存・書き出し | 本番の評価（`~/ControlDeck-評価/`） |

各段階の後に本番での評価を行い、結果を `~/ControlDeck-評価/` に残す（AGENTS.md「評価は本番で」）。CI ではローカルの HTML/JS の試験サイトを使い、実サイトは本番の評価で確かめる。

## 5. 推奨の初期値（実測で直す）

1 回の取得: 20 MiB・30 秒・リダイレクト 5 段。巡回: 200 ページ・深さ 3・HTTP 同時 6（ドメインごとは 1）・ブラウザ同時 1・Job 全体 250 MiB。抽出の worker: メモリ 1 GiB・CPU 30 秒/ページ。

## 6. 守ること

- 外部へ出す検索語・URL は、未公開の発明の内容を含まないことを利用者が確かめたものだけ（Patent の既存の決まり）。OpenCode の調査は外部の許可が無ければ通信しない。
- CAPTCHA・アクセス拒否は回避しない（失敗として記録）。robots.txt と `Retry-After` を守る。
- Web の本文は LLM への指示ではなく、引用する外部の資料として扱う（既存の `strip_instruction`・前置き）。
- ブラウザは利用者の Cookie・ログインを共有しない。内部の API は ControlDeck の認証と権限を必須にする。
- 新しい依存は機能と拡張から導入する（本体の venv へ勝手に入れない）。外部からの取得（pip・npm・モデル）は利用者の操作か了承で行う。
