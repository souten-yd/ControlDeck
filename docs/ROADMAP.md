# 全体計画（ロードマップ）

状態: 2026-11-21 作成。ばらばらだった計画（OpenCode の開発環境・統合ナレッジ・Patent・検索基盤・フロンティアモデル・モデル管理・Terminal）を 1 つの順番にまとめた。
**引き継ぐ AI はまずこの文書を読み、次に「いまの仕事」の引き継ぎ資料（`docs/HANDOFF-*.md`）を読む。**
各計画の中身（設計・判断の理由・実装メモ）は個別の文書が正。この文書は順番・依存・判断待ちだけを持つ。進捗が動いたら、個別の文書と一緒にここの表も直す。

## 方針（2026-11-21 利用者）

1. **最優先は「OpenCode で全ての開発ができる」ようにすること。** Claude や Codex を使わなくても、ローカル LLM と OpenRouter で ControlDeck の拡張・改善や新しいソフトを開発し、ホストの GPU・モデル・API で評価し、更新で本体へ入れられるようにする。
2. それができたら、残りの計画（検索基盤・特許・ナレッジ）は、**この開発環境を使って**進める（OpenCode にやらせ、Opus が監査する）。開発環境の試運転（P6）に、残りの計画の最初の項目を使う。
3. Claude での開発は Opus が司令塔（`AGENTS.md`）。予算の都合で Fable は使わない（画面の設計も Opus と Sonnet）。

## 順番

| 順 | 仕事 | 段 | 状態 | 文書 |
|---|---|---|---|---|
| 1 | **OpenCode で全ての開発（開発環境 第 2 段階）** | P1 安全（箱ごとの pod・中継） | 完了 | `HANDOFF-opencode-dev-environment.md`、`PLAN-opencode-dev-environment.md` |
| | | P2 Project Lab で確かめる・試す（MCP の実行の道具・箱の run・HMR の記録・評価・画面） | 未着手 | 同上 |
| | | P3 ミラーが LLM を使える | 未着手 | 同上 |
| | | P4 GPU の貸し出し | 未着手 | 同上 |
| | | P5 ホストの API で評価 | 未着手 | 同上 |
| | | P6 試運転（下の 2 の W2 の最初の項目を OpenCode にやらせる） | 未着手 | 同上 |
| 2 | 検索基盤（Web 収集） | W2 ブラウザ（JS が要るページ・中継・data_api）→ W3 巡回 | W1 完了（v0.108）。W2 から | `HANDOFF-web-collector.md`、`PLAN-web-collector.md` |
| 3 | 特許の調査（Patent P1） | テーマ→検索語→承認→取得→関連順→全文判定→保存 | 未着手。W2 とナレッジの検索入口が前提 | 同上 |
| 4 | 統合ナレッジ | R3-4 知識エッセンス → R3-5 知識マップ・横断比較 → R3-6 の残り。R2-3 書誌グラフ・R2-5 PDF の座標 | R3-0〜3 完了（v0.102）。R3-4 から | `HANDOFF-knowledge-platform.md`、`PLAN-knowledge-platform.md` |
| 5 | フロンティアモデル | F4 役割と接続（AI アシスタントのゲートウェイ経由化・プロジェクトの「機密」）→ F3 エージェント CLI → F5 開発チーム | F1・F2・F2+（費用・残高・月の上限）完了 | `PLAN-frontier-models.md` |
| 6 | モデル管理の残り | 既知の不具合 4 件（`HANDOVER-model-management-v2.md` の「既知のバグ」）、A の画面、D-5 の画面 | 文書は 2026-08 で止まっている。直す前に今のコードで再確認 | `HANDOVER-model-management-v2.md` |
| 7 | Terminal V2 | 4c-5 実機の iPhone・PWA → 4c-6 既定化 | 利用者の実機が要る | `implementation-plan.md` |

順番の理由:
- 1 ができると、2〜6 を OpenCode とローカル LLM で進められ、Claude の使用量を減らせる。
- 2 の W2 は 3（特許の調査）の前提。3 は 4 の検索入口（`search_documents`）も前提。
- 5 の F4（AI アシスタントのゲートウェイ経由化）は 1 の P1・P3（中継・ミラーの LLM）と同じ場所を触るので、1 の後にまとめて行う。
- 6 の不具合は小さく独立しているので、1 の試運転の題材にしてもよい。

## 計画をまたぐ決まり

- **外部への送信**は 1 つの方針で扱う。基準は `PLAN-knowledge-platform.md` §10.2 の 4 経路（機密区分・クラウド LLM へ confidential を出さない・外へ送る検索語の承認）。フロンティアの外部送信の許可（呼び出し元ごと）、Web 収集の `approved_public` の検索語、OpenCode の外部モデルはこれに従う。
- **GPU は 1 枚**。llama.cpp のエンドポイント（会話・embedding 8094・reranker 8095）、Strata・Radiance・Maya-S、MediaForge・SonicForge、箱への貸し出し（P4）が取り合う。モデルの載せ替えとコンテキストを増やす変更は測ってから入れる。
- **長い処理は Jobs 基盤**（`PLAN-power-jobs-runtime.md` B。完了済み）に載せる。
- **外部からの取得**（pip・npm・モデル・ブラウザ）は利用者の了承を得る。

## 開発の担い手（2026-11-21）

| 担い手 | 動く場所 | 費用 | 向いている作業 |
|---|---|---|---|
| Opus（Claude Code） | ホスト | Claude のプラン | 計画・設計・安全・本番の操作・最終レビュー・マージ |
| Sonnet・Haiku（Opus の子エージェント） | ホストの作業ツリー | Claude のプラン | Sonnet: 仕様の決まった実装・テスト。Haiku: 探す・読む・要約 |
| 箱の OpenCode（モデルの順番: Go の GLM-5.3 → Zen の無料 2 つ → ローカル LLM。使い切ったら次へ） | `./deck.sh sandbox` の箱 | Go の枠（月 500 ドル）→ 無料 → 電気代 | 量が多く手順の決まった実装。必ず Opus が監査する |
| OpenRouter（箱の中の OpenCode） | 同上 | 残高 3 ドル | GPU を箱へ貸している間の開発役、ローカルで手に余る作業 |

- 作業の場所（作業ツリー・箱）を分ければ、Claude の子エージェントとローカル LLM は同時に動かせる（Claude 側は GPU を使わない）。
- 箱の OpenCode のモデルに Claude を使うのは従量課金（OpenRouter か Anthropic の API キー）なので勧めない。Claude のプランは Claude Code で使う。
- ローカル LLM を同時に何本も使うなら llama.cpp か Radiance（並べて伸びる）。Strata は伸びず、Maya-S は並列では起動しない（`parallel-slots` の実測）。
- OpenRouter の残高が尽きたら（402）止めずに、ローカル LLM か外部の上位モデルへ切り替えて続ける（利用者の許可）。

## 使いにくくなる点・限られる点と対策（2026-11-21 調査）

有用性の高い順。根拠は `HANDOFF-opencode-dev-environment.md` の「調査の記録」。

| 点 | 何が起きるか | 推奨の対策 |
|---|---|---|
| ローカル LLM のツール呼び出しが崩れる | edit の引数が文字列でなく JSON になって検証エラーを繰り返す、約 40k トークンを超えると同じ呼び出しを繰り返す、などの報告が多い（OpenCode PR #21713、llama.cpp #19513・#20164） | 1 つの指示を小さくしてセッションを短く保つ。圧縮（compaction）の buffer を早めに（25〜30%）。要らない MCP・道具を載せない（今の方針どおり子エージェントへ分ける）。同じ道具の連続失敗に上限を付ける。完了報告を信じず Opus が箱の中でテストを流し直す |
| OpenRouter の 3 ドルは小さい | 累計の購入が 10 ドル未満だと無料モデル（`:free`）は 1 日 50 回まで。データ方針 deny では無料モデルの提供元がほぼ消える。有料の安いモデル（DeepSeek V4 Flash 系）で 1 件 0.05〜0.2 ドルの見込み（キャッシュが効いた場合。外れると 3〜5 倍） | 主力はローカル。OpenRouter は GPU を貸している間と、ローカルで行き詰まったときの計画・レビューに温存。提供元を固定して（フォールバックしない）キャッシュを効かせる。`cached_tokens` と費用を記録する（F2+ の月の上限を 3 ドルに設定する） |
| GPU が 1 枚 | 箱へ GPU を貸すと、ローカル LLM の開発役も止まる | 貸している間は OpenRouter と Claude の子エージェントで進める。GPU が要る作業はまとめて行う |
| HMR が使えるのは Vite の開発サーバーだけ | 静的なサイト、Vite 以外のサーバー、作り上げたもの（build）には HMR が無く、記録も動かない | まず Vite で作る（ControlDeck の画面も Vite）。それ以外は、後から中継で記録のスクリプトを差し込む形を足す |
| iPhone の Safari で WebSocket が切れる | 背景に回ると接続が閉じ、復帰後に繋ぎ直せないことがある（WebKit の報告 2026-02） | 画面へ戻ったとき（visibilitychange）に HMR に頼らず読み込み直す。記録は送れなかった分を手元にためて、繋がってから送る |
| podman 4.9 の pasta は古い | 4.9 の既定は slirp4netns。`host.containers.internal` は 5.3 以降が前提。`-t auto` は約 1 秒ごとの検出で遅れる | ポートは `-p` で明示し、`host.containers.internal` に頼らない（P1 の設計どおり）。podman 5 への更新は sudo が要るので利用者の判断 |
| 箱の中で確かめられないもの | systemd のサービス、ファン、本番そのものの更新 | ファンは対象外（SMU のリセット禁止）。systemd は偽物で状態遷移だけテストし、最後は正式 Release → 本番の自動更新（異常なら自動で戻る）で確かめる |
| デスクトップの画面を持つソフト | 画面が無いので動作を見られない | 今回の範囲の外。必要になったら仮想の画面（Xvfb など）を足す |
| Project Lab は CodeDEV の中だけ・同時 3 本・メモリ 2GB | 箱の作業ツリーや、ControlDeck のミラーのような大きなものは今の制限に当たる | P2 で、箱の作業ツリーを開く道筋と、箱の run の上限を別に決める |

## 利用者の判断・許可

済み（2026-11-21）:
- 本番のモデルはいつでも止めてよい。
- 開発役を OpenRouter に切り替えてよい（3 ドル。尽きたらローカルかフロンティアで続ける）。
- Fable は使わない。
- ControlDeck 本体のマージと正式 Release は毎回尋ねない（`POLICY-autonomous-development.md`「常設の承認」）。

待ち（各計画から集めた。詳細は各文書）:
- 外部取得: Pillow・Docling（ナレッジ R2-5・R3-6）。
- 鍵・登録: EPO・USPTO の鍵、特許庁 API の利用登録（特許の調査・ナレッジ）。Codex・Claude Code のログイン（フロンティア F3）。
- 本番・データ: 図表の読み取りの 1 回実行、Patent・Knowledge の実画面の確認、qnaplibrary の削除、PatentBuddy・RDH のリポジトリを閉じる時期。
- sudo: 特権 helper、Ollama のモデルの移動、SGLang のコンテナ、podman 5 への更新（任意）。
- 実機: Terminal V2 の iPhone・PWA の確認。
- 公開資料: 公開公報をクラウド LLM に渡してよいか、規格の組織契約、QNAP の実データの有無。

## 文書の古い記述（直す対象）

- `PLAN-opencode-dev-environment.md` の「現状」表の「E2E が動かない」は古い（第 1 段階 D で解消）。
- `PLAN-web-collector.md` の W2 のブラウザ「使い捨ての Chromium」は古い（この PC の Chrome に決定、`HANDOFF-web-collector.md`）。
- `PLAN-knowledge-platform.md` §8 の「K-PR5・7 未着手」は古い（R3-2 完了、`HANDOFF-knowledge-platform.md`）。
- `HANDOVER-model-management-v2.md` は 2026-08 で止まっている。OMO・v1 の記述は過去の経緯。
