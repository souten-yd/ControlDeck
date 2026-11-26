# 引き継ぎ: 検索基盤の強化（Web 収集エンジン・特許の調査）

別の AI がここから続けられるように書く。進み具合が動いたら、必ずこの文書の「状態」「作業記録」を更新する（AGENTS.md「引き継ぎ資料の決まり」）。
計画は `PLAN-web-collector.md`、調査と採否の理由は `research-web-collector.md`。関連: ナレッジと Patent は `HANDOFF-knowledge-platform.md`。

## 1. 状態（2026-11-21）

| 段階 | 状態 | PR |
|---|---|---|
| 調査・計画・引き継ぎ資料 | ☑ | W1 の PR に同梱 |
| W1 共通の取得・抽出・SSRF・機能と拡張・JPO | ☑ v0.108 で本番に適用・評価済み | #504 |
| W2 ブラウザ（中継プロキシ） | ☑ 実装・試験済み（2026-11-26、ブランチ feat/web-collector-w2。マージ・本番評価は司令塔のレビュー後） | — |
| W3 巡回 | ☐ | — |
| P1 Patent の調査 | ☐ | — |
| AI アシスタントのサイト指定の調査・依頼の理解・日付の確認（`research_tools.py`・`research_intent.py`・`research_time.py`） | ☑ 2026-11-22（実機評価済み・Reddit は取得制限あり） | — |

## 1b. Codex の PR の扱い（2026-11-26）
W3 は Codex の #638（発見・SQLite の待ち行列）と #640（巡回の HTTP 境界）を土台に仕上げる（Jobs・画面・ナレッジへの明示の登録が残り）。表の行と列（#639・#646）は、使う所（測定の取り込みなど）ができたら入れる。採否の理由は implementation-status の 2026-11-26。

## 2. W1 で作ったもの

- `backend/app/web_collector/egress.py`: 解決 → 全アドレス検査 → 検査した IP に接続（httpcore の `GuardedBackend`）。`guarded_client`（リダイレクトは自動で追わない・環境のプロキシを使わない）、`open_guarded`（W2 のブラウザ中継用）。試験用に `Policy(resolver_override, allow_networks)`。
- `fetch.py::collect(url, mode, keep_html)`: 1 ページの取得。status で失敗を区別。PDF は pypdf。文字コードは header → meta → utf-8/cp932/euc-jp。
- `extract.py` と `worker.py`: 機能「Web 本文抽出（Trafilatura）」の専用 venv（`data/features/web-extract/venv`）の Python で worker を 1 つ使い回す（`-I`、RLIMIT_AS 1.5 GiB、1 件 30 秒）。無ければ BeautifulSoup（見出し・段落の区切りを残す）。
  **精度寄り（favor_precision）は見出しを落とすので使わない**（ja.wikipedia「特許」で見出し 0 件、標準は 19 件）。`[編集]` の行は取り除く。
- 共通化: Deep Research `_page_text`、Workflow `web.scrape`（`text` は従来どおり全文、`markdown`・`title` を追加）、`knowledge/sources.py::http_get`（urllib → egress）。
- 機能と拡張（検索と収集）: SearXNG・Web 本文抽出・robots.txt 解析・Web 収集用ブラウザ。チェックを付けて「選んだものを導入」。種類 `python-lib`（`packages` 固定・`latest_packages` で更新・import できるかで導入済み判定）、分類 `search`「検索と収集」、
  `features/package_updates.py`（PyPI / npm の最新版と比べて「更新あり」、30 分覚える）、`POST /features/install-all-jobs {category}`（まとめて導入）、
  検索と収集の中に「公開データの取得先」の入口（`SourceKeysSheet`）。
- JPO: `sources.py::jpo`（トークン取得の URL は `https://ip-data.jpo.go.jp/` に限る、password → refresh_token、`app_progress/{10桁}`）。**応答の JSON の形は未確認**（題名・出願人は `_first_key` で名前を探して拾う）。登録後に実際の応答で確かめて直す。

## 2b. W2 で作ったもの（2026-11-26）

- `browser.py`: `RelayProxy`（ループバック・利用者名とパスワード付きの HTTP プロキシ。CONNECT と http 絶対 URI を `egress.open_guarded` で中継、拒否は 403 と記録、転送量・同時接続の上限）と
  `session()`（この PC の Chrome を Playwright の `launch_persistent_context` で。使い捨てのプロファイル、`--proxy-server`＋`--proxy-bypass-list=<-loopback>`＋`--host-resolver-rules=MAP * ~NOTFOUND`、WebRTC 無効・QUIC 無効・Service Worker block、
  画像・動画・フォント遮断、時間・RSS・転送量の上限と監視、終了時に Chrome を必ず kill して一時プロファイルを消す、同時 1）。`render(url)` は status（success / blocked / captcha / login_required / security_rejected / timeout …）・html・stats を返す。
- `js_detect.py`: JS が要るかの判定（本文の短さ・noscript・SPA の空の根・script の大きさ・初期 JSON）。初期 JSON に本文があれば `fetch_method="data_api"` でブラウザなしに取る。見本 15 件（`tests/fixtures/js_pages`）で評価。
- `robots.py`: ブラウザへ進む前の robots.txt 確認（最長一致・`*`/`$`・4xx は可・5xx/失敗は拒否）。巡回（W3）で Protego に替えてよい。
- `fetch.collect(render="auto")`: 静的 → 初期 JSON → ブラウザ。呼び出し元は `read_page`・`_page_text`・`web.scrape`（既定の "never" の他の呼び出しは不変）。機能「Web 収集のブラウザ」が無効なら警告つきの静的の結果。`web.browser` ノードも同じ `session()`（以前は接続先の制限なし）。
- 止める条件: 静的の 401/403/429・ブラウザの 403/429・CAPTCHA・ログイン（password 入力）・robots 拒否は、その status と理由を返して止まる。別経路で取り直さない。UA は詐称しない。
- 試験: `test_web_browser.py`（実 Chrome。127.0.0.2 の「内部」サーバーへ iframe・Worker・fetch・WebSocket・IPv6・rebinding・リダイレクトが 1 件も届かない／中継が内部を 403／認証／上限）、`test_js_detect.py`、`test_js_render.py`。conftest の autouse で試験は自動のブラウザを起こさない。
- 実測: `~/ControlDeck-評価/2026-11-26-検索基盤W2のブラウザ描画.md`。OpenAlex の本文ページは HTTP 403（「API を使って」）。**OpenAlex は公開 API（data_api）で取る**。
- 未了・次: 公開データ API の直接利用（GitHub README・Wikipedia REST・OpenAlex API）、W3 巡回、メモリ上限の kill の実機確認（試験は上限を極端に小さくして確認）。

### W2 で最初にやること（W1 の本番評価から。2b で実装済み）

- JavaScript が無いと本文が出ないページ（例: openalex.org は「needs JavaScript」の案内だけ、254 字）を判定してブラウザへ切り替える。判定の材料: 本文の短さ・`noscript` の案内・SPA の根要素（`#root`/`#app`/`#__next`）・初期 JSON（`__NEXT_DATA__` など）。
- 公開データ API の直接利用（data_api）: GitHub の README は Trafilatura が見出しを拾えない → `api.github.com/repos/{owner}/{repo}/readme`（raw）を使う。同じ考えで Wikipedia は REST API（`/api/rest_v1/page/html/`）も候補。
- ブラウザの安全策は `research-web-collector.md` §4（中継プロキシ・`--proxy-bypass-list=<-loopback>`・WebRTC・Service Worker の block・使い捨てのプロファイル）。`egress.open_guarded` を中継の接続に使う。

## 3. まず読むコード

- 現状の取得: `backend/app/workflows/chat_router.py::_page_text`（Deep Research）、`workflows/nodes.py::node_scrape`・`node_browser`、`knowledge/sources.py`（特許・論文の公開データ。SSRF の検査はここにもある）
- 機能と拡張: `backend/app/features/registry.py`（`FEATURES`・`CATEGORIES`・`_install_package`・`status`）、`features/router.py`、画面は `frontend/src/pages/Settings.tsx::AddonsSection`・`FEATURE_CATEGORIES`
- 取得先の鍵: `knowledge/sources.py`（Fernet で暗号化して保存）、画面は `frontend/src/features/patents/Sources.tsx`

## 4. 守ること（変えない）

- 取得は必ず `web_collector.egress` を通す（解決した IP にそのまま接続）。ライブラリに自分で通信させない（Trafilatura の fetch・sitemaps・spider を使わない）。
- 抽出の worker は機能の専用 venv の Python で、標準入出力の JSON だけで話す。本体の venv に trafilatura を入れない。
- 外へ出す検索語は利用者の確認（approved_public）が要る。未公開の発明・案件の内容は外へ出さない。
- 隔離テストは `env -u CONTROL_DECK_CONFIG`（外さないと本番の data に書く）。本番の worktree（`/data1tb/ControlDeck/app-strata`）で作業しない。
- 外部からの取得（pip・npm）は利用者の操作（機能と拡張のボタン）か了承で行う。

## 5. 開発・検証の手順

- バックエンドのテスト: `cd backend && env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/runtime-lifecycle/.venv/bin/python -m pytest -q`
- 画面: `cd frontend && npm run build`、E2E `env -u CONTROL_DECK_CONFIG ./deck.sh e2e <spec>`（検証用インスタンス 18765）
- テストの目録: `cd backend && python -m app.maintenance.test_catalog`（`./deck.sh test-catalog` は PyPI へ取りに行くので使わない）
- SSRF の試験は実際の外部へ出ない（`Policy.resolver_override` と `allow_networks`、ローカルの試験サーバー）。
- 本物の Trafilatura の試験: Trafilatura を入れた venv の python を `CONTROL_DECK_TEST_WEB_EXTRACT_PYTHON` で渡す（無ければ skip）。
  例: `python3 -m venv /tmp/x && /tmp/x/bin/pip install trafilatura==2.3.1`（PyPI から取得するので利用者の了承が要る。2026-11-21 は了承済み）。

## 6. 未決事項（利用者に確認すること）

- 特許庁 API の利用登録（申込書を特許庁へメール）。登録後に通知されるトークン取得の URL・ID・パスワードを「取得先の設定」に入れる。手引き v2.0 の本文（API 名・上限）は jpo.go.jp が自動閲覧を断るため未確認。
- EPO OPS・USPTO ODP の鍵（無料の登録）。

## 7. 作業記録（新しい順に追記）

- 2026-11-26: 利用者提示の10 OSSを比較（採否・受入条件は `PLAN-web-collector.md` §7）。ドラフト PR #634 に W1a のURL誤重複除外修正・focus による関連リンク選別・回帰テストを追加。既存の W2 安全なブラウザ、W3 明示の巡回、W4 パーサー比較、W5 統合ナレッジを続ける。新しい通信経路・依存・UI変更なし。pytest/実機は未実施、未マージ。まず PR差分、テスト目録再生成、集中テスト、実機評価を実施する。

- 2026-11-22 Claude: AI アシスタントの調査を作り直した（詳細は `implementation-status.md` の同日の項）。`collect` を使う `read_page`（本文の抜粋・リンク一覧）、サイトごとの入口、依頼の理解、今日の日付の確認。W1 の取得は変えず、`fetch._CAPTCHA` に Reddit の「Prove your humanity」を、`extract.fallback` に相対リンクの絶対化を足しただけ。robots.txt は 1 ページずつの取得では見ない（巡回 W3 で Protego を使う）。次にやること: Reddit は未認証の JSON・RSS が拒否されるので、認証つきの API（利用者の許可が要る）か別の取得を検討。

- 2026-11-22 Claude: AI アシスタントの道具 `huggingface_models`（`models_mgmt/hf_search.py`）を追加。Hugging Face のモデルを語の揺れ・GGUF 別作者・ベースの派生で広く検索し、Strata 対応をファイル名から判定して表で返す。秘密の会話は通信の直前で止める。続き: 画面での表示と、Models 画面への「Strata 対応で探す」の口は未着手。
- 2026-11-21 Claude: 利用者「モデルのダウンロードと Workflow のシークレットは設定不要」→ Settings「API キー」から Hugging Face と Workflow のシークレットを外した（クラウドの AI と特許の公開データだけ）。
  Workflow のシークレットは Workflow の画面で従来どおり。保存済みの値は消していない。

- 2026-11-21 Claude: 利用者「EPO や特許庁の API のキーはどこで設定できる？設定の中にキーをまとめてほしい。OpenRouter のキーも。今あるキーを全部移動、消さないように」。
  Settings に「API キー」（`/settings/keys`、`backend/app/settings_keys.py`・`frontend/src/features/settings/KeysSettings.tsx`）。
  キーの置き場（cloud.py・hf.py・sources.py・workflow_secrets 表）は**変えずに**読み書きをまとめた（今あるキーはそのまま出る・消していない）。値は返さない。
  Patent の公報タブ・機能と拡張の検索と収集からは「API キー」へ移るリンクに。Models のクラウドと Workflow のシークレットの画面は使い方の都合で残し、案内を足した。
  Hugging Face のトークンはこれまで画面が無かった（API だけ）→ ここで入れられる。テスト `test_settings_keys.py`（3）、E2E `settings-keys.spec.ts`（2）。

- 2026-11-21 Claude: 利用者「適当に選んだサイト（Gigazine・特許の書誌・GitHub）で情報収集を試し、取得の精度と RAG の改良を含めて評価して」。
  本番の評価 `~/ControlDeck-評価/2026-11-21-実サイトの情報収集とRAG.md`（26 問）: 本文＋見出しで分けると検索の当たり 19→23、答え 20→23/26。
  改良: ① Web の取り込み（ナレッジ・Patent の URL）は見出しで分ける（`rag.add_document(strategy="markdown")`、コレクションの設定は変えない）。
  ② ページの書誌の meta（citation_*・DC.*・og）を `fetch.biblio` で拾い、`biblio_block` を本文の先頭に置く（見出しに番号と題名、発明者と法人を分ける、番号は詰めた形と <title> の種別記号付きも）。
  特許の出願人・発明者の 6 問で答え 0→5/6。Google Patents の米国のページは 39〜54 万字で表が大半（公報は公式 API・PDF のほうが確実）。

- 2026-11-21 Claude: 利用者「Chrome を Playwright などで使っているので二重になっていないか。導入時に Chrome も入れる気がする、その関係も」。調べた結果:
  この PC には ① Google Chrome 155（apt、Playwright MCP＝OpenCode の UI 検証が `--executable-path` で使う）、② `data/cache/ms-playwright` の
  Chromium 1228・headless shell 1228・WebKit・ffmpeg（約 940 MB、画面の E2E＝@playwright/test 1.61 用。開発だけ）がある。
  前の版で足した「Web 収集用ブラウザ」は本体の Playwright 1.62 用の headless shell 1234（約 260 MB）を**3 つ目として**入れるものだったので取りやめ、
  **この PC の Chrome を一時プロファイルで使う**（`web_collector/browser.py`、機能は種類 `system-browser`：導入・更新・削除のボタンなし）。
  ついでに分かった不具合: Workflow の「ブラウザ操作」は Playwright の既定（build 1234 の Chromium）を探して**起動できていなかった** → Chrome で起動・開く URL を egress で検査。
  さらに、ナレッジ・Patent の URL 取り込みと Workflow のページ解析（`scrape_tools.fetch`）も検査なしで内部へ届き、ページ全体の文字を取り込んでいた → 共通の取得と本文（`fetch_document`）へ。

- 2026-11-21 Claude: 利用者「まとめて導入するのはこの検索機能に関する部分のこと。チェックを付けて、付けたものを一括導入でもいい」。
  「検索と収集」に SearXNG（種類 `managed-script`、既存の `scripts/setup-searxng.sh` を導入・更新に使う。機能と拡張からはアンインストールさせない）・
  robots.txt 解析（Protego 0.7.0、python-lib）・Web 収集用ブラウザ（種類 `playwright-browser`、本体の Playwright で `chromium-headless-shell` を
  `data/features/web-browser/browsers` へ）を追加。各分類の未導入にチェック、「すべて選ぶ」「選んだものを導入（n）」（`install-all-jobs` の `feature_ids`）。
  `features/search_kinds.py`。失敗の理由（出力の最後の行）を導入・更新のエラーに添える。テスト 4 件追加、E2E settings-search-tools 更新。

- 2026-11-21 Claude: v0.108 を本番に適用し、利用者の了承で「Web 本文抽出（Trafilatura）」を本番に導入（2.3.1、`data/features/web-extract` 49 MB）。
  本番の評価 `~/ControlDeck-評価/2026-11-21-検索基盤W1の取得と本文抽出.md`: 実サイト 10 件 success、内部アドレス 2 件は拒否、見出し・表を Markdown で保持。
  弱点（W2 で扱う）: JS が要るページ（OpenAlex）、GitHub の README の見出し。

- 2026-11-21 Claude: W1 を実装。egress（解決した IP に接続）・fetch・extract（Trafilatura の worker）・共通化（Deep Research・web.scrape・公開データの取得）・
  機能と拡張（検索と収集・python-lib・更新ありの確認・まとめて導入・取得先の入口）・JPO。テスト `test_web_collector.py`（10、本物の Trafilatura 1 件は環境変数で）・
  `test_feature_search_tools.py`（4）・`test_patent_watch.py`（JPO 1 件追加）、E2E `settings-search-tools.spec.ts`（2）。全体 1910 passed。
  利用者の了承で PyPI から trafilatura 2.3.1 を検証用 venv に入れて確認（実サイト 3 件 success）。本番の「導入」も了承済み。

- 2026-11-21 Claude: 調査（`research-web-collector.md`）と計画・引き継ぎ資料を作成。設計案からの変更: Trafilatura は取得済み HTML の抽出だけ・別プロセス、HTTP は解決した IP に接続、ブラウザは中継プロキシ、Crawlee は入れない、JPO を取得先に追加。

- 2026-11-23 Claude: 利用者「Reddit の API を登録して使えるように。XDA など有望なフォーラムも検索できるように」。調べた結果と入口:
  - **Reddit**: OAuth の application-only（`grant_type=client_credentials`、client_id・client_secret の Basic 認証、`POST https://www.reddit.com/api/v1/access_token`）。トークンは 1 時間・refresh なし。呼び出しは `https://oauth.reddit.com`（`/search`・`/r/{sub}/search`・`/comments/{id}`、`raw_json=1`）。User-Agent は `<platform>:<app id>:<version> (by /u/<username>)`（`linux:controldeck-research:1.0 (by /u/<ユーザー名>)`）。無料は 1 分 100 件程度。新しいアプリに Reddit の承認が要るという情報が多い（**未確認**）ので、設定の「つながりを確かめる」で 403 を「承認が要る可能性」と見せる。キーが無い・失敗なら従来の経路（未認証 JSON → RSS → SearXNG）。
  - **Discourse**（Level1Techs・Hugging Face・PyTorch・Ubuntu・Fedora・Framework・NixOS・Rust・Home Assistant・NVIDIA）: 公開の `https://{domain}/search.json?q=` が匿名で使える（時期は q に `after:YYYY-MM-DD`）。話題は `/t/{id}.json`（posts の cooked が本文 HTML）。一覧は `research_keys.DEFAULT_FORUMS`＋設定で足したドメイン（形だけ検査。localhost・IP・内部の名前は不可）。
  - **Stack Exchange**: `api.stackexchange.com/2.3/search/advanced`（キーなし 1 日 300、キー（stackapps.com）で 10,000）。回答は `/questions/{id}/answers?filter=withbody&sort=votes`。stackoverflow・superuser・serverfault・askubuntu・mathoverflow.net・`*.stackexchange.com` を site 名に直す。
  - **XDA（xdaforums.com、XenForo）**: 外から使える検索の API は無い。SearXNG の `site:` と read_page（HTML）で扱う（入口は作らない）。
  - 実装: `workflows/research_keys.py`（キー・フォーラム一覧）、`research_tools.py`（`_reddit_oauth`・`discourse_search`・`stackexchange_search` ほか）、`settings_keys.py`（群 research・test）。通信は全部 `guarded_client`＋`ensure_external_allowed`。認証のヘッダーは別のホストへのリダイレクトに付けない。実際の Reddit・各フォーラムへは繋いでいない（テストはモック）。
- 2026-11-26: W2 実装（上記 2b）。依存の追加なし・playwright install なし。マージ前に司令塔がセキュリティの最終レビュー。
