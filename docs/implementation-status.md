# 実装状況

最終更新: 2026-07-15

## サマリー

| Phase | 状態 |
|---|---|
| 文書整備 | ✅ 完了 |
| Phase 1 — 認証 + レイアウト | ✅ 完了 |
| Phase 2 — アプリ管理 | ✅ コア完了（アイコン・TCP/HTTP/ファイル等のヘルスチェック対応済み） |
| Phase 3 — 監視 | ✅ コア完了（アラート通知を含む。アプリ別GPU等は残り） |
| Phase 4 — ファイル + ターミナル | ✅ コア完了（ごみ箱・再開可能アップロード対応済み） |
| Phase 5 — ワークフロー | ✅ コア完了（下記参照） |
| Phase 6 — リモートデスクトップ | ✅ コア完了（guacd トンネル + 接続管理 + ビューア） |
| Phase 7 — TOTP ほか | ✅ コア完了（TOTP/PWA/バックアップ。WoL はワークフローノードで対応） |

## Web通信・監視処理の軽量化（2026-07-15）

- 高周波の外向きpingはなく、常時通信は認証済みmetrics WebSocketの2秒更新だった。変更前の実ブラウザでは
  12秒に6 frame、`GET /apps`は5秒周期で3回。`/apps`はsystemd状態・プロセスツリー・待受ポートを走査し、
  平均28.7ms（最大39.1ms）だった
- 主負荷は2秒ごとに起動する`amd-smi metric --json`。実機で1回40〜60ms CPU、最大RSS約25MB、
  約23KB JSONを生成していた。複数AMD GPUからVRAM総量最大のdGPUを選び、同じ主要値
  （使用率・VRAM・温度・hotspot・電力・power cap）をamdgpu sysfsから直読するfast pathへ変更。
  sysfsが不完全な環境だけCLIへfallbackする
- アプリ状態の共有queryを15秒周期へ変更。操作時の楽観更新と完了後invalidate、非表示タブ停止は維持

検証: backend 181件成功、frontend本番ビルド成功。実サービスは`sysfs-amdgpu`で32GB dGPUを選択し、
10秒のservice cgroup CPUは1.67%相当、`amd-smi`周期プロセス0。旧CLI実測分を加えた変更前推計4.2%から
約60%削減。1280pxで31秒確認しmetrics 16 frame（初回含む）、`/apps` 3回、console error・横スクロールなし。
320pxのシステム画面も横スクロール・console errorなし、GPU値とmetrics WS継続を確認。

### 汎用ジョブ制御・Model進捗通信

- 互換用`jobs`表へ`job_controls`表を追加し、owner、冪等キー、priority、heartbeat、revisionを永続化。
  最大4同時実行の安定priority queue、queued/running cancel、再起動時interrupted化を実装
- REST、cancel、全体`WS /jobs/stream`でowner本人とownerなしsystem jobだけを返す。cancelは監査対象
- 個別ジョブstreamの0.4秒pollとModel画面の1〜2秒pollを通知Eventへ置換。全体WS更新を100msで束ね、
  高頻度token/eventでも中間通知を増幅させず最新revisionと最終状態を保持
- Playwright Chromiumの1280px/320pxで12秒撮影・通信計測し、jobs RESTは初回1回、jobs WSは1接続、
  横overflow 0、console error 0を確認。backend 198件、本番build成功

### ターミナルの緑色入力欄・画面欠落の追加再現

- Playwrightの320px touch viewportをキーボード相当の高さ390pxへ縮小して撮影。緑色部分は入力textareaではなく、
  永続化用tmuxの既定status barが最下段で入力欄のように見えていたものと特定
- Control Deckは上部にセッション切替UIを持つため、Control Deckのtmux sessionだけstatus barを非表示化。
  既存の永続sessionにも次回接続時に適用し、表示を1行増やす。他のユーザーtmux sessionへは影響しない

## チャット生成遅延・runtime選択基盤（2026-07-15）

- 実機Qwen3.6-27B + llama.cppでワークフロー生成を再現し、従来は内部推論がctx 2048まで1161 token続いて
  47秒後に本文JSONなしで422となることを確認。ワークフロー生成をthinking off、最大800 token、JSON Schemaへ変更し、
  11.55秒で有効JSON（quality 78）を返すようにした
- 永続チャットを既定thinking offかつ有限出力に変更し、OpenAI互換の`reasoning_content`を本文と分離。
  短文「1+1」の実機応答は初回出力・完了とも0.66秒、本文`2`、thinking 0文字を確認
- GPU/導入済みruntimeから Ollama、llama.cpp/ROCm、llama.cpp/Vulkan の利用可能な構成だけを返す
  RuntimePolicy APIを追加。選択状態、排他/共存、共通idle、チャット出力上限・思考、アシスタント名を保存し、
  llama設定UIのハードコード初期値も保存済み値へ修正
- AMD GPU電力上限を含む後続の詳細設計を`design-model-runtime-assistant.md`へ統合。電力制限機能自体は実装中

検証: backend 183件成功、frontend本番ビルド成功。runtime policyの保存・範囲検証・排他切替を単体テスト済み。

### AMD GPU 静音プロファイル

- 最大VRAMを持つAMD dGPUを選び、実機の電力cap、MCLK/SCLK DPM levelを読んで設定範囲を生成。
  AMD以外および変更非対応GPUではUIを表示しない
- 静音（最小210W・MCLK最大から1段低下）、バランス（255W・clock自動）、フルパワー（既定300W・clock自動）、
  カスタム（実機範囲の電力・MCLK/SCLK上限）をRuntimePolicyとしてサーバー保存。balanced/fullはMCLKを必ずautoへ戻す
- チャット、ワークフロー生成、永続チャット、LLM node、RAG、Ollama手動load、llama.cpp手動startおよび
  systemd `ExecStartPre`の全経路で、モデル起動・生成前に同じpreflightを適用
- `deck.sh service`の初回sudo認証でroot所有の専用helperと限定NOPASSWD sudoersを登録。
  Webプロセスはroot化せず、任意パス/コマンドや範囲外値を受け付けない

実機では静音profileを適用し、power cap 210W、MCLK設定上限1124MHz、負荷中最大875MHzを確認。
81 completion tokenは4.48秒。カスタムSCLK 500MHz制限時は実測最大583MHz、同等生成8.98秒となり、
性能低下を確認後に静音profile（SCLK自動）へ復帰してサーバー保存。1280px/320pxとも全profile・210W・1124MHzを表示し、
横スクロール・console errorなし。backend 191件成功、frontend本番ビルド成功。

### Model画面・llama.cppモデル個別設定の再監査

- ページ名称・説明をOllama固定からLLM Model管理へ変更。選択中runtimeのモデルを共通provider APIから表示し、
  llama.cpp選択時は「GGUF登録」、Ollama選択時は従来の取得/削除を提示
- runtime/backendの選択はシート最上位cardだけに統一。下部の重複backend cardを廃止し、未導入backendの追加と
  現在のGGUFモデル個別設定へ役割を限定
- llama.cppの型付き設定へ、CTX、最大出力、GPU層、K/V別cache量子化、Flash Attention、MTP/draft/ngram、
  MoE CPU配置、batch/ubatch、thread、sampling、mmap/mlockを追加。実バイナリ`--help`に存在する能力だけUI表示
- 自由入力`extra_args`を廃止し、未知キーを422で拒否。model pathはrealpath正規化、許可ルート、GGUF拡張子を検証。
  旧設定は新しい型付き既定値を補いながら移行する
- 保存後、稼働中ユニットの内容が変わった場合だけ再起動して設定を反映。同一設定のloadでは無駄な再ロードを避ける

実機Qwen3.6-27B Q5_K_Mを、新しい`n-predict/batch/ubatch/cache K/V/thread/sampling`引数入りsystemd unitで再起動し、
health 200と短文応答`2`（completion 2 token）を確認。Playwright Chromiumの1280px/320px双方で使用中runtime badgeが1個、
MTP/K/V/MoEが各1箇所、横overflow・console errorなし。複数GGUF catalog/router化は次段の残件。

### 独立AIアシスタント・ワークフロー生成の再評価

- `/assistant`を独立routeとして追加し、PCサイドバー、モバイル操作シート、command paletteから2step以内で起動。
  ワークフロー画面の既存入口も同じcomponentとして維持
- RuntimePolicyで保存したアシスタント表示名を画面へ反映。server DBの会話一覧を選択でき、新規・改名・削除を追加。
  改名/削除は所有者検証し、破壊的な削除だけ確認dialogを出して監査ログへ記録
- 独立routeから実機Qwen3.6-27B + llama.cppで副作用のない最小フローを生成し、10.87秒、品質78/100、
  schema/意味検証済みの開始→結果表示フローとして登録・エディタ遷移を確認。検証用会話/フローは終了後に削除

Playwright Chromiumの1280pxで直接route、会話名server保存、生成・登録を確認。320pxでは会話selectorと全モード、
入力欄が可視範囲内で、横overflow・console errorなし。チャット本文生成の既存実測は0.66秒。

### ワークフロー副作用なしdry-run・node metadata

- 従来の「ノード単体テスト」は実executorを呼び、app停止/file書込/Webhook等の副作用を起こし得たため、
  UI既定をexecutorを呼ばない「安全プレビュー」へ変更。既存APIの明示的実テスト互換は維持
- 編集中/保存済みworkflowを永続化や実行なしで静的走査し、構造/意味error、warning、到達wave、
  条件分岐/loop、予定副作用と必要capabilityを返すdry-run APIと結果sheetを追加。secret名/値もredact
- backend executor 35種とcontrol.loopの計36種にversion、side effect、capability、主要config/output型、
  retry/cancel/progress/dry-run対応metadataを追加。LLM catalogで欠落していた5種も統合し、集合差をテスト
- Playwright Chromium 1280px/320pxでfile.write→Webhookを撮影し、書込1/外部通信1の予定表示、
  executor未実行の明記、横overflow 0、console error 0を確認。詳細設計は`design-workflow-dry-run-metadata.md`

## Phase 2 / Phase 4 残件対応（2026-07-15）

- **アプリアイコン**: PNG / JPEG / WebP / SVG（2MB以下）を登録・更新画面からアップロード。実パスをAPIへ露出せず、
  認証・`apps.view` 権限付きエンドポイントから配信。SVGは script / foreignObject / イベント属性 / 外部参照を除去し、
  ラスター画像はマジックバイトを検証。置換・削除・アプリ削除時の後始末を監査対象化
- **ごみ箱**: 通常削除を `data_dir/trash` への移動に変更。ユーザー単位の一覧 / 復元 / 完全削除 / 空にする、
  保持日数・容量上限による古い項目の自動purgeを自己メンテナンスへ統合。元パス復元時も許可ルート検証を再実施
- **再開可能アップロード**: 4MBチャンク、厳密なoffset検証、進捗、中止、同じファイル再選択時の再開、
  完了時のatomic replace。途中ファイルは非公開の `data_dir/uploads` にユーザー所有者付きで保持

検証: `./deck.sh test` 165件成功、フロントエンド本番ビルド成功。悪意あるSVG、偽装画像、実パス非露出、
ごみ箱復元・完全削除、チャンク順序違反・再開・取消を自動テストで確認。実サービスを再起動して health API を確認し、
一時E2Eユーザーでファイル画面・ごみ箱を1280px / 320pxの実ブラウザで確認（横スクロール・console errorなし）。

## 永続電源予約（2026-07-15）

- Webプロセス内 `asyncio.sleep` を廃止し、予約確定時だけ `control-deck-power-schedule.timer/service` を
  systemdユーザーユニットとして生成・`enable --now`。取消時は無効化してユニットと状態を削除
- systemdユーザーtimerによりWebサービス再起動・SSH切断後も継続。`Persistent=false` として、
  PC停止中に期限を過ぎた予約が次回起動直後に誤実行されないようにした。実行ワーカーは一般ユーザーで動き、
  固定引数・配列subprocessでlogindへ要求し、予約実行と成否を監査ログへ記録。実行後はunitを自動回収
- UIは即時 / 15分 / 30分 / 1時間 / 3時間 / 8時間と現在予約の取消に対応

検証: `./deck.sh test` 168件成功、フロントエンド本番ビルド成功。実機で24時間後の検証用timerを作成し、
`Persistent=false` / `active` / `enabled` / 次回実行時刻を確認後、即時取消して `inactive` を確認。
実サービス上の予約ダイアログを1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。
破壊的な電源実行は未実施。

## アプリ別ヘルスチェック（2026-07-15）

- アプリ登録・編集でプロセス存在 / TCPポート / HTTP GET（期待status・本文文字列）/
  ファイル存在を設定可能。ファイルはrealpath正規化と許可ルート・拒否パス検証を強制
- バックグラウンドで15秒間隔に並列確認し、実行中プロセスのチェック失敗を `DEGRADED` として一覧・詳細へ反映
- `POST /apps/{id}/health-check` で手動確認でき、詳細画面に結果と確認ボタンを追加
- HTTP本文は先頭64KBまで、タイムアウトは0.2〜30秒。任意コマンド型は許可コマンド基盤がないため未開放

検証: TCP / HTTP status・本文 / 許可・拒否ファイル / API保存・手動実行 / `DEGRADED` 遷移を自動テストで確認。
実サービスの詳細・編集画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

## LLM runtime provider一般化（2026-07-15）

- Claude作業中の `Models.tsx` を破棄せず、Ollama / llama.cppを同じ「LLMランタイム設定」のタブへ統合
- providerカタログを追加し、Ollama設定URL、llama.cpp設定ポート、LM Studio等の代表ポート、管理アプリの待受ポートを
  OpenAI互換 `/v1/models` で並列検出。provider名・管理対象・導入/稼働状態・モデル一覧を共通形式で返す
- `GET /models/providers` を追加し、従来の `GET /workflows/llm-endpoints` も同じ検出サービスへ移行。
  既存の `base_url` / `models` 形式は維持し、チャット・ワークフローとの互換性を保持
- 設定画面に検出済みproviderとモデル数を表示。Ollama固有のモデル取得・削除・詳細設定は既存APIに分離したまま維持

検証: providerの稼働/停止判定、モデル列挙、一意ID、API、従来ワークフロー検出形式の互換テストと本番ビルドを確認。

### llama.cpp 複数GGUF catalog / instance（再監査補完）

- 従来の単一GGUF設定を互換mirror付きcatalogへ移行。alias・port・実体pathの一意性と最大8件を検証し、
  GGUFごとにhash付きsystemd user unit、起動/停止/health、自動起動、idle unload除外、最終利用時刻を管理
- Model画面からcatalogの選択・追加・改名・設定削除（GGUF本体は保持）を行い、各モデルにCTX、出力token、
  GPU offload、K/V量子化、Flash Attention、MTP/speculative、MoE、thread/batch/sampling/RAM設定を保存
- provider共通health APIと、Ollama/llama.cppを合算する同時ロード上限を追加。チャット、ワークフロー、RAGの
  endpoint利用時に対象instanceを活動中として記録し、誤ったidle unloadを防止
- 詳細設計を`docs/design-llama-multi-instance.md`へ記録し、旧単一設定/APIも互換維持

検証: `./deck.sh test` 206件成功、frontend本番ビルド成功。Playwright Chromiumで1280px/320pxの設定シートを
上端・下端まで撮影し、横overflow 0、console/page error 0。利用可能runtime、AMD GPU、共通load上限、
CTX、K/V cache、MTP、MoEの表示を確認。

### LLM runtime生成・stream・cancel共通契約（2026-07-16）

- provider lifecycleと分離した`LlmRuntimeProvider`生成契約を追加し、Ollama native JSONL、llama.cpp/外部
  OpenAI互換SSEをcontent/thinking/usage eventへ正規化
- workflow生成の非stream処理、永続chat worker、旧chat WebSocketを同じproviderへ移行し、GPU preflight、
  thinking、keep-alive、structured response fallback、秘密値を含めないエラーを統一
- request IDのactive registry、明示cancel、task cancel、WebSocket切断時のHTTP接続cleanupを実装。
  `chat.completion` job取消はprovider cancelを通知してからtaskを停止
- 詳細設計を`docs/design-llm-runtime-chat-contract.md`へ記録

検証: backend 211件成功、frontend本番ビルド成功。実機Qwen3.6-27B + llama.cppで新providerから短文`2`を0.71秒でstream完了。
長文生成を最初のchunkで明示cancelし、0.54秒、active request 0、完了後cancel=falseを確認。
実サービスの統合設定画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

### Provider共通モデルライフサイクル

- providerごとに `list/load/unload/delete/pull/configure` capabilityを公開し、共通adapterでモデル情報を
  `id/name/size_bytes/modified_at/loaded/details` に正規化
- `GET /models/providers/{provider}/models` とモデル単位の `load` / `unload` / `DELETE` を追加。
  Ollamaは全操作、llama.cppは設定中GGUFの一覧・起動・停止、外部OpenAI互換は一覧のみ対応
- 未対応の変更操作は `409`、未知provider/modelは `404`。ロード・アンロード・削除はprovider付きで監査
- 既存Ollamaモデル画面とllama.cpp起動・停止UIを共通APIへ移行。既存の固有APIも互換のため維持

検証: `./deck.sh test` 178件成功、フロントエンド本番ビルド成功。実サービスの共通APIからOllamaモデルを取得し、
1280px / 320pxの画面を確認（横スクロール・ログイン後のconsole errorなし）。破壊的なモデル操作は未実施。

## リモートデスクトップの環境互換性メモ（2026-07-12、重要）

- **Control Deck 側は完全動作**: WS トンネル・認証・guacd ハンドシェイク・ビューアは実機で確認済み
  （guacd が接続を受理し ready/size/image/cursor を配信）
- **ブロッカー**: Ubuntu 24.04 同梱の guacd 1.3.0（FreeRDP 2.11.5）は GNOME Remote Desktop 46
  （FreeRDP 3 系）と RDP ネゴシエーション非互換（全 security タイプで "wrong security type"）
- **対処**: ヘッドレスは **xrdp**（FreeRDP2 互換）を使う方式へ変更。`enable-desktop`（既定ヘッドレス）は
  xrdp を導入し、システムアカウントで PAM 認証、接続時に新規セッションを作成。GNOME RD の RDP は解放
- **接続フォームに security 選択を追加**（any/nla/tls/rdp）。Windows は nla、xrdp は any
- 既知の注意: xrdp + GNOME は「同一ユーザーが同時に 1 セッションのみ」の制約あり。画面を閉じた
  ヘッドレス運用（コンソール未ログイン）を想定

## この PC のヘッドレスデスクトップ操作（2026-07-12、ユーザー要望）

- **`./deck.sh enable-desktop`**（既定ヘッドレス）: GNOME Remote Desktop を `grdctl --system` で設定し、
  この Ubuntu を Web から操作可能にする。TLS 証明書を openssl で自動生成、RDP 認証情報を対話入力、
  guacd を導入、Control Deck に `127.0.0.1:3389` への接続「この PC（headless）」を自動登録
- **ヘッドレス（既定）**: 接続時に仮想セッションを作成（物理画面不要、画面を閉じた運用向け）。
  **リモート接続を有効化するまで仮想デスクトップは作られない**（enable-desktop を実行し、かつ
  クライアントが接続したときのみ）
- **`--active`**: 現在のログインセッションを共有（画面ミラー）。`grdctl`（ユーザー daemon）
- **`./deck.sh disable-desktop`**: 無効化
- 接続登録は `app.cli register-local-desktop`（パスワードは環境変数経由で argv に載せない、暗号化保存）
- セキュリティ: RDP:3389 は Control Deck 経由での利用を前提。外部はファイアウォール/VPN で遮断を案内

注: enable-desktop はシステム状態変更（サービス有効化・ポート開放・パスワード設定）を伴うため、
ユーザーが明示実行する。アプリ側が勝手に仮想セッションを作ることはない。

## Phase 6 リモートデスクトップ（2026-07-12）

- **guacd トンネル**: WebSocket（guacamole-common-js）↔ guacd(TCP:4822) を橋渡し。接続開始時の
  ハンドシェイク（select → args → size/audio/video/image → connect）をサーバー側で実施し、
  以降は raw ストリームを双方向パイプ（guacamole-lite 相当を Python で実装、外部依存なし）
- **接続管理**: RDP / VNC / SSH の接続 CRUD。パスワード等の機微パラメータは Fernet 暗号化保存、
  API 応答には含めない（has_password フラグのみ）。RDP は ignore-cert / display-update を既定化
- **ビューア**: guacamole-common-js（遅延ロード）。マウス + タッチパッド（タップ=クリック・長押し=右クリック）+
  キーボード、Ctrl+Alt+Del、画面リサイズ追従、モバイルはソフトキーボード呼び出し
- **導入**: `remote_desktop.enabled: true` のとき deck.sh が guacd の apt 導入を試みる。
  未導入時は UI に案内を表示し接続ボタンを無効化
- **バックアップ修正**: sqlite3 CLI 非依存に変更（venv Python の sqlite3 backup API で整合スナップショット）

検証: pytest 79 件成功（命令エンコード/パーサ、モック guacd での select→args→connect ハンドシェイク、
接続 CRUD、パスワード暗号化非漏洩）。Playwright で接続一覧・追加フォームを PC/モバイル確認。
ライブ接続は guacd + 実ホストが必要なためこの環境では未実施。

## バックアップ / リストア（2026-07-12、Phase 7）

- `./deck.sh backup [出力先]`: DB / 設定 / 暗号鍵 / RAG / アプリの systemd ユニットを tar.gz に。
  sqlite3 があれば WAL checkpoint 後にコピー（ログは容量のため既定除外）
- `./deck.sh restore <ファイル>`: 復元前に自動退避コピー、確認プロンプトつき、daemon-reload
- `GET /system/backup`（settings.manage）: 設定ページの「バックアップ」からブラウザで DL 可能
- 検証: backup→DB 改変→restore で復旧＋退避コピー生成を確認。DL API も 200/gzip 確認

## PWA 対応（2026-07-12、Phase 7）

- manifest.webmanifest（standalone、テーマ色、192/512/maskable アイコン）+ apple-touch-icon /
  apple-mobile-web-app メタ。ホーム画面追加・フルスクリーン起動に対応
- Service Worker（sw.js）: **アプリシェル（HTML/JS/CSS/アイコン）のみキャッシュ**。
  `/api/`・WebSocket・認証は一切キャッシュしない（機密を Service Worker に保存しない方針）。
  アセットは cache-first（ハッシュ付き名）、ナビゲーションは network-first + オフライン時シェルフォールバック
- 本番ビルドのみ SW 登録（開発時は登録しない）。アイコンは Chromium で SVG ロゴから生成
- TOTP リセット: `./deck.sh reset-totp <ユーザー名>`（`--all` で全員）でロックアウト復旧可能

検証: SW 登録・アクティブ化・manifest 読込を Playwright で確認。オフライン再読み込みでアプリシェルが
起動することを確認。

## TOTP 二要素認証（2026-07-12、Phase 7）

- 有効化: setup（QR=SVG data URI、Pillow 不要）→ 6 桁 verify → リカバリーコード 10 個を 1 回表示
- ログイン 2 段階: TOTP 有効時は `two_factor_required` → コード入力（6 桁 or 使い捨てリカバリー）
- 無効化はコード確認つき。シークレット/リカバリーコードは Fernet 暗号化保存、使用時に消費
- `require_totp_for_admin` で管理者に推奨バナー。bootstrap に SQLite 軽量マイグレーション追加
- 検証: pytest 71 件、Playwright + pyotp で全フロー E2E

## アラート通知（2026-07-12、Phase 3 残り完了）

- **ルール**: メトリクス（CPU/RAM/GPU/VRAM/CPU温度/GPU温度/ディスク使用率/アプリ停止）× 演算子（>/≥/</≤）
  × しきい値 × 継続時間（sustained）× クールダウン。アプリ停止は対象アプリ指定
- **通知チャンネル**: Discord / Slack / 汎用 Webhook。URL は Fernet 暗号化保存・表示時マスク・テスト送信可
- **評価ループ**: 15 秒間隔で評価。継続時間を満たすと AlertEvent 発火＋通知、条件解消で resolved。
  ウォッチドッグの心拍対象にも追加
- **UI**: 設定に通知チャンネル / アラートルール管理、ダッシュボードにアクティブアラートバナー
- **必要ソフトの自動導入**: deck.sh に tesseract / tmux の apt 導入（passwordless sudo 時）と
  Playwright ブラウザ（Chromium）自動導入を追加

検証: pytest 62 件成功。E2E で webhook 受信サーバーへの発火通知＋テスト送信を確認。

## ワークフロー拡張 v2（2026-07-12、ユーザー要望）

- **ノード追加**（全 25 種）: ループ（回数 / foreach、body/done 2 出力、`{{ID.item}}`/`{{ID.index}}` 参照）、
  変数セット、文字列操作（大小変換 / 置換 / 正規表現抽出 / 分割 / JSON 抽出 / テンプレート）、
  Markdown→HTML、ファイル読込 / 出力（追記可）/ 操作（copy/move/delete/mkdir）、
  LLM 生成（OpenAI 互換 = Ollama/vLLM/llama.cpp/OpenAI）、Web スクレイピング（CSS セレクター）、
  ブラウザ操作（Playwright）、OCR（tesseract）、Wake-on-LAN、
  SSH 実行（鍵認証 BatchMode、host 検証）、Git 操作（サブコマンド許可制）、C++ ビルド（CMake/Make）、
  Python 実行（**初期無効**、`security.allow_arbitrary_commands` で許可、venv python の -I -c 実行）
- **安全性**: すべて shell=False の配列実行。ファイル系は許可ルート検証を通す。SSH host / Git サブコマンドは
  ホワイトリスト。任意シェル文字列ノードは非提供
- **エディター刷新**: アイコン付きノード + カテゴリカラーバー、実行状態リング、ドットグリッド背景、
  ミニマップ、矢印マーカーエッジ、ループの反復/完了ハンドル
- **カスタムノード / スニペット**: 選択ノード群をスニペットとして localStorage 保存 → パレットから再挿入
- **ワークフロー入出力**: 定義を JSON でエクスポート / インポート（他環境への持ち運び）

検証: pytest 56 件成功（v2 ノード 13 件: 文字列 / 変数チェーン / Markdown / ファイル IO / WOL /
Git 許可制 / SSH host 検証 / Python 無効 / ループ foreach・count / スクレイピング）。
E2E で「foreach ループ → 大文字化 → ファイル追記」を実行し APPLE/BANANA/CHERRY 出力を確認。
Playwright でダーク/ライト・PC/モバイルのエディターとパレットを確認、横スクロール 0・エラーなし。

### RAG 構築 / DB 操作ノード（2026-07-12 実装）

- **db.query**: SQLite（許可ルート配下のファイル）または任意 SQLAlchemy URL（PostgreSQL 等）へ SQL 実行。
  名前付きパラメータ（`:id`）でバインド。先頭が SELECT/INSERT/UPDATE/DELETE/CREATE 等の SQL のみ許可、
  SQLite パスは許可ルート検証。SELECT は最大 500 行を dict で返す
- **rag.build**: テキスト（またはファイル）をチャンク分割 → OpenAI 互換 `/v1/embeddings`（Ollama の
  nomic-embed-text 等）で埋め込み → コレクション別 SQLite（data_dir/rag/{name}.db）へ保存
- **rag.query**: 質問を埋め込み、numpy コサイン類似度で top-k チャンクを取得。`{{ID.context}}` を
  LLM ノードへ渡すことで RAG パイプラインを構成（依存はベクトル DB 不要、numpy のみ）

検証: pytest 69 件成功。E2E で DB クエリ（テーブル作成→挿入→カウント）、RAG（フェイク埋め込みで
build→query マッチ）を実機ワークフローで確認。

## 自己メンテナンス / ウォッチドッグ（2026-07-12、ユーザー要望で追加）

- **systemd ウォッチドッグ**: `Type=notify` + `WatchdogSec=30` + `NotifyAccess=main`。
  起動完了時に READY=1、内部ヘルスチェック（DB 接続 / メトリクス収集の鮮度 / スケジューラー心拍）が
  正常な間のみ 15 秒間隔で WATCHDOG=1 を送信。ハング・内部異常時は systemd が SIGABRT → 自動再起動
- **自己メンテナンスループ**（起動 5 分後 + 1 時間間隔）:
  ログローテーション（copytruncate + gzip、`rotate_size_mb`/`rotate_generations`/`retention_days`、
  仕様 §11.3 対応）/ 期限切れセッション purge / 監査ログ保持（`audit_retention_days` 既定 180 日）/
  SQLite WAL checkpoint + optimize / ディスク残量自己点検（10% 未満で警告）
- **自己状態 API/UI**: `GET /system/self-status` + システムページ「Control Deck 自己診断」セクション

検証: pytest 43 件成功（ローテーション世代管理 / purge / ヘルスチェック / sd_notify フォールバック）。
実機で SIGSTOP によるハング模擬 → 30 秒で `Watchdog timeout` → SIGABRT → 自動再起動 → 復旧を確認。

## Phase 5 実装内容（2026-07-12）

- **エンジン**: ノードグラフ実行（トリガー → 逐次 + 条件分岐）。ノード別タイムアウト、
  ステップ上限 / ループ防止、実行キャンセル、ノードごとの入出力・エラー・時刻を保存
- **ノード**: トリガー（手動 / 間隔 / 毎日 / cron）、アプリ起動・停止・再起動・状態取得、
  HTTP リクエスト（期待ステータス検証）、条件分岐（eq/ne/gt/lt/contains、真偽 2 分岐）、待機、
  Webhook 通知（汎用 / Discord / Slack）、ファイル存在確認（許可ルート検証を通す）。
  テンプレート `{{ノードID.フィールド}}` で前段出力を参照可能。**任意シェル実行ノードは提供しない**（§20.6 安全モード）
- **スケジューラー**: 30 秒間隔で有効ワークフローの間隔 / 毎日 / cron（croniter）トリガーを評価
- **API**: workflows CRUD / run / enable / disable、workflow-executions 一覧・詳細・cancel（すべて RBAC + 監査）
- **UI**: 一覧（実行ボタン + 前回結果 + スケジュールトグル）、React Flow エディター（遅延ロード、
  カスタムノード、条件ノードは真/偽 2 ハンドル、ノードパレット + 設定ボトムシート、モバイル対応）、
  実行履歴シート（ノードごとの出力 JSON 表示、実行中は自動更新）

### UI テーマ / ロゴ（同日、PR #4）

- モード（システム / ライト / ダーク）+ アクセント 6 色 + OLED 完全黒。localStorage 永続化
- スライダーモチーフの SVG ロゴ（アクセント色連動、favicon 含む）

検証: pytest 37 件成功（定義検証 / テンプレート / 条件分岐グラフ / API CRUD+実行 / viewer 権限 /
スケジュール判定）。E2E で HTTP ヘルスチェック → 条件分岐 → true 側のみ実行を確認。
Playwright（1280 / 390px）でエディター・パレット・設定シート・実行履歴を確認、横スクロール 0・エラーなし。

## Phase 4 実装内容（2026-07-12）

- **ファイル**: 許可ルート限定（realpath + commonpath + 拒否リスト ~/.ssh 等）。一覧 / アップロード
  （複数・D&D・上書き確認・サイズ上限）/ ダウンロード / プレビュー（画像）/ テキスト編集
  （Monaco 遅延ロード、CDN 不使用、Ctrl+S 保存）/ mkdir / rename / copy / move / 削除（確認 + 監査）
- **ターミナル**: PTY + WebSocket。tmux があれば永続セッション（cdterm-*）、なければプロセス内 PTY
  フォールバック（切断→再接続でバッファリプレイ）。xterm.js 遅延ロード、モバイル全画面 +
  補助キーバー（Esc/Tab/Ctrl/矢印/^C/^D）、visualViewport 対応、リサイズ同期、監査記録
- **運用**: 単一エントリースクリプト `./deck.sh` へ統合（venv / Node 依存 / ビルド / 設定 / linger /
  管理者の不足を自動判定して整えてから起動。旧 scripts/* は互換ラッパー化）
- **修正**: ログインレート制限を「失敗のみカウント」方式へ（正規ユーザーの連続ログインで誤制限しない）

検証: pytest 31 件成功（ファイル API roundtrip / トラバーサル / symlink 脱出 / viewer 権限 /
ターミナルライフサイクル / ブルートフォース制限）。実機 E2E（アップロード→ダウンロード、
symlink→/etc が 403、WS ターミナルで echo 実行→再接続リプレイ）。Playwright で 1280/390/320px
確認（横スクロール 0、コンソールエラーなし、モバイル全画面ターミナル + キーバー表示）。

### ターミナルのモバイルキーボード・長文履歴再監査（2026-07-15）

- mobile software keyboardによるvisual viewportの縮小・移動へ、ターミナルroot自体を追従させた。
  bodyを固定せず背景scrollだけを止め、browserの自動panとの二重移動、入力位置や画面の欠落を解消
- xtermとtmuxの履歴を100,000行へ統一。接続時にtmuxの全履歴を最大16MiBでsnapshot再生し、
  再接続中の出力も復元する。上限超過時は無言で消さず切り詰め通知を表示
- attach直後の端末resetがsnapshotを消していた順序不具合を修正し、初期化→browser reset→snapshotの順へ統一。
  session IDも8桁hexへ限定し、PCヘッダーに全文コピーを追加
- `deck.sh`のservice登録判定が`pipefail`と`grep -q`でSIGPIPE終了し、登録済みでもforeground起動を試みる場合が
  あったため、`systemctl --user cat`による判定へ変更

検証: 実tmuxへ10,000行を出力し、Playwright Chromiumの1280px/320px双方で先頭・末尾を確認、末尾重複1回。
320pxでvisual viewportを`top=180 / height=300`へ移動してroot・補助キーバーが同範囲内、入力textareaが透明、
bodyが`position: static`のままであることを座標・computed style・撮影で確認。詳細は
`docs/design-terminal-mobile-history.md`。

## 実装済み機能

### バックエンド（FastAPI + SQLite WAL）
- 認証: Argon2id / サーバー側セッション（HttpOnly + SameSite Cookie、DB はトークンハッシュのみ）/
  CSRF（X-Requested-With 必須）/ ログインレート制限 / セッション一覧・失効
- RBAC: administrator / operator / viewer。REST・WebSocket 双方で権限依存性を強制
- 監査ログ: ログイン成功・失敗 / アプリ登録・編集・削除・起動・停止・強制終了 / ログ削除 / 電源操作
- アプリ管理: python_script / shell_script / executable / systemd_service（ユーザーユニット）
  - systemd ユーザーユニット生成（`cdapp-{id}.service`、引数エスケープ + インジェクション対策 + StartLimit 再起動ループ検出）
  - start / stop / restart / kill、8 状態マッピング、PID / 稼働時間 / CPU / RAM 取得
  - 環境変数は Fernet 暗号化保存、表示時は秘密キーをマスク
  - Python インタープリター自動検出 / プロジェクト検出（提示のみ）
- ログ: stdout / stderr の append 保存、tail / ダウンロード / 削除 / WS ストリーム
- 監視: psutil + GPU プロバイダー（amd-smi → rocm-smi → sysfs → nvidia-smi、失敗時 N/A）
  - 単一メトリクス WS ストリーム、1 分平均を SQLite へ保存（保持期間つき）、RAPL による CPU 電力推定
- 電源: reboot / shutdown / systemdユーザーtimerによる予約・取消（Web再起動後も継続、期限切れは再実行しない）

### フロントエンド（React + TS + Vite + Tailwind v4、gzip 約 99KB）
- ログイン / 認証ガード / 401 自動リダイレクト
- デスクトップ: 折りたたみサイドバー、Ctrl+K コマンドパレット（アプリ検索・起動停止・ページ移動・電源）
- モバイル: 下部ナビ 5 項目（中央「操作」→ボトムシート、電源は視覚分離）、Safe Area 対応、FAB
- ダッシュボード: CPU / RAM / GPU / VRAM タイル + スパークライン + 実行中 / 失敗アプリ
- アプリ: カード（主操作 1 個 + ⋯メニュー）、詳細ボトムシート、3 ステップ追加フロー（venv・エントリーポイント自動提案）、削除確認ダイアログ
- ログ: WS リアルタイム追従、仮想スクロール（2 万行保持）、正規表現検索、一時停止、stdout/stderr 切替、DL / 削除
- システム: ホスト / CPU コア別バー / GPU / ディスク / ネットワーク / 上位プロセス
- 設定: テーマ（システム / ライト / ダーク）、セッション管理、監査ログ閲覧（admin）
- 楽観的 UI（起動→即 STARTING）、WS 自動再接続 + 再接続中バッジ、タブ非表示時は購読停止

### スクリプト / 運用
- `scripts/setup.sh`（venv + npm + build + linger）、`run-dev.sh`（起動時 venv 自動構築）、
  `create-admin.sh`、`install-service.sh`（systemd ユーザーサービス、root 不要）

## 検証結果（2026-07-12、Ubuntu 24.04 実機）

- pytest 19 件成功（認証 / CSRF / 権限 / ユニット生成エスケープ / パストラバーサル / symlink 脱出 / マスキング）
- API E2E: 登録→起動→ログ→再起動→停止→ログ削除→監査を curl で確認
- **プロセス継続性**: Web バックエンド kill 後もアプリ継続、再起動後に同一 PID・稼働時間を取得
- WS: 未認証 403 / 偽 Origin 403 / 認証済みメトリクス・ログストリーム受信 OK
- GPU: AMD GPU を amd-smi で取得（使用率 / VRAM 23.3/32GB / 温度 / Hotspot / 電力 / ファン）
- UI: Playwright で 1280 / 390 / 320px を検証。横スクロール 0px、コンソールエラーなし。
  ダッシュボード / アプリ / ログ / システム / 設定 / 操作シート / 追加ドロワー / パレットのスクリーンショット確認
- systemd サービス: `control-deck-web` をユーザーサービスとして登録、非 root（一般ユーザー）で稼働、
  linger 有効化により SSH / ログアウト後も継続

## 既知の制約 / 次の作業

1. system レベルの systemd サービス制御は未対応。helper / polkit の権限境界設計が必要
2. アプリ別 GPU 使用量と、許可コマンド型ヘルスチェック（コマンド許可リスト基盤）は未実装
3. ファイルの圧縮・解凍と PDF / 音声 / 動画等の高度プレビューは未実装
4. PostgreSQL の運用切替・プラグイン基盤・provider共通pull/設定管理・OpenCode は未完
5. 電源 reboot/shutdown は API 実装済みだが、破壊的な実機実行は未検証

## 履歴

- 2026-07-16: LLM runtimeのcomplete/stream/cancel契約を統合し、永続chatとworkflow生成の重複処理を置換
- 2026-07-15: llama.cppを複数GGUF catalog/個別systemd unit化し、共通health/load上限とモデル別idle/自動起動を追加
- 2026-07-15: AIアシスタントを独立route化し、表示名・会話切替/改名/削除と実機ワークフロー生成登録を確認
- 2026-07-15: Model画面をruntime横断化し、llama.cppのK/V・MTP・MoE等の型付きモデル個別設定とAMD custom MCLKを追加
- 2026-07-15: ターミナルをmobile keyboardのvisual viewportへ追従。tmux/xterm 100,000行履歴、再接続snapshot、PC全文コピーを追加
- 2026-07-15: capability付きprovider adapterと共通モデル一覧・ロード・アンロード・削除APIを追加
- 2026-07-15: ClaudeのLLM設定タブ統合を流用し、providerカタログと共通エンドポイント検出APIを追加
- 2026-07-15: アプリ別ヘルスチェック（プロセス/TCP/HTTP status・本文/許可ルート内ファイル）、DEGRADED表示、手動確認UIを追加
- 2026-07-15: 電源予約をWeb内タイマーから永続systemdユーザーtimerへ移行し、予約・取消UIと実行監査を追加
- 2026-07-15: 完了表記を受け入れ条件で再監査。アプリアイコン、ごみ箱、再開可能チャンクアップロードを実装し、古い残件一覧を現状へ更新
- 2026-07-12: リポジトリ初期化。要求仕様原本と初期文書 8 点を記録
- 2026-07-12: PR #1 バックエンド（認証 / RBAC / 監査 / アプリ管理 / systemd / 監視 / 電源 / スクリプト）
- 2026-07-12: PR #2 フロントエンド（レイアウト / ダッシュボード / アプリ / ログ / システム / 設定）+ amd-smi パーサー修正
- 2026-07-13: リモートデスクトップ描画の根本修正（WS トンネルの Guacamole 命令境界保存）+ タッチ操作をタッチパッド方式に刷新（長押しドラッグ / 2本指右クリック / 3本指キーボード）+ タッチ端末は2倍解像度で接続し縮小表示
- 2026-07-13: ターミナル永続化の根本修正（tmux を systemd-run --user --scope で独立 cgroup 起動。サービス再起動で tmux ごと kill されていた）+ WS 自動再接続 + モバイル向けコピー/貼り付けシート
- 2026-07-14: 最新RAG/Deep Search強化: 外部検索ノードを4ソース統合(arXiv/Crossref/PatentsView特許[要無料キー]/SEC EDGAR市場調査)、Web検索ノード新設(DuckDuckGo/SearXNG・URL復元)、RAG検索にHyDE+マルチクエリ(RAG-Fusion)追加、Deep Researchノード(サブ質問分解→多ソース反復探索→引用付きレポート)。Deep Searchはノード組合せ(Web検索→スクレイピング→RAG構築→rag.query(HyDE)→LLM統合)でも構築可能
- 2026-07-14: Model(Ollama)管理タブ追加（一覧/取得[Ollamaレジストリ+HuggingFace GGUF検索]/削除/ロード/アンロード/詳細/keep_alive・アイドル自動アンロード[expires_at変化で活動検知]・呼び出し時オートロード・既定モデル設定・pull進捗WS）+ GraphRAG（LLMでトリプル抽出しグラフ化、graph検索モード、Knowledgeにグラフタブ）
- 2026-07-14: Knowledge(RAG) 超強化: RAG エンジン v2（コレクション設定/文書管理/6チャンク戦略[recursive/fixed/sentence/paragraph/markdown/parent_child]/SQLite FTS5 trigramで日本語全文/ベクトル・全文・ハイブリッド(RRF)検索/親子チャンク）+ Knowledge タブと管理ページ（コレクションCRUD・文書取り込み[テキスト/URL/ファイル]・検索テスト・設定）+ ノード統合強化（rag.build/rag.query に戦略・検索方式を選択追加、学術検索ノード[arXiv/Crossref]追加）。ノードは乱立させず統合方針
- 2026-07-13: Web スクレイピング強化: 抽出ビューワ（サニタイズ HTML をサンドボックス iframe に描画→要素クリックで CSS セレクタ自動生成、候補セレクタ一覧、抽出ワード↔結果の対比プレビュー）、複数抽出項目（各項目が出力変数、属性 text/html/href/src 等・複数取得選択可）、単一 selector との後方互換 + 下部ナビを fixed からフロー内配置に変更（iOS Safari 下部ツールバーによる浮き上がりバグ修正）
- 2026-07-13: ワークフロー v3（Dify/n8n 流）: トリガーに型付き入力フィールド定義（テキスト/長文/数値/選択/ファイル、実行時入力ダイアログ）、全後段から参照できる変数ピッカー（ノード出力メタデータ + 名前付き変数 {{vars.*}}）、LLM の稼働サーバー検出 + 構造化出力（json_object / json_schema + プリセット、非対応サーバーへはプロンプト埋め込みフォールバック）、全ノードに出力変数名設定、新ノード util.now / http.download
- 2026-07-13: GitHub 管理（リポジトリ登録でクローン/更新/保存/リバート/削除をボタン操作、~/ControlDeckApps へ格納、gh auth login のターミナル連携）+ 下部ナビ再編 + Overlay フォーカス奪取バグ修正
- 2026-07-13: アプリに Web ボタン（プロセスツリーの LISTEN ポートを検出しブラウザで開く。複数ポートは初回選択→ web_port として保存、設定編集で検出ポートから変更可）
- 2026-07-13: アプリ機能の使い勝手改善: テスト実行のストリーミング化（WS /apps/test-run/stream、常駐アプリ対応・停止ボタン）、実行 cwd を既定ホームに（test-run とユニットの WorkingDirectory）、パス入力にサーバー側ファイル選択ダイアログ（FilePicker）、リモートビューアのタッチ判定を pointer:coarse に精緻化
