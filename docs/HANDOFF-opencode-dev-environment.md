# 引き継ぎ: OpenCode で全ての開発ができるようにする（開発用サンドボックス 第 2 段階）

最終更新: 2026-11-21（Opus、P1 完了）。全体の順番は `docs/ROADMAP.md`、計画と方式の評価は `docs/PLAN-opencode-dev-environment.md` の「開発用サンドボックス 第 2 段階」。
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
- P1（安全）: 完了（実機で確認）。箱ごとの pod と中継。箱の開発役のモデルは順番方式で実装済み（実機の確認待ち）。次は P2a。
- P2〜P6: 未着手。

## 開発対象と機能（2026-11-22）

作業場は「ControlDeck の改修か」ではなく**機能**で分ける。meta の `capabilities`: `llm`（ローカル LLM を借りる）・`tools`（ControlDeck の道具＝MCP）・`mirror`（ControlDeck の複製）・`gpu`（P4 未実装）。開発対象 preset: `app`（tools・branch）、`llm_app`（llm・tools・branch）、`controldeck`（llm・tools・mirror・pr。元が本体のときだけ）。強制は中継の起動引数（`--allow-llm`/`--allow-tools`）、`model_chain`、箱の環境、`mirror_start`、`adopt` で行い、画面は選ぶだけ。作成権限は controldeck と gpu が settings.manage。実装は `maintenance/workspaces.py`（PRESETS・plan・required_permission）と `sandbox.py`（Sandbox.capabilities）。

**作るアプリがローカル LLM を使う標準の変数**: `llm` のある箱の中には `OPENAI_BASE_URL`（`http://127.0.0.1:<中継のポート>/api/v1/llm/v1`）と `OPENAI_API_KEY=sandbox`（意味の無い値。本物は渡さない）が入る。作るアプリは OpenAI 互換の標準の書き方でそのまま呼べばよい（ControlDeck 専用の書き方は不要）。`llm` が無い箱は中継が 403 を返す。

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
- （P2a 前の事実）箱の OpenCode には ControlDeck の MCP を載せていなかった。P2a で、中継経由の bridge として載せた（`sandbox.py::opencode_config`）。

## ファイル地図

| 場所 | 役割 |
|---|---|
| `backend/app/maintenance/sandbox.py` | 箱（作成・pod・ミラー・箱の中の OpenCode の設定・CLI）。`container_argv` は OpenCode のプロジェクトの箱（`integrations/opencode/provider.py`）と共有 |
| `backend/app/maintenance/sandbox_gateway.py` | （P1 で新設）箱の外で動く中継。箱からホストへの唯一の口 |
| `backend/app/models_mgmt/gateway.py` | LLM ゲートウェイ（OpenAI 互換、`/api/v1/llm/v1`）。`resolve_instance`・外部 API への送信 |
| `backend/app/models_mgmt/cloud.py` | 外部プロバイダ（OpenRouter など）、呼び出し元ごとの送信の許可、費用・残高・月の上限 |
| `backend/app/project_lab/runs.py`・`webview.py`・`router.py` | Project Lab の実行・preview の中継（鍵付きの入口・WS）・API |
| `backend/app/addons/agent_mcp.py` | ControlDeck の MCP（`control_deck.report`・`research` など）。P2a で `lab_*` の実行の道具を足した（箱へは中継が許可の道具だけ通す） |
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
| P1 | 箱ごとの pod（pasta、`-T` は中継のポートだけ、`-p` はミラーのポート） | 完了 | 本 PR |
| P1 | 中継 `sandbox_gateway.py`（許可: モデル一覧・chat・許可した箱だけ `/load`。鍵・Cookie を渡さない。32 MiB・同時 4・ストリーム。外部のモデルは既定で断る） | 完了 | 本 PR |
| P1 | `opencode_config` から本番の鍵を外し、中継を向ける | 完了 | 本 PR |
| P1 | 実機: 箱から本番へ直接届かない・許可外は 403・箱の OpenCode が中継越しに会話できる | 完了（`implementation-status.md` 2026-11-21） | 本 PR |
| P1 | 箱の開発役のモデルは順番（`DEFAULT_MODEL_CHAIN`: Go の GLM-5.3 → Zen の無料 2 つ → ローカル LLM。`--run` は上限・残高切れ・レート制限で次へ、`models` で変更。根拠: 利用者 2026-11-21「GLM を使い切り、無料で高性能なものを順次」） | 実装済み（実機の確認待ち） | 本 PR |
| 開発の作業場 1 段目 | 関門 `./deck.sh dev-check`・開発のスキル `controldeck-development`・レビュー役を別のモデルに（作業がローカルなら最初の外部、外部ならローカル）・目録の道具が手書きの説明を残す／venv を作らない・箱の git の作者 | 実装・単体テスト済み（実機の箱でのレビュー役の動作は未確認） | 本 PR |
| 開発の作業場 2 段目 | AI アシスタントの OpenCode の会話から作業場（箱）を選ぶ・作る・消す。箱の中で `opencode serve`（serve_port・pod の公開）・作業場の API・画面・持ち主の確認 | 実装・単体テスト・E2E 済み（実機の箱での会話は未確認。秘密の会話の network none は段 2 の別作業） | 本 PR |
| 開発の作業場 3 段目 | 作業場の結果のカード（差分の要約・関門を Job で・取り込む）。本体は GitHub に PR（マージしない・settings.manage）、CodeDEV は元のリポジトリへ ws/<名前> のブランチ | 実装・単体テスト・E2E 済み（実機の push・gh・箱の中の関門は未確認）。安全: ホストでは box.repo の中で git を動かさない（箱の中で調べ、export.git へ fetch して比べる・push する）。設定（meta）は箱の外 `<sandboxes>/.meta/` へ移し、古い箱は検査して移す | 本 PR |
| 開発の作業場 4 段目 | 行ごとの差分（export.git から・256KB で打ち切り）と、結果のカードの「マージ」（settings.manage。前提・区分 L0/L1/L2・L2 は確認の文言。`gh pr merge --squash --delete-branch --match-head-commit`。Release はしない） | 実装・単体テスト・E2E 済み（実機の `gh pr merge` は未確認・モック） | 本 PR |
| P2a | ControlDeck の MCP に Project Lab の実行の道具（開始・状態・ログ・停止・preview の URL）。中継で箱にも見せる | 実装・単体テスト済み（実機は未確認）。`lab_projects/run_start/run_status/run_logs/run_stop`、中継に MCP の 2 パスと許可の道具、箱の持ち主 `--owner`、環境変数ファイル、箱の bridge | 本 PR |
| P2b | 箱の作業ツリーを Project Lab の run として開く。podman の `--cgroups=split` で preview のポートの確認が通るか実機で | 未着手 | — |
| P2c | 見張りモード（Vite 以外にも）。manifest の `watch`（paths・ignore・build・mode: restart/rerun/native・debounce）、見張り役 `live.py`（watchfiles → build → command を unit の中で）、鍵付き入口の HTML への `live.js` 差し込み、WS `__cd/live`（status・reload・note、画面の記録を `.controldeck/feedback/<run_id>.jsonl` へ）、AI の知らせは `.controldeck/live/<run_id>.notes` に 1 行書く。実行のメモリ上限は設定 `run_memory_max_mb`。Vite は native（HMR はアプリ自身）で、記録だけ使う | 実装済み（単体テスト 43 件）。実機（systemd・Vite・iPhone・C++）は未確認 | — |
| P2d | 実行の結果・ログ・成果物の差分・撮影・気づきを `control_deck.report` で評価として出す | 未着手 | — |
| P2e | 記録中の表示・気づきボタン（スマホは長押しで要素を選ぶ）・評価の画面（Opus と Sonnet） | 未着手 | — |
| P3 | ミラーの LLM 要求を中継経由でホストへ | 実装・単体テスト済み（実機は未確認）。`mirror_config` が `llm_upstream`（箱の中の中継）を書き、ミラー（ISOLATED）のゲートウェイと内部の AI 機能が上流へ送る。中継は機密の印を通す | 本 PR |
| P4 | GPU の貸し出し（`sandbox gpu <名前> acquire/release`、broker の `sandbox:<名前>`、期限切れで回収） | 未着手 | — |
| P5 | 箱のランタイムを `dev:<箱>/<モデル>` で本番のゲートウェイへ一時登録し評価 | 未着手 | — |
| P6 | 試運転: W2 の最初の項目（`HANDOFF-web-collector.md`）を箱の OpenCode にやらせ、PR → Release まで | 未着手 | — |

## Claude が要る部分を減らす: 画面の見た目（2026-11-22）

- 課題: OpenCode の GLM は画像を読めないので、作った画面の見た目は Claude（Opus）が目で見ていた。
- 仕組み: `./deck.sh ui-review <png...> [--spec 説明]`（`app/maintenance/visual_review.py`）が、E2E の撮影を本番のゲートウェイの「画像を読めるローカルのモデル」へ送り、`{"issues":[{severity,where,what,fix}],"summary"}` を返させる。cloud: は使わない（機密の印も付ける）。モデルが無ければ「未実施」。1 回 4 枚まで、縮めず、4 MB 超は飛ばして記録。
- 関門: `dev-check` の「画面の見た目」。frontend を変えたとき、`frontend/test-results/**/*.png`（frontend の変更より新しいもの）が無ければ不合格、high で不合格、medium は注意、モデルが無ければ未実施。`--skip-visual` で外せる（報告に「外した」と出る）。
- 使い方: 画面を変えたら E2E で 320 と 1280、ライト・ダークを撮り、dev-check を通す（スキル `controldeck-development`）。
- 限界: モデルの目は Opus より粗い。high を見落とす・過剰に挙げることがある。最終の見た目の判断（デザインの良し悪し）は人・Opus。

## 不具合と課題の一覧（開発中に見つけたもの）

見つけたら、ここに足して優先度を付ける。直したら状態と PR を書く。

| # | 内容 | 影響 | 優先 | 状態 |
|---|---|---|---|---|
| 1 | AI アシスタントの OpenCode の「プロジェクトの箱」（`provider.py` が `container_argv` を pod 無しで使う）は今も `--network host`。箱の中から本番のゲートウェイへ鍵なしで届き、`/load` も呼べる | 開発用の箱と同じ穴。ただしこちらはゲートウェイを使うのが本来の用途 | 中 | 未着手。P1 の中継（許可のパスを絞る）をプロジェクトの箱にも使うかを決める |
| 2 | （済み: 9 を参照）本番 data の `sandboxes/` に、過去の作業の箱が約 30 個残っている（`cite`・`diag`・`vp`〜`vp6` など）。OpenCode のプロジェクトの箱の一時フォルダ（`project-tmp` など）も同じ場所 | ディスクと見通し。古い箱は中継のポートを割り当て直す | 低 | 未着手。中身（未 push のコミット）を確かめてから片付ける。`sandbox list` に最終使用日と未 push の有無を出す |
| 3 | `opencode_config` は `provider._config_payload` を通すので、鍵が未発行だと本番に鍵を新しく作る（設定ファイルには入れない） | 副作用だけ | 低 | 未着手 |
| 4 | backend の全体テストで、回ごとに顔ぶれの変わるフレーキー（今回 2 件、2 回目は 0 件） | 判定が揺れる | 低 | 既知（メモ） |
| 5 | OpenCode Zen の無料モデル（`opencode/deepseek-v4.1-flash-free` など。保存・学習なしと公式に明記）は「OpenCode の中からしか使えず、他の道具は 403」（opencode.ai/docs/console/harnesses）。ControlDeck のゲートウェイを通せず、箱の OpenCode から opencode.ai へ直接繋ぎ、Console の API キー（`OPENCODE_API_KEY`）を箱へ渡すことになる | 「箱に秘密を置かない」と衝突。コードが外部へ出る（ControlDeck は公開リポジトリなので本体の開発なら影響は小さい。特許などの未公開情報は不可） | 中 | 登録欄を用意（設定 → API キー →「開発」、`sandbox new --no-zen` / `zen <名前> on|off`、`opencode --model opencode/deepseek-v4.1-flash-free`）。利用者がキーを登録したら箱で使える（Console のアカウント作成と、無料の範囲だけのキーが要る） |
| 6 | 中継のログに httpx の INFO（要求の URL）が 1 行ずつ重なる | 見た目だけ | 低 | 未着手 |
| 7 | （済み: souten 以外の 12 個を無効化・セッション取り消し・監査あり、2026-11-21）本番の DB に管理者のアカウントが 13 個ある。ほとんどは過去の E2E・試験の使い捨て（`codex-*`・`e2e-*`・`mf-e2e`・`strata-acceptance-*`・`pb-analysis-test-*` など）。`sandbox new` は管理者が複数だと `--owner` を求める | 使い捨ての管理者が残るのは安全上よくない。E2E が本番に向いていた頃の名残 | 高 | 利用者の判断待ち（無効化か削除）。持ち主は `--owner souten` で指定して進める |
| 8 | MCP の `lab_*` の道具は本番の ControlDeck の中にあるので、本番が更新されるまで箱から見えない | 実機の通し確認が本番の更新待ち | 中 | v0.116 で確認済み（箱から 7 道具・lab_projects が動く） |
| 9 | `data/sandboxes` が見かけ 372GB（実際は約 3.4GB）。pytest の一時フォルダの 90GB の見かけの GGUF が、箱の共有 /tmp・プロジェクトの一時フォルダ・ホストの /tmp に溜まっていた | バックアップや容量の表示で巨大に見える | 高 | 直した（`tmp_path_retention_policy = "failed"`、箱ごとの /tmp）。古い箱 28 個も利用者の許可で消した（未 push の 2 件は `data/backups/sandbox-patches-20261121/`） |
| 10 | 検証用インスタンス（`./deck.sh e2e`）でターミナルと携帯の E2E が 22 件落ちる（main でも同じ顔ぶれ） | 画面の変更の確認でターミナル系が見られない | 中 | 未着手。ターミナルの実体（tmux）が検証用インスタンスで動くか調べる |
| 14 | E2E `model-provider-common`・`resource-load-profile-ui` が main でも落ちる（「閉じる」のボタンが 2 つで strict mode 違反・要素が見つからない） | モデル管理の画面の確認が E2E でできない | 中 | 直した（2026-11-22）。前者は画面shell刷新 ad73f23（☰ サイドバー）の「サイドバーを閉じる」が `name: '閉じる'` の部分一致で増えての strict 違反 → 閉じるを sheet の dialog で絞る。後者は退避(yield)機構の廃止 4deb560 で確かめる UI 自体が画面から無くなっていた → 後継の「Add-on への GPU の譲り方」パネルの表示・連動に読み替え（弱める保証の理由は implementation-status と spec 先頭に記載） |
| 15 | 検証中に見つけた別の既存 E2E 落ち（main・差分と無関係）: `llama-runtime-settings`（LLM 共通設定の summary「Deep Research」が「コンテキストに合わせた量」追加で 2 当たり＋GPU Broker による管理・最低常駐時間・drain 上限などの欄が 4deb560 で削除済み）と `frontier-models`（プロバイダカードの「API キー」欄が見つからない） | モデル管理・フロンティア画面の確認が E2E でできない | 中 | 未着手（2026-11-22、14 の検証中に見つける。14 の PR のスコープ外として切り出した） |
| 11 | AI アシスタントの OpenCode の会話サーバー（箱）が、使われなくなっても止まらない（次の会話を始めたときだけ止めていた） | host network・gh の設定が見える箱が何時間も残る | 高 | 直した（5 分おきの `idle_reap_loop`） |
| 12 | 「この会話は秘密」（`confidential_at`）の段 2。AI アシスタントの OpenCode は「プロジェクト×利用者」で 1 つの箱（host network）を会話の間で共有しているので、箱の中の webfetch・curl・パッケージ取得は止まらない。段 1 は ControlDeck の中の取得・検索と外部の LLM だけで、秘密の会話の OpenCode は「次の版で対応します」と断っている | 秘密の会話では OpenCode を使えない | 高 | 未着手。秘密の会話は専用の `--network none` の箱へ移し、ゲートウェイと MCP へは Unix ソケットの中継だけにする。OpenCode の会話の記録（ディスク）から同じ session を続ける。MCP のトークンに秘密の印を入れる（課題 1 の中継の設計と合わせる） |
| 13 | 箱の OpenCode の試験で Opus の監査が見つけた機械的な問題: (1) `test-catalog` を流すと目録の手書きの説明が消える (2) `./deck.sh test-catalog` が venv を作って PyPI へ取りに行く (3) 箱に git の利用者名が無い (4) 子のレビュー役は同じモデルで、これらに気づかなかった | Opus の監査が要る。外部取得の禁止に反する | 高 | 直した（開発の作業場 1 段目: 説明の引き継ぎ・既存 venv の利用・箱の git の作者・別モデルのレビュー役と `dev-check`）。実機の箱での通しは未確認 |
| 14 | 箱の設定 `sandbox.json`（source・git_common_dir・overlays・ポート・owner・origin）が箱の書ける `box.path` の中にあり、箱の AI が overlays に `~/.ssh` などを足すと、次にホストが作る podman の引数で見せられた。ホストの git を box.repo の中で動かすと `.git/config`（fsmonitor・credential.helper・sshCommand・insteadOf）・フックもホストの権限で動く（2026-11-22 の監査） | 箱の隔離を破れる。L2・安全 | 最高 | 直した（開発の作業場 3 段目の同じ PR）: meta は箱の外 `<sandboxes>/.meta/<名前>.json`（0700・0600）、古い箱は検査して移す、load のたびに検査（git_common_dir・overlays は 2 種類だけ・ポート範囲・型）。ホストでは box.repo の中で git を動かさず、箱の中で調べ、export.git へ fetch して比べる・push する。実機の podman での通しは未確認 |

## 未決事項

- P1: 中継に `/load` を既定で許すか。今は `--allow-load` を付けた箱だけ（chat は要求に応じて自動でモデルを載せるので、開発役は困らない）。
- P2b: 箱の開発サーバーを Project Lab の run にしたとき、`web_preview_ready`（ユニットのプロセスがポートを使っているか）が podman のコンテナでも通るか。通らなければ、確認の仕方を箱の run 用に足す。
- P2c: 実機で確かめること: unit の中の見張り役の起動（PYTHONPATH・python の場所）、restart で preview が繋がり直して reload が届くか、Vite を native で動かしたときの記録、iOS Safari で戻ったときの読み込み直し、C++ の cmake build + ctest（build は 1 つの argv だけ。configure は最初に一度手で）、node_modules の多い tree の inotify の上限。
- P4: 借りる時間の上限・VRAM の上限の既定。

## 外部の状態

- OpenRouter: 本番に鍵が登録済み、残高 3 ドル（利用者の言）。データ方針 deny。
- GitHub: `souten-yd/ControlDeck`。マージと正式 Release は常設の承認あり。

## Claude が要る部分を減らす: マージの判断（2026-11-22）

- 目的: マージの判断を Claude（Opus）に頼らず、利用者が結果のカードで行う。実装済み（開発の作業場 4 段目、詳細は implementation-status.md の同日の項）。
- できること: 「変更」で行ごとの差分（export.git から）、PR のリンクの横の「マージ」（settings.manage）。前提（PR が開いている・マージできる・PR の head が関門で確かめた tree と同じ・関門が合格）を満たすときだけ。L2 は確認の文言「L2 を理解した」。`gh pr merge --squash --delete-branch --match-head-commit`。Release はしない。
- 残り: 実機で本物の PR をマージして確かめる（利用者）。Release を画面から出す仕組みは未着手（今は案内だけ）。

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

- 2026-11-22（OpenCode、E2E 直し）: 不具合と課題 14 の 2 spec を修正（`閉じる` の dialog スコープ化、yield 廃止を受けた resource-load を Add-on 譲り方パネルへ書き換え）。`./deck.sh e2e` は箱では venv 構築で使えないため、`cmd_e2e` と同じ手順を既存 venv で手動再現して流した。関係 E2E で別の main 落ち 2 件を見つけ 15 に記録。レビュー役 minor 3 件直して dev-check 合格。
- 2026-11-21（Opus）: P1 を Sonnet が実装、Opus が差分を読み「外部のモデルを既定で断る」を足し、テストと実機で確かめた。OpenCode Zen の無料モデルの条件を調べた（不具合と課題の 5）。
- 2026-11-21（Sonnet、開発の作業場 1 段目）: `dev-check`・スキル `controldeck-development`・別モデルのレビュー役・目録の道具の修正・箱の git の作者を実装（設計は Opus）。自分の差分に `dev-check` を流して確かめた。
- 2026-11-21（Opus）: ルール（Opus が司令塔）の確認。既存の箱・ミラー・ゲートウェイ・broker・Project Lab の調査（Haiku）と外部の調査（Sonnet）。pasta と pod を実機で確かめ、P1 の設計を決めた。P1 の実装を Sonnet に回した。全体計画 `ROADMAP.md` とこの引き継ぎを書いた。
- 2026-11-21（Sonnet、P2a）: MCP に `control_deck.lab_*` 5 道具、中継に MCP の 2 パス（トークンは箱の外・許可の道具だけ）、箱の持ち主 `--owner` と環境変数ファイル、箱の bridge を実装。単体テストは通った。実機（箱の OpenCode から lab_run_start → preview の URL）は未確認。

- 2026-11-22 作業場からの「取り込む → マージ」を実機で確認した（Opus）。この 1 行は、その確認で作業場から入れたもの。
