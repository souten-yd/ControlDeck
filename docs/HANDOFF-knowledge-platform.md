# 引き継ぎ — 統合ナレッジ基盤・Patent・規格検索

この文書だけを読めば、別の AI・開発者がまっさらな状態から開発を続けられるように書く。
**進捗が動いたら必ず更新する**（`AGENTS.md`「引き継ぎ資料の決まり」）。更新日と、誰が・何を終えたかを §9 の作業記録に追記する。

- 計画（何を・なぜ・どの順で）: `docs/PLAN-knowledge-platform.md`
- 調査と方式の評価（特許・規格の RAG をどう作るか）: `docs/research-patent-standards-rag.md`
- 規格の利用可否（保存・AI 利用）の判断: `docs/standards-license-policy.md`
- 規約の原本: `AGENTS.md`、引き継ぎの入口: `CLAUDE.md`

最終更新: 2026-11-22 K1b（知識エッセンスの画面）を追加 — K1a・K1b 完了（GLM-5.3、開発の箱の OpenCode）

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
  judge.py             AI クレームチャートの判定（R3-3。patent_charts 表。引用が原文に無い ○/△ は ?）
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

### R3（2026-11-21〜、統合後の再設計。詳細は計画 §10）

| # | 作業 | 状態 |
|---|---|---|
| R3-0 | 両リポジトリの計画の棚卸しと整合性の決まり（計画 §10.1・10.2） | ☑ |
| R3-1 | 文献ごとの AI 分析・分類辞書と審査基準・MCP の読み取りの道具 | ☑ `knowledge/analysis.py`、コレクションの種類（law・classification）、`agent_mcp` の `control_deck.knowledge_search`（cloud 扱い） |
| R3-2 | 収集元・検索語の承認・特許ウォッチ・期限の通知 | ☑ backend（`knowledge/sources.py`・`patents/watch.py`）と画面（案件の「ウォッチ」、公報タブの「公開データを取得」「取得先の設定」）。EPO・USPTO の鍵は利用者が登録 |
| R3-3 | AI クレームチャートの判定（`patents/judge.py`・`ClaimJudge.tsx`。○△×?、引用の実在確認で ? に落とす、人の行ごとの修正と確認済み、請求項・原文の版で stale） | ☑ 実装（本番での評価は `~/ControlDeck-評価/`） |
| R3-4 | 知識エッセンス（抽出・レビュー）。PatentBuddy の K1a〜K2（#29〜#33）を作り直す。段 K1 | ☐ |
| R3-5 | 知識マップ・横断比較・Idea Canvas。PatentBuddy の K3〜K5・3 段の画面（#34〜#38、#23 の関係の保存）を作り直す。段 K2〜K5 | ☐ |
| R3-6 | 集計・一括取り込み/書き出し・保存先の画面・PDF の精度 | ◐ 集計（特許の分析）を前倒し: 書誌の読み取り（1 ページ目の OCR）・`/api/v1/patents/analytics`・類似マップ・案件に近い公報・書誌の手直し。画面は R3-UI |
| R3-UI | Patent を ControlDeck のページとして作り直す・分析のダッシュボード | ☑ `/patents`（案件・分析・公報）、`/patents/:id`（左に節、右に次の期限・次にやること）。Fable が作成し Claude が確認 |


### PatentBuddy の未反映の内容の反映（2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」）

PatentBuddy の Draft PR #10・#23・#24・#29〜#40 は 2026-11-21 に閉じた（書き込みなし。PR とブランチは残っていて参照できる）。
#10（AI クレームチャート）・#24（公報の取得）は ControlDeck に実装済み（R3-3・R3-2）。残りを次の順で **作り直す**（PB の PR をそのままマージしない）。

**持ち込まないもの**: PB 独自の認証（qlk_・qle_ のトークン、Unix ソケットの scope）、RDH の SQLite（document_analyses）を保存先にすること、Add-on の Host 改変（addon_host・addon_jobs・generation_contract の複製）、PC 側の vector_cache（2GiB）、素の JavaScript の画面、ソース文字列の grep で済ませるテスト。
**合わせるもの**: ControlDeck の権限（RBAC）と監査、ナレッジの保存先（knowledge のストア。文献の分析 R3-1 の隣）、検索の入口は `knowledge/documents.search_documents` 1 つ（計画 §10.2 の 5）、3 層（原文・知識・アイデア）、`basis=model_inference`・`review_status=unreviewed` の固定、引用は原文の版（revision）と Unicode の位置で持ち、版が変われば stale、人の判断は追記だけ（epoch は単調増加）、React の部品・`docs/ui-ux-guidelines.md`、挙動を確かめるテスト（目的と根拠）。
**LLM**: 抽出・比較・Idea Canvas の AI は **ローカル専用**（`local_only=True` と機密の印。2026-11-21 利用者「Patent の特許フローはローカルでしか使えないように」）。Idea Canvas は利用者の発明そのもの。

| 段 | 内容（PB の PR） | ControlDeck での作り方 | 状態 |
|---|---|---|---|
| K1 | 知識エッセンス: 抽出の契約（8 種: 課題・原因・原理・作用機構・実装・効果・制約・応用、1 文献 12 件まで、引用 1200 字まで）・ローカル AI での生成・検めて保存（引用が原文のその位置に 1 回だけあること、版の一致）・人のレビュー（承認・却下・差し戻し、追記だけ、版が変われば stale で旧承認は戻らない）・文献の画面のカード（#29〜#33） | `knowledge/essence.py`（契約と検め）・Job でローカル AI（`knowledge/analysis.py` の呼び方を使い回す）・ナレッジのストアに表を足す・API は `/api/v1/knowledge/documents/{id}/essences`（見る・始める・レビュー）・文献の画面（DocumentSheet）にカードとレビューの操作 | ☑ K1a・K1b |
| K2 | 承認済みのエッセンスの横断検索と、別文献 2〜3 件の比較（#36） | 検索は `search_documents` を通し、承認済みのエッセンスだけを返す（版と引用を再照合）。比較は並べて見せるだけ（AI の判断を足さない） | ☐ |
| K3 | 知識マップ（1〜2 hop、読み取りだけ）と技術仮説のリンク（承認済みの知識の間に `basis=inferred` で明示登録 → 別の操作で承認）。#23 の関係の保存（根拠つきのノード・エッジ、版で stale）を最小限（#34・#35・#23） | ナレッジのストアに関係の表（R2-3 の書誌の関係と同じ置き場に寄せる）。上限（ノード 61・エッジ 60）と打ち切りの表示 | ☐ |
| K4 | Idea Canvas: 利用者が自分の言葉で発明の仮説を書き、根拠（承認済みのエッセンス 0〜3 件）を固定して版で保存。AI は作らない・保存しない・出願しない（#37） | ローカル専用。保存時に根拠の版と承認を再照合。アイデアから特許の案件を作る導線 | ☐ |
| K5 | 資料 → 知識 → アイデアの 3 段の画面（PC 2 列・iPhone 1 列、引用元へ行って戻る）（#38） | Patent / Knowledge の React の画面として作り直す | ☐ |
| K6 | 容量の表示・図の候補の位置・PNG/WebP の圧縮（#40 の一部）。Knowledge Atlas（#39）は K3 の後で要否を決める | R3-6 にまとめる。公報の取得（EPO・USPTO）は ControlDeck の `knowledge/sources.py` を使い、PB の取得は持ち込まない | ☐ |

**進め方**: 段ごとに PR。実装は開発用の箱の OpenCode（`docs/HANDOFF-opencode-dev-environment.md`。モデルの順番は GLM-5.3 → 無料の高性能 → ローカル）に任せ、Claude（Opus）が指示書・監査・実機の確認・マージを持つ。箱に渡すのは PatentBuddy と ControlDeck のコードだけで、利用者の発明の内容（案件のデータ）は渡さない。PB の diff は `gh pr diff <番号> -R souten-yd/PatentBuddy` で読む。

## 7. 未決事項（利用者に確認すること）

- QNAP 上の QnapLibrary に実データがあるか（あれば閉じる前に回収）。
- 公式経路の公開公報を承認なしで cloud LLM に渡してよいか（いまは機密扱い、手動で公開にできる）。
- 規格の組織契約・書面許諾の有無（あれば規格の本文を AI に使える）。
- Pillow（WebP 保存）・Docling（レイアウト解析）の導入（外部からの取得）。
- PatentBuddy・ResearchDataHub リポジトリを閉じる時期（`PLAN-knowledge-platform.md` §6）。PatentBuddy の Draft PR は 2026-11-21 に閉じた（利用者の指示。書き込みなし）。リポジトリの Archive は反映（K1〜K6）が済んでから。

## 8. 外部の状態

- `qnaplibrary.service`（RDH v0.5.10、**利用者のサービス**（`systemctl --user`）。システムのサービスではない）は 2026-11-21 に利用者の指示で停止・自動起動を無効にした（ポート 17871 も閉じた。データ `/data1tb/ResearchDataHub/data` は残す）。それまでこの PC で稼働していた。中身は ControlDeck へ取り込み・照合済み。**最終的に削除する**（利用者合意 2026-11-21）。手順: 停止・無効化（データは残す）→ リポジトリを Archive → データとアプリを削除。削除の前に利用者に確認する。QNAP 上の QnapLibrary に実データがあるかは未確認。
- `cdapp-feature-patent-buddy.service` は v0.102 で停止・撤去済み。
- `/data1tb/ResearchDataHub-standards-license`: RDH 側に書いた規格判定のローカルブランチ（未 push）。ControlDeck へ移したので不要。
- GitHub: souten-yd/PatentBuddy（Draft PR #10・#23・#24・#29〜#40 は 2026-11-21 に閉じた。ブランチは残す）、souten-yd/ResearchDataHub。Archive は反映の後。

## 9. 作業記録（新しい順に追記）

- 2026-11-22 GLM-5.3（開発の箱の OpenCode、K1b）: 知識エッセンスの画面。`frontend/src/features/knowledge/EssenceSection.tsx`
  （DocumentSheet の「知識エッセンス」の節）・API を `api/knowledge.ts` に追加・Job の進捗表示を `features/knowledge/JobProgress.tsx`
  へ移して共用。抽出の開始（ローカルのモデルが無い 503・利用条件 422 は理由をそのまま表示）→ 種類ごとのカード（要約・
  AI の推論（未確認）・引用の折りたたみ・前回のレビュー）→ 承認・却下・差し戻し（メモ任意）→ stale の印・捨てた件数・
  見るだけの利用者への出し分け。E2E `knowledge-essence.spec.ts`（5 passed、1280/320・ライト/ダーク、API は差し替え）と
  knowledge・patents の回帰（17 passed）。本物のローカルモデルでの抽出と撮影 PNG の目視は未確認（検証用インスタンスに
  モデルが無い・このモデルは画像を読めない）。

- 2026-11-21 Claude: 利用者「各ボタンなどの機能動作も確認して」。`frontend/e2e/button-sweep.spec.ts`（Patent・Knowledge のボタンを入力を入れて押し、結果まで確かめる。押した一覧は test-results/button-sweep.json）。
  見つけて直した不具合 2 件: (1) 埋め込みのサーバーに届かないと資料の取り込みが丸ごと失敗していた → 本文と全文索引は残し、埋め込みは「作り直し」で後から（`rag.add_document` の embedded/embed_error）。
  (2) 作り直しで一部が失敗しても Job が「成功」と出ていた → `rebuild.apply_job` が失敗の件数と理由を出して Job を失敗にする。
  本番の画面での確認は Chrome 拡張がつながらず未実施（利用者が Chrome をつなぐと選択）。v0.106 の判定の本番評価は `~/ControlDeck-評価/2026-11-21-AIクレームチャートの判定.md`（近い公報 ○3△1・遠い論文 ×1△3、引用 7 件すべて原文にあり）。

- 2026-11-21 Claude: R3-3 AI クレームチャートの判定。請求項 N × 1 件の文献を構成要件ごとに ○△×? で判定（ローカル専用・Jobs・`patent_charts`）。
  ○/△ は原文からの引用が必須で、NFKC・空白と句読点を落とした原文に引用が無ければ「?」に落とす（「…」の省略は前後の塊がそれぞれあれば可、6 文字未満は根拠にしない）。
  人が行ごとに判定を直す（human_verdict、直すと未確認へ戻る）・確認済み/不採用。請求項の版・原文の版（analysis.revision）が変わると stale。
  文献が予算に入らなければ骨格＋請求項に当たった箇所を渡し、引用の照合は常に全文で行う。AI に渡せない文献（規格など）は 422。
  ついでに Patent の入力欄の幅の不具合（`input` の w-full が w-28 に勝ち、「クレームチャートを作る」が縦に潰れていた）を `inputBase` で直した。
  テスト `tests/test_patent_judge.py`（5）、全体 1894 passed、E2E patents 9（判定の表示は応答を差し替え、PC 幅・320px）。
  決まりを追加（#501）: 評価は本番で行い、結果を `~/ControlDeck-評価/` に日本語で残す（AGENTS.md「評価は本番で」）。

- 2026-11-21 Claude: v0.104 を本番へ適用し、`./deck.sh knowledge-rebuild --apply` を実行（利用者の了承済み、事前の写しは `data/backups/knowledge-before-rebuild-20261121/`）。AI の回答 65 件を推論に、公報を原本から作り直し（書誌・ページ・図のページ 12）。ただし埋め込みが「1 件 512 トークンまで」で断られ（日本語 1200 文字の断片）、図表の読み取りは CLI がサーバーの外で GPU の順番待ちを通れず動かなかった。埋め込みに送る文字を 600 で切る・図表の読み取りは画面の「作り直し」（サーバーの中）だけにする修正を入れた。**本番では、作り直しの画面から図表の読み取りを 1 回実行する必要がある。**

- 2026-11-21 Claude: R3-2 の画面と Patent の作り直し（Fable）を合わせた。本番の RAG を調べ、AI の回答 57 件が原文扱い・別の会話の資料が横断検索に混ざる・公報に埋め込みと書誌が無いことを見つけ、会話の資料は会話の中だけ、AI の回答は推論、`./deck.sh knowledge-rebuild`（作り直し）を作った。案件を消してもウォッチ・候補・通知の記録が残り、番号の使い回しで次の案件に出る不具合を直した（Fable が E2E で発見）。結果は日本語で書く決まりを AGENTS.md に追加。全体 1887 passed、E2E 14 passed。

- 2026-11-21 Claude: v0.103（#498）を Release・本番へ適用。R3-2 の backend（EPO OPS・USPTO ODP・OpenAlex・RSS・GDELT の取得、検索語の公開確認、ウォッチ、期限の通知は案件名なし）をブランチ `feat/knowledge-r3-2`（worktree `/data1tb/ControlDeck/knowledge-r3-2`）に。全体 1882 passed。SQLite の表の有無を接続ごとに確かめるよう変えた（inode は使い回される）。Patent の画面は Fable が `feat/knowledge-r3`（worktree knowledge-platform）で作業中、終わったら r3-2 に載せて 1 つの PR にする。

- 2026-11-21 Claude: 利用者「特許の開発フローが浮いた窓になっている。ControlDeck と統合した UI/UX に」「グラフで傾向・書誌・請求項・類似特許を見る機能はどうなっているか（Fable を使ってよい）」。確認すると、RDH の集計画面は未移植、PB の 10 種のグラフは ChatGPT の会話内で作った試作で、アプリには無かった。書誌の読み取り（JP 公報の INID、1 ページ目の書誌が画像なら OCR。実公報で出願人・IPC・日付・請求項の数を取得）と分析の API を作った（`patents/analytics.py`、テスト `test_patent_analytics.py`）。画面は Fable の子エージェントに frontend だけを任せた。PR #498（R3-1）はマージ待ち（自動許可の判定で止まった）。

- 2026-11-21 Claude: R3-1。文献ごとの AI 分析（要約・請求項の確認、ローカル専用、原文が変わったら stale として持ち越す）、審査基準・分類辞書のコレクションと AI 支援への引用、MCP のナレッジ検索（cloud 扱い）、資料の作成画面で詳細設定を隠した。全体 1869 passed（落ちた 1 件は既知の不安定なテスト）、E2E knowledge・patents 通過。

- 2026-11-21 Claude: R3 を開始。RDH・PB の計画を棚卸しし、統合の決まり（3 層・根拠の版・外部へ出る 4 経路・ローカル専用・検索の入口は 1 つ）を計画 §10 に書いた。RDH main にあって未移植だったもの（ウォッチ・期限の通知・分類辞書・審査基準・文献ごとの分析）を見つけた。

- 2026-11-21 Claude: v0.102 を本番へ適用（自動更新が 40 分ほど適用しなかったため、利用者の指示で更新の経路を直接呼んだ）。PatentBuddy の自動撤去を確認。旧データを取り込み、件数とハッシュを照合。起動直後に Add-on の health 取得の失敗が 1 回（撤去の最中、再発なし）。

- 2026-11-21 Claude: 画面（Knowledge の探す・資料の管理・規格の管理、文書の表示、Patent のヒアリング）。E2E `patents`・`knowledge`（1280/320）通過。実 PDF で本文冒頭の番号を自分の番号とする・要約の切り出しを直した。

- 2026-11-21 Claude: R2 の backend（文書単位の検索・全文か骨格・書誌の関係・規格・PDF の取り込み・アシスタントの道具・ヒアリングの記録と分析）。テスト `test_knowledge_documents.py` ほか 87 件通過。schema の覚え書きをファイルの inode で持つよう直した（ファイルが置き換わると表が作られない不具合）。

- 2026-11-21 Claude: 方式を調査して採用（research-patent-standards-rag.md）。
- 2026-11-21 Claude: 本書を作成。R2 を開始。PR #473 に Patent 画面・PatentBuddy 撤去・保存先の設定まで入っている（全体テスト 1859 passed、E2E patents 通過）。
- 2026-11-19 Claude: 統合ストア・旧データの取り込み・規格の判定・保存先の設定（PR #473）。本番データの複製で取り込みのリハーサル成功。
