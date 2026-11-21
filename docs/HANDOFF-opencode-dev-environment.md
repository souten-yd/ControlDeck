# 引き継ぎ: OpenCode で全ての開発ができるようにする（開発用サンドボックス 第 2 段階）

最終更新: 2026-11-21（Opus）。全体の順番は `docs/ROADMAP.md`、計画と方式の評価は `docs/PLAN-opencode-dev-environment.md` の「開発用サンドボックス 第 2 段階」。
この文書だけで、まっさらな AI が続きを始められるように書く。進捗が動いたら、同じ PR の中で「作業一覧」と「作業記録」を直す。

## 目的と利用者の決定（時系列）

- 2026-11-21: 「Claude や Codex を使わなくても、ローカル LLM と OpenRouter などの API を使って、ControlDeck の拡張機能や別の新しいソフトを開発したい」。新しいコードは podman の箱と OpenCode でかなりできるが、**ControlDeck 本体の機能の開発が課題**。
  - 想定: リポジトリの写しで開発 → 動作確認はホストの ControlDeck の API で評価 → LLM ランタイムは箱で開発しても、モデルと GPU はホストのものを使う（モデルのフォルダに届く必要がある）→ 更新で本体へ入れる。
  - 「私の方法も、Web で見つけた方法もフラットに評価して」「既存のコードを流用し、車輪の再発明・同じコードの重複をしない」。
- 2026-11-21: Project Lab の preview（鍵付きの入口と WebSocket の中継、#494）を使い、**利用者が箱の作業中の画面を見て触り、その操作と気づきを記録して改善に使えるように**。「HMR をうまく使えないか」。
- 2026-11-21: OpenCode からの Web・ソフトの動作確認に **Project Lab を使えるように**。
- 2026-11-21: 本番のモデルは**いつでも止めてよい**。開発役は **OpenRouter（残高 3 ドル）に切り替えてよい**。止まったらローカル GPU かフロンティアで続ける。予算の都合で **Fable は使わない**（画面の設計も Opus と Sonnet）。
- 2026-11-21: ばらばらの計画を 1 つにまとめる（`docs/ROADMAP.md`）。**まずこの仕事（OpenCode で全ての開発）を優先**し、引き継ぎ文書を PR・マージしてから次へ進む。使いにくい点・限られる点は正直に書き、対策を示す。

## いまの状態

- 計画: 確定（`PLAN-opencode-dev-environment.md` 第 2 段階、`ROADMAP.md`）。
- P1（安全）: 実装中。ブランチ `feat/sandbox-network-boundary`（作業ツリー `/data1tb/ControlDeck/devenv`）。Sonnet が実装し、Opus が差分・テスト・実機を確かめてから PR にする。
- P2〜P6: 未着手。

## 実機で確かめた事実（2026-11-21、podman 4.9.3）

- 今の箱は `podman run --network host`（`sandbox.py::container_argv`）。箱の 127.0.0.1 はホストと同じ。
- ゲートウェイ（`models_mgmt/gateway.py::_authorize`）は loopback＋`x-requested-with: ControlDeck` なら鍵を見ない。**箱から本番のモデル一覧・chat・モデル切り替え（`/load`）を鍵なしで呼べた。**
- `opencode_config` は本番のゲートウェイ鍵を、箱のホームの `opencode.json` に平文で書いていた。
- `--network pasta`: 箱からホストへは届かない（127.0.0.1・LAN の IP とも）。インターネットには出られる。
- `pasta:--map-gw`: ホストへ届くが、本番からは送り元が 127.0.0.1 に見え、鍵なしで通る。
- `podman pod create --userns=keep-id --network pasta:-T,<ポート> -p 127.0.0.1:<ポート>:<ポート>`: pod の中は互いに 127.0.0.1 で届き、ホストへは `-T` で許したポートだけ、ホストからは `-p` のポートだけ見える。uid は 1000 のまま。
- ControlDeck は `0.0.0.0:8765` で待ち受けている。
- 本番のゲートウェイには OpenRouter の 2 モデル（`cloud:openrouter/deepseek/deepseek-v4.1-flash-1017`、`cloud:openrouter/qwen/qwen4-max`）が登録済み。
- Project Lab に「利用者の操作を検知・記録する仕組み」はまだ無い（全ブランチ・作業ツリーを探した）。あるのは preview の鍵付きの入口と WebSocket の中継（HMR）。要素を選ぶ処理は Workflow の `ScrapeViewer.tsx` の `PICKER_SCRIPT`（`cssPath`）にある。
- Project Lab の実行（`project_lab/runs.py::_launch`）: systemd の一時ユニット、書けるのはプロジェクトだけ、メモリ 2GB、CPU 200%、同時 3 本、対象は CodeDEV の中だけ。LLM による評価は未実装（`llmEvaluation: False`）。ControlDeck の MCP に実行の道具は無い（ファイルの受け渡しの許可だけ）。
- 箱の OpenCode には ControlDeck の MCP（`controldeck_addons`）を載せていない（`sandbox.py::opencode_config`）。

## ファイル地図

| 場所 | 役割 |
|---|---|
| `backend/app/maintenance/sandbox.py` | 箱（作成・pod・ミラー・箱の中の OpenCode の設定・CLI）。`container_argv` は OpenCode のプロジェクトの箱（`integrations/opencode/provider.py`）と共有 |
| `backend/app/maintenance/sandbox_gateway.py` | （P1 で新設）箱の外で動く中継。箱からホストへの唯一の口 |
| `backend/app/models_mgmt/gateway.py` | LLM ゲートウェイ（OpenAI 互換、`/api/v1/llm/v1`）。`resolve_instance`・外部 API への送信 |
| `backend/app/models_mgmt/cloud.py` | 外部プロバイダ（OpenRouter など）、呼び出し元ごとの送信の許可、費用・残高・月の上限 |
| `backend/app/project_lab/runs.py`・`webview.py`・`router.py` | Project Lab の実行・preview の中継（鍵付きの入口・WS）・API |
| `backend/app/addons/agent_mcp.py` | ControlDeck の MCP（`control_deck.report`・`research` など）。P2 で実行の道具を足す |
| `backend/app/resources/router.py`・`addon_runtime/resources.py`・`models_mgmt/resource_provider.py` | GPU の broker と貸し出し（P4 で `sandbox:<名前>` を足す） |
| `backend/app/models_mgmt/radiance.py` | podman に GPU を渡す実例（`--device /dev/kfd --device /dev/dri --group-add keep-groups`） |
| `backend/app/models_mgmt/runtime_provider.py` | ランタイムの共通部分（P5 の `dev:` 登録で使う） |
| `frontend/src/features/workflows/ScrapeViewer.tsx` | 要素を選ぶ処理（P2 で部品として切り出す） |
| `backend/app/maintenance/self_update.py` | 本番の更新（正式 Release だけ取り込む） |

## 守ること

- 箱に秘密を置かない（ゲートウェイの鍵・Cookie・利用者のトークン）。鍵が要るなら箱の外の中継が付ける。
- 箱からホストへの口は中継だけ。新しい道（MCP・Project Lab・GPU の申し込み）も中継に許可のパスとして足す。中継を通らない道を作らない。
- OpenCode のプロジェクトの箱（AI アシスタント用）の振る舞いは、意図して変えるとき以外は変えない（`container_argv` の既定はそのまま）。
- GPU のリセットを箱からさせない（SMU14）。VRAM を分ける仕組みは無いので、GPU は broker で借りたときだけ渡す。
- 記録（P2）は利用者がオンにしたときだけ。入力の値は既定で伏せ、パスワードは残さない。外部のモデルで読むときは呼び出し元ごとの送信の許可に従う。
- テストは `env -u CONTROL_DECK_CONFIG`（外さないと本番 data に書く）。
- `AGENTS.md` の規約（shell=True 禁止、秘密をログに出さない、パス検証、監査）。

## 開発と検証の手順（環境の落とし穴）

- 本番は `/data1tb/ControlDeck/app-strata`（main）。ここでブランチを切らない（自動更新が止まる）。作業は別の作業ツリーで（`git -C /data1tb/ControlDeck/app-strata worktree add -b <ブランチ> <場所> origin/main`）。
- backend のテスト: `cd backend && env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/app-strata/.venv/bin/python -m pytest -q -p no:cacheprovider`。全体で落ちる既知のフレーキーがある（メモ: 全体実行でだけ落ちる 4+1 件）。main でも落ちるかで切り分ける。
- 箱の中で `./deck.sh test` を流すと、空の venv を作って PyPI へ取りに行き止まる。元の venv の python で pytest を直接流す。
- 箱の node_modules は podman の `:O`（一時的な重ね合わせ）。`sandbox exec` の中の `npm install` は終わると消える。依存を足したら同じ exec の中で build まで行う。
- `./deck.sh sandbox` は本番の設定で動く（箱は本番 data の `sandboxes/` の下に作る）。試しの箱は確かめたら `destroy` する。
- podman 4.9 の既定のネットワークは slirp4netns。pasta は明示する。`host.containers.internal` は podman 5.3 以降が前提なので使わない。
- 画面の E2E は `./deck.sh e2e`（ポート 18765 の検証用インスタンス）。

## 作業一覧と状態

| 段 | 項目 | 状態 | PR |
|---|---|---|---|
| 計画 | 方式の評価・段階・全体計画・この引き継ぎ | 完了 | 本 PR |
| P1 | 箱ごとの pod（pasta、`-T` は中継のポートだけ、`-p` はミラーのポート） | 実装中 | — |
| P1 | 中継 `sandbox_gateway.py`（許可: モデル一覧・chat・許可した箱だけ `/load`。鍵・Cookie を渡さない。32 MiB・同時 4・ストリーム） | 実装中 | — |
| P1 | `opencode_config` から本番の鍵を外し、中継を向ける | 実装中 | — |
| P1 | 実機: 箱から本番へ直接届かない・許可外は 403・箱の OpenCode が中継越しに会話できる | 未着手 | — |
| P1 | 箱の開発役の既定のモデルを選べるようにする（OpenRouter / ローカル）。残高切れ（402）でローカルへ切り替える | 未着手 | — |
| P2a | ControlDeck の MCP に Project Lab の実行の道具（開始・状態・ログ・停止・preview の URL）。中継で箱にも見せる | 未着手 | — |
| P2b | 箱の作業ツリーを Project Lab の run として開く。podman の `--cgroups=split` で preview のポートの確認が通るか実機で | 未着手 | — |
| P2c | Vite のプラグイン（`apply: 'serve'`、`transformIndexHtml` で記録のスクリプト、`import.meta.hot.send` / `server.ws.on` で記録、`server.ws.send` で知らせ、`vite:error` を記録）。包みの設定を `vite --config` で渡す。記録は `.controldeck/feedback/*.jsonl` | 未着手 | — |
| P2d | 実行の結果・ログ・成果物の差分・撮影・気づきを `control_deck.report` で評価として出す | 未着手 | — |
| P2e | 記録中の表示・気づきボタン（スマホは長押しで要素を選ぶ）・評価の画面（Opus と Sonnet） | 未着手 | — |
| P3 | ミラーの LLM 要求を中継経由でホストへ | 未着手 | — |
| P4 | GPU の貸し出し（`sandbox gpu <名前> acquire/release`、broker の `sandbox:<名前>`、期限切れで回収） | 未着手 | — |
| P5 | 箱のランタイムを `dev:<箱>/<モデル>` で本番のゲートウェイへ一時登録し評価 | 未着手 | — |
| P6 | 試運転: W2 の最初の項目（`HANDOFF-web-collector.md`）を箱の OpenCode にやらせ、PR → Release まで | 未着手 | — |

## 未決事項

- P1: 中継に `/load` を既定で許すか。今は「箱を作るときに `--allow-load` を付けたときだけ」。本番のモデルの停止は許可済みなので、開発役の切り替えに要るなら既定にしてもよい。
- P2b: 箱の開発サーバーを Project Lab の run にしたとき、`web_preview_ready`（ユニットのプロセスがポートを使っているか）が podman のコンテナでも通るか。通らなければ、確認の仕方を箱の run 用に足す。
- P2c: Vite の公式文書（v8）の API が、ControlDeck の画面の Vite 5.4 でも同じに動くか（最初のテストで確かめる）。`base` を接頭辞にしたときの HMR の接続先（`server.hmr.path`）の合成の仕方は文書に無いので、実機で WebSocket の URL を見る。
- P4: 借りる時間の上限・VRAM の上限の既定。

## 外部の状態

- OpenRouter: 本番に鍵が登録済み、残高 3 ドル（利用者の言）。データ方針 deny。
- GitHub: `souten-yd/ControlDeck`。マージと正式 Release は常設の承認あり。

## 調査の記録（2026-11-21）

外部の調査は検索結果の要約が中心。採用する前に原典を確かめる。

- ROCm＋podman: `--device /dev/kfd --device /dev/dri --group-add keep-groups`（Red Hat AI Inference の手順、ROCm の docker-containers の文書）。VRAM を分ける仕組み（MIG 相当）は無く、コンテナはデバイスの隔離だけ（ROCm 公式）。AMD の CDI（`amd-ctk cdi generate`）は Instinct 向けの文書しかなく、gfx1201 では未確認なので使わない。
- 隔離の方式: gVisor の GPU は NVIDIA だけ（gvisor.dev）、Firecracker は GPU を渡せない（issue #849）、Docker Sandboxes の Linux での GPU は未対応（docs.docker.com/ai/sandboxes）。
- 権限の絞り方: タスクごとの短命・単一用途の資格情報、鍵は箱の外で付ける（コミュニティの記事）。
- Vite: `import.meta.hot.send` / `server.ws.on(event, (data, client))` / `server.ws.send` / `import.meta.hot.on`、組み込みのイベント `vite:error` など、`transformIndexHtml`、`apply: 'serve'`（vite.dev/guide/api-hmr、api-plugin）。プロキシが WS を通せないと Vite は直結しに行く（vite.dev/config/server-options）。
- iOS Safari: 背景に回ると WebSocket が閉じ、復帰後に繋ぎ直せないことがある（bugs.webkit.org 308073、247943）。
- OpenRouter: 累計購入が 10 ドル未満なら `:free` は 1 分 20 回・1 日 50 回、超えると 429、残高が負なら 402（openrouter.ai/docs/api-reference/limits）。ZDR・deny で提供元が絞られる（openrouter.ai/docs/guides/features/zdr）。DeepSeek のキャッシュは自動、Qwen は明示の `cache_control`、同じ提供元へ固定（sticky routing）（openrouter.ai/docs/guides/features/prompt-caching）。
- OpenCode とローカル LLM: edit の引数が JSON になる（opencode PR #21713）、約 40k トークンを超えると呼び出しを繰り返す（llama.cpp #19513）、ループ（llama.cpp #20164）。v2 の圧縮の既定は auto・keep.tokens 15000・buffer 15%、`agents.compaction.model` で要約のモデルを分けられる（opencode.ai/v2/docs/compaction）。
- 指して AI に直させる道具: Vercel Toolbar の「Copy for Agents」（URL・ビューポート・要素の位置・コンポーネント木を構造化して渡す）、Stagewise、React Grab（ソースの位置つきの文脈）、Agentation（MCP で双方向、ただしライセンスが PolyForm Shield でデスクトップだけ）。採り入れる工夫: 選んだ要素にセレクタ・ソースの位置・ビューポート・URL を付ける、気づきに AI が返事や解決を返せる、操作・コンソール・失敗した通信・撮影を 1 組にする、スマホは長押しで選ぶ。

## 作業記録

- 2026-11-21（Opus）: ルール（Opus が司令塔）の確認。既存の箱・ミラー・ゲートウェイ・broker・Project Lab の調査（Haiku）と外部の調査（Sonnet）。pasta と pod を実機で確かめ、P1 の設計を決めた。P1 の実装を Sonnet に回した。全体計画 `ROADMAP.md` とこの引き継ぎを書いた。
