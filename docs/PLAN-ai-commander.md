# 計画: AI Commander（複数の AI の CLI を 1 画面で操作・監視・自動再開・チーム）

状態: 2026-11-26 作成（Opus）。引き継ぎは `docs/HANDOFF-ai-commander.md`（実装を始めるときに作る）。

## 利用者の要望（2026-11-26）

- Codex・OpenCode・Claude・Google の AI（Gemini CLI）・X の AI（Grok）などを、1 つの画面で操作する。
- 各 AI に複数のアカウントを使える。同じ AI を別のアカウントで同時に起動できる（例: Claude を 2 アカウントで同時）。
- コンソールは何個でも開ける（Claude 3・Codex 2・Grok 1 など自由な組み合わせ）。アカウントは数だけ要るわけではなく、同じアカウントでも別のアカウントでも開ける。コンソールを足すときに「どう開くか」を選べる。
- 画面を監視する機能。AI アシスタントから AI Commander にアクセスして状況を確かめられる。止まっているものは理由を確かめ、**利用量の上限で止まっていれば回復の時刻を調べ、回復したら自動で再開**する。**これは LLM なしでも自動でできるとよい**。監視にローカル LLM を使ってもよい。
- 開いた AI 同士が簡単にチームを組める。ローカル LLM・OpenCode のモデル・OpenRouter のモデルが、リーダー・司令塔・チームメンバー・開発メンバーなどの役割をこなせる方法を検討して実装する。
- 各コンソールの進捗と、個別の利用料が一目で分かる。
- 既存のダッシュボード・OSS を調べ、流用できるものは流用し、参考にできるものは参考にする。**無駄なコードは要らない。実装に見合う効果があるかを評価する**（`AGENTS.md`「機能を取り込む条件」）。
- 作り方: ControlDeck 自身で ControlDeck を開発できる状態にしてから、それを使って作る。開発環境の使いにくい所は、作りながら直す。

## 調べた OSS（2026-11-26。出典は各 README・公式 docs）

| 名前 | ライセンス | 何が参考になるか | 採否 |
|---|---|---|---|
| Agent Deck（asheshgoplani/agent-deck） | MIT | tmux で分ける・複数の CLI・状態（working / waiting / done）の見せ方・`usage` | **画面と状態の分け方を参考**（コードは Go。流用はしない） |
| Claude-Code-Usage-Monitor | MIT | Claude の JSONL の集計・5 時間の窓・`--data-paths` でアカウント別 | **集計の仕方を参考**（Python。必要な関数だけ書き直す） |
| Claude Squad | AGPL-3.0 | tmux と git worktree の分け方 | 考え方だけ（AGPL なのでコードは使わない） |
| Happy | MIT | スマホからの操作・中継 | 参考止まり（ControlDeck は既に Web） |
| opcode（旧 Claudia） | AGPL-3.0 | `~/.claude/projects` からの履歴と費用 | 考え方だけ |
| Vibe Kanban・Crystal | Apache / MIT | — | **採らない**（それぞれ終了予定・非推奨と明記） |

公式で確かめた土台:
- **複数アカウント**: Claude Code は `CLAUDE_CONFIG_DIR`、Codex は `CODEX_HOME`、OpenCode は `XDG_CONFIG_HOME`・`XDG_DATA_HOME`（ControlDeck は既に job ごとに分けている）。Gemini CLI は `HOME` を分けるのが確実。Grok は xAI の Grok Build（2026-05 ベータ）とコミュニティ版がある → 実機で確かめてから。
- **上限と回復の時刻**: Claude Code の statusline の JSON に `rate_limits.five_hour.resets_at`・`seven_day.resets_at`（公式）。画面の文言（`You've hit your limit · resets 11pm (Asia/Tokyo)` など）は公式の形が無いので、文言の読み取りは 2 段目。Codex は `codex exec --json` の JSONL（`turn.failed`・`error`）。

## 方針（今ある部品を使う）

- **コンソール = ControlDeck の Terminal の tmux セッション**（`terminals/manager.py`）。新しい PTY の仕組みは作らない。AI Commander のコンソールは、CLI とアカウントの環境変数を付けて開いた tmux セッションに印（種類・アカウント・役割・チーム）を付けたもの。
- **アカウント = 設定のディレクトリ**: `data/ai-commander/accounts/<cli>/<名前>/`。開くときに `CLAUDE_CONFIG_DIR` などを向ける。ログインは利用者がコンソールの中で行う（ControlDeck は鍵・トークンを読まない・見せない）。
- **状態の判定は LLM なしの決まりで**: tmux の画面（`capture-pane`）と、CLI が書く記録（Claude の JSONL・statusline、Codex の sessions）から「作業中・入力待ち・上限で停止・終了・エラー」を出す。決まりは CLI ごとの小さな表（正規表現と記録の読み方）にする。
- **自動再開**: 上限で止まったら回復の時刻を出し、時刻が来たら同じセッションへ「続けて」を送る（Terminal の automation の送信を使う）。時刻が読めないときは間隔を空けて試す。LLM は要らない。
- **利用量**: Claude は JSONL のトークン数と statusline の使用率、Codex は sessions の記録、OpenRouter は ControlDeck の `cloud.py` の費用。アカウントごと・コンソールごとに出す。料金に換算できないもの（定額のプラン）は使用率で見せる。
- **AI アシスタントから見る**: ControlDeck の MCP に `commander.status`（一覧と状態）・`commander.read`（画面の最後の数十行）・`commander.send`（入力を送る。権限の確認つき）を足す。
- **チーム**: コンソールに役割（司令塔・リーダー・メンバー・開発）を付け、司令塔が MCP でメンバーへ指示を送り、結果を読む。ローカル LLM・OpenRouter のモデルは OpenCode のコンソールとして入る（OpenCode のモデルの選択はそのまま使う）。役割の考え方は OpenCode の roles（`integrations/opencode/roles.py`）に合わせる。

## 段

| 段 | 中身 | 効果の確かめ方 | 状態 |
|---|---|---|---|
| C0 | 実機の確認: 各 CLI の導入状況・`CLAUDE_CONFIG_DIR`/`CODEX_HOME` で 2 アカウント同時・上限の文言と記録の形を集める | 実機の記録を文書に | 済み（2026-11-26。c1-spec.md「実機で確かめた事実」） |
| C1 | コンソールの一覧と追加（CLI・アカウント・作業フォルダを選んで開く）・アカウントの管理・画面（タイル） | 画面と E2E | 済み（2026-11-27。`app/commander/`・`/commander` の画面・pytest 15 件・E2E 4 件。実機で 2 アカウントのログインと同時起動は利用者の操作待ち） |
| C2 | 状態の判定（LLM なし）・一覧に状態と最後の行 | 実機のログで判定の正しさを数える | 済み（2026-11-27。実機の画面 6 種で試験。上限の実物の画面は未） |
| C3 | 上限の検知と自動再開 | 実際に上限に当たったセッションで確かめる | 実装済み（2026-11-27。実機の tmux で送信まで確認。実際の上限は当たったときに） |
| C4 | 利用量の表示（コンソール・アカウントごと） | 公式の表示（`/usage` など）と数字を比べる | 一部（2026-11-27。5 時間・週の使用率と回復の時刻。トークン・費用・コンテキストは未） |
| C5 | AI アシスタントの道具（MCP） | AI アシスタントから状態を聞く | 未 |
| C6 | チーム（役割・司令塔からの指示と結果の受け取り） | 小さな課題をチームで解かせて比べる | 未 |

## 守ること

- 各 AI の利用規約に従う（1 つのアカウントを他人と共有しない。自動の操作が規約で禁じられている CLI では自動再開を出さない）。
- 鍵・トークン・ログインの情報を ControlDeck のログ・画面・MCP の返事に出さない。
- 画面の文字を MCP で返すときは、秘密らしい値を伏せる。
- 監視の負荷: `capture-pane` は数秒おき・変化が無ければ読み直さない。測って PR に書く。

## 2026-11-27 の変更（C1 の後）
- アカウントに画面の名前（既定のアカウントにも）。
- OpenCode は ControlDeck の OpenCode を OpenCode 画面の API 設定で開く（アカウントは既定の 1 つ）。
- 2026-11-27 利用者の指定で画面の名前を「Nexarch」に改名（コードの名前 commander・URL /commander は変えない）。
