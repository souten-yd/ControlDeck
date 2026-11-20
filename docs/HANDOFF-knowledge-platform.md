# 引き継ぎ — 統合ナレッジ基盤・Patent・規格検索

この文書だけを読めば、別の AI・開発者がまっさらな状態から開発を続けられるように書く。
**進捗が動いたら必ず更新する**（`AGENTS.md`「引き継ぎ資料の決まり」）。更新日と、誰が・何を終えたかを §9 の作業記録に追記する。

- 計画（何を・なぜ・どの順で）: `docs/PLAN-knowledge-platform.md`
- 調査と方式の評価（特許・規格の RAG をどう作るか）: `docs/research-patent-standards-rag.md`
- 規格の利用可否（保存・AI 利用）の判断: `docs/standards-license-policy.md`
- 規約の原本: `AGENTS.md`、引き継ぎの入口: `CLAUDE.md`

最終更新: 2026-11-21 v0.102 を本番へ適用・旧データを取り込み済み（Claude, session_01Vr7X7mm3aPQSc8WA3mYwEk）

## 1. 目的と利用者の決定（時系列）

| 日付 | 利用者の発言・決定 |
|---|---|
| 2026-11-19 | ControlDeck の RAG を強化し、ResearchDataHub（RDH）と連携したい。特許の DB は分けたいが ControlDeck からは意識しない。PatentBuddy で作っていた PDF・図表の抽出を一般 RAG にも |
| 2026-11-19 | DB 分割は「1 インスタンス・分野ごとのファイル」でよい。後で分けたくなるかも |
| 2026-11-19 | 横断検索したい。守りたいのは**アイデアの流出だけ** |
| 2026-11-19 | 規格の利用可否の判断方法・コード・説明を調べられるように残す（→ `standards-license-policy.md`） |
| 2026-11-19 | **すべて ControlDeck に統合**。PatentBuddy・RDH のデータを回収し、使わなくなったリポジトリは閉じる |
| 2026-11-19 | 保存先（ローカル・サーバーのバックアップ・図表）と保存対象を分けて設定できるように。当面は Linux 単体で検証 |
| 2026-11-21 | PatentBuddy Add-on は不要。機能追加画面の導入項目も一式削除し、本体に **Patent 機能**を |
| 2026-11-21 | 特許・規格は書誌全体が関係する → GraphRAG が要るのでは。文脈がすべて重要なので上位 k 件に絞れない。コンテキストが十分なら**書誌の全文を投入して回答**も。Web・フォーラム・公式を調べて方式を評価・採用 |
| 2026-11-21 | 引き継ぎ資料を完備し、進捗ごとに更新することをルールに（本書） |
| 2026-11-21 | UX: 利用者が知らなくてよい情報は隠し、必要なら開けるように。ControlDeck のデザインと統一。Patent は左上の ☰ に項目を |
| 2026-11-21 | 発明は**音声で長時間話して入力**し、AI が本質・請求項に分解する**ヒアリング**を |
| 2026-11-21 | 規格検索システム = PDF から文章・画像・波形グラフ・表を抽出して記録し、体系的（グラフ的）に引き出す RAG。AI アシスタントと接続 |
| 2026-11-21 | テスト・評価の後にマージとリリースを許可。本番（この PC）へ反映まで |

ControlDeck 本体は「関門を通せばマージと Release を毎回尋ねない」常設の承認がある（`docs/POLICY-autonomous-development.md`）。
ただし本番データの変更・外部からの取得（pip・npm・モデル）・本番モデルの停止は、その都度の了承が要る。

## 2. いまの状態（2026-11-21）

- PR **#473** をマージ（`7a5a647`）、**ControlDeck v0.102** として Release（2026-11-21）。worktree `/data1tb/ControlDeck/knowledge-platform` は以後の作業用。
- 本番: `/data1tb/ControlDeck/app-strata`（main。正式 Release に追従）。**v0.102 を適用済み**（2026-11-21、利用者の指示で設定画面の更新と同じ経路から。監査に記録）。
- 旧データの取り込み済み（`./deck.sh knowledge-migrate --apply`、2026-11-21）: 旧 RAG 47 コレクション・187 文書・1,213 断片・1,213 ベクトル、ResearchDataHub の公報 1 件（原本・図 14 点はハッシュ一致、注釈 2）。旧 `data/rag/` は不変のまま残し、取り込み前の写しは `data/backups/knowledge-integration-20261121/`。
- PatentBuddy は起動時に片付いた（`data/features/patent-buddy/.retired`、データは `data/feature-data/patent-buddy` に残る）。
- 実装済み（PR #473）:
  - `backend/app/knowledge/`: 統合ナレッジストア（分野別 SQLite・2-gram 全文・RAM 行列のベクトル・RRF・機密区分）、旧データの取り込み、保存先の設定・バックアップ、規格の利用判定。
  - `backend/app/workflows/rag.py`: 既存の呼び出し口。中身は store へ。
  - `backend/app/patents/` と `frontend/src/pages/Patents.tsx`（`/patents`）: 案件・期限・請求項の版と構成要件・先行文献・クレームチャート・ローカル専用 AI 支援・公報の取り込み。
  - PatentBuddy をカタログから外し、導入済みなら起動時に片付ける（`features/registry.py` `RETIRED_FEATURES`）。
- 進行中（このラウンド R2）: §6 の一覧。

## 3. 構成とファイル地図

```
backend/app/knowledge/
  store.py             分野別 SQLite（general / patents / standards）。collections・documents・bodies・chunks・
                       chunks_fts（2-gram）・vectors・blobs・document_assets・annotations・legacy_imports
  textproc.py          日本語 2-gram・規格番号の正規化・請求項の分割（RDH から移植）
  migrate.py           旧 data/rag/*.db（初回利用時にも）・RDH の library.sqlite3 の取り込み
  storage.py           保存先（target）と保存対象（category）の設定 data_dir/knowledge-storage.json
  backup.py            バックアップの世代・照合・空の場所への復元・blobs の移し替え・DB の移動
  storage_cli.py       ./deck.sh knowledge-storage
  standards_license.py 規格の保存・AI 利用の判定（fail-closed）
backend/app/patents/
  domain.py            段階・期限の目安・構成要件分解・AI 支援の指示文（RDH patents.py から移植）
  store.py             案件など（patents.sqlite3 に同居）
  service.py           横断検索・クレームチャート・ローカル専用 AI 支援（Jobs）
  router.py            /api/v1/patents
backend/app/workflows/rag.py, rag_graph.py, chat_tools.py, chat_persist.py   既存の RAG と AI アシスタント
frontend/src/pages/Patents.tsx, api/patents.ts, navigation.tsx（ナビ: AI → Patent）
```

data_dir 配下: `knowledge/{general,patents,standards}.sqlite3`、`knowledge/graphs/`、`knowledge/blobs/ab/<sha256>`、`knowledge-storage.json`。
旧 `data/rag/*.db` は消していない。回収前のスナップショットは `data/backups/knowledge-integration-20261119/`（MANIFEST.json に SHA-256）。

## 4. 守ること（設計の不変条件）

1. 呼び出し側に DB（分野）を選ばせない。ID は `分野:番号`。ファイルをまたぐ JOIN・トランザクションを作らない（後で分けられるように）。
2. SQLite はネットワーク上の FS に置かない（`storage.validate`）。NAS は目印（`.controldeck-knowledge`）が一致するときだけ使う。
3. 機密区分: 既定は confidential。cloud LLM 向けの横断検索では会話外の confidential を返さない（`store.search_all`）。公開にするのは公式経路の公開資料か利用者が明示したものだけ。
4. 特許案件・ヒアリングの内容は **ローカル専用のモデル**（`ai_gateway.require_local_target`）でだけ処理。検証できなければ止める。クラウドへ回さない。
5. AI の出力は `basis=model_inference`・`review_status=unreviewed` で保存し、原文の事実と混ぜない。
6. 規格の本文は `standards_license.evaluate` が許すときだけ保存・埋め込み・LLM 入力。購入した ISO/IEC/JIS/ASTM は既定でメタデータのみ。
7. async の中で同期の SQLite・ファイル I/O をしない（`asyncio.to_thread`）。起動時に重い処理をしない。
8. UI: ControlDeck の部品（`components/ui.tsx` の BottomSheet・SegmentedTabs・EmptyState・IconButton、`PageHeader`）を使う。主操作 1 つ＋その他は奥へ。利用者が知らなくてよい情報（内部 ID・分野名・モデル名・スコア）は既定で隠し、「詳細」で開ける。

## 5. 開発・検証の手順（環境の落とし穴）

- 本番の worktree（`app-strata`）で作業しない。開発は `git worktree add` した別ディレクトリで。
- シェルには `CONTROL_DECK_CONFIG` が本番を指して入っている。テストは必ず `env -u CONTROL_DECK_CONFIG` を付ける。
- Python: `/data1tb/ControlDeck/runtime-lifecycle/.venv/bin/python`（requirements が同じなら流用。`./deck.sh test` は空の venv を作って PyPI に行くことがある）。
  - 集中: `cd backend && env -u CONTROL_DECK_CONFIG <venv>/bin/python -m pytest -q -p no:cacheprovider tests/test_knowledge_store.py tests/test_patents.py ...`
  - 全体: 同じ形で `tests/` 全部（約 2 分半）。毎回 1 件ほど入れ替わって落ちる既知の不安定なテストがある（`test_workflow_events`・`test_workflow_cache` など）。単独で通るか確かめる。
- テスト目録: `env -u CONTROL_DECK_CONFIG <venv>/bin/python -m app.maintenance.test_catalog`。テストを足す前に `docs/test-catalog.md` で同じ観点を探す。テストファイル冒頭に目的と根拠を書く。
- Frontend: worktree に `node_modules` が無ければ、`package-lock.json` が同じことを確かめて `runtime-lifecycle/frontend/node_modules` へ symlink（コミットしない）。`npm run build`。
- 画面: `env -u CONTROL_DECK_CONFIG ./deck.sh e2e patents.spec.ts`（ポート 18765 の使い捨てインスタンス）。PC 幅と 320px。
- マージ: `gh pr merge` は自動許可判定に止められることがある。止められたら利用者に「マージ許可」をもらう。Release は `gh release create ControlDeck_v0.NNN -R souten-yd/ControlDeck --target <sha> --title "ControlDeck v0.NNN" --notes-file ~/<file>.md`。本番は更新機能で入る（手で merge・再起動しない）。
- 外部からの取得（pip の Pillow・Docling、モデル）は利用者の了承が要る。調べるための Web 閲覧・検索は不要。

## 6. このラウンド（R2, 2026-11-21〜）の作業一覧

状態: ☐ 未着手 / ◐ 途中 / ☑ 済み

| # | 作業 | 状態 |
|---|---|---|
| R2-0 | 本書と引き継ぎの決まり（AGENTS.md） | ☑ |
| R2-1 | 特許・規格の RAG の方式を Web・コミュニティ・公式で調べて評価し採用（`research-patent-standards-rag.md`） | ☑ 文書単位・全文か骨格・確定した書誌グラフ・節分割・図表の説明 |
| R2-2 | 文書単位の検索と「全文を入れる」回答（コンテキストの長さに合わせる。入らなければ節単位） | ☑ `documents.py`・`context.py`。Patent の AI 支援・アシスタントの道具が使う |
| R2-3 | 書誌の関係グラフ（引用・引用規格・ファミリー・分類・版の置き換え）と検索での 1 hop 拡張 | ◐ 番号の関係（引用する・される・別の版）と 1 hop は済み。ファミリー・分類（IPC）は未 |
| R2-4 | 規格検索: 規格番号・版の索引、条項単位の分割、利用判定の適用、規格の画面 | ☑ Knowledge →「規格の管理」。登録前に判定と理由（既定で隠す）、旧版の自動判定 |
| R2-5 | PDF の取り込み: ページごとの文章（座標）・埋め込み画像・OCR・ページ画像、図・表・グラフを VLM で記述（未確認の推論） | ◐ ページ単位・OCR・図のページ画像・埋め込み画像・VLM の説明は済み。座標（bbox）・WebP・レイアウト解析は未（Pillow・Docling の了承待ち） |
| R2-6 | AI アシスタントからナレッジ横断の検索（道具）。cloud なら機密を除く | ☑ `chat_tools.search_knowledge`（ローカル判定は local-only 契約と同じ） |
| R2-7 | 発明の音声ヒアリング（長時間の音声 → 文字 → ローカル AI が本質・構成要件・請求項案・追加質問に分解） | ☑ Patent → ヒアリング（話す）。区切りごとに保存、AI 支援「ヒアリングの分析」 |
| R2-8 | Patent 画面の UX 見直し（詳細を隠す・ControlDeck と統一）と API の実動作確認 | ☑ 文書の表示（DocumentSheet）・根拠は折りたたみ・内部情報は「詳細」。実 PDF（特開2026-116114）で取り込み・番号一致・骨格を確認 |
| R2-9 | テスト・評価 → マージ → Release → 本番へ反映 → 旧データの取り込み | ☑ v0.102、本番適用・取り込み・照合済み |

## 7. 未決事項（利用者に確認すること）

- QNAP 上の QnapLibrary に実データがあるか（あれば閉じる前に回収）。
- 公式経路の公開公報を承認なしで cloud LLM に渡してよいか（いまは機密扱い、手動で公開にできる）。
- 規格の組織契約・書面許諾の有無（あれば規格の本文を AI に使える）。
- Pillow（WebP 保存）・Docling（レイアウト解析）の導入（外部からの取得）。
- PatentBuddy・ResearchDataHub リポジトリを閉じる時期（`PLAN-knowledge-platform.md` §6）。

## 8. 外部の状態

- `qnaplibrary.service`（RDH v0.5.10）がこの PC で稼働中。中身は ControlDeck へ取り込み・照合済み。**最終的に削除する**（利用者合意 2026-11-21）。手順: 停止・無効化（データは残す）→ リポジトリを Archive → データとアプリを削除。削除の前に利用者に確認する。QNAP 上の QnapLibrary に実データがあるかは未確認。
- `cdapp-feature-patent-buddy.service` は v0.102 で停止・撤去済み。
- `/data1tb/ResearchDataHub-standards-license`: RDH 側に書いた規格判定のローカルブランチ（未 push）。ControlDeck へ移したので不要。
- GitHub: souten-yd/PatentBuddy（Draft PR #10・#23・#24・#29〜#40）、souten-yd/ResearchDataHub。閉じる手順は計画 §6。

## 9. 作業記録（新しい順に追記）

- 2026-11-21 Claude: v0.102 を本番へ適用（自動更新が 40 分ほど適用しなかったため、利用者の指示で更新の経路を直接呼んだ）。PatentBuddy の自動撤去を確認。旧データを取り込み、件数とハッシュを照合。起動直後に Add-on の health 取得の失敗が 1 回（撤去の最中、再発なし）。

- 2026-11-21 Claude: 画面（Knowledge の探す・資料の管理・規格の管理、文書の表示、Patent のヒアリング）。E2E `patents`・`knowledge`（1280/320）通過。実 PDF で本文冒頭の番号を自分の番号とする・要約の切り出しを直した。

- 2026-11-21 Claude: R2 の backend（文書単位の検索・全文か骨格・書誌の関係・規格・PDF の取り込み・アシスタントの道具・ヒアリングの記録と分析）。テスト `test_knowledge_documents.py` ほか 87 件通過。schema の覚え書きをファイルの inode で持つよう直した（ファイルが置き換わると表が作られない不具合）。

- 2026-11-21 Claude: 方式を調査して採用（research-patent-standards-rag.md）。
- 2026-11-21 Claude: 本書を作成。R2 を開始。PR #473 に Patent 画面・PatentBuddy 撤去・保存先の設定まで入っている（全体テスト 1859 passed、E2E patents 通過）。
- 2026-11-19 Claude: 統合ストア・旧データの取り込み・規格の判定・保存先の設定（PR #473）。本番データの複製で取り込みのリハーサル成功。
