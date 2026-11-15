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
- **選ぶのは「作業の種類」だけにする。** 利用者が MCP サーバーを個別に組み合わせる画面は作らない。導入・更新・削除は既存の「設定 → オプション機能」に寄せる。
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
| 第 1 段階 C（画面） | 未着手 | — |
| 第 1 段階 D（E2E） | 未着手 | — |
| 第 2 段階 | 未着手 | — |
| 第 3 段階 | 未着手 | — |

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
