# 調査: 次世代 Web 収集エンジン（検索基盤の強化）

2026-11-21。利用者から「ControlDeck 次世代Web収集エンジン 設計案」（Firecrawl を使わず、Trafilatura・Playwright・Crawlee・SearXNG で完全ローカルの Web 収集）を受け、
「さらに良い方法があるのかまず調査を行って良い方法を採用」との指示。この文書は調べた事実と採否の理由。計画は `PLAN-web-collector.md`、引き継ぎは `HANDOFF-web-collector.md`。

## 1. ControlDeck の現状（main `2a1a684` で確認）

| 機能 | 場所 | 事実 |
|---|---|---|
| Web 検索 | `workflows/searxng.py`・`nodes.py::node_web_search` | SearXNG（既定・ローカル）と DuckDuckGo |
| Deep Research のページ取得 | `workflows/chat_router.py::_page_text` | httpx。リダイレクトを手で追い、毎回 `getaddrinfo` で公開 IP を確かめる。20 MiB 上限・PDF 80 ページ。本文は BeautifulSoup で script/nav 等を落として連結（見出し・表・リンクは失う） |
| Workflow の `web.scrape` | `nodes.py::node_scrape` | httpx `follow_redirects=True`、**公開 IP の確認なし**（内部アドレスへ届く） |
| Workflow の `web.browser` | `nodes.py::node_browser` | Playwright を直接起動。**接続先の制限なし**、既定の Chromium |
| 公開データの取得（特許・論文） | `knowledge/sources.py` | 取得先ごとの関数。SSRF 検査・リダイレクトなし |
| 本文抽出のライブラリ | 本番 venv | `bs4` 4.15 のみ。`lxml`・`trafilatura` は無い。Playwright 1.62・システムの Chrome（`/usr/bin/google-chrome`）あり |

設計案の指摘（取得が 3 か所に分かれ、安全性も揃っていない）は正しかった。さらに `_page_text` の検査にも穴がある:
**確かめた IP と httpx が実際に接続する IP が別に解決される**（DNS rebinding で検査を通り抜けられる）。

## 2. 本文抽出

| 候補 | 根拠 | 判断 |
|---|---|---|
| **Trafilatura 2.3.1**（2026-10-06、Apache-2.0、Python ≥3.10） | 第三者の評価で Readability と並び最も頑健（Webis SIGIR 2023、SIGIR 2025 多言語評価「経験則の手法が ML より頑健で速い」）。WCXB 2026 でも Python 版 F1 0.791・94 ms/ページ | **採用**（標準の抽出器） |
| ReaderLM-v2（1.5B の生成モデル） | WCXB で F1 0.741・10 秒/ページ。GPU を使う | 不採用（遅く精度も劣る。GPU は後段の LLM に使う） |
| Resiliparse | 速い（DCLM で約 8 倍）が、本文がリストのページに弱い | 不採用（日本語の技術文書・公報の一覧に弱いのは困る） |
| rs-trafilatura / MinerU-HTML | 精度は上だが、前者は作者の自己申告・新しすぎ、後者は 1.5 秒/ページ | 見送り（実測の比較の候補として記録） |
| Crawl4AI・Scrapling | Markdown 化・動的取得をまとめて持つ | 不採用（取得を自前で行い、下の安全策の外に出る。必要なら比較試験だけ） |

**決定**: 取得（ネットワーク）は ControlDeck が行い、Trafilatura には**取得済みの HTML だけを渡す**。Trafilatura の `fetch_url`・`sitemaps`・`spider` は自分で urllib3 で通信するので使わない（使うと SSRF 対策の外に出る）。
抽出は**別プロセス**（機能の専用 venv の Python）で動かす。lxml で信頼できない HTML を解析するので、メモリ・CPU 時間の上限を付けた子プロセスに閉じ込める（設計案 §6「制限されたワーカー」と同じ考え）。Trafilatura が入っていなければ従来の BeautifulSoup の抽出に戻る。

## 3. HTTP 取得の安全性（SSRF・DNS rebinding）

- OWASP と実例（CVE-2026-55391: datamodel-code-generator。検査の後に httpx が別に名前解決していた）から、**「解決 → 検査 → その IP に接続」を 1 か所で**行う必要がある。
- 外部の小さなライブラリ（drawbridge・httpx-secure・safehttpx）もあるが、依存を増やさず **httpcore のネットワークバックエンドを差し替える**方法を採る:
  `connect_tcp(host, port)` の中で `getaddrinfo` → 全アドレスを検査 → 検査済みの IP に接続。TLS の SNI と Host は元の名前のまま（httpcore が上の層で付ける）。
  リダイレクトは自動で追わず、各段で同じ経路を通る。応答の大きさ・時間・段数の上限を持つ。
- 拒否する範囲: private・loopback・link-local（169.254.0.0/16 のクラウドのメタデータを含む）・multicast・reserved・unspecified・CGNAT（100.64/10）・IPv4 射影の IPv6・ポートは 80/443 と明示の許可だけ。

## 4. JavaScript が必要なページ（ブラウザ）

- Playwright の `page.route` での遮断は補助にしかならない（公式文書: Service Worker の更新スクリプトは route できない）。
- **採用**: Chromium の通信を、ControlDeck が持つ**中継（ループバックの HTTP CONNECT プロキシ、利用者名とパスワード付き）に全部通す**。中継は上の §3 と同じ「解決 → 検査 → 接続」を使う。
  `--proxy-bypass-list=<-loopback>` で localhost も中継に通す（Chromium は既定でループバックを中継しない）。WebRTC は `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`。Service Worker は `service_workers="block"`。
  ブラウザは使い捨てのプロファイル（利用者の Cookie・ログインを共有しない）、画像・動画・フォントは止める。
- ブラウザを使うのは、HTTP で取った本文が足りないときだけ（本文の文字数・SPA の根要素・`__NEXT_DATA__` などの初期 JSON・noscript の案内で判定）。初期 JSON に本文があればブラウザを起動せずに使う（Scrapy 公式文書「裏の公開 JSON を直接使うほうが効率的」）。

## 5. サイトの巡回

- Crawlee for Python 1.0（2025-09 に beta を抜けた）は機能が揃うが、独立した運用の報告が少ない（Product Hunt 7 件・G2 のレビューなし）。HTTP クライアント・ブラウザ・保存を自前で持つので、§3・§4 の安全策を通すには差し替えが多い。
- **決定**: Crawlee は入れない。巡回は ControlDeck の SQLite（URL の待ち行列・状態・本文のハッシュ）で自前に持つ。発見は sitemap（索引を含む）・RSS/Atom・ページ内リンク、robots.txt は **Protego 0.7.0**（Scrapy の robots.txt 解析。Google の仕様のワイルドカードに対応、依存なし）。
  停止・再開・キャンセルは Jobs と待ち行列の状態で行う。ドメインごとの同時取得 1・`Crawl-delay`・`Retry-After` を守る。

## 6. 特許庁（JPO）の特許情報取得 API

- 利用登録が必要（申込書を特許庁へメールで送る。法人・個人とも可）。登録後に ID・パスワード・**トークン取得の URL が通知される**（URL は公開されていない）。
- 認証: 通知された URL へ POST（`grant_type=password`・ID・パスワードを URL エンコード）→ `access_token`・`refresh_token`。以後 `Authorization: Bearer`。
- API は**番号で引く取得だけ**（例 `https://ip-data.jpo.go.jp/api/patent/v1/app_progress/{出願番号10桁}`、経過情報・分割・優先権・書類など特許で 14 種）。**言葉や分類で探す検索 API は無い**。上限は API ごと（app_progress は 400 回/日、2023 年資料）。
- **決定**: JPO は「見つけた公報の経過・関連出願を詳しくする」取得先として足す（トークン取得の URL・ID・パスワードを利用者が設定）。言葉からの発見は EPO OPS（JP の公報も含む）・OpenAlex・ナレッジで行う。
  特許庁のサイト（jpo.go.jp）は自動の閲覧を 403 で断るため、手引き v2.0 の本文は読めていない。トークンと API の細部は登録後に利用者の手元の手引きで確かめる（`HANDOFF-web-collector.md` の未決事項）。

## 7. 出典

- 抽出の比較: WCXB（arXiv 2605.21097）、Webis SIGIR 2023 スライド、SIGIR 2025 多言語評価、DCLM（arXiv 2406.11794）、Trafilatura の評価の文書
- SSRF: OWASP SSRF Prevention Cheat Sheet、CVE-2026-55391 の解説、drawbridge・LangChain `SSRFSafeTransport` の文書
- ブラウザ: Playwright「Service Workers」、Chrome「Private Network Access」「Local Network Access」
- 巡回: Crawlee for Python v1 の告知、Product Hunt・G2、Scrapy の文書（動的ページ）、Protego（PyPI）
- JPO: 特許情報取得 API の手引き v1.1〜v1.3・アクセス方法（ip-data.jpo.go.jp/files）、JAPIO 2023 年鑑、Qiita の利用例
- 利用者の資料: 「ControlDeck 次世代Web収集エンジン 設計案」（r/LocalLLaMA の SearXNG→Scrapling→Trafilatura の構成例を含む）
