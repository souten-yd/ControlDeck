# AIアシスタント Deep Research 強化設計

## 1. 問題と目標

従来のDeep Searchは、LLMが検索語を3件生成し、Web検索結果の先頭8ページを一度だけ収集して、そのまま1回要約していた。
探索結果を評価して次の検索へ進む制御、学術ソース、GitHubコード解析、反証探索、引用品質検証がないため、実態は
「検索結果付き要約」であり、数件の資料を表示して終わる場合があった。

強化後は次を満たす調査エージェントとする。

- 調査目的を複数の検証可能な問いへ分解する
- 最低2ラウンド探索し、結果から不足・矛盾・追加検索語を再計画する
- Web、学術メタ検索、直接URL、GitHub公開コードを横断する
- 検索結果の一覧だけでなく本文、abstract、リポジトリ構造、主要ファイルを読む
- 根拠の量ではなく、問いのcoverage、source diversity、引用可能性で終了を判断する
- 長文レポートと、調査履歴・取得上限・未解決事項を保存する

## 2. 参考にする公開動作

OpenAIのDeep researchは、計画、複数段階の探索、途中の方針転換、ソースの分析、引用付き構造化レポートを
一連のagentic processとして説明している。また実行中のplan/activity/source表示と、長時間の非同期実行を提供する。
Claude Researchも、複数検索が前の結果を受けて次の調査対象を決め、異なる観点とopen questionを系統的に探索すると説明する。

- [OpenAI: Deep research in ChatGPT](https://help.openai.com/en/articles/10500283-deep-research-fa)
- [OpenAI: Research with ChatGPT](https://openai.com/academy/search-and-deep-research/)
- [Anthropic: Using Research on Claude](https://support.anthropic.com/en/articles/11088861-using-research-on-claude-ai)

本実装は外部サービスの内部実装を複製せず、この公開されている設計特性をローカルLLMと公開検索で再構成する。

## 3. 調査ステートマシン

```text
PLAN
  ↓
SEARCH round N ─→ FETCH/FAN-OUT ─→ NORMALIZE/DEDUP ─→ ASSESS
  ↑                                                      │
  └──────── gaps / contradictions / next queries ────────┘
                                                         │ sufficient or limit
                                                         ↓
SELECT EVIDENCE → SYNTHESIZE → VERIFY CITATIONS → REVISE(if needed) → COMPLETE
```

### PLAN

LLMへJSON Schemaを指定し、目的、4〜8個のサブ質問、初期検索語、調査観点を生成する。
構造化出力を利用できないruntimeでは、元の質問、最新動向、比較、限界・反証を含む決定論的planへfallbackする。

### SEARCH / FETCH

- 各検索語をWeb検索し、ラウンドごとに最大3語は学術串刺し検索も行う。
- prompt中の直接URLは検索順位に関係なく候補へ入れる。
- Web結果はスニペットだけで終えず、公開ページ本文を取得する。
- URL、またはURLがない場合は正規化タイトルで重複排除する。
- 同じdomainだけで結果が埋まらないよう、最終根拠では通常domainを最大4件に制限する。
- private/loopback/link-local宛ては取得せず、検索結果を使ったSSRFを防ぐ。

### ASSESS

各ラウンド後に、coverage score、未解決の問い、矛盾、次の検索語をJSONで評価する。
最低2ラウンドかつ12件以上の根拠が揃うまでは、LLMが十分と判定しても終了しない。
終了条件は「十分」判定、最大4ラウンド、最大24検索呼び出し、または新規検索語がなくなった場合とする。

## 4. GitHub構造調査

GitHubリポジトリURLをpromptまたは検索結果から検出した場合、公開REST APIを読み取り専用で使用する。

1. repository metadataからdefault branch、説明、主要言語、更新時刻を取得
2. recursive treeからディレクトリ/ファイル構造を取得
3. README、manifest、lock/config、主要entry point、テスト、CIを優先選択
4. 調査語とpathの一致度、ファイル種別、サイズで最大12ファイルを選択
5. raw contentを取得し、ファイルpath付きの独立した根拠として登録
6. PythonはAST、TypeScript/JavaScriptは保守的な構文抽出で、関数、クラス、主要変数、import/export、
   API route、観測できる呼び出しを静的索引化
7. manifest・主要実装・テスト・CI・静的索引を横断し、既存機能の組み合わせで実現できる統合機能、
   接続点、依存関係、制約、追加実装をレポートで評価
8. treeがtruncated、rate limit、取得失敗の場合はcoverage limitへ明記

GitHubのrecursive treeは最大100,000 entries / 7MBでtruncatedになり得る。公開リソースの未認証REST APIは
IP単位のrate limitがあるため、1リポジトリあたりmetadata/treeの2要求と、選択ファイルだけのraw取得に抑える。

- [GitHub: REST API endpoints for Git trees](https://docs.github.com/en/rest/git/trees)
- [GitHub: REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)

## 5. 有限資源とコンテキスト制御

既定値は次の通り。時間をかけても無制限探索にはしない。

| 資源 | 上限 |
|---|---:|
| 探索ラウンド | 4（最低2） |
| 検索呼び出し | 24 |
| 発見候補 | 120 |
| ページ本文取得 | 32 |
| GitHub repository | 3 |
| GitHub主要ファイル | 12/repository |
| 最終根拠 | 36 |
| 1根拠の保存抜粋 | 6,000文字 |
| 最終LLM根拠context | 90,000文字 |
| レポート総出力 | 既定32,768 token（設定上限131,072） |

最終根拠は既存の会話内文献レジストリへ登録し、後続ターンでは`[R1]`等で必要な文献だけを再展開する。

### モデル個別Deep Research CTX

Deep Researchだから一律256Kへ変更する共通policyは持たない。Model画面の各モデル個別設定だけに専用CTXを置く。

- Ollama: `deep_research_num_ctx`（未設定時は同じモデルの通常`num_ctx`）
- llama.cpp: `deep_research_ctx_size`（0/未設定時は同じinstanceの通常`ctx_size`）
- runtime共通policy: 根拠文字数`evidence_context_chars`、HTTP待機上限`timeout_seconds`、
  レポート総出力`max_report_tokens`（既定32K、最大128K）だけを保持

Ollamaはrequest単位の`num_ctx`を使い、通常値と異なる場合は完了後に実行前のロード状態と通常optionsへ戻す。
管理中llama.cppはserver起動時にCTXが固定されるため、値が異なる場合だけ開始前に専用CTXで再ロードし、
成功・失敗・キャンセルのいずれでも`finally`で通常`ctx_size`と実行前の稼働状態へ復元する。同値なら再ロードしない。
外部OpenAI互換endpointはrequest単位変更を保証できないため変更せず、理由をmetadataへ記録する。

## 6. Source portfolio

planは問いごとに`web / academic / github / patent / market / direct`を選ぶ。

- academic: OpenAlex、Crossref、arXiv、Europe PMC、DBLP、DOAJを並列検索し、titleで重複排除
- patent: PatentsViewを検索し、発明のabstract、出願/公開情報、assigneeを一次根拠化。APIキーは既存の暗号化
  Workflow Secret `PATENTSVIEW_API_KEY`を利用し、値をログやmessage metaへ出さない
- market: SEC EDGARの企業開示を一次情報として検索
- direct: prompt内URLを検索順位に関係なく本文取得。HTML/textに加え、20MiB以下・最大80ページのPDFを抽出
- github: repository metadata/tree/主要ファイル/静的symbol索引

各sourceの失敗は握り潰さずcoverage limitへ型名と対象を保存する。別sourceの成功があれば調査自体は継続する。

## 7. レポート品質

最終promptは、エグゼクティブサマリー、調査範囲、サブ質問別分析、コード/構造分析、矛盾、限界、結論を要求する。
コード対象では関数・変数の役割、データフロー、外部依存、API境界、テスト済み範囲、機能間統合の実現可能性を
根拠path付きで評価する。静的解析結果とLLMによる推論を区別し、動的dispatch等の未確認事項は断定しない。
根拠にない推測は禁止し、事実主張には一時引用番号を必須とする。保存前に会話内文献IDへ変換する。

生成後に次を決定論的に検証する。

- 存在しない引用番号がない
- 根拠を伴う段落の割合（citation coverage）
- 引用された異なる資料数
- レポート文字数

coverageが55%未満、または主要根拠数に対して引用source diversityが不足する場合は、同じ根拠だけを使って1回修正する。
検証値はmessage metaへ保存し、UIとテストから評価可能にする。

最終レポートは単発8,192 token生成に依存せず、固定6章を独立生成する。各章末尾の完結markerが無い場合は
その章の続きだけを最大8回生成し、末尾重複を除去して結合する。総token予算は章へ均等配分し、前半章だけで
使い切らない。完結章数と未完結の可能性がある章名をmetadata/UIへ出し、短い改稿結果で長い草稿を置換しない。

## 8. 進捗・中断・永続化

既存のserver jobを維持し、WebSocket切断後も調査を続行する。plan、各round、検索、本文取得、coverage評価、
GitHub解析、統合、引用検証をprogress eventとして表示・checkpointする。ジョブキャンセル時は現在のprovider生成を停止し、
取得済み部分と状態をDBへ残す。

## 9. 評価

- unit: 2ラウンド以上、次検索語へのpivot、重複排除、上限、引用検証、fallback
- integration: assistant jobのprogress/sources/meta、会話内文献ID変換、履歴復元
- GitHub fixture: metadata/tree/raw取得からmanifest・source・test・CIが選択されること
- live: 実LLM + Web/学術検索で、従来の8件/1回要約を超えて探索し、長文引用レポートを保存すること
- UI: 320pxとPC幅でround進捗、資料数、完了後の文献カードにoverflowがないこと

### 9.1 実機評価結果（2026-07-17）

Ollama Qwen3.6-27B Q5_K_Mのモデル個別設定を`deep_research_num_ctx=262144`として、公開GitHub repositoryを含むコード評価を実行した。
4ラウンド、検索24回、発見81件、GitHub 1 repository、最終23件を使い、1,206.7秒で5,860文字の
レポートを生成した。引用101箇所、引用資料12件、不正引用0、引用段落率100%だった。

初回評価では最終長文生成が従来の固定300秒HTTP timeoutへ到達した。Deep Research専用timeoutを1,800秒へ
伝播するよう修正し、同じ規模の再評価を完了できた。公開repositoryのdefault branchが作業treeより古かったため、
モデルが現行AI実装を未実装と判定する差異も観測した。GitHub調査は公開取得時点の根拠であり、ローカル未公開差分を
推測で補わない。今後ローカルrepository adapterを追加する場合も、許可rootの`Path.resolve()`検証を前提に別sourceとして扱う。
