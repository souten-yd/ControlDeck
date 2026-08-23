# ControlDeck Add-on Platform v2 / AI Resource Broker — 改訂実装計画（UX主導版）

Status: Revised plan (rev.2) / supersedes the previous implementation instruction
Date: 2026-08-20
Scope: ControlDeck host side only（Media Forge本体の実装は含まない）

**確定済み前提**

```text
配信方式        HTTPS
Embedded View   host-mediated proxy + opaque origin        （§7.2）
LLM supervision managed（yield level 0〜4）                 （§8.7）
モデル配置      ローカル NVMe
LLM 入口        /api/v1/llm/v1 単一入口（生ポート直叩きは非推奨・無保証）
```

Related:

- `ControlDeckMediaForge/docs/controldeck-integration-plan.md`（add-on側 normative）
- `ControlDeckMediaForge/docs/base-plan.md`
- ControlDeck `docs/plugin-sdk.md` / `docs/architecture.md`

---

## 0. この文書の位置づけ

前版（初版実装指示）は **境界設計としてはほぼ正しい**。
本改訂は前版を否定するものではなく、以下2点を補う。

1. 前版に含まれていた**設計上の穴・矛盾の修正**
2. 前版がほぼ扱っていなかった **UI/UX の一次仕様化**

前版の最大の弱点は次の一文に集約される。

> 「Effective Registryから除去する」「policyに応じてhiddenまたはdegraded表示」

これは *実装の記述* であって *体験の定義* ではない。
ローカルAI add-on の体験品質を決めるのは、正常系の描画位置ではなく、

```text
起動していない
モデルが落ちていない
VRAMが足りない
60秒待たされている
別のプロセスがGPUを掴んでいる
```

という**異常系・待機系をどう見せるか**である。ここが未定義のまま実装すると、
「Media が消えたり出たりする」「押しても無反応」「原因不明のまま待つ」UIになる。

本改訂では **UX原則を PR-A より前に固定**し、全PRの受け入れ条件に組み込む。

---

## 1. 評価サマリ

### 1.1 妥当であり維持すべき判断

| 項目 | 評価 |
|---|---|
| Plugin SDK v1 後方互換維持 | 妥当。破壊する理由がない |
| Add-on v2 = 別プロセス / declarative contribution | 妥当。crash伝播とdependency汚染を確実に防ぐ |
| `installed` / `enabled` / `effective` の3層分離 | 妥当。ここを曖昧にした基盤はほぼ必ず腐る |
| Protocol Gateway と Resource Broker の分離 | 妥当かつ重要。LLM GatewayをImage Gatewayへ拡張するのは誤り |
| **`waiting_resource` が runner slot を消費しない** | 本計画で最も価値のある指摘。維持 |
| llama.cpp capacity を adapter化してから broker化 | 妥当。既存 `test_llama_kv_capacity.py` 回帰防止は必須 |
| Media固有名称の core hard-code 禁止 | 妥当 |
| fake add-on による E2E | 妥当。ただしUXシナリオが不足（§9） |

### 1.2 修正が必要な点（重大順）

| # | 問題 | 影響 |
|---|---|---|
| C1 | UXが未仕様。待機/劣化/失敗の見せ方が policy 任せ | 実装者ごとに挙動が割れ、体験が破綻 |
| C2 | Embedded View の origin / mixed content 未決 → **決定済** | https配信のため loopback直参照は表示不能。host-mediated proxy + opaque origin に確定（§7.2） |
| C3 | 「unknown fieldはfail closed」の適用範囲が広すぎる | host側の些細なcosmetic追加が全て破壊的変更になる |
| C4 | health degraded 時に navigation を消す可能性を残している | ナビが点滅する最悪UX |
| C5 | LLM(llama-server)を broker が unload できない → **決定済** | `managed` supervision を採用し yield 0〜4 を有効化（§8.7）。前提は Gateway 単一入口 |
| C6 | Job state を12個に増やす提案がUIへ直結 | ユーザに12状態は無意味。DB互換リスクも高い |
| C7 | 部分capability健康度（video worker欠如等）の host契約が無い | add-on全体unavailable扱いになり過剰縮退 |
| C8 | permission/consent のUIが無い | 何を許可したか不可視。scoped grantの意味が薄れる |
| C9 | plugin / addon の語彙二重化が「検討」で放置 | UI文言に必ず漏れる |
| C10 | effective contributions の push無効化が無い | disable後もリロードするまで残る |
| C11 | 初回セットアップ（未起動/モデル未DL）のUXが無い | 最も遭遇頻度の高い失敗が add-on 任せ |
| C12 | mobile 320px で embedded workspace を成立させる前提が非現実的 | 「対応した」体裁だけ残る |
| C13 | aging/fairness が定性記述のみでテスト不能 | 実装が固定priorityに退化する |
| C14 | PR分割の粒度が不均一（PR-Cが巨大、DB migrationが混在） | レビュー困難・ロールバック不能 |

---

## 2. 批判と修正方針（詳細）

### C1. UXが「描画スロットの列挙」で止まっている

**問題**
前版 §7/§10/§31 は「どこに出すか」しか定義していない。
ローカル生成AIでユーザが実際に見る時間の大半は、

```text
待機中 / モデルロード中 / GPU取得待ち / add-on未起動 / 一部worker欠如
```

であり、これらの仕様が無い。

**修正**
§3 に **UX原則（normative）** を新設し、全contributionが
`available / degraded / unavailable / setup_required` の4状態を必ず持ち、
各状態に **(a) 短い理由文 (b) 最低1つの実行可能アクション** を伴うことを必須とする。

---

### C2. Embedded View の origin 設計 — **決定済み**

**前提（確定）**: ControlDeck は **HTTPS 配信**。

これにより選択肢は1つに絞られる。`http://127.0.0.1:9130` を iframe `src` に
直接指定する構成は **mixed content でブラウザがブロックし、そもそも描画されない**。
manifest の `source` を「iframeが直接読むURL」として扱う設計は成立しない。

**決定: host-mediated proxy + opaque origin**

```text
iframe src  = https://deck.example/addon-frame/{addon_id}/{path}
              ControlDeck が受け、session cookie で閲覧可否を判定
              → Cookie ヘッダを剥奪
              → 短命 add-on token を付与
              → http://127.0.0.1:9130/{path} へ proxy
sandbox     = "allow-scripts allow-forms allow-popups allow-downloads"
              allow-same-origin は付けない → iframe は opaque origin
```

manifest の `source` は **upstream 指定**であり、ブラウザには一切露出しない。
サブドメイン分離（`addon.deck.example`）は origin 分離として綺麗だが
ワイルドカード証明書が必要になるため採用しない。`allow-same-origin` を外せば
iframe 内 JS は opaque origin となり `document.cookie` / `localStorage` /
親frame DOM のいずれにも到達できない。

**実装上の落とし穴（必ず対処）**

> opaque origin でも、iframe から `/addon-frame/*` へ出るリクエストには
> **Cookie が付く**。Cookie の送信可否はリクエストURL基準であり、
> JS の origin 基準ではない。

したがって「sandbox したから Cookie は渡っていない」は誤り。
**proxy 層で明示的に `Cookie` / `Authorization` ヘッダを削除してから
upstream へ渡す**実装が必須。ここを落とすと Media Forge が
ControlDeck の session cookie を平文で受け取る。

必須ルール:

- iframe に `allow-same-origin` を付けない
- proxy は `Cookie` / `Authorization` を **削除**して upstream へ（テストで検証）
- upstream からの `Set-Cookie` も**剥奪**する（add-on が親originにcookieを置けない）
- `Content-Security-Policy: frame-src 'self'`（proxy経由なので self で足りる）
- `postMessage` は送受信双方で `origin` 検証 + セッション毎 nonce
- upstream は loopback / 明示allowlist のみ。IP literal 検証、redirect 追従禁止
- proxy は WebSocket/SSE を透過させる（進捗配信に必要）
- upstream 応答に上限サイズとタイムアウトを設ける

---

### C3. fail closed の適用範囲を分ける

**問題**
「未知field / contribution / capabilityは原則fail closed」は
forward compatibility を殺す。新しいhostが `"icon"` を足しただけで旧hostが manifest を弾く、
または新add-onが旧hostで一切動かない。

**修正**

| 種別 | 挙動 |
|---|---|
| 未知の `api_version` | **reject**（fail closed） |
| 未知の contribution **type** | **reject**（実行面に関わる） |
| 未知の `host_capabilities` 値 | **reject**（権限面に関わる） |
| 未知の URL scheme / host | **reject** |
| 未知の**presentational field**（icon, hint, badge, order等） | **ignore + warning記録**（fail open） |
| 既知typeの未知**サブフィールド** | ignore + warning |

warning は Add-on 詳細画面に「このホストでは未対応の宣言が N 件あります」として可視化する。
黙って無視しない、が黙って壊さない。

---

### C4. enabled な add-on を health で消してはいけない

**問題**
前版 §30 は「policyに応じてhiddenまたはdegraded表示」。
hidden を選ぶと、health flap のたびに sidebar から項目が消える。
これはユーザから見て**アプリが壊れている**のと区別がつかない。

**修正（normative）**

```text
navigation contribution の可視性は enabled/disabled のみで決まる。
health は「見た目の状態」を変えるが「存在」は変えない。
```

- `enabled + healthy` → 通常表示
- `enabled + degraded` → 表示 + 状態チップ（例: 一部機能停止）
- `enabled + unavailable` → 表示 + 状態チップ（停止中）。クリック時は **hostが描画する状態ページ**へ
- `disabled` / `not_installed` → 非表示（ルートは404/unavailable）

「消える」のは**ユーザが自分で disable した時だけ**。これを不変条件とする。

例外: 実行面 contribution（workflow executor / agent tool / context action）は
`unavailable` 時に **discovery から外す**。UI可視性と実行可能性を分離する。

---

### C5. llama-server を broker が制御できない問題

**問題**
前版 §24 は「residency は broker が認識、load/unload は runtime/worker 側」と書くが、
現行の llama-server は **ControlDeck 外の常駐プロセス**で、VRAMを起動時から掴む。
この状態で「大型video modelのため exclusive-required」を要求しても、
broker は待つことしかできず、**永久に granted されない**。

これは設計の穴であり、テストでは fake device を使う限り露見しない。

**修正: Resource Provider に yield 契約を追加**

```python
class ResourceProvider:      # llama.cpp / media worker / 将来runtime
    def snapshot() -> CapacitySnapshot
    def can_yield(request) -> YieldPlan | None
    def request_yield(plan, deadline) -> YieldResult
    def reclaim() -> None
```

yield levelを段階化する:

```text
level 0  none            解放不可（固定予約として扱う）
level 1  drain           新規受付停止 + 実行中完了待ち（VRAMは保持）
level 2  shrink          KV/slot縮小・context縮小
level 3  unload          モデル退避（プロセスは維持）
level 4  stop            プロセス停止（supervised時のみ）
```

**決定: supervision mode = `managed`**（詳細仕様は §8.7）

R9700 32GB において Qwen3系 resident 14〜20GB / video系 20GB超 という実測envelopeでは、
`observed`（読むだけ）のままだと **video 系は原理的に永久に入らない**。
1 GPU で LLM と Media を共存させる以上、supervise は必須要件である。

| mode | 起動/停止の主体 | broker が使える yield level |
|---|---|---|
| `external` | ユーザ | 0（固定予約） |
| `observed` | ユーザ | 0〜2（KV admission のみ） |
| **`managed`（採用）** | ControlDeck | 0〜4 |
| `owned` | ControlDeck（モデル選択まで） | 0〜4（今回は不採用） |

**`managed` の前提条件（これが崩れると危険）**

> LLM トラフィックの単一入口を `/api/v1/llm/v1`（LLM Gateway）に統一すること。

OpenCode / Codex が llama-server の生ポートを直接叩いている状態で unload すると
connection refused が返って壊れる。Gateway 経由なら
「受信 → unload済みを検知 → lease要求 → 必要なら load 完了待ち → 転送」
と**待たせて吸収**でき、クライアントからは初回応答が遅いだけになる。

生ポートは localhost bind のまま「非推奨・無保証」と docs に明記し、
`managed` 有効時は外部から到達させない。

UIとしては「LLMランタイムが 14.2 GB を保持中」＋「LLMを一時退避して続行」を出す。

---

### C6. Job state を12個に増やさない — 内部状態とUI投影を分ける

**問題**
前版 §20 は12状態を提案。DB/API互換性を壊すうえ、ユーザに `postprocessing` と
`validating` と `packaging` を区別させる意味がほぼ無い。

**修正: 2層化**

内部（DB）:
既存 enum は**変更しない**。追加は最小限、かつ新カラムで表現する。

```text
status        既存 enum（queued/running/succeeded/failed/canceled/interrupted）を維持
phase         新規 nullable text（waiting_resource / starting / generating / postprocess / validate / package）
wait_reason   新規 nullable text
```

- `waiting_resource` は **`status=queued` + `phase=waiting_resource`** として表現する
  → 既存API/既存クライアント/既存クエリを壊さない
  → §21 の「runner slotを消費しない」要件と自然に整合（queuedは元々消費しない）
- 旧クライアントは phase を無視すれば従来通り動く

UI投影（ユーザに見せる状態は5つ）:

```text
待機中     queued              （+ 待ち理由）
準備中     running/starting    （モデルロード等）
生成中     running/generating  （+ 進捗）
仕上げ中   running/postprocess|validate|package
完了/失敗/キャンセル
```

「キャンセル中」は遷移表示として別途持つ（C13関連、§3.6）。

---

### C7. 部分capability健康度の host 契約を追加

**問題**
add-on側plan §13は「video worker欠如でもadd-on全体を落とさない」と正しく書いているが、
host側指示には対応する契約が無い。結果 host は binary な health しか持てない。

**修正: contribution 単位の availability を health応答に含める**

```json
{
  "status": "degraded",
  "contract_version": "2.0",
  "contributions": {
    "navigation:media": "available",
    "embedded_view:workspace": "available",
    "workflow_executor:media.generate": "available",
    "workflow_executor:media.video": {
      "state": "unavailable",
      "reason_code": "worker_not_installed",
      "message": "Video worker is not installed",
      "action": { "kind": "open_route", "route": "/media/settings#workers" }
    }
  }
}
```

host は contribution 単位で effective 判定する。
`reason_code` は host が i18n するための enum、`message` は add-on 提供のfallback。

---

### C8. 権限の consent / 可視化 UI を必須にする

**問題**
`host_capabilities` は宣言されるが、ユーザがそれを見る画面が無い。
scoped grant を実装しても、ユーザが「何を許可したか」を確認・取消できなければ意味が薄い。

**修正**

- **Enable時レビュー画面**（host描画）: 要求capabilityを平易な文で列挙
  ```text
  このアドオンは以下を要求しています
  ・許可したファイル/プロジェクトの読み書き（都度確認）
  ・Jobsへのジョブ登録と進捗更新
  ・通知の表示
  ・GPUリソースの確保
  ```
- **Add-on詳細画面**に付与済み権限一覧 + 個別revoke（可能なもの）
- **file grant は都度 host UI で選択**。add-onがパス文字列を要求する経路を作らない
- 直近のbridge呼び出しを「アクティビティ」として N 件表示（audit logのUI露出）

単一ユーザのローカル環境でも、将来の複数add-on共存時に必要になる。後付けは困難。

---

### C9. 語彙を今決める

**決定**

- ユーザ向け語彙は **「拡張機能 / Extensions」に統一**
- v1 plugin も v2 add-on も同じ一覧に並べ、`v1` は「基本」バッジで区別
- CLI は `./deck.sh plugin ...` を**エイリアスとして永続維持**（破壊しない）。
  新形は `./deck.sh ext ...` を追加してもよいが必須ではない
- 内部module名は `plugins/`（v1）/ `addons/`（v2）で分離してよい

UIに `Plugin` と `Add-on` の2語を同時に出さない。

---

### C10. effective contributions の push 無効化

**問題**
`GET /api/v1/addons/effective` を認証後に取得する設計だが、
enable/disable/health変化の伝播手段が無い。前版 §31 の
「即時またはmetadata refresh後」は曖昧。

**修正**

- 既存の event/SSE チャンネルに `addons.effective.changed` を追加（`etag` 同梱）
- frontend は etag 一致なら再取得しない
- 受信時は**ナビ全体を再マウントしない**（フリッカー防止）。差分適用
- SSE不達時のfallback: フォーカス復帰時とルート遷移時に条件付き再検証
- disable直後に該当 embedded view を開いていた場合 → host が状態ページへ置換
  （iframeを白紙のまま残さない）

---

### C11. セットアップ体験を host 側 generic 機能にする

**問題**
ローカル重量add-onで最頻の失敗は「サービス未起動」「モデル未DL」「ROCm不一致」。
これを各add-onが独自に作ると、毎回品質が違う壊れたonboardingが生まれる。

**修正: `setup_checklist` contribution を追加**

add-on が health応答内で返す:

```json
{
  "status": "setup_required",
  "setup": [
    { "id": "service", "label": "サービス起動", "state": "ok" },
    { "id": "gpu", "label": "ROCm ランタイム", "state": "ok", "detail": "gfx1201 / ROCm 7.x" },
    { "id": "model", "label": "画像モデル", "state": "missing",
      "message": "既定モデルが未インストールです",
      "action": { "kind": "open_route", "route": "/media/settings#models" } }
  ]
}
```

host は共通スタイルのチェックリストを描画し、`再確認` ボタンを提供する。
`setup_required` は `unavailable` とは別状態として扱う（ユーザの行動が変わるため）。

---

### C12. mobileは「縮小」ではなく「別モード」

**問題**
320pxのiframeにMedia workspace全体を入れるのは非現実的。
「PC幅と320pxの両方を確認」だけでは、確認したという記録が残るだけ。

**修正: manifest で mobile 戦略を宣言させる**

```json
"embedded_views": [{
  "id": "workspace",
  "route": "/media",
  "source": "https://.../",
  "mobile": "companion"
}]
```

| 値 | 挙動 |
|---|---|
| `embedded` | mobileでもiframe（add-on側が対応済みと申告） |
| `companion` | **hostが描画する簡易画面**（Jobs一覧・通知・直近asset・再実行）＋「デスクトップで開く」 |
| `link_out` | 外部/別タブ（最終手段） |

未宣言時の既定は `companion`。
`companion` 画面は host の generic 機能なので、全add-onが最低限のmobile体験を得る。

---

### C13. スケジューラを「テスト可能な式」にする

**問題**
「priority + aging + fairness + residency affinity」は正しいが、
このままでは実装者が `sorted(key=priority)` に退化させても仕様違反にならない。

**修正: 初期実装の式と不変条件を固定する**

```text
effective_score =
      base_priority
    + aging_gain    * min(queue_age_sec / AGING_PERIOD, AGING_CAP)
    - fairness_cost * running_or_granted_count(owner)
    + residency_bonus if residency_key already resident
```

初期値（設定可能）:

```text
AGING_PERIOD    = 30s
AGING_CAP       = 4          （最大 +4 * aging_gain）
aging_gain      = 5
fairness_cost   = 3
residency_bonus = 2
```

**不変条件（テストで検証する）**

1. `interactive` は同条件下で `background` より先に granted
2. `background` job は `STARVATION_LIMIT`（既定 600s）以内に必ず1回は先頭評価される
3. 単一 owner が全 grant を占有し続けない（fairness_cost により交互化）
4. residency_bonus は不変条件1,2を破れない（bonus上限 < aging最大寄与）
5. スコア同点時は FIFO（決定的）

これで「実装が単純でも仕様準拠」が判定可能になる。

---

### C14. PR分割の再構成

**問題**

- PR-C が「broker新規 + llama adapter移行 + Jobs admission + DB変更」で巨大
- PR-B（UI）は検証対象のadd-onが無い状態で作られる
- DB migration が機能追加PRに混在 → ロールバック困難

**修正: 6 PR + 先行PR-0**

```text
PR-0  contract & harness
      manifest schema (v1/v2 dispatch), contract定数, fake add-on service,
      manifest lint CLI, docs骨子
      → 以降のPRが常に実物で検証できる

PR-A  Add-on Registry / Effective Contributions（backendのみ）
PR-B  UI contribution rendering + 状態表示（embedded view無し）
PR-C  Embedded View host + Host Bridge + theme/origin/CSP
PR-D1 Resource Broker core（fake device / lease / queue / aging）
PR-D2 llama.cpp adapter移行 + Jobs admission（+ 単独DB migration PR）
PR-E  Workflow remote executor + Agent remote tools
```

PR-B を embedded view より先に出すことで、
「ナビ/コマンド/状態表示」というUX基盤を早期に実機確認できる。

DB migration（`phase` / `wait_reason` カラム追加）は **独立PR**として PR-D2 の前に出す。

---

## 3. UX原則（normative / 全PRに適用）

以下は実装判断が割れた時の**優先ルール**である。

### 3.1 消さずに説明する

enabled な add-on の navigation は health で消えない（C4）。
消えるのは disable / uninstall のときだけ。

### 3.2 すべての待機は「理由」と「出口」を持つ

待機表示は最低限これを含む:

```text
[理由]      GPUの空き待ち（LLMランタイムが 14.2 GB を保持中）
[位置]      待ち行列 2 番目
[見込み]    約 40 秒（推定・信頼度 低）
[出口]      キャンセル / 優先度を下げる / LLMを一時停止
```

ETAは**信頼度ラベル必須**。外れる推定を断定形で出さない。
出口が1つも無い待機画面を作ってはならない。

### 3.3 最悪ケースはホストが描画する

以下は add-on ではなく **ControlDeck が描く**:

```text
読み込み中（skeleton）
接続タイムアウト
add-on停止中
セットアップ未完了
権限不足
バージョン非互換
iframeが所定時間内に描画しない
```

add-on の失敗が「真っ白なiframe」として現れることを禁止する。
embedded view には **host側ロードtimeout（既定8秒）** を置き、
超過時は host の状態ページへ置換 + 再試行ボタン。

### 3.4 行き止まりを作らない

すべてのエラー/空状態は、最低1つの次アクションを持つ。

```text
再試行 / 設定を開く / ログを見る / アドオンを無効化 / ドキュメント
```

### 3.5 既定は静か

通知ポリシー:

- toast は **終了イベントのみ**（成功/失敗/キャンセル）
- 進捗は Jobs パネルへ。toastで流さない
- ユーザが該当 workspace を**表示中**のジョブは toast しない（画面内で分かる）
- add-on あたり **6件/分** のレート制限、job_id で dedupe
- 超過分は「他 N 件」に集約

### 3.6 取り消しは即座に見える

- `waiting_resource` でのキャンセル → **同期的に即時**（lease要求を即除去）
- `running` でのキャンセル → `canceling` 遷移表示 + 進捗停止
  add-on が `CANCEL_HARD_TIMEOUT`（既定30秒）内に応答しない場合は
  lease強制解放 + `interrupted` へ。UIは「強制終了しました」と明示

### 3.7 遷移を跳ねさせない

- ナビは差分更新（C10）
- theme切替はイベントで伝播。iframe再読み込みしない
- 初回描画前に token を渡し、**dark modeで白背景が一瞬出る（FOUC）を禁止**
  → bridge handshake 完了までは host が背景色付き skeleton を出す

### 3.8 ユーザ語彙は1つ

「拡張機能」に統一（C9）。内部語彙をUIに漏らさない
（`effective`, `contribution`, `lease` 等はUI文言に出さない）。

---

## 4. PR-0 — Contract & Harness（新設）

### 4.1 成果物

```text
backend/app/addons/contract.py     契約バージョン定数・reason_code enum
backend/app/addons/schema.py       PluginManifestV1 / AddonManifestV2 分離 + dispatch
tools/fake-addon/                  テスト用add-onサービス（FastAPI等、軽量）
./deck.sh ext lint <manifest>      manifest静的検証CLI
docs/design-addon-platform-v2.md   骨子
```

### 4.2 contract versioning

```text
addon.contract.version   = "2.0"
bridge.schema.version    = "1.0"
theme.token.version      = "1.0"
health.schema.version    = "1.0"
```

互換規則: **minor増加は後方互換必須、major増加は host が明示対応するまで incompatible**。
add-on manifest は `"requires": { "addon_contract": ">=2.0 <3.0" }` を宣言できる。

### 4.3 fake add-on が持つもの

```text
navigation / embedded_view / command / quick_action / settings
workflow_executor / agent_tool / context_action
setup_checklist
health 切替エンドポイント（healthy / degraded / unavailable / setup_required を手動切替）
fake GPU job（任意秒数占有、任意VRAM要求、cancel対応）
```

health を手で切り替えられることが重要。UX受け入れ試験の全シナリオがこれに依存する。

### 4.4 Exit

- v1 plugin の既存テストが全て通る
- `ext lint` が正常/異常manifestを判定
- fake add-on が起動し health を返す

---

## 5. PR-A — Registry / Effective Contributions

前版 §2〜§6 を踏襲しつつ以下を修正。

### 5.1 状態モデル

```text
not_installed
installed_disabled
enabling
setup_required        ← 追加（C11）
healthy
degraded
unavailable
incompatible
```

`incompatible` は「contract version不一致」「未知contribution type」「未知capability」。
UIには理由と、可能なら必要バージョンを表示する。

### 5.2 effective 判定

```text
effective(contribution) :=
      installed
  AND enabled
  AND manifest valid
  AND contract compatible
  AND capability granted
  AND per-contribution availability != unavailable   （C7）
```

navigation contribution のみ、可視性判定に health を含めない（C4）。

### 5.3 API

```text
GET  /api/v1/addons
GET  /api/v1/addons/{id}
GET  /api/v1/addons/effective          ETag必須
POST /api/v1/addons/{id}/enable
POST /api/v1/addons/{id}/disable
POST /api/v1/addons/{id}/recheck       health/setup 再確認
GET  /api/v1/addons/{id}/activity      直近bridge呼び出し（C8）
```

`GET /api/v1/meta` は肥大化させない（前版 §8 維持）。
ただし **未認証時にadd-on存在を漏らさない**ことも明記する。

### 5.4 health polling

- 既定 interval 15s、失敗時 exponential backoff（最大120s）
- 連続失敗 N=3 で `unavailable`
- 復帰は1回成功で `healthy`（flap抑制のため degraded→healthy は2回成功）
- ユーザが `recheck` を押した時は即時

### 5.5 Exit

- enable/disable が effective registry に即反映
- disable後、実行系エンドポイントが 404/409 を返す（CSS非表示ではない）
- 既存 workflow の add-on node が `unavailable` として保持され、削除されない

---

## 6. PR-B — UI contribution rendering / 状態表示

### 6.1 host が描画するもの

```text
desktop sidebar
mobile navigation / More
Command Palette
Quick Actions
Settings entries
状態チップ（degraded / unavailable / setup_required）
Add-on詳細画面（権限・アクティビティ・setup checklist・再確認）
状態ページ（unavailable時のfallback描画）
通知アクション
```

### 6.2 navigation 組み立て

```text
core navigation
+ enabled feature navigation
+ effective addon navigation（宣言順 / order hint尊重、ただしcoreより上に来ない）
```

`Media` 文字列を core に置かない（前版 §9 維持）。

### 6.3 label / icon の制約

- label は **manifest文字列をそのまま出さない**。長さ上限（24文字）+ サニタイズ + 省略
- locale別labelを許す: `"label": { "en": "Media", "ja": "メディア" }`、fallbackは `en`
- **icon は host のアイコンセット名のみ**（`"icon": "image"`）。
  任意URL/inline SVGは禁止（インジェクション・視覚的不整合）
- badge は数値または短文のみ、色は host が決める（add-onが赤を自称できない）

### 6.4 状態チップの表現

```text
healthy         チップ無し
degraded        黄 「一部機能が利用できません」
unavailable     灰 「停止中」
setup_required  青 「セットアップが必要」
```

チップクリック → Add-on詳細（理由 + アクション）。

### 6.5 Exit（UX受け入れ）

- enable/disable で **ページリロード無しに** ナビが差分更新される
- health flap でナビ項目が出入りしない
- 全状態チップから1クリックで理由とアクションに到達できる
- 320px幅でラベルが破綻しない

---

## 7. PR-C — Embedded View / Host Bridge

### 7.1 route

```text
/x/{addon_id}/{view_id}
```

（`/addon-view/...` でもよいが、ユーザに見える URL は短くする）

- disable中の直接アクセス → host の unavailable ページ（404相当、silent renderしない）
- **add-on内のルート状態を URL に反映する**（下記 route sync）。
  これが無いと戻る/進む/リロード/共有が全部壊れる

### 7.2 origin / isolation（確定仕様 — HTTPS前提）

ControlDeck は HTTPS 配信で確定。したがって **loopback を iframe src に
直接指定する構成は採らない**（mixed content でブロックされる）。

#### 7.2.1 経路

```text
browser
  └─ iframe src = https://deck.example/addon-frame/{addon_id}/{path}
        │
        ├─ [1] ControlDeck session cookie で閲覧可否を判定
        ├─ [2] add-on が effective か / capability があるかを判定
        ├─ [3] Cookie / Authorization ヘッダを削除          ★必須
        ├─ [4] 短命 add-on token を付与
        └─ [5] http://127.0.0.1:9130/{path} へ proxy
                 └─ 応答の Set-Cookie を削除                ★必須
```

manifest の `runtime.base_url`（旧 `source`）は **upstream 指定**であり、
ブラウザには露出しない。

#### 7.2.2 sandbox / CSP

```text
iframe sandbox = "allow-scripts allow-forms allow-popups allow-downloads"
                 allow-same-origin は付けない（opaque origin）
CSP            = frame-src 'self'
                 （proxy経由のため self で足りる。外部originを列挙しない）
```

#### 7.2.3 Cookie に関する誤解の明示

> **opaque origin であっても、iframe から `/addon-frame/*` へ出る
> リクエストには Cookie が自動付与される。**
> Cookie 送信可否はリクエストURL基準であり、JS の origin 基準ではない。

「sandbox したから安全」は成立しない。
**proxy 層でのヘッダ削除が唯一の防御線**であり、テストで直接検証する（§11.3）。

#### 7.2.4 その他

```text
credential   短命 add-on token（audience=addon_id, TTL 10分, silent refresh）
upstream     loopback / 明示allowlist、IP literal検証、redirect追従禁止
WebSocket    /addon-frame/*/ws を透過（進捗配信に必要）
SSE          透過。バッファリングしない
size/timeout upstream 応答に上限（既定 32MB / 60s、ストリームは除外）
Set-Cookie   upstream 応答から削除
disable時    proxy が 409 を返し、host が状態ページへ置換
```

### 7.3 Host Bridge（`postMessage` + MessageChannel）

初期API（前版 §11 を維持しつつ追加）:

```text
host.handshake                  version交換・token受領（最初に必須）
host.context.get
host.theme.get
host.route.open                 ControlDeck内ルート遷移
host.route.sync                 add-on内state → host URL反映   ← 追加
host.title.set                  ヘッダタイトル/breadcrumb       ← 追加
host.file.pick / host.file.export
host.project.pick
host.job.open / host.job.subscribe                              ← 追加
host.notification.show
host.permission.has
host.busy.set                   未保存変更あり（離脱確認）      ← 追加
```

規約:

- すべて schema検証 + capability検査 + addon_id束縛 + audit記録
- レスポンスサイズ上限・タイムアウト・呼び出しレート制限
- 未許可メソッドは **明示的エラーコード**を返す（無応答にしない）
- host→add-on イベント: `theme.changed` / `locale.changed` / `visibility.changed` /
  `safe_area.changed` / `disable.pending`

`disable.pending` は重要: disable前に add-on に猶予（既定2秒）を与え、
保存/中断処理をさせてから contribution を撤去する。

### 7.4 theme token contract（v1.0）

```text
color_scheme        light | dark
accent              #RRGGBB
bg / surface / text / border / muted
radius_sm / radius_md
spacing_unit
density             comfortable | compact
locale
safe_area           top/right/bottom/left (px)
motion_reduced      bool
token_version       "1.0"
```

- ControlDeck の private React component は公開しない（前版 §11.1 維持）
- `motion_reduced` を追加（アクセシビリティ）
- handshake 完了前は host が背景色付き skeleton を描画（§3.7）

### 7.5 キーボード / フォーカス

- Command Palette のショートカットは **host が capture**（iframeに食わせない）
- `Esc` はまず add-on、未処理なら host
- iframe がフォーカストラップにならないこと（Tab で host へ抜けられる）

### 7.6 Exit（UX受け入れ）

- 戻る/進む/リロード/URL共有が add-on 画面で成立する
- theme切替が iframe再読込無しに反映される
- 8秒以内に描画しない場合 host のタイムアウト画面へ
- disable時、開いている view が状態ページへ置換される
- mobile: `companion` 宣言時に host簡易画面が出る（C12）

---

## 8. PR-D1/D2 — AI Resource Broker + Jobs

### 8.1 module

```text
backend/app/resources/
    broker.py       admission / grant / wait
    schema.py
    leases.py       acquire / renew / release / cancel / TTL
    scheduler.py    §C13 のスコア式
    devices.py      device collection（1GPUでも配列）
    providers.py    ResourceProvider 抽象（§C5 yield契約）
    probes.py       provider固有 admission probe
    telemetry.py
    router.py
```

### 8.2 Resource Request

前版 §16 を踏襲。追加フィールド:

```json
{
  "owner": "addon:media-forge",
  "job_id": "abc123",
  "device": "auto",
  "vram": {
    "resident_bytes": 10000000000,
    "execution_peak_bytes": 15000000000,
    "cold_load_peak_bytes": 18000000000,
    "headroom_bytes": 1500000000,
    "confidence": "low"
  },
  "compute_mode": "exclusive-preferred",
  "priority": 20,
  "class": "interactive",
  "residency_key": "runtime:model-hash",
  "max_wait_sec": 300,
  "on_insufficient": "queue"
}
```

- `confidence` を必須化（`measured` / `estimated` / `low`）
- `max_wait_sec` 超過で自動失敗（無限待ち禁止）
- `on_insufficient: "queue" | "fail_fast"`
  → **物理的に入り得ない要求**（固定予約を差し引いても不足）は
  `queue` 指定でも即 `insufficient_capacity` を返す（§C5）

### 8.3 wait reason（UX直結・enum固定）

```text
device_busy_exclusive
insufficient_vram
held_by_other_owner
queue_position
model_loading
provider_draining
dependency_pending
insufficient_capacity      （待っても入らない）
```

各 reason は host 側に i18n 文言と推奨アクションを持つ。
broker は enum + 構造化データを返し、**文言を持たない**。

```json
{
  "state": "waiting",
  "reason": "insufficient_vram",
  "queue_position": 2,
  "blocking": [{ "owner": "llm:llama-server", "bytes": 14200000000, "yieldable": false }],
  "eta_sec": 40,
  "eta_confidence": "low",
  "actions": ["cancel", "lower_priority"]
}
```

### 8.4 llama.cpp adapter（回帰厳守）

- 既存 `endpoint_capacity()` / `await_capacity()` / `/slots` / KV used-free / busy slots を**削除しない**
- まず `LlamaCapacityProvider` として adapter 化。挙動は完全維持
- `backend/tests/test_llama_kv_capacity.py` を無改変で通す
- `/api/v1/llm/v1` の OpenAI互換挙動は不変（OpenCode設定を変えさせない）

### 8.5 Jobs 統合

- DB: `phase` / `wait_reason` カラム追加のみ（**独立migration PR**）
- `waiting_resource` は `status=queued, phase=waiting_resource`（C6）
- 順序: `queued → resource request → (GRANTED) → runner slot → running`
- **`waiting_resource` は runner slot を消費しない**（前版 §21 の中核要件、維持）
- 進捗イベント: **最大 2 Hz、単調増加、phase必須**。host側で coalesce
- 進捗のDB書き込みは 5 秒間隔 or phase変化時のみ（イベントは即時、永続化は間引き）

### 8.6 Exit

- fake GPU job A/B で exclusive 検証、B は runner slot を占有しない
- LLM 併用時に KV admission が従来通り動作
- 待機中キャンセルが即時
- §C13 の不変条件1〜5がテストで検証される
- 複数device（fake 2枚）で lease が device別に管理される
- `managed` 有効時、LLM 退避 → Media job 実行 → LLM 復帰が成立する
- thrash ガード（§8.7.3）により短時間 job で退避が発生しない

---

### 8.7 LLM supervision（`managed`）— 確定仕様

**前提（確定）**

```text
supervision      managed
モデル配置        ローカル NVMe（NAS 配置は非対応）
LLM 入口          /api/v1/llm/v1 のみ（生ポート直叩きは非推奨・無保証）
GPU              単一 RDNA4 32GB を想定。ただし内部は device collection
```

#### 8.7.1 設定

```yaml
llm:
  supervision: managed
  gateway_only: true          # 生ポートを外部bindしない
  warm_idle_sec: 600          # 無操作でもこの間は載せたまま
  min_uptime_sec: 120         # 起動直後の即退避を禁止（振動防止）
  drain_timeout_sec: 120      # 実行中completionの完了待ち上限
  cold_load_cost_sec: measured  # 起動時に実測。推定値の使用を禁止
  yield_max_level: 4
```

#### 8.7.2 yield 経路

```text
level 1  drain    新規受付停止 + 実行中completion完了待ち（drain_timeout_sec）
level 2  shrink   KV/slot/context 縮小（可能な場合のみ）
level 3  unload   モデル退避、プロセスは維持（再loadが速いため主経路）
level 4  stop     プロセス停止（level 3 が実装上不可能な場合のfallback）
```

llama-server にモデルの動的 unload 手段が無い場合、level 3 は実質 level 4 に
縮退する。**PR-D1 の段階で実機確認し、結果を `cold_load_cost_sec` に反映する**。
level 3 が使えないなら復帰コストはプロセス起動時間になり、§8.7.3 の閾値が変わる。

復帰は **遅延復帰**とする。Media job 終了直後に即 load せず、
次の LLM リクエストが来た時点、または `warm_idle_sec` 内に別の
Media job が控えていないことを確認してから load する。
連続する Media job の合間に毎回 LLM を載せ直さない。

#### 8.7.3 退避判断（thrash ガード）— 最重要

```text
LLM を退避してよいのは

    media_job.estimated_runtime_sec  >  cold_load_cost_sec * THRASH_FACTOR

を満たすときのみ。THRASH_FACTOR 既定 = 2.0
```

10 秒の画像生成のために 25 秒かけて LLM を降ろして載せ直すのは純損。
これを満たさない job は「LLM 常駐のまま入る範囲」でのみ admission し、
入らなければ待機させる（`held_by_other_owner` + `yieldable: true, deferred`）。

追加ガード:

- `min_uptime_sec` 未満の LLM は退避対象にしない
- 直近 `THRASH_WINDOW`（既定 300s）に 2 回退避していたら 3 回目を抑止し、
  代わりに待機させる。抑止は telemetry に記録し UI から確認できる
- 退避中に新規 LLM リクエストが来た場合、Gateway は待たせる。
  ただし `drain` 段階なら中止して復帰してよい（unload 前ならロールバック可能）

#### 8.7.4 cold_load_cost の実測

推定値を使わない。以下を起動時および load のたびに記録する。

```text
process_start_sec        プロセス起動〜listen
model_load_sec           listen〜最初のtoken生成可能
first_token_latency_sec
measured_at
sample_count / p50 / p90
```

broker は **p90** を `cold_load_cost_sec` として使う（楽観見積りで
thrash を起こすより、保守的に退避を控える方が安全）。

NVMe 配置が前提。モデルディレクトリが NVMe 上にない場合は
起動時に警告し、`managed` を自動的に `observed` へ降格する。
（NAS 上 20GB のモデルは 1GbE で 200 秒超となり、退避判断が成立しない）

#### 8.7.5 Gateway 側の吸収

```text
LLM リクエスト受信
  ├─ LLM resident        → 従来通り（KV admission そのまま）
  ├─ LLM draining        → drain中止・復帰して転送
  ├─ LLM unloaded        → lease要求 → load → 転送
  │                         この間クライアントには 200 を返さず待たせる
  └─ load が LOAD_TIMEOUT（既定180s）超 → 503 + Retry-After
```

- OpenAI 互換の応答形式は不変。**OpenCode の設定変更を要求しない**
- streaming リクエストは load 完了後に stream 開始
- load 待ち中のクライアント切断は lease をキャンセルする

#### 8.7.6 UI

待機画面（§3.2 準拠）:

```text
[理由]    GPUの空き待ち
[詳細]    LLMランタイムが 14.2 GB を保持中
[見込み]  約 55 秒（LLM退避 12s + モデルロード 43s・信頼度 中）
[出口]    キャンセル / 優先度を下げる / 今すぐLLMを退避して実行
```

- 「今すぐ退避」は thrash ガードを**手動で上書き**する明示操作
- 退避中は Jobs パネルに `LLMを退避しています` を表示（無言で止めない）
- LLM 復帰中に OpenCode が待たされている場合も同様に可視化する

#### 8.7.7 段階導入

```text
1. Gateway 単一入口の徹底（生ポート依存の洗い出しと排除）   ← PR-D1 前
2. observed のまま cold_load_cost を実測・記録              ← PR-D1
3. managed を opt-in フラグで追加（既定は observed）        ← PR-D2
4. thrash が起きないことを実測確認してから managed を既定化  ← PR-D2 後
```

いきなり既定化しない。手順 2 の実測値が無い状態で手順 3 を有効にすると
§8.7.3 の閾値が機能せず、載せ替え地獄になる。

---

## 9. PR-E — Workflow / Agent contributions

前版 §27/§28 を踏襲。追加:

- executor/tool の discovery は `enabled AND contribution available`（C7の粒度で判定）
- 保存済み workflow の node は削除せず `unavailable` 表示 + 「このノードは拡張機能 X が必要です」
- dry-run 契約: host が schema検証だけ行い add-on を呼ばないモードを持つ
- agent tool 呼び出し時、**project全体権限を渡さない**（前版 §28.1 維持）。
  scoped project grant / scoped input / scoped output を要求単位で発行
- tool呼び出しは Jobs に紐づけ、agent には job_id + asset_id を返す
  （ログからファイル名をscrapeさせない）

### 9.1 実装結果（2026-08-21）

- `GET /api/v1/addons/execution-contributions`が利用者permissionとeffective availabilityでWorkflow／Agent／Contextを発見し、Workflow schema取得失敗をcontribution単位で隔離する。
- remote Workflowは`addon.workflow:{addon_id}:{contribution_id}`としてhost node catalog／Editorへ動的登録される。schema取得・input/output検証・request/response上限・timeout・redirect拒否はhostが一元管理する。dry-runはschema cacheだけを使いupstreamへ送信しない。disable後も保存済みnodeとedgeは残り、unavailable表示とpublish blockerになる。
- Agent toolはWorkflow execution ownerの現在RBACで再評価し、callごとにowner付きdurable Jobを作る。結果は`job_id`とopaque `job-result:` asset IDで参照し、timeout／cancelをJobへ伝播する。raw host path引数は境界で拒否する。
- Context ActionはFiles／Project Labのhost UIからだけ開始し、hostが対象を検証してfile pathを`grant:`へ変換する。upstreamへ渡すのはcontext type、opaque resource ID、要求単位のscoped token、明示inputだけである。
- 実fake Add-on processと実Chromiumによりremote Workflow、Agent Job、Context grant、320px／1280px host UIを通し、終了後に一時registry／Workflowを清掃した。

### 9.2 実行主体と委譲権限（2026-08-21 追補）

service tokenの`sub`は、`job:{id}`、`workflow:{execution_id}`、`context:{user_id}`のような
呼出元相関IDであり、常に利用者IDとは限らない。Runtime APIが`sub`を利用者権限としても解釈すると、
Workflow／Context ActionはHost Job作成とfile grant利用を正しく認可できない。このため、Hostだけが
発行できる署名済みclaimを次のように分離する。

| claim | 意味 | Runtimeでの扱い |
|---|---|---|
| `sub` | 監査・取消・Job attach用の呼出元相関ID | `job:`は対象Jobへ厳密に束縛。それ以外を文字列規則で権限化しない |
| `actor_user_id` | 呼出しを開始したControlDeck利用者 | Workflow／ContextのHost Job ownerとgrant ownerの照合に使う |
| `grant_ids` | この呼出しへ委譲した要求単位のRuntime grant集合 | claim無しは既存token互換、空集合はgrant利用不可、非空集合は完全一致だけ許可 |

- Workflowは`WorkflowExecution -> Workflow.created_by`からactorを解決する。解決できなければAdd-onを
  呼ばずfail closedとする。
- Context ActionでHost UIがraw fileを受け取った場合、Hostは実在する短命Runtime read grantを作り、
  Add-onへpathではなくその`grant:` IDだけを渡す。asset／project contextはfile grantを暗黙付与しない。
- Agent toolは従来どおり`job:{id}`を第一のscopeとし、actor claimはowner情報を明示する補助である。
- browser proxyの数値`sub`は後方互換のため維持し、同じ利用者をactor claimにも署名する。
- Add-onはclaimを自己申告できず、HostのRuntime APIはaddon ID、現在のcapability、actor、grant allowlist、
  grant自体のowner／TTL／kindを毎requestで再検証する。

検討したが採用しなかった案:

1. `workflow:*`／`context:*` subjectをそのままRuntimeで許可する案は、利用者ownerを証明できず権限昇格になる。
2. 利用者IDを`sub`文字列へ追加する案は、相関と権限を再び結合し、将来の呼出元追加ごとに危険なparserを増やす。
3. Add-onへpathやHost cookieを渡す案は、opaque grant境界とAdd-on分離を壊す。
4. 空のgrant allowlistを既存互換の無制限とみなす案は、asset-only contextから既知のgrant IDを再利用できるため、
   claim無しと空集合を区別する。
5. payload上の`grant_id`へAdd-onが検証不能な別種HMAC tokenを置く案は、Runtime grantとして利用できない。
   payloadとtoken claimの双方へ同じ実在`grant:` IDを渡し、Runtime側で完全一致を検証する。

### 9.3 Context ActionのHost画面遷移（2026-08-21 追補）

Context Actionは任意のJSON objectを結果として返せる既存互換を維持する。結果にHost操作を要求する
場合だけ、次の明示形を使う。

```json
{ "action": "open_route", "route": "/x/example-addon/workspace/edit?asset=opaque" }
```

Hostは`action=open_route`を認識した場合、scheme／authority／fragment／制御文字を拒否し、routeの
pathが呼出し元Add-on自身の`/x/{addon_id}`またはその子孫であることをbackendで検証してから遷移する。
外部URL、Host設定、他Add-onのrouteへは遷移できない。未知actionは成功として握り潰さず
`invalid_context_response`で失敗する。`action`を含まない既存の結果objectは変更せず呼出し元へ返す。

検討したが採用しなかった案:

1. 成功toastだけを表示する案は、編集画面を開くというContext Actionを完了できず、利用者に次の操作を
   探させる。
2. Add-onが返したrouteをfrontendで無条件に`navigate`する案は、Host設定／他Add-onへの誘導を許す。
3. Media固有の`open_workspace` actionを追加する案は、Host coreへ用途固有語彙を持ち込む。

### 9.4 OpenCodeへのAgent Tool投影（2026-08-21 追補）

OpenCode featureが有効な場合、Hostが現在の利用者に見えるAdd-on `agent_tools`をローカルstdio MCPへ
投影する。OpenCodeのユーザー設定やグローバル設定は変更せず、既存のjob／TUI専用0600
`OPENCODE_CONFIG`へだけ`controldeck_addons` serverを追加する。bridgeは標準ライブラリだけで動く薄い
転送processであり、Add-on registry、DB、利用者session cookieを直接読まない。

- HostはOpenCode実行ごとに、利用者IDと相関subjectを署名した`kind=agent-mcp` tokenを発行する。有効期限は
  最大8時間で、通常のAdd-on service tokenの10分既定は変更しない。
- bridgeは固定loopback endpointへbearer tokenを送り、Hostはrequestごとに署名、期限、active user、
  `workflows.run`、現在のeffective contribution、schema availabilityを再評価する。Add-on disableや権限変更は
  生存中bridgeにも次の`tools/list`／`tools/call`から反映される。
- tool IDが有効なAdd-on間で一意ならmanifestの公開IDを保つ。衝突時だけ`{addon_id}.{tool_id}`へ
  namespaceする。呼出しは既存のowner付きHost Job経路を使い、結果は`job_id`、opaque `asset_id`、
  bounded outputとして返す。
- tokenを含むruntime configは0600かつjob別で、ログ、audit、MCP errorへ秘密値を出さない。TUIが8時間を
  超える場合は再起動して新しいtokenを得る。OpenCode featureが無効ならendpoint自体を登録しない。
- MCP client timeoutは、Host Agent Jobの120秒上限とstdio bridgeの130秒HTTP上限を先に切らない135秒とする。
  capability照会だけでなく、Broker待機やlocal generationを含む長時間toolを同じ汎用経路で完了させる。

検討したが採用しなかった案:

1. Media Forge専用plugin／tool設定をHostへ追加する案は、汎用Add-on Platformの境界を壊す。
2. ユーザーのOpenCode configを永続変更する案は、disable／uninstall後もstale toolを残し、外部導入を汚す。
3. MCP childがHost DB／Python内部moduleを直接読む案は、process分離と現在RBACの一元評価を壊す。
4. browser cookieをbridgeへ渡す案はCSRF／session authorityを長時間processへ拡散する。
5. 起動時のtool snapshotを固定する案は、disableや権限剥奪後も呼出せる時間差を作る。

### 9.5 OpenCodeのcurrent-project output grant（2026-08-23 追補）

OpenCodeからAdd-onが生成した成果物をコードプロジェクトへ配置する場合も、Add-onへproject pathや
project全体権限を渡さない。Hostが生成するjob／TUI専用MCP tokenへ、実行開始時に解決済みの
Project Lab project IDだけを署名し、次の汎用Host toolを同じstdio MCPへ追加する。

- `control_deck.project_output_grant`は、tokenにcurrent projectがあり、利用者が`project_lab.view`と
  `files.edit`を持ち、対象Add-onが有効なagent toolと`projects.pick`／`files.export` grantを持つ場合だけ
  discoveryされる。
- 入力は対象`addon_id`とproject相対の既存directoryだけである。HostはProject Labのproject解決、
  realpath containment、directory種別を再検証し、project root、絶対path、traversal、symlink脱出を拒否する。
- 応答は既存Add-on Runtimeの短命`grant:` metadataだけで、Host pathを含まない。Add-on固有の配置規則、
  model、route、provider判断はHostへ追加しない。
- Add-on agent toolを呼ぶservice tokenの`grant_ids`は、schema検証済みinput内に明示されたopaque grantだけを
  最大8件まで収集する。grantが無い呼出しは空allowlistとなり、同じ利用者の既知grantを横取りできない。
- project外で起動したOpenCodeにはproject claimを付けず、このHost toolを公開しない。project移動後も
  tokenを使って別projectへ切り替えられず、新しい実行でtokenを再発行する。

検討したが採用しなかった案:

1. Add-onへcurrent projectのraw pathを渡す案は、Host境界のopaque grant契約を壊す。
2. project root全体を常時grantする案は、単一出力配置に不要な書込み範囲を与える。
3. Media Forge専用Host toolを追加する案は、Add-on Platformを用途固有実装へ結合する。
4. Add-on tool tokenへownerの全grantを暗黙委譲する案は、別要求で得たgrantの再利用を許す。

---

## 10. セキュリティ要件（前版 §29 を拡張）

```text
schema validation（fail closedの範囲は §C3 に従う）
addon ID binding
capability check
RBAC + (plugin_id, permission_id) 形式の権限保存（uninstall後に stale化させない）
audit logging（UI露出込み）
CSRF
loopback / allowlist URL validation + IP literal検証 + redirect追従禁止
symlink containment + realpath検証
scoped file grants（host UI経由のみ）
lease TTL + crash後の自動回収
bounded response size / timeout / cancel
iframe sandbox（allow-same-origin禁止 / opaque origin）
CSP frame-src 'self'（proxy経由のため外部origin列挙不要）
postMessage origin検証 + nonce
proxy: Cookie / Authorization を upstream へ渡す前に削除     ★唯一の防御線
proxy: upstream の Set-Cookie を削除
add-on token: audience束縛 / 短TTL / 自動更新 / disable時即revoke
LLM 生ポートを外部bindしない（managed 時）
LLM 退避/復帰操作を audit log に記録する
```

service token の自動更新は、通常 token を長寿命化しない。`resources.acquire`
を許可された Add-on が、同じ subject / actor / grant allowlist に束縛された
active Host Job と current resource lease を持つ間だけ、lease ID を proof として
新しい10分 tokenへrotationできる。別Add-on、別subject、終了job、終了lease、
disable pending／disabled状態ではfail closedとする。旧tokenは元の短い期限までだけ
有効であり、更新response、token本文、grant IDはaudit/logへ保存しない。

検討して却下した案:

- service token 全体を8時間へ延長する案は、通常のiframe／単発executionまで漏洩時の
  影響時間を広げるため採らない。
- active jobだけで更新を許す案は、GPUを使わない通常jobまで不要にcredential寿命を
  延ばせるため採らない。Host brokerがownerとjobを再検証するcurrent leaseも要求する。
- expiry後の猶予更新は、盗難tokenの復活を許すため採らない。更新は現tokenが有効な間に行う。

---

## 11. テスト要件（前版 §32 を拡張）

### 11.1 契約 / registry

```text
v1 backward compatibility（既存テスト全通過）
v2 manifest success
unknown api_version rejection
unknown contribution type rejection
unknown capability rejection
unknown presentational field → ignore + warning   ← 追加（C3）
unsafe URL rejection / IP literal / redirect
enable/disable → effective registry
health degraded / unavailable / setup_required    ← 追加
per-contribution availability                     ← 追加（C7）
permission filtering
disabled contribution が実行不能（404/409）
```

### 11.2 UI / UX（新設・最重要）

```text
health flap 中に navigation が出入りしない          （C4）
enable/disable が reload無しに差分反映される        （C10）
theme切替が iframe再読込無しに伝播する
handshake前に白背景FOUCが出ない
embedded view タイムアウト → host画面へ置換
disable中の /x/{id}/{view} → unavailable（silent renderしない）
戻る/進む/リロード/URL共有（route sync）
Command Palette ショートカットが iframe に奪われない
Tabフォーカスが iframe から抜けられる
mobile companion fallback が描画される              （C12）
320px でラベル/チップが破綻しない
全エラー状態が最低1アクションを持つ（dead-end監査）
通知レート制限 / dedupe / 表示中jobは非toast
```

### 11.3 Bridge / addon-frame proxy

```text
schema validation
未許可メソッド → 明示エラー（無応答でない）
origin不一致 postMessage 拒否
nonce不一致拒否
disable.pending 猶予後に撤去

--- proxy（§7.2 の防御線を直接検証） ---
Cookie ヘッダが upstream に到達しない        ★最重要
Authorization ヘッダが upstream に到達しない
upstream の Set-Cookie がブラウザに到達しない
未認証セッションで /addon-frame/* → 401
disabled add-on で /addon-frame/* → 409
allowlist外 upstream の manifest → 登録拒否
upstream の 302 を追従しない
WebSocket / SSE が透過する
応答サイズ上限 / timeout
```

### 11.4 Broker / Jobs

```text
exclusive lease
shared-safe concurrency
lease release / TTL expiry / crash回収
cancel while waiting（即時）
cancel while running（canceling → hard timeout → interrupted）
priority
aging 不変条件 1〜5                                  （C13）
multiple devices（fake 2枚）
yield: level 0 provider に対し insufficient_capacity 即返し  （C5）
waiting_resource が runner slot を消費しない
進捗イベント 2Hz 上限 / DB書き込み間引き

--- managed LLM supervision（§8.7） ---
drain → unload → media job grant → LLM復帰 の一連が成立する
drain 中の新規LLMリクエストで drain がロールバックされる
drain_timeout 超過時に強制 unload され lease が grant される
min_uptime_sec 未満の LLM は退避対象にならない
thrash ガード: estimated_runtime < cold_load_cost*2 の job で退避しない
thrash ガード: THRASH_WINDOW 内 3 回目の退避が抑止される
cold_load_cost が p90 で算出され推定値にfallbackしない
モデルディレクトリが NVMe 外 → managed が observed へ降格し警告
遅延復帰: 連続 media job の合間に LLM が載せ直されない
Gateway: unloaded 時にリクエストが待たされ、load後に正常応答
Gateway: LOAD_TIMEOUT 超過で 503 + Retry-After
Gateway: load待ち中のクライアント切断で lease がキャンセルされる
手動「今すぐ退避」が thrash ガードを上書きする
```

### 11.5 LLM 回帰（絶対条件）

```text
test_llama_kv_capacity.py（無改変）
LLM gateway tests
OpenCode gateway tests
KV near-full waits / slot busy waits / capacity unavailable handling
```

---

## 12. 禁止事項（前版 §33 を拡張）

```text
Media Forge名称の core routing への hard-code
/media の core 直接登録
FLUX / Qwen / Wan / Blender 依存追加
PyTorch を ControlDeck 本体 dependency 化
ComfyUI 依存
plugin Python import / plugin React module import
CSSのみでの disable
空きVRAMだけでの安全判定
Jobs queue と GPU queue の概念統合

--- 以下 追加 ---
iframe への allow-same-origin 付与
add-on への Cookie 転送
health変化による navigation の出入り                （C4）
出口の無い待機画面 / 行き止まりエラー
真っ白なiframeを放置すること
add-on manifest文字列の無検証描画（label/icon/badge）
任意URL/inline SVG の icon 許可
進捗のtoast通知
ユーザ向けUIに plugin と add-on を併記すること
Job status enum の破壊的変更
無限待ち（max_wait_sec 無し）の lease 要求
broker が PyTorch / diffusers に依存すること

--- HTTPS / managed 決定に伴う追加 ---
loopback URL を iframe src に直接指定すること（mixed contentで死ぬ）
proxy でのヘッダ削除を省略し sandbox のみに依存すること
upstream の Set-Cookie をそのまま通すこと
cold_load_cost に推定値/カタログ値を使うこと（実測 p90 のみ）
thrash ガード無しでの LLM 退避
LLM 生ポートへの直接依存を前提とした設計
モデルを NAS 上に置いたまま managed を有効化すること
Media job 終了ごとに LLM を即時載せ直すこと（遅延復帰を守る）
LLM 退避/復帰を無言で行うこと（Jobs パネルに必ず出す）
```

---

## 13. 受け入れシナリオ（前版 §36 を拡張）

fake add-on を用いて E2E 確認する。

### Scenario A — install / disabled
ControlDeck上に一切表示されない。`/x/fake/workspace` は unavailable。

### Scenario B — enable / healthy
manifest通りの UI/Tool/Workflow が出る。ナビは reload無しに現れる。

### Scenario C — scoped bridge
project context / file pick / notification / job open が動作。
Cookie が add-on 側に到達していないことを検証。

### Scenario D — GPU 競合
fake GPU job 2件（exclusive）。
A=running / B=queued+waiting_resource。B は runner slot を占有しない。
B の待機画面に理由・待ち位置・キャンセルが表示される。
A終了 → lease release → B開始。

### Scenario E — LLM 併用（KV admission 回帰）
LLM Gateway 稼働中に KV admission が従来通り動作。
`test_llama_kv_capacity.py` 相当の挙動が実機で再現する。

### Scenario F — disable
navigation / command / workflow executor / agent tool が全消滅。
開いていた embedded view が状態ページへ置換。
ControlDeck 本体と LLM Gateway は継続。既存データは保持。

### Scenario G — degraded（新規）
fake add-on の video executor だけ unavailable にする。
→ navigation は残る。workflow catalog から video のみ消える。
→ 状態チップ「一部機能が利用できません」。詳細に理由とアクション。

### Scenario H — setup_required（新規）
fake add-on が model missing を返す。
→ host描画のチェックリストと「再確認」ボタン。
→ 解消後、再確認1回で healthy へ遷移。

### Scenario I — health flap（新規）
health を 10 秒間隔で healthy/unavailable に振動させる。
→ navigation 項目が**一度も消えない**。チップのみ変化。

### Scenario J — mobile（新規）
320px で `mobile: companion` の add-on を開く。
→ host簡易画面（Jobs/通知/再実行/デスクトップで開く）が出る。

### Scenario K — 古いbundle（新規）
frontend再ビルド前の古いbundleで effective contributions を取得。
→ 未知contribution typeを無視して描画が壊れない。

### Scenario L — LLM 退避と復帰（新規 / managed）
LLM resident（14GB）状態で 20GB 要求の fake video job を投入。

```text
job → waiting_resource
    「LLMランタイムが 14.2 GB を保持中」＋見込み＋出口を表示
broker → LLM drain（新規受付停止・実行中completion完了待ち）
       → unload
       → lease grant → job running
job 終了
    → 遅延復帰。次のLLMリクエストまで載せ直さない
OpenCode からリクエスト
    → Gateway が待たせ、load完了後に正常応答（設定変更不要）
```

### Scenario M — thrash ガード（新規 / managed）
`cold_load_cost = 40s` の状態で、推定 15 秒の fake image job を投入。

→ **LLM を退避しない**。LLM常駐のまま入るかを判定し、
入らなければ待機（`held_by_other_owner`, `deferred`）。
「今すぐ退避」を手動で押した場合のみ退避する。

続けて 300 秒以内に 3 回目の退避条件を発生させる。
→ 3 回目は抑止され、待機に切り替わる。抑止が telemetry に記録される。

### Scenario N — proxy 境界（新規）
`/addon-frame/fake/` へのリクエストを upstream 側で全ヘッダ記録する。

→ `Cookie` / `Authorization` が **1件も届いていない**こと。
→ upstream が `Set-Cookie` を返してもブラウザに到達しないこと。
→ disable 後は 409 を返し、開いている iframe が状態ページへ置換されること。

---

## 14. Documentation

```text
docs/design-addon-platform-v2.md      本計画の反映
docs/design-ai-resource-broker.md     yield契約・スコア式・wait reason enum
docs/addon-ux-guidelines.md           新規：状態表現・文言・アイコン・通知規約
docs/plugin-sdk.md                    v1/v2差分・移行方針
docs/architecture.md
docs/implementation-status.md
```

`docs/addon-ux-guidelines.md` を新設する。
UX原則（§3）が docs に無いと、次の実装者が必ず policy 任せへ戻る。

---

## 15. 完了条件（改訂）

前版の完了条件:

> Media Forge のような高度なローカルAIサービスを、ControlDeck本体を汚染せず、
> enabled時だけネイティブUX・Jobs・Workflow・Agent・Files・GPU管理へ参加させられる
> 汎用Add-on Platformが成立していること。

これに以下を**追加**する。

> かつ、add-on が **未起動・劣化・GPU待ち・セットアップ未完了**のいずれの状態にあっても、
> ユーザが「何が起きているか」と「次に何ができるか」を、
> ControlDeck が描画する一貫したUIから常に把握できること。
>
> add-on の障害が、ユーザから見て「ControlDeckの不具合」として現れないこと。

境界の正しさは実装者のための条件であり、
状態の説明可能性はユーザのための条件である。両方を満たして初めて完了とする。

---

## 付録 A. 前版との対応表

| 前版 | 本改訂 | 変更 |
|---|---|---|
| §0.1 v1互換 | §4, §11.1 | 維持 |
| §0.2 別プロセス | §7.2 | origin/CSP/Cookieを具体化（C2） |
| §1 PR分割 | §C14 | PR-0追加、PR-C分割、migration独立化 |
| §2-3 manifest | §4.2, §6.3 | fail closed範囲分離（C3）、label/icon制約追加 |
| §4 状態 | §5.1 | `setup_required` 追加 |
| §5 registry | §5.2-5.4 | contribution単位availability（C7）、health flap抑制 |
| §6 無効時保証 | §5.5, §7.3 | `disable.pending` 猶予追加 |
| §7-9 UI | §6 | 状態チップ・詳細画面・差分更新を明文化 |
| §10-11 embedded/bridge | §7 | route sync / title / busy / job.subscribe 追加 |
| §12 context actions | §9 | 維持 |
| §13-19 broker | §8 | yield契約（C5）、managed supervision（§8.7）、wait reason enum、スコア式（C13） |
| §20-21 Jobs | §8.5, §C6 | 状態を phase カラムへ。enum非破壊 |
| §22-23 priority/class | §C13 | テスト可能な式へ |
| §24-26 residency/VRAM/GPU | §8.2, §8.3 | confidence必須、blocking可視化 |
| §27-28 workflow/agent | §9 | dry-run追加 |
| §29 security | §10, §7.2 | proxy境界・ヘッダ削除・iframe/CSP/token を追加 |
| §30 health isolation | §3.1, §C4 | hidden選択肢を削除 |
| §31 UI受け入れ | §6.5, §7.6, §11.2 | UXテストを正式要件化 |
| §32 test | §11 | UX/Bridge/yield テスト追加 |
| §33 禁止 | §12 | 13項目追加 |
| §34 migration | §C14, §8.4 | adapter先行を維持、PR順を明確化 |
| §36 シナリオ | §13 | G〜N 追加（degraded/setup/flap/mobile/旧bundle/LLM退避/thrash/proxy境界） |
| §37 完了条件 | §15 | UX条件を追加 |

---

## 付録 B. 前提と残課題

### B.1 確定済み前提

| # | 項目 | 決定 | 影響先 |
|---|---|---|---|
| 1 | ControlDeck 配信方式 | **HTTPS** | §7.2 を host-mediated proxy + opaque origin で確定 |
| 2 | LLM supervision | **`managed`** | §8.7 を確定。yield level 0〜4 を有効化 |
| 3 | モデル配置 | **ローカル NVMe** | §8.7.4 の cold_load_cost 実測が成立 |

### B.2 上記に伴う前提条件（守られないと設計が崩れる）

- LLM トラフィックの単一入口は `/api/v1/llm/v1`。生ポート直叩きは非推奨・無保証
- `cold_load_cost_sec` は実測 p90。推定値へのfallback禁止
- モデルディレクトリが NVMe 外を検出したら `managed` を `observed` へ自動降格
- proxy でのヘッダ削除（Cookie / Authorization / Set-Cookie）はテストで常時検証

### B.3 PR-0 で確定した判断

1. `./deck.sh ext lint <manifest>` を正規のv1/v2静的検証導線として追加する。既存の
   `./deck.sh plugin ...` はv1管理APIとして永続維持する。
2. `host.notification.show` はtoastのみとし、同じ通知本文を別tableへ重複永続化しない。
   Job終端状態は既存Jobs履歴、bridge呼出しは本文を含めないaudit/activityへ残す。
3. add-on service tokenはproxyがリクエストごとに発行・更新し、browserへ公開しない。
   browser bridgeはsession nonceを使い、service credentialと分離する。
4. Add-on一覧・manifest・存在情報はログイン前に公開しない。認証後かつ権限検査後だけ返す。

### B.4 PR-D1 で実機確認が必要な項目

- llama-server が **モデルの動的 unload に対応するか**
  → 非対応なら level 3 は level 4（プロセス停止）へ縮退し、
    `cold_load_cost_sec` が跳ね上がる。§8.7.3 の THRASH_FACTOR 再評価が必要
- `process_start_sec` / `model_load_sec` の実測値（Qwen3系・NVMe）
- gfx1201 / ROCm 環境での VRAM 解放が unload 後に実際に返るか
  （ドライバによっては即座に返らないケースがある）
