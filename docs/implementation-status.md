# 実装状況

最終更新: 2026-07-12

## サマリー

| Phase | 状態 |
|---|---|
| 文書整備 | ✅ 完了 |
| Phase 1 — 認証 + レイアウト | ✅ 完了 |
| Phase 2 — アプリ管理 | ✅ 完了（アイコンアップロードは未対応、頭文字アイコンで代替） |
| Phase 3 — 監視 | 🟡 コア完了（CPU/RAM/GPU/VRAM/ディスク/ネット/電力推定/1分平均履歴。アラートは未実装） |
| Phase 4 — ファイル + ターミナル | 未着手 |
| Phase 5 — ワークフロー | 未着手 |
| Phase 6 — リモートデスクトップ | 未着手 |
| Phase 7 — TOTP ほか | 未着手 |

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
- 電源: reboot / shutdown / 予約（メモリ内、Web 再起動で消える制約は既知）

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
- systemd サービス: `control-deck-web` をユーザーサービスとして登録、非 root（souten）で稼働、
  linger 有効化により SSH / ログアウト後も継続

## 既知の制約 / 次の作業

1. 電源の予約実行はプロセス内タイマー（Web 再起動で消失）→ helper + systemd timer へ移行予定
2. system レベルの systemd サービス制御は未対応（ユーザーユニットのみ）→ helper (polkit) で対応予定
3. アラート、アプリごとの GPU 使用量、アイコンアップロード、ヘルスチェックは未実装
4. Phase 4 以降（ファイル / ターミナル / ワークフロー / リモート / TOTP）は未着手
5. 電源 reboot/shutdown は API 実装済みだが実機での破壊的実行は未検証

## 履歴

- 2026-07-12: リポジトリ初期化。要求仕様原本と初期文書 8 点を記録
- 2026-07-12: PR #1 バックエンド（認証 / RBAC / 監査 / アプリ管理 / systemd / 監視 / 電源 / スクリプト）
- 2026-07-12: PR #2 フロントエンド（レイアウト / ダッシュボード / アプリ / ログ / システム / 設定）+ amd-smi パーサー修正
