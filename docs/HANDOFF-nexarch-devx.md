# 引き継ぎ: Nexarch の AI チームの開発機能を磨く（devx A〜F）

別の AI・開発者がここから続けられるように書く。進み具合が動いたら、必ずこの文書の「作業一覧」「作業記録」を更新する（`AGENTS.md`「引き継ぎ資料の決まり」）。
計画と Web 調査の比較表は `docs/PLAN-nexarch-team.md` の「2026-11-28 devx」節、全体の順番は `docs/ROADMAP.md` 方針 3。

## 1. 目的

利用者（2026-11-28）「AI チームの表示をもう少しグラフィカルにしてかっこよく。Hermes Agent やその他オープンソースの AI チーム・エージェントのアプリを調査して、進んでいるのを見るのがワクワクするような見た目に。ただし機能は損なわずに」「Nexarch の OpenCode の守りは適切に。厳しすぎると開発ができないので、OpenCode・Codex・Claude に合わせて開発ができる仕組みに」「調査などの道具は適切に持たせ、必要な機能は各自に配分して」「AI チームの停止と再開。PC の再起動や更新のときに、事前の状況を記録しておき、途中から再開できるように。データが消えないか心配」「使えるモデルを事前に抽出して選択肢から選べるように」「大量のファイルを受け渡すときは、場所を指定して読み込ませるなど工夫できないか」「ControlDeck の検索（Deep Research など）を OpenCode と AI チームの開発でも使い、使いにくい点・情報が不足している点を評価して改良」。

作るものは A〜F（`docs/PLAN-nexarch-team.md` の devx 節の表が正）。要点:

- A 画面の作り直し（アバターつきカード・段の流れ・動いている／止まっているが一目で分かる・詳細は押して開く・エキスパートで全部）。
- B 役割ごとのモデルの選択肢（使えるモデルを前もって抜き出し、選べば CLI・接続が決まる）。
- C 道具の配分と守り（担い手の OpenCode／Claude／Codex に ControlDeck の MCP を渡す・読み取り役には書く道具を渡さない・`web_research` を担い手にも効かせる・「閉じたチーム」）。
- D 再起動・更新から自動で続きを再開（起動時の自動再開・中断した担い手の配り直し・teams.json の原子的な保存と `.bak`）。
- E 資料の受け渡しと worktree（`.nexarch/inputs/`・`.nexarch/` と `node_modules`／`.venv` のリンク・確認のコマンドの作業フォルダ指定）。
- F チームリーダーの道具の説明を実際の動きに合わせる（`.nexarch/prompt-audit.diff` の `leader_mcp.py`・`team_leader.py` の塊だけ）。

## 2. 決定（時系列）

| 日付 | 決定 | 出どころ |
|---|---|---|
| 2026-11-27 | AI チーム（Nexarch）の設計。状態は ControlDeck（板）が正本、成果物は git の worktree、起こすのは画面への短い送信 | `docs/PLAN-nexarch-team.md` |
| 2026-11-28 | 以降の ControlDeck の機能開発は Nexarch と OpenCode から行い、使いながら開発機能を磨く | `docs/ROADMAP.md` 方針 3 |
| 2026-11-28 | 今回の 7 つの要望と A〜F の分担。画面（A）は画面の担い手、B〜F は backend の担い手、文書はこの担当 | `.nexarch/brief-devx.md` |
| 2026-11-28 | Claude・Codex は定額のログインだけで使い、API キーは使わない | `docs/PLAN-nexarch-team.md`、`docs/implementation-status.md` 2026-11-28 |
| 2026-11-28 | 不要な表示は隠し、エキスパートで開く（既定は毎回使う操作と要対応・危険の印だけ） | `AGENTS.md`「UI ルール」 |
| 2026-11-28 | 外部から何も取得しない（npm・pip・モデル）。Web の調査で読むだけは可。本番のデータ・tmux・プロセスは触らない | `.nexarch/brief-devx.md`「守ること」 |
| 2026-11-28 | push・PR・main へのマージはしない（担当 worktree でコミットまで） | 同上 |

## 3. 現状（2026-11-28）

- **このブランチの作業は文書だけ**。A〜F のコード・試験・画面・実機は**未着手**で、`docs/PLAN-nexarch-team.md` と `docs/implementation-status.md` に「未検証」と明記した段階。実装完了・試験成功を先取りして書かない。
- 既にあるもの: T1（板と送り込み）・T2a（見張りと 5 行の報告）・T3a〜T3c（ヘッドレスの担い手・チームリーダー・`nexarch_team` の道具）は実装済み。T2b は実装中、T4・T5 は未着手（`docs/PLAN-nexarch-team.md`「進め方（段）」）。
- 「使いながら見つけた直し」は 2026-11-28 に 6 件を記録済み（GLM の担い手・依頼の上限・SIGXFSZ・ローカルの枠・進み具合の live・確認のコマンドの作業フォルダ）。最後の 1 件は未対応。
- 並行して別のチームが `backend/app/commander/consoles.py`・`status.py`・`resume.py`・`frontend/src/features/commander/ConsoleTile.tsx`（コンソールのロック）を変更している。触るなら最小にし、同じファイルを大きく書き換えない。

## 4. ファイル地図

| 場所 | 中身 |
|---|---|
| `backend/app/commander/` | `teams.py`（板・保存）・`team_runner.py`（ヘッドレスの実行）・`team_dispatch.py`（配り手）・`team_leader.py`（チームリーダーの起動）・`leader_mcp.py`（`nexarch_team` の道具）・`team_roles.py`（役割と段）・`models.py`（使えるモデルの一覧）・`router.py`（API）・`status.py`（コンソールの状態）・`consoles.py`（tmux）・`resume.py`（自動再開）・`team_checks.py`（確認のコマンド） |
| `backend/app/integrations/opencode/` | `provider.py`（`_runtime_config`・`owner_user_id` の MCP）・`model_choice.py`（選べるモデル）・`zen.py`（Zen の鍵） |
| `backend/app/addons/agent_mcp.py` | ControlDeck の MCP（`control_deck.research`・`knowledge_search` ほか） |
| `frontend/src/features/commander/` | `TeamView.tsx`・`TeamRoles.tsx`・`LeaderTimeline.tsx`・`ConsoleTile.tsx`（別チームの担当） |
| `backend/app/route_map.py` → `docs/ROUTE-MAP.md` | 情報の伝達ルートの地図。新しい道（MCP の道具の配分・資料・自動の再開）を足すときは先にここへ書き、`./deck.sh route-map` で作り直す |
| `backend/tests/test_commander_*.py` | チームの試験（`test_route_map.py` が地図との一致を見る） |
| `.nexarch/`（作業場所） | 依頼の全文・資料の置き場。E で担い手の worktree から見えるようにする |

## 5. 守ること（この仕事の制約）

- `shell=True` 禁止・subprocess は配列引数。パスは `Path.resolve()` で正規化し、許可ルート配下か確かめる。秘密値（鍵・トークン・パスワード）をログ・返り値・argv に出さない。
- **外部から何も取得しない**（npm install・pip install・モデルの取得）。Web の調査で読むのはよい。本番（`/data1tb/ControlDeck/app-strata` のデータ・tmux・プロセス）を触らない。
- 隔離の試験は `env -u CONTROL_DECK_CONFIG`（外さないと本番の data に書く）。本番の `.venv` の Python を試験に使うのは可。
- push・PR・main へのマージをしない。担当 worktree（`.nexarch-wt/4`、ブランチ `nexarch/<id>-4`）でコミットまで。
- 壊れやすいファイル（`consoles.py`・`status.py`・`resume.py`・`ConsoleTile.tsx`）は別チームの担当。触るなら最小の差分にする。
- 完了の条件（全体）: `env -u CONTROL_DECK_CONFIG ./deck.sh dev-check --base origin/main --skip-visual` の最後の行が「合格」で始まる。画面は 320px／1280px・エキスパートの切と入の写真を E2E の成果として残す。
- 報告・文書・PR 本文は日本語（`AGENTS.md`「結果は日本語で」）。

## 6. 開発と検証の手順（環境の落とし穴）

```bash
# backend の試験（集中。全体の ./deck.sh test は使わない）
env -C backend env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/app-strata/.venv/bin/python \
  -m pytest -q -p no:cacheprovider tests/<ファイル>

# 画面
cd frontend && npm run build     # node_modules はリンク済み（外部取得しない）

# E2E（検証用のインスタンス）
./deck.sh e2e <spec>

# 地図・試験の目録（新しい道・試験を足したとき）
./deck.sh route-map
./deck.sh test-catalog

# 関門（最後）
env -u CONTROL_DECK_CONFIG ./deck.sh dev-check --base origin/main --skip-visual
```

- 試験を足す前に `./deck.sh test-catalog` で `docs/test-catalog.md` を作り直し、同じ観点が無いか探す。あれば直す・広げる。試験ファイルの冒頭の docstring に「何を守るか（目的）」「なぜ要るか（根拠: 起きた不具合・日付・決まり）」を書く。
- 新しい AI・道具・外部への出口を足したら `backend/app/route_map.py` にルートを書き、`./deck.sh route-map`。地図に無いルートは `backend/tests/test_route_map.py` が落とす。
- 本番の ControlDeck は止めない・再起動しない。本番のモデルの停止・再起動も、この仕事では行わない（評価は別の許可が要る）。

## 7. 作業一覧と状態

| # | 作業 | 担当（想定） | 触る場所 | 状態 |
|---|---|---|---|---|
| A | チームの画面の作り直し（カード・段・動き・詳細を開く・エキスパート） | 画面の担い手 | `frontend/src/features/commander/`（`ConsoleTile.tsx` は最小） | 未着手 |
| B | 役割ごとのモデルの選択肢 | backend＋画面 | `commander/models.py`・`integrations/opencode/model_choice.py`・`TeamRoles.tsx` | 未着手 |
| C | 道具の配分と守り（MCP・`web_research`・閉じたチーム） | backend | `commander/team_runner.py`・`team_leader.py`・`provider.py`・`agent_mcp.py`・`route_map.py` | 未着手 |
| D | 再起動・更新からの自動再開（teams.json の `.bak` と原子的な保存） | backend | `commander/teams.py`・`resume.py`・起動の処理 | 未着手 |
| E | 資料の受け渡しと worktree（`.nexarch/inputs/`・リンク・作業フォルダ指定） | backend＋画面 | `commander/team_runner.py`・`team_checks.py`・`TeamView.tsx` | 未着手 |
| F | チームリーダーの道具の説明（`prompt-audit.diff` の取り込み） | backend | `commander/leader_mcp.py`・`team_leader.py` | 未着手 |
| 文書 | PLAN・implementation-status の devx 節、この引き継ぎ、CLAUDE.md からの道 | devx（このブランチ） | `docs/PLAN-nexarch-team.md`・`docs/implementation-status.md`・`docs/HANDOFF-nexarch-devx.md`・`CLAUDE.md` | 完了（2026-11-28。文書のみ） |
| 調査 | 公式資料の比較表（8 製品）と research の評価 | devx（このブランチ） | `docs/PLAN-nexarch-team.md` | 完了（2026-11-28。表は事実と提案を区別） |

## 8. 未決事項

1. **CrewAI Studio の具体画面は未確認**（`https://docs.crewai.com/en/platform/crew-studio` はトップへ戻る）。Studio の画面を採用の根拠にしない。確認できたら比較表を直す。
2. **Conductor の形態（クラウドかローカルか・料金・ローカル LLM の可否）は未確認**。並列 worktree とレビューの考え方だけを参考にする。
3. 画面 A のアニメーションの範囲（どこまで動かすか・`prefers-reduced-motion` の既定）。利用者の「控えめ」を超えない線を、実画面を見て決める。
4. 「閉じたチーム（ローカルだけ）」の既定は切。入にしたとき、既存のチームの担い手（外部モデル）をどう扱うか（止める・選び直させる）は実装時に決める。
5. research の小改善（検索結果への通し番号 `[R1]`・返り値の URL と取得日・長い本文の続きの案内）は、**backend 担当への提案**として PLAN に記録した。このブランチでは実装しない。
6. E の「箱から見えるパス」の具体（podman の bind の一覧に `.nexarch/inputs/` をどう足すか）は実装時に `team_runner` の箱の起動と合わせて決める。
7. **この作業場所では git のコミットができない**（共有の `.git` が読み取り専用で mount されている。`git add` が "Read-only file system" で落ちる）。文書の変更は作業ツリーに残してあるので、統括がコミットするか、書き込める場所で `git add` してコミットする。開発機能への要望として: チームの worktree でもコミットできるようにする（成果を git で渡す設計なので、コミットできないと引き継ぎが手作業になる）。

## 9. 外部の状態

- 本番 `/data1tb/ControlDeck/app-strata`: **触っていない**（データ・tmux・プロセス・モデル）。この仕事の評価も本番では行っていない。
- この作業場所: `/data1tb/ControlDeck/team-devx/.nexarch-wt/4`、ブランチ `nexarch/t76b7b6dd-4`。基点は `origin/main`（`aa7e7a07` 付近）。**共有の `.git`（`/data1tb/ControlDeck/team-devx/.git`）は読み取り専用で mount されていて、この箱からはコミットできない**（`git add` が "unable to create temporary file: Read-only file system"）。変更は作業ツリーに残っている。`/data1tb/ControlDeck/team-devx/.nexarch-wt/4` 自体と `.git/worktrees/4` は書き込み可。
- 並行のチーム: コンソールのロック（`consoles.py`・`status.py`・`resume.py`・`ConsoleTile.tsx`）を別のチームが担当。`docs/` の同じファイルを別のチームが触る可能性があるので、コミット前に `git status` と `git diff` を見る。
- 外部への取得: していない。Web は公式資料の読み取りだけ（リーダーの research、2026-11-28）。

## 10. 作業記録

- 2026-11-28: 依頼の全文・`AGENTS.md`・`docs/PLAN-nexarch-team.md`・`docs/ROUTE-MAP.md` を読んだ。A〜F の計画と、公式資料 8 製品の比較表（URL つき、事実と提案を区別）を `docs/PLAN-nexarch-team.md` に書いた。`docs/implementation-status.md` の先頭に「計画のみ・未検証」の節を足した。この引き継ぎを作り、`CLAUDE.md` からたどれるようにした。コード・試験・画面は変えていない（文書だけ）。
- 2026-11-28: コミットを試したが、共有の `.git` が読み取り専用のため `git add` が "Read-only file system" で失敗した。**変更は作業ツリーに残した**（未コミット）。統括が書き込める場所でコミットするか、この箱の mount を直してから `git add docs/HANDOFF-nexarch-devx.md docs/PLAN-nexarch-team.md docs/implementation-status.md CLAUDE.md` する。
