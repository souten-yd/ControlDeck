# CLAUDE.md — ControlDeck 実装の引き継ぎ

規約の原本は `AGENTS.md`（root で動かさない、`shell=True` 禁止、秘密をログに出さない、パス検証、監査、ローカル優先の検証、PR とリリースの運用）。ここには、いま進んでいる仕事と、引き継ぐ AI が最初に知るべきことだけを書く。

## いま進んでいる仕事

**全体の順番は `docs/ROADMAP.md`（2026-11-21）。最初に読む。** 下の仕事の優先順位・依存・利用者の判断待ちはそこにまとめてある。

**最優先: OpenCode で全ての開発ができるようにする**（開発用サンドボックス 第 2 段階。ローカル LLM と OpenRouter で ControlDeck 本体も開発し、ホストの GPU・API と Project Lab で確かめる）。
**まず `docs/HANDOFF-opencode-dev-environment.md` を読む。** 計画・方式の評価は `docs/PLAN-opencode-dev-environment.md`（第 1 段階の UI 検証・精密調査・コードレビューもここ）。

**統合ナレッジ基盤と Patent 画面**（PatentBuddy・ResearchDataHub を ControlDeck へ統合。一般・特許・規格の分野別 DB、横断検索、PDF 解析。特許の業務は `backend/app/patents/` と `/patents`。PatentBuddy Add-on は 2026-11-21 に撤去）。
**まず `docs/HANDOFF-knowledge-platform.md`（引き継ぎ・現状・作業一覧）を読む。** 計画・棚卸し・段階・データ回収とリポジトリを閉じる手順は `docs/PLAN-knowledge-platform.md`。進捗が動いたら両方を更新する（`AGENTS.md`「引き継ぎ資料の決まり」）。

**検索基盤の強化（Web 収集エンジン・特許の調査）**（2026-11-21〜）。**まず `docs/HANDOFF-web-collector.md` を読む。** 計画は `docs/PLAN-web-collector.md`、調査と採否は `docs/research-web-collector.md`。

**モデル登録の拡充（2026-11-26〜）**: Strata・Maya・Radiance の目録・取得・再開・フォルダの選択・設定の形。計画は `docs/PLAN-model-catalog.md`。

**AI Commander（2026-11-26〜）**: 複数の AI の CLI（Claude・Codex・OpenCode・Gemini・Grok）を 1 画面で、複数アカウント・監視・自動再開・利用量・チーム。計画は `docs/PLAN-ai-commander.md`。ControlDeck の作業場（OpenCode）で作る。

**Nexarch の開発機能の磨き込み（devx A〜F、2026-11-28〜）**: チームの画面・役割ごとのモデル選び・道具の配分と守り・再起動からの自動再開・資料の受け渡し・チームリーダーの道具の説明。**まず `docs/HANDOFF-nexarch-devx.md` を読む。** 計画と Web 調査の比較表（8 製品・URL つき）は `docs/PLAN-nexarch-team.md` の「2026-11-28 devx」節。現状は計画のみで A〜F は未検証。

## 読む順

1. `AGENTS.md`（と問題の台帳 `docs/ISSUES.md`。未対応・対応中の問題から片付ける）
2. `docs/ROADMAP.md` と、いまの仕事の `docs/HANDOFF-*.md`
3. `docs/PLAN-opencode-dev-environment.md`
4. `docs/design-opencode-feature.md`（v2 の設定の形、ゲートウェイ、MCP、圧縮）
5. `docs/implementation-status.md`（新しい順。最新の数件）
6. `docs/ROUTE-MAP.md`（情報の伝達ルートの地図。`backend/app/route_map.py` から作る。AI・道具・通知・外部通信を足す前に読み、足すときは先に地図へ書く）
7. コード: `backend/app/features/registry.py`（オプション機能）、`backend/app/integrations/opencode/provider.py`（session の設定生成）、`backend/app/addons/agent_mcp.py`（ControlDeck の MCP）、`backend/app/skills/`（スキル）

## 現行構成（2026-11-15）

- OpenCode は **v2 だけ**（`@opencode/cli`、実行ファイル `opencode2`）。v1（opencode-ai）と OMO は撤去済み。ECA は扱っていない。古い文書に残る ECA / OMO / v1 の記述は過去の経緯であって現行仕様ではない。
- v2 の拡張は OpenCode プラグイン 3 本（Superpowers・Adversarial Review・Auto Advisor）。管理領域へ npm で版固定して入れ、session の設定から `file://` で読ませる。使う/使わないは OpenCode 画面の設定。
- session の設定は job ごとに作る config directory に書き、`XDG_CONFIG_HOME` で向ける。利用者の `~/.config/opencode` は読まない・書かない。
- 重い道具（3D・音）は主の会話に載せず、子エージェントへ分ける（`provider.py::_SUBAGENT_TOOLS`）。新しい MCP の道具も同じ方針で足す。
- ローカル LLM は 1 枚の R9700 を取り合う。モデルの載せ替えとコンテキストの消費を増やす変更は、測ってから入れる。

## 言葉

利用者に見せる結果・報告・評価・PR 本文・Release ノート・引き継ぎ資料は日本語（`AGENTS.md`「結果は日本語で」）。子エージェントへの指示にも「報告は日本語で」と書く。

## 開発の分担

Opus が司令塔（ルーター）になり、作業を費用に見合う担い手へ回す（ローカル LLM・箱の OpenCode・OpenRouter の DeepSeek・Haiku・Sonnet は自由に使ってよい。設計・本番・最終レビューは Opus）。品質の確認は Opus が行う（`AGENTS.md`「Claude での開発: Opus が司令塔」）。

利用者が「ルールにして」と言った決まりは `AGENTS.md` に節として書く（`AGENTS.md`「利用者の決まりは、このリポジトリにも書く」）。

## 評価

AI 機能・RAG・画面の評価は原則として本番（実モデル・実データ）で、ゲートウェイ・サーバーの中を通して行い、結果を `~/ControlDeck-評価/` に日本語で残して一覧（README.md）を更新する（`AGENTS.md`「評価は本番で」）。評価の本文は公開リポジトリに入れない。

## 誰が判断するか

`docs/POLICY-autonomous-development.md`。L0・L1 は統括（Claude）が関門を通して自動で進め、L2 だけ利用者の承認を得る。区分は `./deck.sh change-class`。

**ControlDeck 本体のマージと正式 Release は、L2 を含めて毎回尋ねない**（2026-11-21 利用者合意、同文書「常設の承認」）。関門を通したらマージ → Release まで進め、何を変えたか・L2 に当たる点・未検証の点を事後に報告する。本番データの変更、外部からの取得などは、これまでどおり了承を得る。**本番のモデルの停止・再起動は、2026-11-21 の利用者の許可により、いつでも尋ねずに行ってよい**（`docs/ROADMAP.md`「利用者の判断・許可」）。

## 開発の進め方（2026-11-15 から）

AI に開発させるときは `./deck.sh sandbox new <名前> --repo <リポジトリ>` で箱を作り、箱の中の OpenCode（`./deck.sh sandbox opencode <名前> [--run 指示]`）とミラー（`mirror-start`）で直して確かめる。本番の ControlDeck は止めない。第 2 段階（2026-11-21〜）で、箱からホストへの口を中継 1 つに絞り、Project Lab・GPU の貸し出し・ホストの API で確かめられるようにしている（`docs/HANDOFF-opencode-dev-environment.md`）。

## 実機で確かめるとき

- 本番は `/data1tb/ControlDeck/app-strata` の **main ブランチ（正式 Release に追従）**。手で merge して再起動しない。マージ → 正式 Release → 本番の更新機能（使われていない時間に自動、または設定画面のボタン）で入る。旧 deploy ブランチは `backup/deploy-strata-20261115` に残してある。
- 開発は `/data1tb/ControlDeck/runtime-lifecycle`。**ここで main を checkout しない**（本番の worktree が main を使っている）。`git checkout -B dev-base origin/main` から作業ブランチを切る。
- OpenCode は既定で「プロジェクトの箱」（podman）で動く。設定 `isolation: host` で従来どおり。
- 隔離テストは `env -u CONTROL_DECK_CONFIG`（外さないと本番の data に書く）。
- ゲートウェイの鍵・受け入れ用アカウントのパスワードを表示しない。
- 外部からの取得（npm・モデル・ブラウザ）は利用者の許可を得てから行う。
- 知識と手元の調査で分からないことは、公式の資料 → コミュニティ（Reddit・XDA・GitHub issue など）の順に調べる（`AGENTS.md`「分からないことの調べ方」）。調べるための閲覧・検索は許可の要る「取得」に当たらない。

## 完了の条件

コードを書いただけでは完了ではない。テスト（集中 → 全体 `./deck.sh test` → `cd frontend && npm run build`）、実画面（PC 幅と 320px）、`docs/implementation-status.md` と計画文書の更新、PR のマージまでそろえて完了とする。
