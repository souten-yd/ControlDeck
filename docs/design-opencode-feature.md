# OpenCodeオプトインfeature 詳細設計

## 方針

OpenCodeはControl Deckの必須依存にしない。通常の`./deck.sh`、service登録、Web起動では導入も有効化も
行わず、`./deck.sh feature ... opencode`という明示操作だけで状態を変える。外部に既にあるOpenCodeは
検出するが、明示enableまでは利用しない。

公式仕様に合わせ、管理導入はnpm package `opencode-ai`をControl Deck data directory内の専用prefixへ
入れる。グローバルnpmやユーザーの既存OpenCode設定を変更しない。OpenAI互換ローカルモデルは
`@ai-sdk/openai-compatible`、`options.baseURL`で構成し、実行時専用`OPENCODE_CONFIG`を使う。
根拠はOpenCode公式の[インストール](https://opencode.ai/docs)、[CLI](https://opencode.ai/docs/cli)、
[provider](https://opencode.ai/docs/providers)、[config](https://opencode.ai/docs/config)仕様とする。

## Feature registry

- 状態: `available / installed / managed / enabled / version / health / executable`。
- 保存: `data_dir/features/state.json`。既知feature ID以外を拒否し、atomic replaceする。
- `status`: 読取のみ。PATH上の外部導入もinstalledとして検出する。
- `install`: 専用prefixへ配列引数で`npm install --prefix ... opencode-ai`。明示enableは別操作。
- `enable`: installedかつhealthが正常な場合だけ有効化する。反映にはWeb service再起動が必要。
- `disable`: 状態だけ無効化し、セッションデータや外部導入を消さない。
- `uninstall`: 先にdisableし、registryが管理する専用prefixだけを`Path.resolve()`境界確認後に削除する。
  外部導入は絶対に削除しない。

## 登録境界

- coreの`GET /features`だけは常時登録する。
- OpenCode router、`code.agent` executor/catalog/metadataはprocess起動時にenabledの場合だけimport・登録する。
- 公開`/meta`はenabled feature IDだけを返す。Frontendはmeta取得後にrouterを構築し、enabled時だけ
  OpenCode chunk/route、sidebar、command paletteを登録する。
- backend SPA fallbackはdisabled時の`/opencode`を404にし、CSS非表示だけの実装にしない。

## Code agent境界

- `backend/app/integrations/opencode/`だけがOpenCode CLIを知り、workflowは汎用`CodeAgentProvider`へ依存する。
- operationは`analyze / implement / fix / test / review`。project pathは`files.resolve()`で既存の許可root・
  deny root・symlink境界を検証する。
- `opencode run --format json --model controldeck/<model> --dir <project> --file <prompt-file>`を配列引数で構築する。
  prompt本文はargvへ露出せず600権限のjob別一時ファイルに置く。provider設定もjob別に分離して同時実行を妨げない。
- Webプロセスの子としてagentを常駐させず、`systemd-run --user --wait --pipe --collect`のtransient unitで実行。
- stdout/stderrは上限付き。API key、prompt全文、秘密値を監査ログへ出さない。cancel時はunitを停止する。
- workflow `code.agent`はfeature有効時だけ存在し、既存job/engineのtimeout・cancelを継承する。

### Add-on agent tools

- 利用者authorityがあるjob／TUIの実行時configに限り、Host管理のローカルstdio MCPを追加する。
- MCP bridgeはHostのloopback APIだけを呼び、OpenCodeやAdd-onへHost cookie、DB access、project全体権限を
  渡さない。署名済みuser-bound tokenは0600のruntime configへ置き、最大8時間で失効する。
- tool discovery／callごとに現在のRBAC、Add-on enable、capability、schemaをHostで再検証し、callは既存の
  owner付きAdd-on Host Jobへ流す。結果はjob IDとopaque asset IDを含み、ログからpathを拾わせない。
- Project Lab管理下のprojectで起動した場合だけ、署名済みtokenをcurrent projectへ束縛する。
  `projects.pick`／`files.export`を許可されたAdd-onには、project相対の既存subdirectoryをHostが検証して
  短命output `grant:`へ変換する汎用toolを投影する。OpenCode／Add-onへproject pathは渡さない。
- Add-on toolへ渡すservice tokenは、検証済みinputに含まれる`grant:`だけを要求単位で委譲し、
  grant入力が無い場合は空allowlistとする。
- OpenCodeのユーザー／グローバルconfigは変更しない。feature無効時はMCP Host endpointも登録しない。

## UI

- `/opencode`は状態、provider endpoint/model、project、operation、指示を表示する独立feature page。
- 設定はserverへ保存し、provider endpointは既存LLM provider候補から選ぶ。
- 実行はserver jobとして開始し、既存jobs streamで進捗を追跡する。モバイル320pxは1列表示。

## 受け入れ条件

- clean stateで通常起動してもinstall/enableされず、OpenCode API/route/nodeが404またはcatalog非掲載。
- isolated stateでenableするとrouter/menu/command/nodeが登録され、disable後の再起動で消える。
- 外部OpenCodeをuninstall操作しても外部binary/config/dataを削除しない。
- project symlink脱出、未知operation、未有効実行を拒否する。
- prompt本文をargvへ入れず`shell=True`を使わない。cancelでtransient unitが停止し、一時ファイルが残らない。
- llama.cpp OpenAI endpointを使った実機analyzeが成功する。全test/buildと1280px/320pxを確認する。
- 実OpenCodeがAdd-on toolをdiscovery／callでき、Add-on disable後の再discoveryでtoolが消える。

## OpenCode v2（2026-09-12 追加）

### 位置づけ

v1（npm `opencode-ai`、実行ファイル `opencode`）はそのまま残し、v2（npm
`@opencode/cli`、実行ファイル `opencode2`）を別のアドオンとして並べる。導入先prefixが
`data/features/opencode` と `data/features/opencode-v2` で別なので、片方の導入・更新・
削除がもう片方に影響しない。どちらを使うかは OpenCode 画面の設定 `runtime`（`v1`/`v2`）
で決める。既定は `v1`。設定した系列が未導入なら、導入済みのほうへ落として動かす。

`@opencode/cli` は bin に `opencode` と `opencode2` の両方を持つ。registry の
`executable` に `opencode2` を選ぶのは、PATH 上の外部 v1 を「外部導入の v2」と
誤検出しないため（v1 の bin は `opencode` だけ）。

アンインストールは従来どおり管理prefixだけを消す。v2 が持つ `opencode2 uninstall`
サブコマンドは利用者のセッションDBや設定まで消すので呼ばない。

### v1 との差（実測: 2.0.2）

| | v1 | v2 |
|---|---|---|
| runtime configの渡し方 | `OPENCODE_CONFIG=<file>` | `XDG_CONFIG_HOME=<dir>`（`<dir>/opencode/opencode.json`） |
| provider | `provider` / `npm` / `options` | `providers` / `package` / `settings` |
| 独自ヘッダー | `options.headers` | provider直下の `headers`（`settings` の中では送られない） |
| 画像 | `attachment` + `modalities` | `capabilities.{tools,input,output}` |
| 権限 | `permission` の tool別map（`bash`） | `permissions` の配列 `{action,resource,effect}`（`shell`） |
| agent | `agent` + `tools` map | `agents`（`tools` は無い。agent毎の `permissions` で書く） |
| MCP | `mcp.<name>` / `enabled` / `timeout` 数値 | `mcp.servers.<name>` / `disabled` / `timeout.{catalog,execution}` |
| compaction | `reserved` / `prune` | `buffer`（`prune` は警告付きで無視） |
| skills | `{paths:[...]}` | 配列 |
| `run` の対象 | `--dir <project>` | 作業ディレクトリ（`--dir` は無い） |
| サーバー | プロセス内 | 既定は常駐 background service。`--standalone` でjob専用にする |

`OPENCODE_CONFIG` / `OPENCODE_CONFIG_DIR` / `OPENCODE_CONFIG_CONTENT` は
2.0.2 では効かない（文字列は binary に在るが、`debug config` の読み込み元は
変わらない）。そのため v2 は job ごとに config directory を作り、
`XDG_CONFIG_HOME` で向ける。利用者自身の `~/.config/opencode` は読ませない。

runtime config は v1 の形で組み立ててから機械的に読み替える（`_v2_payload`）。
窓の大きさ・圧縮の発火点・重い道具の内訳といった実測に基づく判断を二重に持たないため。

### ゲートウェイ／MCP／llamaサーバー

いずれも v1 と同じ経路を使う。provider は ControlDeck の OpenAI 互換ゲートウェイ
（`/api/v1/llm/v1`）を指し、APIキーは既存の発行済みキー、モデルは仮想モデル `auto`。
llama.cpp / Lucebox の起動保証（`ensure_ready_by_base_url`）、KVの受け入れ制御、
アイドル判定も従来どおり通る。Add-on の MCP は既存の `addon_mcp_bridge.py` を
そのまま使う（v2 の MCP client から `connected` を確認済み）。

### 画面（v1 / v2 で1枚ずつ）

`/opencode` が v1、`/opencode-v2` が v2。中身は同じ `OpenCodePage` で、`runtime`
prop だけが違う。ナビ・コマンドパレット・クイックアクションにもそれぞれ項目を出し、
アドオンが無効な系列は SPA fallback が 404 を返す（CSS で隠すだけにしない）。

**画面のボタンで開くセッションは、常にその画面の系列で起動する**（`POST
/opencode/sessions` の `runtime`）。設定画面の系列トグルは、画面を持たない経路
——AIチャットとワークフロー `code.agent`——の既定を決めるためのもの。名指しした系列が
未導入なら導入済みのほうへ落として起動し、見出しにその旨を出す。

このページで開始したセッション ID は系列ごとに別の localStorage キーへ分ける
（v1 の画面に v2 のセッションが並ぶと、どちらで開いたか分からなくなる）。

### TUI の設定（keybind / theme）は1枚へ集約する

v2 は TUI 側の設定を config directory の `cli.json` から読む（v1 の層状 `tui.json` から
変わった）。ControlDeck は job ごとに config directory を作り替えるので、そのままだと
keybind を変えても次の起動で消える。`data/integrations/opencode/cli.json` を**共有の1枚**と
し、生成した directory からはそこへ symlink する。ControlDeck は最初に既定を書いた後、
このファイルの中身を触らない。TUI が設定画面から symlink を実体ファイルへ置き換えた
場合も、そちらを尊重して触らない。

既定は **v1 の操作へ揃えてある**。v1（1.18.30）と v2（2.0.2）の既定キーを突き合わせると
162 件中 16 件だけが変わっており、そのうち「v2 が割り当てを外した／ずらした」ものを
v1 の値へ戻す。plan ⇔ build の切替（`agent.cycle`）が `tab` でなくなったのが実用上
いちばん響くため、`tab` / `shift+tab` に戻す。併せて v2 が割り当てを外した diff viewer
（`left` / `right` / `E` / `tab` / `enter,space`）と入力欄の先頭・末尾（`home` / `end`）も戻す。

`tab` は v2 で `prompt.autocomplete.complete` も使うが無害である。autocomplete 一群は
候補が出ている間だけ効く文脈限定の割り当てで（`next=down` / `prev=up` / `select=return` と、
通常操作と重なるキーを並べていることから分かる）、v1 でも `tab` は `agent_cycle` と
`diff_switch_focus` が共有していた。`left` / `right` / `home` / `end` / `space` も v1 で
同じ共存をしていた組み合わせをそのまま戻している。

戻さないものが 2 つある。v2 が新機能へ割り当て直したキーで、戻すと新機能が潰れる:

| action | v1 | v2 |
|---|---|---|
| `theme.switch` | `<leader>t` | v2 では `terminal.toggle` |
| `session.child.first` | `<leader>down` | v2 では `terminal.select` |

### 道具の結果の上限と自動整形（v2 のみ）

v2 は `tool_output: {max_lines, max_bytes}` と `formatter` を組み込みで持つ
（v1 には対応する設定が無い）。

v2 の既定は 2,000 行 / 51,200 バイト。51,200 バイトは約 14,600 トークンで、窓
131,072 なら 1 件で 11% を使う。実測（262,142 トークンで打ち止めた会話）では道具の
結果が 48.1% を占め、内訳は bash 178k 文字・read 135k・edit 95k だったので、既定の
ままでも数件で窓が埋まる。そこで**入力窓の 1/16 を上限とし、v2 の既定を超えない**形に
する（3.5 バイト/トークン換算。ctx 131,072 なら 26,880 バイト）。窓が分からない
ときは既定に任せ、勝手に絞らない。狭いモデルへ回っても 1 件が窓を食い尽くさない。

削っても情報は失われない。v2 は全文を data directory の `tool-output/` へ落とし、
その directory を読む権限を既定で開けている。必要な行は agent が grep で拾える
——ControlDeck が MCP bridge で手作りした形（`RESULT_INLINE_LIMIT`）と同じである。

`formatter: true` は編集のあとに整形を通す。差分が整形だけで膨らむのを防ぎ、
lint との往復を 1 往復ぶん減らす。

### 道具の出し入れ

v2 の agent は `tools` map を持たない。代わりに agent 毎の `permissions` へ
`{action: "<道具名のパターン>", resource: "*", effect: "deny"}` を並べる。これは
v2 自身が v1 のトップレベル `tools: {pattern: false}` を移行する先と同じ形である。

実機で確かめたところ、主エージェント（`build`）に deny を置いた道具はモデルの
一覧そのものから消えた（Add-on の道具 7 個だけが見え、`media_scene_*` /
`media_job_*` / `sonic_*` は出なかった）。v1 の `tools: false` と同じ効きで、
重い道具を専門の子へ委ねる形と、それによる文脈の節約は v2 でも保たれる。

### v2 で失われるもの

- `compaction.prune`（完了した道具の出力だけを捨てる剪定）は v2 に無い。
  v2 は `prune` を警告付きで無視する。畳む側（`compaction.auto` / `buffer`）は
  そのまま効く。

### 対応していないアドオン

- **OMo（`oh-my-openagent`）は v2 未対応**。4.19.4 も 5.0.0-beta.56 も依存は
  `@opencode-ai/plugin` / `@opencode-ai/sdk` の 1.x 系（1.15.13 / 1.18.22）で、
  v2 の `@opencode/plugin` 2.x には載っていない。`runtime: v2` を選ぶと OMo は
  効かない。OMo を使う間は v1 のままにする。
