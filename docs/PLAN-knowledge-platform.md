# 統合ナレッジ基盤 — PatentBuddy・ResearchDataHub の ControlDeck への統合

状態: 2026-11-19 着手。計画の正本。続きを始める前に読み、終えたら §8「進捗」を更新する。

## 1. 決定事項（利用者、2026-11-19）

- **すべて ControlDeck に統合する。** PatentBuddy（ControlDeck Add-on）と ResearchDataHub（QNAP/Linux の保存・検索サーバー、内部名 QnapLibrary）は開発を止め、データを回収し、使わなくなったリポジトリを閉じる。
- **DB は分野ごとに分ける**（一般・特許・規格）。ControlDeck からは分割を意識しない。後で別の場所へ分けたくなっても動かせる作りにする。
- **横断検索する。** 守りたいのは「アイデアの流出」。分野ではなくデータの機密区分（confidential / public）で、外部（クラウド）LLM へ渡すかを決める。
- 規格の本文は利用規約上ほとんど AI に入れられない。判定方法は `docs/standards-license-policy.md`、判定コードは `backend/app/knowledge/standards_license.py`。
- PDF の解析（レイアウト・OCR・図表・VLM）は特許と一般で共通の処理にし、重い依存を持つので独立サービスにする（K-PR4）。

統合を選んだ理由（2026-11-19 の評価）: 分割の目的（NAS を正本・機密防壁・他端末から利用）は統合しても満たせる一方、Add-on 契約（トークン照合・iframe・Cookie なし読込・health 契約）、リポジトリごとの署名・リリース・更新ブリッジ、積み上がった Draft PR（PatentBuddy #10・#23・#24・#29〜#40）の結合費用が大きい。重い処理はすべて PC で行うため、NAS（J3455）は API 付きの遅いディスクになり、RAG の検索ごとに LAN を往復する。

## 2. 目標の構成

```
ControlDeck（PC）
├ app/knowledge/            統合ナレッジストア（正本）
│   store.py               分野別 SQLite・2-gram 全文・RAM 行列のベクトル・RRF・機密区分
│   migrate.py             旧 rag/*.db・ResearchDataHub からの取り込み
│   textproc.py            日本語 2-gram（ResearchDataHub から移植）
│   standards_license.py   規格の保存・AI 利用の判定（ResearchDataHub から移植）
├ app/workflows/rag.py      既存の呼び出し口（会話 RAG・ノード・Knowledge 画面）。中身は store へ
├ app/patents/ （K-PR3）     案件・請求項・期限・クレームチャート（PatentBuddy/ResearchDataHub から移植）
└ 文書解析サービス（K-PR4）   レイアウト・OCR・図表切り出し・VLM。結果を store へ保存

data_dir/knowledge/
  general.sqlite3   一般（Web・論文・会話・利用者資料）
  patents.sqlite3   特許（公報・PDF・案件）
  standards.sqlite3 規格（メタデータ・条項番号・許諾のあるもののみ本文）
  graphs/{collection}.sqlite3  GraphRAG の三つ組
  blobs/ab/<sha256>  原本 PDF・図・抽出結果（ハッシュ名、書いたら変えない）

NAS（QNAP）: blobs と DB スナップショットのバックアップ先（K-PR6）。稼働中の SQLite は置かない。
```

後で分けるための約束: 呼び出し側に分野を選ばせない／ID は `分野:番号`（`chunk_ref`・`doc_ref`）／ファイルをまたぐ JOIN・トランザクションを作らない／バックアップはファイル単位。

## 3. 棚卸し（2026-11-19）

### データ

| 置き場所 | 中身 | 件数 |
|---|---|---|
| `data/rag/*.db`（ControlDeck 旧 RAG） | 会話別 44 + その他 1 | 45 コレクション・158 文書・1,144 チャンク・1,144 ベクトル（12 MB） |
| `/data1tb/ResearchDataHub/data/library.sqlite3`（qnaplibrary.service v0.5.10） | 公報 PDF 1 件（JPA 2026116114、15 ページ）・抽出 44 チャンク・図 13 点・ローカル AI の要約/請求項確認 2 件 | 案件・請求項・ベクトル 0 |
| `data/feature-data/patent-buddy` | ジョブの記録だけ（空） | — |
| QNAP 上の QnapLibrary QPKG | **未確認**（利用者に確認する） | — |

回収前のスナップショット: `data/backups/knowledge-integration-20261119/`（`MANIFEST.json` に SHA-256。ResearchDataHub は SQLite Online Backup、quick_check ok）。

### コード

ResearchDataHub と PatentBuddy の main は同じ `qnaplibrary` パッケージ（約 7,800 行、標準ライブラリのみ）。PatentBuddy の Draft には約 16,000 行（知識エッセンス・Idea Canvas・Knowledge Atlas・公式ソース取得・図の切り出し）。

| 機能（元のファイル） | 行き先 | 判断 |
|---|---|---|
| 日本語 2-gram 全文検索（textproc, search, store） | `app/knowledge/` | **移植済み**（K-PR1） |
| 規格番号の正規化・規格の利用判定 | `app/knowledge/` | **移植済み**（K-PR1） |
| PDF 原本・抽出版・図の保存（pdf_store, pdf_images） | `app/knowledge/` の blobs・document_assets | 保存形式は K-PR1、取り込み処理は K-PR4 |
| PDF 抽出（pdf_extract: Poppler + Tesseract） | 文書解析サービス | K-PR4 で Docling・PaddleOCR-VL・VLM と合わせて作り直す |
| 機密防壁（privacy: local_only の確認） | ControlDeck の local-only AI 契約（PR #349）に一本化 | K-PR2 |
| 特許案件・請求項・期限・クレームチャート・AI 支援（patents, Draft #10） | `app/patents/`・Patent 画面 | **移植済み**（K-PR3。AI 支援はローカル専用のモデルだけ、結果は未確認の推論として保存） |
| 公的データ取得（connectors: GDELT・RSS・EPO・JPO・USPTO・e-Stat・EDINET・SEC、Draft #24・#40） | `app/knowledge/sources/` | K-PR5。外部へ送る検索語の承認は残す |
| 監視・通知（watch, notify） | ControlDeck の Jobs・通知 | K-PR5 |
| 根拠付き GraphRAG・知識エッセンス・Idea（Draft #23・#29〜#39） | `app/knowledge/graph` と Knowledge 画面 | K-PR7。Draft を読み直して必要な部分だけ |
| MCP（mcp） | ControlDeck の MCP（`addons/agent_mcp.py`）に道具を追加 | K-PR2 |
| 公開資料 API（public_catalog） | 使わない | 外部 AI へ出す経路は機密区分で代替 |
| 認証・パスキー・LAN ペアリング（auth, webauthn, local_auth） | 使わない | ControlDeck の認証 |
| 更新・QPKG・Linux 配布・Add-on 契約（updater, addon_*, qpkg, packaging） | 使わない | 統合で不要 |
| Qdrant 連携（qdrant） | 使わない（当面） | 100k で 15 ms（§7）。100 万を超えたら再評価 |

## 4. 段階（PR）

| PR | 内容 | 受け入れ条件 |
|---|---|---|
| **K-PR1** | 統合ナレッジストア・旧 RAG の取り込み（初回利用時）・ResearchDataHub 取り込み CLI・規格の判定の移植 | 既存の RAG テストが通る／旧ファイルを書き換えない／2,000 チャンク制限なし／2 文字語が当たる／100k で 50 ms 未満 |
| K-PR2 | 横断検索を AI アシスタント・Deep Research・MCP へ。答えを作る LLM が local かを ControlDeck の local-only 契約から判定し、cloud なら会話外の confidential を除く。根拠に `doc_ref`・ページ・図を付ける | 機密が cloud へ出ないテスト／会話内の根拠表示 |
| K-PR3 | 特許の業務（案件・請求項の版・期限・クレームチャート）を移植し、Knowledge 画面に特許を統合 | 移植した機能のテスト／PC 幅と 320px |
| K-PR4 | 文書解析サービス（pypdfium2・Docling・PaddleOCR-VL・VLM）。PatentBuddy の PDF 取り込みを置き換える | 図・表の座標と WebP、OCR の信頼度、VLM は model_inference で保存 |
| K-PR5 | 公的データの取得・監視・通知を Jobs へ | 検索語の外部送信は承認制のまま |
| K-PR6 | NAS へのバックアップ（DB スナップショット・blobs の複製）と復元の試験 | 復元して件数・ハッシュが一致 |
| K-PR7 | 根拠付き GraphRAG・知識エッセンス・Idea（PatentBuddy Draft の再評価） | 推論は確定事実に昇格しない |

## 5. データの回収手順

1. **K-PR1 を本番に入れる**（マージ → Release → 本番の更新）。旧 `data/rag/*.db` は、各コレクションを初めて使ったときに取り込まれる。旧ファイルは消さない。
2. 本番に入った後で `./deck.sh knowledge-migrate`（計画の表示）→ `./deck.sh knowledge-migrate --apply`。旧 RAG の残りと ResearchDataHub の文献を取り込む。
   - **新しいコードが入る前に `--apply` しない。** 古いコードが旧ファイルへ書いた分は、取り込み済みの印があるため取り込まれない。
   - ResearchDataHub は、取り込む前に `qnaplibrary.service` と PatentBuddy を止める（その後の書き込みを防ぐ）。
3. 件数とハッシュを照合する（`store.stats()`、blobs の SHA-256）。
4. 利用者の承認を得てから、旧 `data/rag/` と ResearchDataHub のデータを片付ける。それまでは残す。

前の版へ戻す場合の注意: 戻した後に旧コードが書いた会話の資料は、再び新しい版にしたとき自動では取り込まれない（旧ファイルには残る）。戻す期間が長い場合は、該当コレクションの `legacy_imports` の印を消してから取り込み直す。

2026-11-19 のリハーサル（本番データの複製、隔離した data_dir）: 計画どおり 45 コレクション・158 文書・1,144 チャンク・1,144 ベクトル、ResearchDataHub 1 文書・blobs 15・注釈 2 を 3.6 秒で取り込み。旧ファイルのハッシュは不変。2 回目は何も書かない。

## 6. リポジトリを閉じる手順（利用者の確認を得てから）

1. K-PR3（特許の業務）まで本番で動き、データの照合が済んでいること。
2. PatentBuddy は ControlDeck の更新で自動的に外れる（`features/registry.py` の `RETIRED_FEATURES`。サービス・Add-on の登録・実行ファイルを消し、`data/feature-data/patent-buddy` は残す。済んだら `data/features/patent-buddy/.retired`）。`qnaplibrary.service` は ResearchDataHub の取り込み（§5）の後に停止・無効化する（データは消さない）。
3. PatentBuddy の Draft PR（#10・#23・#24・#29〜#40）と ResearchDataHub の未マージのブランチ（`docs/standards-license-policy`）に、ControlDeck の該当 PR へのリンクを書いて閉じる。
4. 両リポジトリの README の先頭に「ControlDeck へ統合した」と書いて push し、GitHub で Archive する（読み取り専用。後で戻せる）。Release は残す。
5. QNAP の QnapLibrary QPKG を使っている場合は、そのデータを回収してから停止する（§3 の未確認事項）。

## 7. 計測（2026-11-19、Ryzen 7 9700X、隔離した data_dir）

| 項目 | 結果 |
|---|---|
| 100k チャンク×1024 次元の取り込み | 16.7 秒、DB 490 MB |
| ベクトル検索 warm（100k） | p50 15.5 ms / p95 17.6 ms |
| 旧方式（全件を読んで 1 件ずつ計算、100k） | 431 ms |
| ベクトル検索 cold（行列の読み込み込み） | 732 ms（書き込み後の初回だけ） |
| 1M×1024 の行列計算のみ | 115 ms（fp32 で 4.1 GB） |
| hybrid warm（100k、全行に同じ語がある最悪の合成データ） | p50 88 ms |

100 万チャンクを超えたら、fp16 化（RAM 半分）か近似検索（Qdrant・vec1）を測って決める。

## 8. 進捗

| PR | 状態 |
|---|---|
| K-PR1 | 統合ストア・旧 RAG/ResearchDataHub の取り込み・規格の判定（PR #473） |
| K-PR3 | **前倒しで実装**（2026-11-21、同じ PR #473）。Patent 画面（/patents）、`backend/app/patents/`、PatentBuddy Add-on の撤去 |
| K-PR6 の一部 | 保存先・保存対象・バックアップの設定（`app/knowledge/storage.py`・`backup.py`、`./deck.sh knowledge-storage`）。設定画面と定時バックアップは未着手 |
| K-PR2・4・5・7 | 未着手 |

## 9. 未決事項（利用者に確認）

- QNAP 上の QnapLibrary QPKG に実データがあるか。
- 公式経路で取得した公開公報を、承認なしでクラウド LLM へ渡してよいか（ResearchDataHub は 1 件ずつの承認制だった）。
- 規格の組織契約・書面許諾の有無（あれば規格の本文を AI に使える）。
