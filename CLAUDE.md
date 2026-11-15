# CLAUDE.md — ControlDeck 実装の引き継ぎ

規約の原本は `AGENTS.md`（root で動かさない、`shell=True` 禁止、秘密をログに出さない、パス検証、監査、ローカル優先の検証、PR とリリースの運用）。ここには、いま進んでいる仕事と、引き継ぐ AI が最初に知るべきことだけを書く。

## いま進んでいる仕事

**OpenCode v2 を ControlDeck の開発環境にする**（UI 検証・精密調査・コードレビュー）。
計画・評価・段階・進捗は `docs/PLAN-opencode-dev-environment.md` が正。続きを始める前に必ず読み、終えたら「進捗」の表を更新する。

## 読む順

1. `AGENTS.md`
2. `docs/PLAN-opencode-dev-environment.md`
3. `docs/design-opencode-feature.md`（v2 の設定の形、ゲートウェイ、MCP、圧縮）
4. `docs/implementation-status.md`（新しい順。最新の数件）
5. コード: `backend/app/features/registry.py`（オプション機能）、`backend/app/integrations/opencode/provider.py`（session の設定生成）、`backend/app/addons/agent_mcp.py`（ControlDeck の MCP）、`backend/app/skills/`（スキル）

## 現行構成（2026-11-15）

- OpenCode は **v2 だけ**（`@opencode/cli`、実行ファイル `opencode2`）。v1（opencode-ai）と OMO は撤去済み。ECA は扱っていない。古い文書に残る ECA / OMO / v1 の記述は過去の経緯であって現行仕様ではない。
- v2 の拡張は OpenCode プラグイン 3 本（Superpowers・Adversarial Review・Auto Advisor）。管理領域へ npm で版固定して入れ、session の設定から `file://` で読ませる。使う/使わないは OpenCode 画面の設定。
- session の設定は job ごとに作る config directory に書き、`XDG_CONFIG_HOME` で向ける。利用者の `~/.config/opencode` は読まない・書かない。
- 重い道具（3D・音）は主の会話に載せず、子エージェントへ分ける（`provider.py::_SUBAGENT_TOOLS`）。新しい MCP の道具も同じ方針で足す。
- ローカル LLM は 1 枚の R9700 を取り合う。モデルの載せ替えとコンテキストの消費を増やす変更は、測ってから入れる。

## 誰が判断するか

`docs/POLICY-autonomous-development.md`。L0・L1 は統括（Claude）が関門を通して自動で進め、L2 だけ利用者の承認を得る。区分は `./deck.sh change-class`。

## 開発の進め方（2026-11-15 から）

AI に開発させるときは `./deck.sh sandbox new <名前> --repo <リポジトリ>` で箱を作り、箱の中の OpenCode（`./deck.sh sandbox opencode <名前> [--run 指示]`）とミラー（`mirror-start`）で直して確かめる。本番の ControlDeck は止めない。公開はプレリリースまで、正式化と本番への適用は利用者。

## 実機で確かめるとき

- 本番は `/data1tb/ControlDeck/app-strata` の **main ブランチ（正式 Release に追従）**。手で merge して再起動しない。マージ → 正式 Release → 本番の更新機能（使われていない時間に自動、または設定画面のボタン）で入る。旧 deploy ブランチは `backup/deploy-strata-20261115` に残してある。
- 開発は `/data1tb/ControlDeck/runtime-lifecycle`。**ここで main を checkout しない**（本番の worktree が main を使っている）。`git checkout -B dev-base origin/main` から作業ブランチを切る。
- OpenCode は既定で「プロジェクトの箱」（podman）で動く。設定 `isolation: host` で従来どおり。
- 隔離テストは `env -u CONTROL_DECK_CONFIG`（外さないと本番の data に書く）。
- ゲートウェイの鍵・受け入れ用アカウントのパスワードを表示しない。
- 外部からの取得（npm・モデル・ブラウザ）は利用者の許可を得てから行う。

## 完了の条件

コードを書いただけでは完了ではない。テスト（集中 → 全体 `./deck.sh test` → `cd frontend && npm run build`）、実画面（PC 幅と 320px）、`docs/implementation-status.md` と計画文書の更新、PR のマージまでそろえて完了とする。
