# 引き継ぎ: 検索基盤の強化（Web 収集エンジン・特許の調査）

別の AI がここから続けられるように書く。進み具合が動いたら、必ずこの文書の「状態」「作業記録」を更新する（AGENTS.md「引き継ぎ資料の決まり」）。
計画は `PLAN-web-collector.md`、調査と採否の理由は `research-web-collector.md`。関連: ナレッジと Patent は `HANDOFF-knowledge-platform.md`。

## 1. 状態（2026-11-21）

| 段階 | 状態 | PR |
|---|---|---|
| 調査・計画・引き継ぎ資料 | ☑ | W1 の PR に同梱 |
| W1 共通の取得・抽出・SSRF・機能と拡張・JPO | ☑ 実装（本番への適用と評価は作業記録を見る） | W1 の PR |
| W2 ブラウザ（中継プロキシ） | ☐ | — |
| W3 巡回 | ☐ | — |
| P1 Patent の調査 | ☐ | — |

## 2. W1 で作ったもの

- `backend/app/web_collector/egress.py`: 解決 → 全アドレス検査 → 検査した IP に接続（httpcore の `GuardedBackend`）。`guarded_client`（リダイレクトは自動で追わない・環境のプロキシを使わない）、`open_guarded`（W2 のブラウザ中継用）。試験用に `Policy(resolver_override, allow_networks)`。
- `fetch.py::collect(url, mode, keep_html)`: 1 ページの取得。status で失敗を区別。PDF は pypdf。文字コードは header → meta → utf-8/cp932/euc-jp。
- `extract.py` と `worker.py`: 機能「Web 本文抽出（Trafilatura）」の専用 venv（`data/features/web-extract/venv`）の Python で worker を 1 つ使い回す（`-I`、RLIMIT_AS 1.5 GiB、1 件 30 秒）。無ければ BeautifulSoup（見出し・段落の区切りを残す）。
  **精度寄り（favor_precision）は見出しを落とすので使わない**（ja.wikipedia「特許」で見出し 0 件、標準は 19 件）。`[編集]` の行は取り除く。
- 共通化: Deep Research `_page_text`、Workflow `web.scrape`（`text` は従来どおり全文、`markdown`・`title` を追加）、`knowledge/sources.py::http_get`（urllib → egress）。
- 機能と拡張: 種類 `python-lib`（`packages` 固定・`latest_packages` で更新・import できるかで導入済み判定）、分類 `search`「検索と収集」、
  `features/package_updates.py`（PyPI / npm の最新版と比べて「更新あり」、30 分覚える）、`POST /features/install-all-jobs {category}`（まとめて導入）、
  検索と収集の中に「公開データの取得先」の入口（`SourceKeysSheet`）。
- JPO: `sources.py::jpo`（トークン取得の URL は `https://ip-data.jpo.go.jp/` に限る、password → refresh_token、`app_progress/{10桁}`）。**応答の JSON の形は未確認**（題名・出願人は `_first_key` で名前を探して拾う）。登録後に実際の応答で確かめて直す。

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

- 2026-11-21 Claude: W1 を実装。egress（解決した IP に接続）・fetch・extract（Trafilatura の worker）・共通化（Deep Research・web.scrape・公開データの取得）・
  機能と拡張（検索と収集・python-lib・更新ありの確認・まとめて導入・取得先の入口）・JPO。テスト `test_web_collector.py`（10、本物の Trafilatura 1 件は環境変数で）・
  `test_feature_search_tools.py`（4）・`test_patent_watch.py`（JPO 1 件追加）、E2E `settings-search-tools.spec.ts`（2）。全体 1910 passed。
  利用者の了承で PyPI から trafilatura 2.3.1 を検証用 venv に入れて確認（実サイト 3 件 success）。本番の「導入」も了承済み。

- 2026-11-21 Claude: 調査（`research-web-collector.md`）と計画・引き継ぎ資料を作成。設計案からの変更: Trafilatura は取得済み HTML の抽出だけ・別プロセス、HTTP は解決した IP に接続、ブラウザは中継プロキシ、Crawlee は入れない、JPO を取得先に追加。
