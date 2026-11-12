# AGENTS.md — 開発エージェント向けガイド

このリポジトリで作業する AI エージェント / 開発者向けの規約。

## プロジェクト概要

Ubuntu Control Deck — Ubuntu PC を Web から一元管理するセルフホストアプリ。
要求仕様の原本は `docs/requirements.md`。実装は Phase 単位（`docs/implementation-plan.md`）。
現在の進捗は必ず `docs/implementation-status.md` を確認・更新すること。

## 技術スタック

- Backend: Python 3.11+ / FastAPI / Uvicorn / SQLAlchemy 2.x / Pydantic v2 / psutil / SQLite（将来 PostgreSQL）
- Frontend: React / TypeScript / Vite / React Router / TanStack Query / Zustand / Tailwind CSS
- プロセス実行: systemd ユーザーユニット（`systemctl --user`）。Web プロセスの子プロセス化は禁止。

## 起動・テスト

```bash
./deck.sh          # 自動セットアップ + 起動（唯一のエントリースクリプト。サービス登録済みなら再起動）
./deck.sh test     # バックエンドテスト（= cd backend && ../.venv/bin/python -m pytest）
./deck.sh service  # systemd ユーザーサービス登録
cd frontend && npm run build   # フロントエンドのみビルド
```

scripts/ 配下は deck.sh への互換ラッパーのみ。新しい運用操作は deck.sh にサブコマンドとして追加する。

- Python venv はリポジトリ直下 `.venv/`。起動スクリプトが自動構築する。手動で `pip install` する場合も必ず venv 内で行う。
- フロントエンドのビルド成果物は `frontend/dist/`。バックエンドが静的配信する。

## 絶対に守るルール

1. **root で動くコードを書かない**。特権操作（再起動等）は helper 経由の設計を守る。
2. **`shell=True` 禁止**。subprocess は必ず配列引数。ユーザー入力をシェル文字列へ連結しない。
3. **パスは `Path.resolve()`（realpath 相当）で正規化**し、許可ルート配下か検証してから使う。
4. **秘密値（パスワード、トークン、TOTP シークレット等）をログへ出力しない**。
5. **API・WebSocket ごとに認証と権限を確認**する。デコレーター / 依存性で強制する。
6. **破壊的・重要操作は監査ログ**（`audit` サービス）へ記録する。
7. GPU 監視などセンサー取得の失敗でアプリ全体を落とさない。取得不可は `N/A`。
8. エラーを握り潰さない。ユーザー向けメッセージと内部ログを分離する。
9. **`async def` の中で同期的に待つ処理を呼ばない**。ORM の属性アクセス（遅延読み込み）・
   `db.execute` / `db.commit`・同期 HTTP client・大きな読み書きや hash 計算・
   `subprocess.run`・`time.sleep` はどれも event loop を止める。止まるのはその要求
   だけではなくプロセス全体で、systemd の watchdog に落とされる（実測: 3 日で 68 回、
   原因は `addon_frame_proxy` の遅延読み込みによる DB 接続待ち）。同期処理は
   `asyncio.to_thread` へ出すか、endpoint を `def` にして threadpool へ任せる。
   使う関連は eager load し、DB session は用が済んだ時点で返す（上流への往復の間
   抱えると pool が尽きる）。調べ方は `docs/runbook-service-hangs.md`。

## UI ルール

- 主要機能へ 2 ステップ以内。全操作ボタンの常時表示禁止（主操作 1 個 + その他メニュー）。
- モバイル（320〜767px）はボトムシート + 下部ナビ。Safe Area（`env(safe-area-inset-*)`）対応。
- 破壊的操作のみ確認ダイアログ。完了通知はトースト。赤色は破壊的操作と重大エラー限定。
- Monaco / React Flow / xterm.js / チャートは遅延ロード。大量リストは仮想化。
- ダーク / ライト両対応。等幅数字（`font-variant-numeric: tabular-nums`）。

## コード規約

- Backend: 型ヒント必須。Pydantic スキーマは `app/schemas/`、モデルは `app/models/`。
- Frontend: 関数コンポーネント + hooks。API 呼び出しは `src/api/` に集約し TanStack Query 経由。
- コミットは Phase / 機能単位で日本語 or 英語の簡潔なメッセージ。

## CI使用コストの抑制（必須）

- **自前Ubuntu/開発PCでのローカルテストを最優先**する。集中テスト→必要な全体テスト→Frontend build→実機確認の順に実施。
- GitHub Actionsは自動実行せず、ローカル検証後に必要な範囲のみ `workflow_dispatch` で手動起動する。PR/commitごとの全テスト実行は原則禁止。
- 実機にアクセスできない場合は未検証と明記し、実施コマンドを提供する。CIを実機試験の代替にしない。
- 具体的な費用抑制規定・例外は `docs/development-release-policy.md` を参照。

## PR・リリース運用（必須）

- 仕様・テスト・既存の `main` 実コードを照合してからPRを作る。完了した変更はPRを経てマージする。PR本文だけでなく、実差分・manifest・docs・テスト結果を点検する。
- マイルストーンが達成された時点で、検証済み `main` のSHAをタグ付けし、GitHub Releaseを公開する。PRのマージとReleaseの発行は別工程として必ず双方を確認する。
- リリース時は更新方法、前バージョンへの復旧方法、テスト・未検証事項、対応PRを記録する。
- 運用詳細・チェックリストは `docs/development-release-policy.md` を正とする。

## 完了の定義

コードを書くだけでは完了ではない。Ubuntu 上で実起動し、API 確認、PC ブラウザ幅とモバイル幅（320px 含む）の
両方で動作確認し、`docs/implementation-status.md` を更新してから完了とする。

## PR・マイルストーン・リリース運用（必須）

- 開発はブランチで実施し、コード・ドキュメント・テスト・安全性確認を完了してからPRを作る。PR本文だけで検証せず、`main`の実ファイル、diff、manifest、docs、tests、runtimeの実装を照合する。
- PRの変更範囲を前回の実装指示と照合する。allowed以外の変更、Do NOT違反、runtime semanticsの変更、未要求のexecution/mutation/autonomous機能、pointer driftをチェックする。
- テストとレビューが通り、PRの成果物を確認できたら、**開発完了の一環としてPRをmainへマージする**。マージできなければ「開発完了」とは報告せず、未完了の理由・残作業を記録する。
- **マイルストーンの受け入れ条件を満たしたら、マージ済みmainからバージョンタグとGitHub Releaseを発行**する。リリースノートには達成項目、互換性、移行手順、既知の制約、検証結果を含める。テスト未通過・未マージ・未達成の段階で「リリース済み」と言わない。
- PRマージとリリースは別のゲート。軽微な追加修正は原則次の実装PRに統合し、スコープや検証が混ざる場合のみ別PRとする。次のPRへの実装指示は正確なファイル、条件、禁止事項、期待状態、必要テストまで具体化する。
- 本体アップデーターは管理者の明示操作のみ。起動時のgit fetch / npm build、未承認の自動マージ・自動リリースは禁止。実行前後にコミット・変更範囲・稼働状態を確認し、失敗時は原因と復旧方法を明記する。

## GitHub Actions費用削減・ローカル優先検証（必須）

- **ユーザーへ作業を依頼する前に、AIエージェントが利用できる実行環境（ChatGPTのPython/コンテナ、Codexの作業環境など）で確認・修正・テストを先に完了する。** リポジトリや依存関係をその環境へ取得できない場合は、取得不可の理由を明示し、GitHub連携による実ファイル/差分検査を行ったうえで、必要最小限のCIに委ねる。実際に実行できていないテストを成功と表現しない。
- AIエージェントの実行環境で、変更範囲に応じた個別テスト → 関連モジュールの回帰テスト → `./deck.sh test`（Backend全件）→ `cd frontend && npm run build`（TypeScript/Vite）の順に可能な限り実行する。実行不能なテストと原因はPRに記録し、後段のCIで補う。テスト環境が違うことによる結果の差も明記する。
- ローカルで再現できる失敗は**ローカルで修正・再テスト**し、CIの繰り返し実行をデバッグ手段にしない。環境差による失敗はランタイム変更ではなく、まず検証環境・fixture・期待値を調査する。テストを安易に削除/skip/xfailしない。
- **PRはDraftで作成・編集し、検証を完了した時点でReady for reviewにする。** 有料CIは必要な時に`workflow_dispatch`で明示的に起動し、PR/branch pushやscheduleによる自動実行は禁止する。GitHubで手動実行できない場合は結果を未確認として記録し、無断で自動トリガーを追加しない。
- 連続push/細かな修正は可能な限りまとめる。CIが必要な際は同じPRの古い実行をキャンセルし、不要な再実行はしない。対象を絞ったStrata等の追加検証は必要なときだけ`workflow_dispatch`で明示起動する。
- CIが通っていても、実機起動/更新/権限制御/安全性チェックを省略しない。ユーザーにはR9700/ROCm、PC固有のOS権限や接続状態など、ChatGPT/CIで再現できない確認事項だけを最小限依頼する。実機アクセス可能な連携がある場合はそれもAI側で行う。
- CIを省略・節約することと、安全性を下げることは別。必要な最終CIは必ず通し、成功前にマージ・リリースしない。ローカル確認の証拠（コマンド、対象、結果）をPR本文に記録する。
