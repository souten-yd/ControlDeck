# 計画: OpenCode v2 を ControlDeck の開発環境にする（UI 検証・精密調査・コードレビュー）

状態: 計画確定（2026-11-15）。進捗はこの文書の「進捗」と `docs/implementation-status.md` に書く。
引き継ぎ: 他の AI が続きを担当するときは、まずこの文書、次に `CLAUDE.md`、`docs/design-opencode-feature.md` を読む。

## 目的

ControlDeck の中で、Add-on・Web アプリ・ゲームを作って確かめるところまでを一続きにする。
「コードを書いたから完成」ではなく、**実際の画面で動き、使いやすいことを確かめて完成**と判定できるようにする。

OpenCode 画面で、次の 3 つを簡単に選べ、結果が目で分かることを目標にする。

| 作業 | AI がすること | 画面で見えるもの |
|---|---|---|
| UI 検証 | 開発中の画面を実ブラウザで開き、幅を変え、操作し、撮る | 幅ごとのスクリーンショット、見つかった問題、コンソールのエラー |
| 精密調査 | 公式文書・一次資料を集め、出典と反証を付けて報告 | 結論、根拠（URL・取得日時・版）、未確認事項 |
| コードレビュー | 差分を別の文脈で読み、影響範囲と抜けを探す | 重大度順の指摘、該当箇所 |

## 現状（2026-11-15 に main と実機で確認）

| 機能 | コード | 実機 |
|---|---|---|
| OpenCode v2（@opencode/cli） | あり | 2.3.0 導入済み |
| Superpowers / Adversarial Review / Auto Advisor | あり（`features/registry.py`、`integrations/opencode/provider.py::_workflow`） | 3 つとも導入済み、**設定はすべて off** |
| Add-on MCP Bridge（`addon_mcp_bridge.py`） | あり。3D・音は専用の子エージェントへ分けて主の文脈を節約（`_SUBAGENT_TOOLS`） | 動作中 |
| スキル（`app/skills`、`bundled/`） | 画像・音楽・効果音・3D の 4 本 | 導入済み |
| Deep Research（`workflows/deep_research.py`） | 複数ラウンド検索・再評価・反証・引用検証・GitHub・学術・特許・ローカルコード | AI チャットから使える。**OpenCode からは呼べない** |
| Playwright E2E（`frontend/e2e`、69 本） | あり | **ブラウザ本体が未導入で動かない**。既定の接続先が本番 8765 |
| OpenCode の headless 実行（operation） | analyze / implement / fix / test / review | API・チャットから |

## 提案の評価

元の提案（Playwright MCP・Chrome DevTools MCP・Context7・Web 検索強化・axe/Lighthouse・Serena/ast-grep・専用スキル・画像比較）を、
この環境（R9700 1 枚、ローカル LLM、窓 128K〜320K、OpenCode 6 スロット）に照らして評価した。

| 提案 | 判断 | 理由 |
|---|---|---|
| Playwright MCP | **採用（最優先）** | 実画面の操作・撮影・幅変更・コンソール・通信まで 1 つで足りる。システムの Google Chrome を使えるのでブラウザの追加取得が要らない（`--executable-path`）。ただし道具が約 60 個・スナップショットが大きいので、**主の会話には載せず「UI 検証」の子エージェントに持たせる**（3D・音と同じ方式）。 |
| Chrome DevTools MCP | **見送り（任意・後回し）** | Playwright MCP の `--caps devtools`・コンソール・通信の道具と大半が重なる。両方載せると道具の説明だけで数万トークンを使い、ローカル LLM の窓を圧迫する。性能トレース（LCP など）が要る場面が出たら、同じ子エージェントにだけ足す。 |
| Context7 | **任意（既定 off）** | 版に合った文書を引ける利点は大きいが、問い合わせが外部サービス（context7.com）へ出る。特許の未公開内容を外へ出さない方針と衝突しうるので、明示的に有効にした場合だけ、精密調査の子エージェントに持たせる。 |
| Web 検索＋根拠検証 | **既存 Deep Research を流用** | 新しい調査エージェントは作らない。Deep Research を OpenCode から呼べる道具（ControlDeck の MCP）として出す。外部検索を使うかは設定で選び、既定は「ローカル（コード・RAG）だけ」。 |
| axe-core | **採用** | `@axe-core/playwright` を E2E に足すだけで済み、実行時の常駐も無い。 |
| Lighthouse | **見送り** | 重く、結果がこの環境（ローカル配信・管理画面）では指標として弱い。 |
| Serena | **見送り** | Python と言語サーバーの常駐が要り重い。OpenCode v2 に LSP と検索が既にある。 |
| ast-grep | **採用（第 3 段階）** | 単一バイナリで軽い。構文で探す・影響を洗う用途は grep より正確。レビューの子エージェントに持たせる。 |
| ControlDeck 専用スキル | **採用（最優先）** | 安く効く。Add-on の契約・安全要件・UI 規約（`docs/ui-ux-guidelines.md`、`docs/addon-ux-guidelines.md`）と、UI 検証の手順・合否基準を書く。外部の Web Interface Guidelines は取り込まず、自前の規約を正とする。 |
| 画像比較（Visual Regression） | **後回し** | フォントや描画差で揺れやすく、基準画像の管理が要る。UI 検証の撮影結果を残す仕組みができてから検討する。 |

使いやすさのための判断:

- **道具を常時載せない。** 作業の種類ごとに子エージェントへ道具を持たせ、主の会話は軽いまま保つ。
- **（2026-11-16 撤去）選ぶのは「作業の種類」だけにする。** 利用者の判断で、選ばせずに自動で回す形（自動レビュー）へ置き換えた。 利用者が MCP サーバーを個別に組み合わせる画面は作らない。導入・更新・削除は既存の「設定 → オプション機能」に寄せる。
- **結果は ControlDeck が受け取って表示する。** AI に自由形式で書かせず、ControlDeck の道具（`control_deck.report`）で構造化して渡させる。画面はそれを幅ごとの写真・指摘一覧として出す。

## 安全

- ブラウザは使い捨てのプロファイル（`--isolated`）・headless で動かす。ControlDeck 本番（8765）と外部への通信は遮る設定を既定にする。
  ただし Playwright MCP の origin 制限は安全境界ではない（公式の注意書き）。境界は従来どおり OpenCode の権限設定と、AI が持つ権限そのもの。
- `browser_run_code_unsafe` は使わせない（権限で deny）。
- 外部へ出る道具（Context7、Deep Research の Web 検索）は既定 off。有効にする画面で、何が外へ出るかを明示する。
- 既存の Host 権限・Add-on 境界・認証・RBAC・スコープ制限は変えない。PatentBuddy の `ui.html` 既定、Vue read-only、backend authoritative、`level_0_manual_only`、no execution capability は触らない。
- 導入は ControlDeck の管理領域へ版を固定して入れる。利用者のグローバル OpenCode 設定は変えない。

## 段階と PR

各段階は小さな PR に分け、実装・テスト・文書・実画面確認をそろえてからマージする。

### 第 1 段階: UI 検証

1. **PR A（この文書）** 計画と引き継ぎ文書の更新。古い ECA / OMO 前提の記述を現行の v2 構成へ直す。
2. **PR B（backend）**
   - オプション機能に `opencode-mcp` の種類を足し、`@playwright/mcp` を版固定で入れる（npm、管理領域）。
   - OpenCode 設定に「UI 検証」を足す。有効なら Playwright MCP を session の MCP に足し、道具は子エージェント `ui-verifier` だけに見せる。
   - ControlDeck の MCP に `control_deck.report`（検証・調査・レビューの結果を構造化して受け取る）を足す。撮影画像は session の出力先から取り込む。
   - スキル `controldeck-web-ui-verification`（幅 320/390/768/1280、ライト/ダーク、処理中・失敗・未設定の表示、フォーム・戻る・再接続、合否の書き方）と `controldeck-addon-development` を同梱する。
   - 接続診断: 導入済みか、ブラウザが起動するか、道具の一覧が取れるか。
3. **PR C（画面、Fable が担当）**
   - OpenCode 画面のセッション開始に「作業の種類」（ふつう／UI 検証／精密調査／レビュー）。選ぶと必要な道具と最初の指示が決まる。
   - 結果の画面: 幅ごとの写真、指摘（重大度・該当箇所）、コンソールのエラー、根拠の一覧。
4. **PR D（E2E）** `@axe-core/playwright` によるアクセシビリティ検査、幅ごとの project、本番 8765 へ向かない既定、失敗時の trace・写真を成果物に残す。

### 第 2 段階: 精密調査

- Deep Research を OpenCode から呼べる道具にする（`control_deck.research`）。既定は「ローカルだけ」（`local_code`・`rag`）。Web・GitHub・学術を使うかは設定で選ぶ。
- 結果に一次資料・版・取得日時・URL・反証・未確認事項を残し、`control_deck.report` で画面へ出す。
- Context7 を任意の機能として足す（既定 off、子エージェント `researcher` だけ）。

### 第 3 段階: コード解析・レビュー

- ast-grep を任意の機能として足し、子エージェント `reviewer` に持たせる。
- 「レビュー」作業: 差分・影響範囲（ast-grep）・テスト結果を照らし、Adversarial Review の独立した文脈で読む。指摘は `control_deck.report` へ。

## 進捗

| 項目 | 状態 | PR |
|---|---|---|
| 計画・引き継ぎ | 完了 | #377 |
| 第 1 段階 B（backend） | 実装済み（Playwright MCP 導入・接続診断を実機で確認） | #378 |
| 第 1 段階 C（画面） | 完了（Fable 担当。検証用インスタンスで 320/1280・ライト/ダークを撮影確認） | #379, #381 |
| 第 1 段階 D（E2E） | 実装済み（`./deck.sh e2e`、axe 12 件合格） | #380 |
| 第 2 段階 | 完了（精密調査 #382、Context7 #383。どちらも実機確認） | #382, #383 |
| 開発用サンドボックス | 第 1 段階完了（`./deck.sh sandbox`。ミラー・箱の中の OpenCode・本番 deck.sh の禁止） | #385 |
| 第 3 段階 | 完了（reviewer 子エージェント＋ast-grep。実機で呼び出し側の取りこぼしを critical で検出） | #383 |
| AI アシスタントで OpenCode をフル機能（2026-11-17、利用者の依頼）段階 1 | `opencode serve` の API へ切替。道具のカード・許可の確認（今回だけ/常に/拒否）・停止・権限モード（Auto 既定 / 確認、タップと Shift+Tab）。段階 2: ローカル LLM による許可の判断・5 つのモード・`/` コマンド・実行中の追加の指示。段階 3: 子エージェント・計画・セッション一覧・スクショの共有 | 本 PR |
| 作業の種類 → 自動レビュー（2026-11-16、利用者の依頼） | 「作業の種類」の選択を撤去。代わりに session の指示 `auto-review.md`（既定オン）で、コードを変えたら完了前に reviewer、行き詰まったら advisor()。レビュー・相談・開発担当に外部モデルも可 | 本 PR |

## AI アシスタントの OpenCode（opencode serve の API、2026-11-17）

方式の比較（利用者と合意）: ACP（`opencode acp`）は private server で TUI とセッションを共有できず、エディタ向けの共通部分しか使えない。公式 Web アプリの埋め込みは画面の部品の取得元とスマホでの使い勝手が不明。**常駐サーバーの API（公式 `@opencode/client` と同じ HTTP + SSE）を ControlDeck の backend から使い、画面は今のアシスタントのまま**にした。

- `integrations/opencode/chat_server.py`: プロジェクト × 利用者ごとに `opencode serve` を systemd のユーザーユニット（`cd-opencode-chat-<key>`）で、プロジェクトの箱の中に起こす（隔離は今の箱と同じ: 書けるのはそのプロジェクトだけ）。パスワードは環境変数ファイル（0600、`podman --env-file` / `EnvironmentFile`）で渡し argv に載せない。30 分使われなければ止める。セッションの記録は既存の OpenCode と同じ場所（TUI からも見える）。
- 1 回分の対話（`run_turn`）: `/api/event`（SSE）を先に開き、`/api/session/{id}/prompt` を送る。`session.text.delta`・`session.reasoning.delta`・`session.tool.*`・`permission.asked/replied`・`session.execution.*` をチャットへ流す。ジョブが止められたら `/interrupt`。
- 権限: `plugins/controldeck-permission`（依存なし。`package.json` の `exports["./server"]` でディレクトリとして渡す。ファイルで渡すと WARN だけで読まれない）。判断させる操作（shell・edit・write・webfetch・execute）は設定で ask にしてプラグインの `permission.hook("evaluate")` に回す（v2 は規則の既定が allow で、shell も聞かずに通るため）。ask は禁止リスト（deny）より前に置くので、プラグインから禁止は緩まない。モードは設定ディレクトリの `controldeck-permission.json` を毎回読む（画面で切り替えると次の操作から効く）。
  - auto（既定）: 編集・読むだけ・テスト/ビルドは自動。削除・上書き・push・外への通信・パッケージの導入・sudo などと、判断できないものは確認。
  - ask: 判断させる操作はすべて確認。
- API: `GET/PUT /api/v1/chat/opencode/permission-mode`（切替は settings.manage）、`POST /api/v1/chat/conversations/{id}/opencode/permission`（once / always / reject、監査あり）。停止は既存の `POST /api/v1/jobs/{id}/cancel`。
- 実測（v2.3.0）: ネットワークを切った箱で、`evaluate` が呼ばれ、allow で問い合わせなしに実行、ask のままなら `permission.asked`、reply 204 で続く、reject で道具が失敗しそのターンは interrupted、`/interrupt` で `session.execution.interrupted`。本物の OpenCode を `chat_server.ensure`（systemd + 箱）で起こし、偽の OpenAI 互換サーバーで 3 場面（Auto の ls・Auto の rm → 許可・確認モードの ls → 拒否）を確認。

## 版の固定（2026-11-15 時点、公開から 2 週間以上経った版）

| パッケージ | 版 | 用途 |
|---|---|---|
| `@playwright/mcp` | 0.0.84 | UI 検証 |
| `@axe-core/playwright` | 4.14.0 | E2E のアクセシビリティ検査 |
| `@upstash/context7-mcp` | 4.4.4 | 精密調査（任意） |
| `@ast-grep/cli` | 0.51.0 | レビュー（任意） |
| `chrome-devtools-mcp` | 1.12.0 | 見送り。必要になったら |

## 実装メモ（引き継ぎ用）

- v2 の agent の指示は `system`。v1 形の `prompt` は `_v2_agents` が読み替える（そのままだと黙って無視される。2.3.0 の `opencode2 debug agents` で確認）。
- Playwright MCP 0.0.84 は既定で 25 個の道具、定義は約 22KB（約 6,300 トークン）。主の `build` からは `playwright_*` を deny で外し、`ui-verifier` だけが持つ。
- 画面写真は `data/integrations/opencode/ui-verify/<session>/` に撮られ、`control_deck.report` の時点で `reports/<id>/images/` へ取り込む。TUI の session 名は利用者ごと（`tui-<user>`）なので、取り込まないと次の session が上書きする。
- 接続診断: `POST /api/v1/opencode/tools/ui-verify/diagnose`（導入 → Chrome → 起動 → 道具の一覧 → about:blank を開く）。
- 結果: `GET /api/v1/opencode/reports`、`/reports/{id}`、`/reports/{id}/images/{name}`、`DELETE /reports/{id}`。管理者（settings.manage）は全員分、それ以外は自分の分だけ。
- E2E は `./deck.sh e2e [spec]` で、本番とは別の検証用インスタンス（一時 data、ポート 18765、使い捨ての管理者）を起こして流す。`CONTROL_DECK_ISOLATED=1` でファン制御・通知・保守・Ollama/LLM の管理・SearXNG の停止を起動しない。
- axe（`e2e/a11y.spec.ts`）は critical だけで落とす。serious 以下は結果に添付する。2026-11-15 時点で主要 6 画面 × 320/1280 すべて critical 0・横スクロール無し。
- `e2e/support/session.ts` のログインは「ログイン」と「顔認証・パスキーでログイン」の 2 つに当たって全 spec が落ちていた（`exact: true` で直した）。
- オプション機能の画面（設定 → 機能と拡張）で、OpenCode の拡張・作業の道具の「初期設定」（OpenCode で使う・外部通信・接続診断）ができる。導入済みで未設定なら一覧に「初期設定がまだです」と出し、「詳細と操作」を開いておく（`features/opencode/FeatureSetup.tsx`）。
- OpenCode が入っていない検証用インスタンスで OpenCode 画面を試すときは `e2e/support/opencode.ts` の `openOpenCode`（meta の enabled_features を補い、最初に読む API を差し替える）。
- 精密調査: ControlDeck の MCP に `control_deck.research`。AI チャットの `_deep_search` をそのまま呼び、結果を `kind: research` の報告として残す。情報源は既定で `local_code`（と `rag`）だけ。`research_external` をオンにしたときだけ web / academic / github / direct を使う。外部を頼まれて許可が無いときは落として、報告の「確かめられなかったこと」に書く。`project_dir` は CodeDEV 配下だけ受ける（Project Lab の project_id があればそちらが優先）。
- Context7（`context7-mcp`、@upstash/context7-mcp 4.4.4）: 設定 `context7` をオンにしたときだけ session に載せる（既定オフ、未設定として促さない）。道具は 2 つで小さいので主にも持たせる。実機で Phaser のライブラリ ID を 5 件引けた。
- レビュー: 子エージェント `reviewer`（edit / write / patch を deny、指示は agents/reviewer.md）。`ast-grep`（@ast-grep/cli 0.51.0、npm 種類、版固定）を導入していれば session の PATH の先頭に足す。実機の試験（関数の引数を増やし、呼び出し側を 1 つ直し忘れた差分）で、取りこぼしを critical として検出し、ファイルは変更しなかった。
- GitHub 連携: session は XDG_CONFIG_HOME を job ごとに向けるので、gh は `~/.config/gh` を見失う（未ログイン扱い、git の https push も gh の credential helper 経由で失敗）。設定 `github_cli` をオンにしたときだけ `GH_CONFIG_DIR` を渡す（既定オフ）。`gh auth`・`gh repo delete/archive`・`gh secret`・`gh variable`・`gh ssh-key`・`gh gpg-key`・`gh api` は常に deny。
- 観察: レビューの実機試験で、Adversarial Review が無効のとき主のエージェントが利用者の `~/.config/opencode` を読んで探し回った（書き込みは無し）。有効にするか、指示から外すかは今後の課題。
- **v2 の agent の `system` は既定の指示を丸ごと置き換える。** 既定には Code Mode（MCP の道具は `execute` の中で JavaScript から `tools.<名前空間>.<道具>()` で呼び、`search()` で探す）の説明が入っている。置き換えた子（ui-verifier / reviewer）は MCP を見つけられず `<function=search>` を本文に書いて止まり、自前で CDP のスクリプトを書いて代用した。`_v2_agents` が子の指示の先頭に `CODE_MODE_PRIMER` を足す（#384）。
- 実機の通し試験（#384 の後）: ローカル LLM が UI 検証の作業で ui-verifier に任せ、Playwright MCP で 320/390/768/1280 × ライト/ダークを撮影（17 枚）、仕込んだ不具合（ダークの文字色、空送信）に加え、数量の検証漏れ 13 例・タップ領域・aria-live 不足を検出。コードは変更しなかった。報告の送信（control_deck.report）は Add-on の bridge が要るため、この試験では未確認（単体テスト済み）。

## 開発用サンドボックス（2026-11-15 決定・実装）

本番の ControlDeck には触れず、箱の中で直し、箱の中のミラー（別ポートの ControlDeck）で確かめ、
GitHub のプレリリースまで出す。正式 Release への格上げと本番への適用（更新機能）は利用者が行う。
更新機能（`maintenance/self_update.py`）は正式 Release しか取り込まないので、プレリリースは本番に入らない。

方式の比較（実機で確認）:

| 方式 | 判断 |
|---|---|
| ホストでミラーだけ別ポート | AI は同じ利用者のシェルを持つので隔離が弱い |
| コンテナのイメージに一式を入れる | 数 GB の取得・作り直しが遅い。ホストの Chrome を使えない |
| **podman の空のルート＋ホストの /usr・/etc・/opt を読み取り専用（採用）** | イメージ不要、起動 0.07 秒、ホストの Chrome・node・python・gh・git・OpenCode がそのまま動く。書けるのは箱のフォルダだけ |
| bubblewrap | Ubuntu の非特権 user namespace の制限で動かない |
| 仮想マシン | GPU が 1 枚で渡せない |

- `./deck.sh sandbox new <名前> [--repo パス] [--ref 版] [--github]`: `git clone --shared` で作業ツリーを作る（オブジェクトは元を読み取り専用で参照、コミットは箱の中だけ、push 先は origin＝GitHub）。`.venv` と `frontend/node_modules` は overlay でつなぐ（箱の中で pip / npm を使ってもホストは変わらない）。
- `shell` / `exec` / `opencode [--run 指示]`（箱の中の OpenCode。設定は本番と同じ生成処理、Add-on の bridge は載せない、UI 検証の写真は箱の中）/ `mirror-start`（`CONTROL_DECK_ISOLATED=1`、ポート 18800〜、使い捨て管理者）/ `mirror-stop` / `status` / `list` / `destroy`。
- 箱から見えないもの: 本番の data（DB・秘密鍵・ログ）、gh の token（`--github` のときだけ読み取り専用で見せる）。箱の中の AI 自身に確かめさせて、見えないことを確認した。
- OpenCode から本番を止める `deck.sh`（引数なし・start・service・stop・restore・update-prepare など）を禁止した。`test`・`e2e`・`ext lint`・`sandbox` は使える。
- 確かめられないもの: systemd のサービス・ファン・GPU ランタイムの起動停止など、ホストそのものが要る機能。
