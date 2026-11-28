# 引き継ぎ: AI Commander（複数の AI の CLI を 1 画面で開く・アカウントを分ける）

計画: `docs/PLAN-ai-commander.md`。指示書: 箱の `../c1-spec.md`（C1）。
状態: 2026-11-27 C1 実装済み（OpenCode / GLM-5.3、箱 c1-commander。コミットは `docs/implementation-status.md` の 2026-11-27 の項を参照）。C2 以降は未着手。

## 目的と利用者の決定（時系列）

- 2026-11-26 利用者: Codex・OpenCode・Claude・Gemini・Grok などのコンソールを 1 つの画面で
  何個でも開く。同じ CLI を別アカウントで同時に。アカウントの数だけ要るわけではない
  （同じアカウントでも何個でも）。開くときに CLI・アカウント・作業フォルダ・個数を選ぶ。
- C1 の範囲: 開く・並べる・アカウントの管理だけ。状態判定（C2）・自動再開（C3）・利用量（C4）・
  MCP（C5）・チーム（C6）は作らない。ただし C2 以降で使う印（tmux の user option）は C1 で付ける。

## 実機で確かめた事実（2026-11-26 Opus、c1-spec.md より）

- この PC: `claude`（2.1.332）・`codex`（0.172.0）・`opencode`（1.18.30）が PATH にある。
  `gemini`・`grok` は無い → 画面では「未導入」と出して開けない。
- 複数アカウントの公式の方法: claude は `CLAUDE_CONFIG_DIR`、codex は `CODEX_HOME`、
  opencode は `XDG_CONFIG_HOME`・`XDG_DATA_HOME`。Gemini は確かな方法が無い → C1 では既定だけ。

## いまの状態（C1）

- backend: `app/commander/`（accounts・consoles・router）+ `app/schemas/commander.py`。
  `terminals/manager.py` に `create_session(..., env=)` と印の読み書きを足した
  （既定の引数のままなら今までと同じ挙動）。
- frontend: `/commander`（pages/Commander.tsx + features/commander/）。
  XtermView に `embedded` props を足し、タイル内に収まるようにした（全画面は従来どおり）。
- 検証の道（この箱）: backend のテストは元の venv を直接使う
  （`cd backend && env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/wt-c1/.venv/bin/python -m pytest ...`。
  箱の wt-c1 は venv だけでコードの無い置き場）。`dev-check` は
  `CONTROL_DECK_SOURCE_VENV=/data1tb/ControlDeck/wt-c1/.venv ./deck.sh dev-check --base origin/main`。
  e2e は `cmd_e2e` と同じ手順を既存 venv で手動再現（HANDOFF-opencode-dev-environment の実績に同じ）。

## ファイル地図

| ファイル | 役割 |
|---|---|
| `backend/app/commander/accounts.py` | CLI の表（CLIS）とアカウントフォルダの作成・一覧・削除。ログイン済みはファイルの有無だけ |
| `backend/app/commander/consoles.py` | コンソール = Terminal の tmux セッション + 印。一覧・作成（個数ぶん）・削除 |
| `backend/app/commander/router.py` | `/api/v1/commander/*`。権限は terminal.use（Terminal と同じ） |
| `backend/app/schemas/commander.py` | リクエストの形（名前の正規表現・個数 1〜8） |
| `backend/app/terminals/manager.py` | `create_session(env=)`・`tag_session()`・一覧での印の読み取り |
| `frontend/src/api/commander.ts` | API の型と呼び出し |
| `frontend/src/features/commander/` | タイル・「＋ コンソール」シート・アカウント管理シート |
| `frontend/src/pages/Commander.tsx` | 画面（PC: タイル 2〜3 列 / モバイル: リスト→全画面） |
| `frontend/e2e/commander.spec.ts` | E2E（API・WS はモック） |

## 守ること（C1 固有）

- `shell=True` 禁止。tmux へは配列引数。環境変数の値は accounts が作ったフォルダのパスだけ。
- 秘密（CLI のログイン情報）を読まない・返さない・ログへ出さない。ログイン済みはファイルの有無のみ。
- アカウントの作成・削除、コンソールの作成・削除は監査ログへ。
- `async def` の中で同期的に待たない（endpoint は `def` で threadpool へ）。
- 既存の Terminal 画面と API の振る舞いを変えない（`create_session` の既定の引数なら今と同じ）。
- 鍵・トークンを E2E・テストの期待値へ書かない。

## 未決事項

- OpenCode・Gemini の「ログイン済み」の判定は C1 では行わない（claude・codex の公式のファイル名だけ）。
  opencode の auth.json の場所（XDG_DATA_HOME 側か）は C2 で公式文書を確認してから足す。
- `config.terminal.max_sessions` を変える UI は既存に無い。案内は「config.yaml で変えられる」と書く
  （c1-spec の「設定の画面」と異なるが実態に合わせた）。

## 作業記録

- 2026-11-26 (OpenCode/GLM-5.3): C1 を実装開始。
- 2026-11-27 (OpenCode/GLM-5.3): C1 実装済み。backend `app/commander/`・frontend `/commander`・pytest 15 件・
  E2E `commander.spec.ts` 4 件（API・WS モック、320/1280 ライト・ダークの撮影）・Terminal E2E 12 passed /
  2 skipped・`./deck.sh dev-check --base origin/main` 合格。実機の確認が要るもの:
  2 アカウントでの実際のログインと同時起動（利用者の操作）、実 CLI でのタイルの見た目。
- 2026-11-28 (Nexarch #3/#5 + 統合 #6): コンソールのロック（3 段）を実装・隔離で検証。ブランチ
  `nexarch/tab13eba8-6` に 5 コミット（#3・#5 とその統合）。push / PR / マージはしていない（統括の Claude が行う）。
  状態・効果・処理時間・未検証は `docs/implementation-status.md` の先頭の節を正とする。

## 2026-11-28 の追加（コンソールのロック）

- いまの状態: ロックの切り替え・閉じる 409（4 経路）・外から消えた検知・10 分 3 回の自動復旧まで実装済み。
  地図に 5 ルート追加（`./deck.sh route-map` で `docs/ROUTE-MAP.md` 再生成済み）。目録再生成済み。
  隔離の検証は `test_commander_console_lock.py` 67 件 + 回帰 65 件 pass、`dev-check --base origin/main --skip-visual` の最終行は `合格`（frontend build・画面・Alembic は未実施）。
- 追加ファイル: `backend/app/commander/locks.py`（中核 451 行・保存・検知・復旧・診断 summary）、
  `frontend/src/features/commander/AutoRestoreSheet.tsx`（設定シート）。変更: `terminals/manager.py`
  （中央ガード・`set_locked` / `is_locked`）、`commander/router.py`・`schemas/commander.py`・`settings.py`・
  `resume.py`、`diagnostics/report.py`・`selftest.py`、`route_map.py`、`terminals/router.py`・`stream.py`・
  `attachments.py`、`alerts/events.py`、画面は `Commander.tsx`・`ConsoleTile.tsx`・`api/commander.ts`。
  試験: `backend/tests/test_commander_console_lock.py`(1379 行)・`conftest.py` の tmux 隔離 wrapper・
  `frontend/e2e/commander.spec.ts`（ロック 3 件）。
- 検証の手順（この掘り出しで使ったもの。本番へ向けない）: worktree 直下へ `.venv` のシンボリックリンクだけ作り、
  `cd backend && env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/app-strata/.venv/bin/python -m pytest -q
  -p no:cacheprovider tests/test_commander_console_lock.py`。tmux は conftest の `-L nexarch-test-<pid>`
  wrapper が全呼出しを隔離し、`kill-server` と大域 `-L` / `-S` は終了コード 97 で拒否される
  （`test_all_raw_tmux_calls_use_explicit_private_socket` が保証）。frontend の node_modules はどこにも無く、
  npm install は禁止なので build / E2E は未実施。`env -u CONTROL_DECK_CONFIG ./deck.sh dev-check --base
  origin/main --skip-visual` で合否を確認する。
- 作業一覧: C0 済み / C1 済み / C2 済み / C3 実装済み（実上限は未） / C4 一部 / C5 未 / C6 設計済み。
  コンソールのロックは C1・C3 の追加として完了（隔離検証まで）。本番での受け入れは未。
- 未決事項（開発機能への要望。実コードの修正はしていない）: (1) `.nexarch/` の brief が git 管理外で
  worktree へ届かない、(2) worktree の `.git`（共通ディレクトリ参照）が箱から見えない、
  (3) MCP の進捗が終了まで空、(4) 内部 review 待ちが見えない、(5) queued の理由と停止／引継ぎ MCP が足りない、
  (6) Zen も local 扱い＋起動準備、(7) 子レポートが `Unknown tool`。詳細は `docs/PLAN-ai-commander.md` の節。
  本番 tmux を外から消す試験・実 CLI の「続きから」再開・frontend の build/E2E と 320/1280 の実画面は未検証。
