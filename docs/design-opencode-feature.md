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
