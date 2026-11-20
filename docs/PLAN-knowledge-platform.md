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
| R2（2026-11-21） | 文書単位の RAG（全文か骨格）・書誌の関係・規格の画面・PDF の取り込み（ページ・OCR・図・VLM）・アシスタントの道具・音声ヒアリング。詳細は `HANDOFF-knowledge-platform.md` §6 |
| K-PR5・7 | 未着手（公的データの取得、知識エッセンス） |

## 10. 統合後の再設計（R3、2026-11-21）

利用者「各リポジトリの計画を確認し、ControlDeck 側の計画に反映して開発を進める。バラバラに開発していたので、統合したときに整合性を持つように再検討してブラッシュアップしてから進める」。

### 10.1 各リポジトリの計画の棚卸し

ResearchDataHub（main、`docs/roadmap.md`・`proposals.md`・`feasibility.md`・`watch-notify-classification.md`・`patent-feature-plan.md`）と
PatentBuddy（main と Draft #10・#23・#24・#29〜#40、`docs/knowledge-graph-plan.md`・`knowledge-k*.md`・`source-collector-k7a.md`・`controldeck-ux-contract.md`）を読んだ。

| 元の計画・機能 | 元の状態 | ControlDeck での扱い |
|---|---|---|
| 特許案件・期限・請求項・構成要件・AI 支援 12 種 | RDH main 実装 | ✅ v0.102 で移植 |
| 2-gram 全文＋ベクトル、RRF、規格番号の正規化、PDF/OCR | RDH main 実装 | ✅ v0.102 で移植・強化 |
| **特許ウォッチ**（定期収集・新着だけを案件の請求項で順位付け・X/Y 候補・除外） | RDH v0.2 実装 | R3-2 で移植。収集は Jobs、外部へ送る検索語は利用者の承認 |
| **期限通知**（60/30/7 日前、しきい値ごとに 1 回） | RDH v0.2 実装 | R3-2。ControlDeck の通知（Web Push）。**本文に案件名を書かない** |
| **分類辞書**（IPC/FI/Fターム/CPC/ICS）と AI 支援への分類候補 | RDH v0.2 実装 | R3-1。種類 classification のコレクション。辞書は利用者が手で入手したものを取り込む |
| **審査基準・法令の引用**（AI 支援が [基準n] で引く） | RDH v0.2 実装 | R3-1。種類 law のコレクション |
| **文献ごとの AI 分析**（要約・請求項の確認、原文の版付き、未確認） | RDH/PB main 実装 | R3-1。注釈（annotations）として。原文が変わったら古い（stale）と表示 |
| 収集コネクタ（GDELT・RSS・EPO OPS・JPO・USPTO ODP・e-Stat・EDINET・SEC・OpenAlex） | RDH v0.2〜0.4・PB #40 | R3-2（特許系・RSS を先に、市場系は後） |
| 調査ジョブ | RDH v0.4 | ControlDeck の Deep Research が担う（移植しない） |
| Qdrant・外部 reranker | RDH v0.3 | 移植しない（RAM 行列で 100k 15 ms、reranker は ControlDeck 既存） |
| 出願人・分類・年の集計ダッシュボード | RDH v0.3 | R3-6 |
| 一括取り込み（NDJSON/CSV）・書き出し | RDH main | R3-6 |
| MCP（他の AI から検索・登録） | RDH main | R3-1（読み取りの道具だけ。MCP の相手は cloud とみなす） |
| AI クレームチャートの判定（構成要件ごと ○/△/×/?、引用の実在確認、人の承認） | PB Draft #10 | R3-3 |
| 根拠付き関係データ（GraphRAG） | PB Draft #23 | 番号の関係は v0.102。意味の関係は R3-4 の知識層で |
| 知識エッセンス K1〜K2（課題・原因・原理・機構・実装・効果・条件・限界の根拠付き抽出、人のレビュー、版失効） | PB Draft #29〜#33 | R3-4 |
| K3〜K5（知識マップ・横断比較・Idea Canvas・Atlas） | PB Draft #34〜#39 | R3-5 |
| 分野限定の公式取得・IPC 辞書の取得・USPTO 一括・容量表示・図の切り出し | PB Draft #40 | R3-2（取得）・R3-6（容量）。図の切り出しは R2 の続き |
| 認証・パスキー・ペアリング・自動更新・QPKG・公開資料 API・Add-on 契約 | RDH/PB | 移植しない（ControlDeck の仕組みで代わる） |
| QNAP を正本にする計画 | RDH | 取りやめ（PC の SSD が正本、NAS はバックアップ先。§2） |

### 10.2 統合で揃えること（整合性の決まり）

1. **情報の 3 層**（PB の設計を採用）: 原文（documents・chunks・assets）／知識（課題・原理などの命題、根拠の引用付き）／アイデア（利用者の仮説）。
   層をまたいで混ぜない。AI の出力は `basis=model_inference`・`review_status=unreviewed` で、人が承認しても「原文の事実」にはしない。
2. **根拠の版**: AI の注釈・知識・アイデアは、原文の版（文書の sha256 か本文のハッシュ）を持つ。原文が変わったら stale と表示し、古い承認を復活させない。
3. **機密**: 既定は confidential。外部へ出る経路は次の 4 つだけで、それぞれ条件を持つ。
   - クラウドの LLM: 公開・AI 利用可の文書だけ（v0.102 の `consumer`）
   - 外部 API への検索語（ウォッチ・コネクタ）: 利用者が「公開してよい語」と確認したものだけ。案件の自由記述を自動で送らない
   - 通知（Web Push は Apple・Google を通る）: 件数と「期限が近い案件があります」だけ。案件名・本文を書かない
   - MCP: 相手は cloud とみなす（機密を返さない）
4. **AI はローカル専用**: 案件・ヒアリング・知識抽出・アイデアは Host 管理の GGUF だけ。重い処理は Jobs と GPU の順番待ちに乗せる。
5. **1 つの検索の入口**: 画面・Patent・アシスタント・MCP・ウォッチの順位付けは、すべて `knowledge/documents.search_documents` を通す（別の検索を作らない）。
6. **UI**: ControlDeck の部品と ☰ のナビ。主操作 1 つ、内部の値は「詳細」へ。Patent の中は「資料 → 知識 → アイデア」の流れで、案件と行き来できる。
7. **常駐の処理を増やさない**: ウォッチの起動は既存の保守の周期で「期限の来たものがあるか」だけを見る。重い処理は Jobs。

### 10.3 段階（R3）

| 段階 | 内容 | 受け入れ |
|---|---|---|
| R3-1 | 文献ごとの AI 分析（要約・請求項の確認・stale）、分類辞書と審査基準のコレクション種別と AI 支援への引用、MCP の読み取りの道具 | 機密が MCP・cloud に出ないテスト、stale の表示、引用の [基準n] |
| R3-2 | 収集元（EPO OPS・JPO・USPTO ODP・OpenAlex・RSS・GDELT）、検索語の承認、特許ウォッチ（新着・順位付け・X/Y・除外）、期限の通知（案件名なし） | 外部へ送る語は承認済みだけ、新着だけを順位付け、通知の本文に機密が無い |
| R3-3 | AI クレームチャートの判定（○/△/×/?、引用文字列の実在確認、人の承認） | 引用が原文に無ければ「?」に落とす |
| R3-4 | 知識エッセンス（抽出・根拠照合・レビュー・正規化候補・stale） | 引用の偽造・版の変更・推論の事実化を防ぐテスト |
| R3-5 | 知識マップ（範囲を限った部分グラフ）・横断比較・Idea Canvas・アイデアから案件へ | 320px で単一ペイン、打ち切りを明示 |
| R3-6 | 集計ダッシュボード・一括取り込み/書き出し・保存先の設定画面と定時バックアップ・PDF の精度（了承後） | — |

ResearchDataHub と PatentBuddy を閉じるのは R3-2（収集とウォッチ）を移した後（それまで RDH にしか無い機能が残るため）。

## 9. 未決事項（利用者に確認）

- QNAP 上の QnapLibrary QPKG に実データがあるか。
- 公式経路で取得した公開公報を、承認なしでクラウド LLM へ渡してよいか（ResearchDataHub は 1 件ずつの承認制だった）。
- 規格の組織契約・書面許諾の有無（あれば規格の本文を AI に使える）。
