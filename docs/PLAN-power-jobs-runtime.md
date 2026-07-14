# 大規模計画：PSU電力/電気代 + サーバー主導ジョブ基盤 + llama.cpp/OpenCode

> このドキュメントは大規模改修の設計・進捗・**Codex 等への引き継ぎ**用。
> セッションのトークン制限で中断した場合、ここを起点に再開すること。

## 進捗サマリ（随時更新）

| 計画 | 状態 |
|---|---|
| A. PSU電力監視 + 電気代（起動中/日/月） | ✅ 完了（実機検証済み・マージ済み） |
| B. サーバー主導ジョブ基盤の汎用化 | ✅ 完了（DB永続化・再起動復元・API拡充、マージ済み） |
| C. 永続チャット（ブラウザを閉じても回答生成・復元） | ✅ 完了（全モードサーバー側・実機切断試験済み、マージ済み） |
| D. ワークフロー生成の意味検証・品質スコア | ✅ 完了（既存 /chat/build を強化、マージ済み） |
| E. LLMランタイム抽象（Ollama/llama.cpp provider） | ⬜ 未着手 |
| F. llama.cpp 導入（Vulkan/ROCm・systemd・MTP・思考深度） | 🚧 F-1完了（backend・実機動作確認、マージ済み）／F-2 UI 未着手 |
| G. OpenCode オプトイン統合（feature registry・プラグイン境界） | ⬜ 未着手 |
| H. ワークフローノード超強化（型/capability/dry-run/新ノード） | ⬜ 一部済（v2エンジンで承認/リトライ/並列/flow.call/エージェント実装済み） |

---

## 既存資産（再利用前提。重複実装を避ける）

- **メトリクス収集**: `backend/app/monitoring/collector.py` の `MetricsCollector`。
  - `_collect_once()` が約2秒周期（`config.monitoring.interval_seconds`）。RAPL(CPU)/GPU/hwmon を読む。
  - `run()` ループ、`_flush_minute()`（60秒毎にDB保存）、`subscribe/unsubscribe`（WS配信）。
  - snapshot の `"power"` = `{cpu_watts_estimated, gpu_watts, total_watts_estimated, is_estimate}`。
- **API**: `backend/app/monitoring/router.py` の `GET /api/v1/system/overview`、`WS /api/v1/system/metrics/stream`。
- **ジョブ基盤（簡易）**: `backend/app/jobs/service.py`（プロセス内 dict、`Job` dataclass、`create/get/list/cancel/wait_events`）。**DB永続化なし・再起動で消える**。計画Bで DB化・復元対応が必要。
- **ワークフローエンジン v2**: `backend/app/workflows/engine.py`。並列DAG/join/リトライ/on_error/承認/flow.call/イベント・Webhookトリガー実装済み。`docs`不要。
- **チャット**: `backend/app/workflows/chat_router.py`。`/chat/stream`(WS)、`/chat/build`(WS・ジョブ化済み)。**通常チャットはWS所有で永続化なし**（計画Cで要修正）。
- **LLM**: OpenAI互換 `/v1/chat/completions` 経由。think は Ollama ネイティブ `/api/chat` 経由（`_native_base`）。
- **設定**: `backend/app/config.py`（pydantic）、`config/config.yaml`、`config/config.example.yaml`。
- **暗号化**: `app/security/crypto.py`（Fernet）。**シークレット**は `WorkflowSecret`。
- **DBモデル**: `backend/app/models/__init__.py`。テーブル追加は `create_all` で自動。**既存テーブルへの列追加は不可**（マイグレーション無し）→ 新テーブルで対応。

---

## 計画A：PSU電力監視 + 電気代（実装中）

### 実機
- `corsairpsu` は hwmon 番号可変（実測 hwmon6）。`/sys/class/hwmon/hwmon*/name` を探索。
- `power1_label = "power total"`, `power1_input`(µW)。62000000 → 62.0W。
- 電力は **PSUのDC総出力**。AC入力は `output/efficiency` で概算（効率既定0.85）。
- `liquidctl`の`Estimated input power`/`v_in`は使わない。

### 設計
- `backend/app/monitoring/psu.py` — `read_corsair_psu()`：hwmon動的探索→power total等を dict、無ければ `{"available": False}`。`sensors`/`liquidctl`のサブプロセスは使わず sysfs 直読み。
- `backend/app/monitoring/electricity.py` — `ElectricityAccumulator`：
  - `time.monotonic()` で台形積分。`delta_kwh = ((prev+cur)/2)*dt/3_600_000`（W・秒）。
  - boot_id（`/proc/sys/kernel/random/boot_id`）でセッション管理。boot_id変化で session リセット。
  - 日別（`electricity_daily`）/月別（日別のSUM）。日付境界で積分区間分割。タイムゾーンはOS/設定。
  - 異常間隔（>上限, 逆行, 欠測, サスペンド跨ぎ）は積算しない。
  - 保存：メモリ積算 + 600秒毎チェックポイント + 日/月境界 + 終了時(lifespan) + 単価/効率変更時。
- collector `_collect_once` で PSU 読取→accumulator.update→snapshot `"power"` に統合フィールド追加。
- DB: `electricity_daily`(local_date PK, energy_kwh, cost_yen, price_per_kwh_yen, sample_duration_sec, first/last_sample_at, updated_at)、`electricity_state`(boot_id, session_energy_kwh, last_input_power_w, last_sample_wall_time, updated_at)。
- config: `monitoring.electricity {enabled=true, price_per_kwh_yen=35.69, psu_efficiency=0.85, persistence_interval_seconds=600}`。検証: price>=0, 0.50<=eff<=1.00, 60<=interval<=3600。
- API `power` 追加: output_power_w, estimated_input_power_w, session/today/month energy+cost, price_per_kwh_yen, psu_efficiency, persistence_interval_seconds, last_persisted_at, vrm/case温度, fan, available, source。
- ホーム(Dashboard): PSU総出力 / コンセント側推定 / 起動中・今日・今月の電気代+kWh。取得不可(null)と0Wを区別。

### 完了条件A
- 実機で負荷変動に追従、起動中/今日/今月が増加、再起動で維持、OS再起動で起動中0、PSU消失で他監視継続。
- 単価 35.69円/kWh に統一（旧31円を残さない）。2秒毎DB書込しない（10分チェックポイント）。

---

## 計画B〜G：サーバー主導ジョブ基盤ほか（未着手・設計メモ）

### B. 汎用ジョブ基盤 ✅ 完了
- **実装済み**: `jobs` テーブル（冗長化回避のため job_events/artifacts は作らず events_json スナップショットに集約）。
- メモリ(`_jobs`)=高速WSストリーム、DB(`Job`)=状態/進捗/結果/末尾50イベントを永続化。DB書き込みは作成・状態変化・終了の要所のみ（毎トークン書かない）。
- 再起動時 `recover_on_startup()` が running→interrupted。owner_user_id 記録。
- API: `GET /jobs`(list_any=メモリ+DB統合), `GET /jobs/{id}`(メモリ→DBフォールバック), `POST /jobs/{id}/cancel`。既存 model.pull/register/workflow.build は owner 付きで移行済み。
- **未実装（計画C以降で追加予定）**: idempotency_key/heartbeat/優先度、WS /jobs/stream（現状は個別ジョブの WS は chat_router 側にある）。チャットの部分出力チェックポイントは計画C（ChatMessage）で対応。

### C. 永続チャット ✅ 完了
- **根本原因**（従来 /chat/stream）: WS ハンドラ内で LLM を直接 stream → 切断＝中断、回答はブラウザのみ保持・DB未保存だった。
- **実装済み**: `Conversation`/`ChatMessage` テーブル。`chat_persist.py` で送信時に user + assistant placeholder + chat.completion ジョブを DB 作成。ワーカーがサーバー側で生成し ChatMessage へ 1秒毎チェックポイント保存。WS(`/chat/messages/{id}/stream`)は通知のみで**切断してもジョブ継続**。再接続は job_id 購読、再オープンは履歴 API で復元（generating は snapshot + 継続）。
- **全モードサーバー側**（ユーザー指示）: chat/web/academic/deep を全て永続パスへ。web/academic/deep は `_server_search`（chat_router の検索/`_deep_search` を再利用）がジョブ内で検索→LLM。出典は ChatMessage.meta_json に保存し WS "sources" で配信。gen/run は元々サーバー計算（run は workflow executions で永続）。
- **UI**: AssistantChat の localStorage 履歴を廃止し DB 会話へ一本化。設定は⚙ボタンに集約（モデル/検索エンジン/SearXNG）。「🆕新規」で会話切替。マウント時に DB 復元 + generating 再購読。
- **未対応（軽微）**: 会話一覧のUI（複数会話の切替ピッカー）は未実装（現状は単一「現在の会話」+新規）。gen モードの生成中プレビューは非永続（1 LLM 呼び出しで短い）。

### D. ワークフロー生成の意味検証・品質スコア ✅ 完了
- **実装済み**: `validation.py` に `semantic_check`（到達不能ノード・存在しない変数参照・主要必須設定欠落・ループ/エージェント終了条件）と `quality_score`（構造/到達性/出力/エラー処理/実動作の 0-100 内訳）。
- `chat_router._validate_generated` が構造検証の後に意味エラーも返し、自動ビルドの LLM 修正へフィードバック。生成 API と build 完了イベントに `quality` を付与。UI（AssistantChat）に品質スコアバッジ（内訳・検証結果の折り畳み）を表示。
- **方針**: 完全な親子ジョブ分割ではなく、既存 `/chat/build`（既にジョブ化済み・generate→validate→register→run→自動修正）を強化する形（冗長化回避）。JSON Schema 厳格出力・dry-run 専用段は未実装（実動作確認で代替）。

### E. LLMランタイム抽象
- `LlmRuntimeProvider`（detect/install/list_models/start/stop/health/stream_chat/cancel/get_capabilities...）。
- `OllamaRuntimeProvider` + `LlamaCppRuntimeProvider`。既存 Ollama 実装を provider 化。

### F. llama.cpp 導入
**F-1 完了（backend、実機動作確認済み）:**
- `models_mgmt/llama.py`: リリース asset 照合（vulkan/rocm/cuda・Linux）、DL+展開+SHA256（ジョブ `llama.install`）、`~/.local/share/control-deck/runtimes/llama.cpp/<tag>/<backend>/` + `current` シンボリックリンク。
- systemd ユニット `cdapp-llama.service`（cdapp- 前置で既存 systemd ヘルパー再利用）。**LD_LIBRARY_PATH=current** が必須（共有ライブラリ libllama-server-impl.so 等がバイナリ同階層）。
- start/stop/health、`detect_options`（`llama-server --help` 解析で実在フラグ 316 件・`--draft-*`=MTP/speculative も検出）。
- API: `/models/llama/{status,assets,install-jobs,config,start,stop,options}`。OpenAI 互換 `http://127.0.0.1:<port>/v1` として既存チャット/ワークフローから利用可（base_url 指定）。
- **実機検証**: ROCm 版を DL→展開→27B GGUF で起動→health OK→/v1/chat/completions 200→停止まで確認。experimental フラグ付き。
- 未実装(F-2): 設定 UI（ランタイム選択・導入・起動設定・MTP/思考深度・`--help` 由来の動的フォーム）、モデル別インスタンス複数管理。host は 127.0.0.1 固定。

### G. OpenCode（オプトインのみ）
- **自動導入禁止**。`./deck.sh feature install/enable/disable/uninstall opencode`。
- feature registry（installed/enabled/available/version/health）。未導入時はルート/メニュー/ノード/コマンドパレット/API能力に**登録しない**（CSS非表示でなく未登録）。直URLは404。
- プラグイン境界: `backend/app/integrations/opencode/`, `frontend/src/features/opencode/`。汎用抽象 `CodeAgentProvider` 等にのみ依存。削除で他機能に影響しないこと。
- llama.cpp の OpenAI互換エンドポイントを provider に選択可。ワークフローは統合ノード `code.agent`（operation: analyze/implement/fix/test/...）を feature 有効時のみ登録。

### H. ノード超強化
- 既存ノードに version/capability/side_effect/supports_*(retry/cancel/progress/dry_run)/型付き入出力/help を付与。重複は operation 化。
- 便利ノード（parallel map/JSON transform/schema validate/csv/glob/health check/rerank/embedding/judge 等）は既存統合ノードと重複しない範囲で追加。
- ノード検索/カテゴリ/お気に入り/導入済み機能のみ表示。単体テストUI強化。

---

## 実装順序（推奨）
A（自己完結・実機あり）→ B（土台）→ C（Bに依存）→ D → E → F → G → H。
各フェーズ完了ごとにテスト通過・PR/マージ。**冗長化を避け既存の改修・統合を優先**。

## セキュリティ不変条件
shell=False/引数配列/許可ルート/symlink脱出防止/RBAC/CSRF/Origin/監査ログ/シークレット暗号化/root非要求/出力上限/プロセスツリー終了。read_only原則（PSUは読取のみ、sudo/hidraw書込禁止）。
