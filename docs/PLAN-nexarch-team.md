# Nexarch の AI チーム（C6）の設計

2026-11-27 利用者「AI チームを作りたい。どうしたら情報の橋渡しができるのか。リーダーはローカル LLM やフロンティアモデルを自由に設定したい。どういう形が使いやすいか。調査検討してブラッシュアップして」。

## 2026-11-28 devx: 画面・道具・守り・再開・モデル選び（計画のみ・未検証）

利用者（2026-11-28）の 7 つの要望（グラフィカルな画面・OpenCode の守り・道具の配分・停止と再開・モデルの選択肢・資料の受け渡し・検索の評価と改良）に対する計画。**この節を書いた時点ではコード・試験・画面・実機は未着手で、下の表の A〜F はすべて未検証**。実装が入ったら状態を書き換え、`docs/implementation-status.md` と `docs/HANDOFF-nexarch-devx.md` を同じコミットで直す。実装完了・試験成功を先取りして書かない。

### 作るもの（A〜F）

| # | 作るもの | 中身（要約） | 主に触る場所 | 状態 |
|---|---|---|---|---|
| A | チームの画面の作り直し | チームリーダーと担い手をアバターつきのカードと段（調べる→計画→実装→評価→統合→試験）で見せ、動いている所が控えめな動きで分かる（`prefers-reduced-motion` を守る）。文字は 1 行の要約だけにし、詳しい報告・差分・確認の結果・履歴は押して開く（ボトムシートか広がる枠）。エキスパートで今の細かい表示を全部出す。動いている・待っている（質問・許可・上限）・止まった（失敗・中断・閉じた）・終わったを、色・形・動きの 3 つで分ける（色だけに頼らない）。止まった・失敗・要対応はエキスパートを切っていても常に出す | `frontend/src/features/commander/`（`ConsoleTile.tsx` は別チームの担当なので最小） | 未着手 |
| B | 役割ごとのモデルの選択肢 | 役割の設定に、いつも見える「モデル」の選択肢を 1 つ置く。中身は使えるモデルを前もって抜き出して種類ごとにまとめたもの（ローカル・OpenCode の無料／Go・OpenRouter・Claude・Codex）。選べば CLI・接続は自動で決まる。アカウントを細かく選ぶのはエキスパートだけ | `commander/models.py`・`integrations/opencode/model_choice.py`・`TeamRoles.tsx` | 未着手 |
| C | 道具の配分と守り | 担い手の OpenCode に ControlDeck の MCP（`control_deck.research`・`knowledge_search` ほか、ふつうの OpenCode の会話と同じもの）を付ける（`_runtime_config(owner_user_id=…)`。チームを作った利用者の権限。作った利用者が分からないチームは付けない）。Claude・Codex の担い手にも同じ道具を MCP の橋渡しで渡す。読み取りの役割（explore・review）には書く道具を渡さない。箱（podman）と `--auto` は今のまま。チームの設定 `web_research` を担い手の Web の道具にも効かせる。チームに「閉じたチーム（ローカルだけ）」の切り替えを足す（入にすると、ローカル LLM 以外の担い手・外部のモデル・Web の調査を断る。既定は切） | `commander/team_runner.py`・`team_leader.py`・`integrations/opencode/provider.py`・`addons/agent_mcp.py`・`route_map.py` | 未着手 |
| D | 再起動・更新から自動で続きを再開 | ControlDeck の起動のとき、再起動で中断されたチームリーダーを自動で再開する（チームの設定「自動で再開」既定は入。1 チームにつき 30 分に 2 回まで。失敗が続いたら止めて通知）。中断された担い手の仕事も配り手が自動で配り直し、本文に「前回は途中で中断。worktree の変更はそのまま残っている。git status と diff を見て続きから」を足す。teams.json は原子的に書き、書き込みの直前に 1 世代のバックアップ（teams.json.bak）を残し、壊れていたら .bak から読む。保存の途中で落ちても前の状態が読めることを試験で確かめる。中断・自動の再開が一目で分かる印（要対応は常に出す） | `commander/teams.py`・`resume.py`・起動の処理・`TeamView.tsx` | 未着手 |
| E | 資料の受け渡しと worktree | 「チームリーダーに頼む」に「資料を添える」（/data1tb・/data2tb のファイル・フォルダを FilePicker で選ぶ）。選んだものは作業場所の `.nexarch/inputs/` から読み取り専用で見られるようにし（シンボリックリンクではなく、箱から見えるパスで）、チームリーダーにはパスの一覧だけを渡す。担い手の worktree を作るとき、作業場所の `.nexarch/`（依頼の全文・資料）が見えるようにし、`frontend/node_modules`・`.venv` が作業場所にあればリンクする。確認のコマンドに作業フォルダ（作業場所の中の相対パス）を指定できるようにする | `commander/team_runner.py`・`team_checks.py`・`TeamView.tsx` | 未着手 |
| F | チームリーダーの道具の説明 | `.nexarch/prompt-audit.diff` の `leader_mcp.py`・`team_leader.py` の塊を取り込み、道具の説明を実際の動きに合わせる（`AGENTS.md` の塊は入れない） | `commander/leader_mcp.py`・`team_leader.py` | 未着手 |

機能を損なわない条件（A）: 頼む・止める・再開・質問に答える・取り込む・引き継ぎ先を選ぶ、が今と同じ手数以内。ダーク／ライト・320px と 1280px。E2E はエキスパートの切と入の両方を撮る。

### 調査: 進み具合の見せ方の比較（2026-11-28）

読み方: 「特徴」はリーダーの `research(web)` が公式資料を読んで確かめた**事実**（2026-11-28。出典は各行の URL）。「採用したい点」はこの計画の**提案**で、まだ実装していない。「限界・注意」は読んだ範囲での注意で、**未確認を含む**。

| 製品 | 特徴（公式で読んだ事実） | 採用したい点（提案） | 限界・注意 | 出典 |
|---|---|---|---|---|
| Hermes Agent | ストリーム表示の TUI。実行の中断と方向転換。サブエージェントの設定 | 実行中の流れを 1 本の時系列で見せ、止めずに方向を変えられる操作を画面に置く | TUI 中心。チームの板・複数 CLI の横断は持たない（読んだ範囲） | https://github.com/NousResearch/hermes-agent |
| ChatDev | 2.0 は DevAll。1.0 の「仮想ソフト会社」は `chatdev1.0` ブランチ（ピクセル絵の会社の画面） | 役割を組織として見せる考え方（誰が何を担当しているかの地図） | **1.0 のピクセル画面を現行版と混同しない**。2.0 の画面は別物 | https://github.com/OpenBMB/ChatDev |
| MetaGPT | product manager・architect・project manager・engineer の役割と SOP（決まった手順と成果物） | 段の名前と成果物の型を SOP として画面の段に写す（今の explore／implement／review／hard の段と対応づける） | 役割と手順が固定で、モデルを横断して自由に割り当てる形ではない | https://github.com/FoundationAgents/MetaGPT |
| CrewAI | flows・state persistence（状態の永続化）・observability（観測） | 工程の状態と記録を画面に出す。再起動後の続きの表示の手本にする | Studio の具体画面は**未確認**（`/en/platform/crew-studio` はトップへ戻る）。Studio の画面を根拠にしない | https://docs.crewai.com/ |
| AutoGen Studio | ドラッグ＆ドロップの TeamBuilder。メッセージフローのグラフ・ライブ表示・停止。**prototype** | メッセージの流れ図＋ライブ＋停止の組み合わせ | prototype（API・画面が変わりやすい）。そのままは取り込まない | https://microsoft.github.io/autogen/stable/user-guide/autogenstudio-user-guide/index.html |
| OpenHands | agent-canvas: 会話・files・terminal・モデル管理を切り替える 1 画面 | 1 画面の中で会話・ファイル・端末・モデルを切り替える整理（詳細を開く枠の参考） | 単一エージェント中心。チームの板は持たない | https://docs.openhands.dev/openhands/usage/agent-canvas/overview |
| Devin | session tools: Progress に工程の詳細・commands・code・browser を統合 | 「進み具合」に工程の詳細と道具の実行（コマンド・コード・ブラウザ）をまとめて出す | 商用。画面の作りは参考にするだけ | https://docs.devin.ai/work-with-devin/devin-session-tools |
| Conductor | parallel-agents: 担い手ごとに独立した branch／worktree、レビュー | 並列の担い手ごとに branch／worktree を分け、レビューを並べて見せる（T4 と同じ方向） | 形態（クラウドかローカルか・料金・ローカル LLM の可否）は**未確認** | https://www.conductor.build/docs/concepts/parallel-agents |

この表から画面 A に取り込む順（提案）: (1) 段の流れとカード（MetaGPT の SOP・ChatDev の組織）→ (2) 動いている／止まっているの 3 つの印（Hermes・AutoGen のライブ）→ (3) 詳細を押して開く（OpenHands・Devin の統合表示）→ (4) 並列の branch／worktree とレビューの並び（Conductor）。色だけに頼らず、形と動きでも分ける。

### 検索（research）の評価と小改善の提案（2026-11-28）

このチームの調べもので `control_deck.research` を実際に使った結果（リーダーの記録）:

- **事実**: 複数の製品をまとめて調べる検索はノイズが多く、製品ごとの公式 URL を個別に読む方が有効だった。URL の本文は約 6000 文字で末尾が途切れた。`[R番号]` の形で出典を書くよう促しても、検索の返り値に番号が無いため対応づけられなかった。
- **提案（backend 担当へ。この節では未実装）**: (1) 検索の返り値の各項目に通し番号（`[R1]`…）を付ける、(2) ページを読む返り値に URL と取得日を必ず含める、(3) 長い本文は「続き（次の範囲）」の案内か範囲指定を返す、(4) まとめ検索より個別 URL を優先する案内を道具の説明に書く。効果（出典の対応づけの取りこぼし・読み直しの回数）と処理時間を測ってから入れる（`AGENTS.md`「機能を取り込む条件」）。

### 確かめ方（A〜F 共通）

- 集中の試験 → 関連の回帰 → `./deck.sh test` → `cd frontend && npm run build` → 実画面（320px・1280px、エキスパートの切と入）→ `env -u CONTROL_DECK_CONFIG ./deck.sh dev-check --base origin/main --skip-visual` が「合格」。
- 新しい道（C の MCP の配分・E の資料・D の自動の再開）は `backend/app/route_map.py` に書いて `./deck.sh route-map`。試験は目的と根拠を docstring に書き、`./deck.sh test-catalog` で重複を確かめる。
- D は「保存の途中で落ちても前の状態が読める」ことを試験で確かめる。A は止まった・失敗・要対応がエキスパートを切っていても出ることを E2E で確かめる。
- 効果と処理時間は実データの前後で測り、数字を PR に書く（`AGENTS.md`「機能を取り込む条件」）。

## 目標（2026-11-27 利用者「勝手に完成している状態を目指して欲しい。ユーザーの負担はない形」）

**利用者は目標を 1 行書くだけ。あとはチームが計画・実装・確認・レビュー・合流まで自動で進め、人は任意で見るだけ。**

- 人の確認は既定ですべて自動: 計画はそのまま板へ（通知だけ）、試験に落ちたら同じ担当に直させ（2 回まで）→ 強いモデルへ付け替え、上限なら空いているモデルへ、確認のコマンドと別系統のレビューを通ったら自動で合流。
- 合否はコードが決める（AI の「できました」を信じない）: Nexarch が確認のコマンドを担当の作業場所で実行し、通らなければ完了にしない。
- 止まらない・暴れない: タスクごとに新しい会話＋チームの前提を毎回渡す。タスクごとの試行回数・時間、チーム全体の費用（OpenRouter の月の上限など）に上限。超えたタスクだけ止め、他は進める。
- 安全: 作業は担当ごとの worktree とブランチの中だけ。本番に触らない。
- それでも利用者に聞くもの（最小）: 決まりで承認が要るもの（外部からの取得・本番データの変更・秘密）、付け替えも尽きて自動で直せないタスク（他は止めない）、目標そのものがあいまいなときの最初の 1 回の確認。
- 効果の測り方: 小さな目標で回し、「人が手を入れた回数」を数えてゼロに近づける。

## 調べたこと（要約。出典は末尾）

| 既存のもの | 情報の受け渡し | この環境に合うか |
|---|---|---|
| Claude Code agent teams（実験機能） | 共有タスク板（ファイル＋ロック）・受信箱（JSON ファイル）・名前宛ての SendMessage。リーダー 1＋メンバー | 形は手本。ただし Claude 専用でリーダーも Claude 固定 → 「リーダーを自由に」を満たさない |
| MCP Agent Mail | MCP の受信箱・送信箱・スレッド・ファイルの予約（助言）。Git＋SQLite | 受信箱とファイルの予約の考え方を参考に。登録の手順が弱いモデルの失敗点。ライセンス未確認なので取り込まない |
| Agent Deck（MIT）・Claude Squad（AGPL） | tmux と git worktree で並べるだけ（受け渡しは無い） | Nexarch がすでに同じ層（状態の判定・自動再開）を持つ。参考だけ |
| OpenCode・Codex・Gemini の subagent | 親が子を呼び、子の最後の返答だけが戻る | 1 つの CLI の中で閉じる。CLI をまたぐチームには使えない |
| Anthropic の multi-agent research | リーダー（強いモデル）＋メンバー（安いモデル）。指示は「目的・出力の形・使ってよい道具・境界」の 4 点 | 指示の形をそのまま使う。トークンはチャットの約 15 倍になる点に注意 |

弱いローカル LLM の注意（論文・実例）: 道具の選び間違い・引数の作り間違い・同じ呼び出しの繰り返し・長い文脈で崩れる。対策は「道具を少なく・引数を平らに・状態をモデルに覚えさせず板に置く・機械的な判断はコードで」。

## 方式の比較（橋渡し）

| 方式 | 確実さ | 文脈の消費 | 弱いモデル | 人が見て分かる |
|---|---|---|---|---|
| 画面へ直接送る・画面を読む | 低い | 大 | ○（道具不要） | ◎ |
| 共有のタスク板（ControlDeck が持つ） | 高い | 小 | ○ | ◎（画面に出す） |
| MCP の受信箱 | 中 | 中 | △（道具の呼び出し） | ○ |
| git の worktree・ブランチで成果物 | 高い | 小（差分） | ○ | ○ |
| 構造化した引き継ぎ（課題・受け入れ条件・報告の形） | 高い | 小 | ◎ | ◎ |

→ **中身は「タスク板＋構造化した引き継ぎ」、成果物は git、起こすのは画面への短い送信**、の組み合わせにする。どれか 1 つに頼らない。

## 推奨の形

### 1. チーム = Nexarch のコンソールの集まり＋タスク板

- **メンバー**: いまの Nexarch のコンソール（Claude Code・Codex・OpenCode のどれでも、どのアカウントでも）に役割（リーダー・開発・レビュー・調査など）を付けたもの。状態の判定（C2）・自動再開（C3）・使用率（C4）はそのまま効く。
- **タスク板**: ControlDeck が持つ（DB）。1 件 = 目的・受け入れ条件・担当ファイル・担当・状態（待ち・作業中・レビュー待ち・完了・差し戻し）・結果の報告（何をしたか・変えたファイル・試験の結果・残り）。**状態はここが正本**で、モデルの記憶に頼らない。
- **成果物**: メンバーごとの git worktree とブランチ（同じファイルの同時編集を構造で避ける）。担当ファイルが重なるタスクは板に入れるときに警告する。

### 2. 橋渡し = 3 つの道

1. **MCP の道具（主の道）**: ControlDeck の MCP に 4 つだけ足す。引数は平らな文字列。
   - `team_tasks`（自分の担当・待ちのタスクを見る）
   - `team_claim`（タスクを取る）
   - `team_report`（結果を報告する。形は決まった欄を埋めるだけ）
   - `team_post`（リーダー・メンバーへ短い連絡）
   リーダーは加えて `team_create_task`・`team_assign`・`team_review`（合格・差し戻し）。
2. **画面への短い送信（起こす道）**: 道具を呼ばない・呼べないときのため、Nexarch がコンソールへ短い文を送る（「タスク #12 を担当しています。板の内容: …。終わったら team_report で報告」）。タスクの本文も一緒に送るので、MCP が使えない CLI・弱いモデルでも動く。
3. **コードの見張り（LLM なし）**: Nexarch がメンバーの状態を見て、「入力待ちになった＝止まった」「報告が来た」「上限で止まった」をリーダーへ短く知らせる（リーダーのコンソールへ送るか、板の印）。誰が何をしているかの判断は、LLM ではなくコードが持つ。

### 3. リーダー = 好きなモデルのコンソール（推奨）＋必要ならループ

- **既定はリーダーも 1 つのコンソール**: OpenCode のコンソールなら、接続（＝アカウント）を選ぶだけでローカル LLM（ゲートウェイ）・OpenRouter の外部モデル（DeepSeek・Claude・GPT など）を自由に選べる。Claude Code・Codex のコンソールなら契約のプランで動く。**Claude・Codex は定額のログインだけで使い、API キー（従量課金）は使わない**（利用者 2026-11-28。鍵の環境変数を渡さず、Claude は `forceLoginMethod`・空の `apiKeyHelper`、Codex は `forced_login_method="chatgpt"` を付ける。上限で止まったときの引き継ぎ先は別の定額アカウントか OpenCode）。利用者はリーダーのコンソールで普通に話しかけて指示できる（使いやすさの要）。
- **弱いモデルをリーダーにする場合**: リーダーの道具は板の操作だけにし、状態の取得・起こす・完了の確認はコード（2-3）が肩代わりする。リーダーの仕事を「課題を板に書く」「報告を読んで合否を決める」だけにする。
- **後から足す: 自動のリーダー（ControlDeck 側の小さなループ）**: 板を読んで次の一手を選んだモデルで決める。夜間に人がいないときの運用向け。リーダーの形を後から差し替えられるよう、どちらも同じ板と道具の上に置く。

### 4. 画面（Nexarch の中）

- **チームを作る**: テンプレート（「開発＋レビュー」「調査 3 人＋まとめ」など）を選び、役割ごとに CLI・アカウント（＝モデル）・作業フォルダを選ぶ → まとめて開く。
- **チームの画面**: 左にタスク板（待ち・作業中・レビュー待ち・完了）、右にメンバーのタイル（状態・使用率・最後の行）。タスクのカードを押すと担当のコンソールが開く。
- **人が入る場所を 4 つに絞る**: 計画（最初のタスク）の承認・レビューの合否・差し戻し・担当の付け替え。上限の再開は自動。

## 追加調査（2026-11-27 午後: 提供元をまたぐ協調）

利用者「AI チームについて Reddit・GitHub などでさらに良い・柔軟な方法がないか。ローカル LLM・Claude・Codex・OpenRouter・OpenCode Go を横断的に協調させたい」。分かったこと（出典は末尾の追加分）:

- **リーダー任せの委譲は当てにならない**: 実測で、リーダー（Opus）にサブエージェントへの委譲を任せると 7 回中 0 回しか委譲しなかった。委譲は**板が割り当てて送り込む**方式（T1 の形）が確実。
- **提供元をまたぐ実例の多くは「共有の文書＋git＋人の確認」**: Claude が計画とレビュー、Codex が実装、Gemini が試験、の 3 社の例。最大の失敗は「通ったと言ったが実は通っていない」（環境変数が残った試験）・共有メモを読まないモデル。対策は「仕様を毎回プロンプトに全文」「完了報告にきれいな環境での出力を付ける」。
- **別の系統のモデルにレビューさせる（cross-model review）**: 同じモデルは同じ見落としをする。実装と違う系統（Claude ↔ Codex ↔ GLM）に「差分と受け入れ条件だけ」を渡して反論させる例が多い。指摘は助言として扱い、重大なものだけ差し戻す。
- **上限と費用**: 実例は「上限に当たったら次の CLI へ」。Nexarch は上限の検知と自動再開を持つので「回復を待つ」と「別のモデルへ付け替える」を選べるのが強み。自動で付け替える完成品は見つからなかった。同じ Opus でも素の Claude Code より OpenCode 経由の方がキャッシュの扱いで費用が大きく下がった実測がある。
- **役割と向き**: 計画・設計・レビュー＝Claude、定型の実装＝Codex（`codex exec`）、GLM は 1 関数の中は強く複数ファイルに弱い、ローカルは道具の呼び出しが弱い（1 ファイル・1 関数の下働き・要約に限る）。
- **協調の失敗の 1 位は同じファイルの同時編集**: 担当領域（レーン）と WIP 制限、worktree、試験を通ったものから 1 本ずつマージ。
- **人の関わり**: スマホへの通知（止まった・上限・報告あり）と、承認のボタン。tmux を Telegram・ntfy につなぐ OSS が多い（CCGram・Muxflow など）。
- **他の CLI を道具として呼ぶ MCP**（Chorus・PAL の clink・Codex の公式 Claude プラグイン・cli-orchestrator-mcp）: 「リーダーの CLI が別のモデルに相談する」形。成熟度は低いものが多い。Nexarch の板と並べて使える（後で `ask` の道具として）。

### 設計へ取り込むこと（効果の大きい順）

1. **検証できる完了報告**: 4 行報告に「確認: <実行したコマンドと結果>」を足して 5 行に。確認の行が無い・試験の行が空なら板で「未検証」の印を付け、合格を押す前に目立たせる。
2. **別の系統によるレビューを標準の段に**: 報告が来たら、実装と違う系統のメンバー（または指定のレビュー役）へ「差分・受け入れ条件・報告」だけを送るレビューのタスクを自動で作る（チームごとに入り切り）。系統は CLI と接続から決める（Claude／Codex／OpenCode の接続ごとのモデル）。
3. **割り当ては板が決める・上限で付け替え**: タスクに「担当の候補（順位つき）」を持たせる。担当が上限で止まったら、チームの設定で「回復を待つ（自動再開）」か「次の候補へ付け替えて送り直す」。実際にどのコンソール（どのモデル）がやったかを履歴に残す。OpenRouter は月の上限額（既存の cloud の上限）を超えたら候補から外す。
4. **大きさの目安で既定の担当**: タスクに大きさ（小＝1 ファイル・1 関数／中／大＝複数ファイル・設計）を付け、既定の候補を出す（小はローカルや定額の GLM、大は Claude・Codex）。利用者が変えられる。
5. **担当領域と WIP**: 担当ファイルが重なる作業中のタスクがあれば、送り込みを止めて確認を出す（警告だけでなく）。メンバーごとの worktree（T4）。
6. **通知**: 「入力待ちに戻った（止まった）」「上限で止まった」「報告あり」を ControlDeck の通知（スマホの Web Push など既存の通知先）へ。通知から板のタスクを開ける。
7. **相談の道具（後で）**: MCP に `team_ask(role, question)`。リーダーのコンソールから別の系統へ小さな質問・第二の意見。1〜6 の後、必要が見えたら。

## 進め方（段）

| 段 | 中身 | 状態 |
|---|---|---|
| T1 | タスク板と役割の印・担当への送り込み・画面からの報告の取り込み | 実装済み（#684） |
| T2a | 見張り（止まった・報告あり・上限）と通知、検証できる 5 行の報告 | 実装済み（#687） |
| T2b | 確認のコマンドを Nexarch が実行して合否（LLM なし）、タスクごとの会話のリセットとチームの前提、落ちたら自動で直させる（2 回まで → blocked） | 実装中 |
| T3 | 目標 1 行 → リーダーが決まった形の Markdown で計画 → 板へ自動で取り込み（承認なし・通知）。担当はコードが選ぶ（大きさ・空き・使用率・上限）、上限・blocked で付け替え、実行したモデルの記録 |  T3a 実装済み（ヘッドレスの担い手・上限の方針（待つ／引き継ぐ）・並列と配り手・チームの作業場所）。T3b 実装済み（チームリーダーの任命・既定のチームリーダー・目標から自動でチームを作る・役割と必要数・プライマリ／セカンダリと回復後の戻り・引き継ぎ先を選ぶ・モデルと考えるレベル・チームの保存先（team_root）・エキスパート表示）。T3c 実装済み（一文で頼む → チームリーダーが MCP の道具 `nexarch_team` で調べ・計画し・段（explore・implement・review・hard）で振り・評価・統合・試験する。下の「T3c の形」） |
| T4 | メンバーごとの worktree・担当ファイルの重なりで同時に送らない、別系統のレビュー（差分と受け入れ条件だけ）、確認とレビューを通ったら自動で合流 | |
| T5 | 費用・試行の上限、夜間の連続運転、相談の道具（MCP の `team_ask`）、MCP の板の道具（使えるモデルの近道） | |

確かめ方（各段の共通）: 小さな目標（例「Nexarch のタスク板に検索を付ける」）を回し、人が手を入れた回数・費用・時間・合否を `~/ControlDeck-評価/` に残す。

## T3c の形（2026-11-27 利用者「一文リーダーに依頼 → 調査・計画・振り分け・個別に実装と評価・統合して評価・試験。Opus が Haiku・Sonnet を使う流れの模倣。LLM は横断的に」）

- **状態は Nexarch、動かすのはチームリーダー（MCP）**: 計画・仕事・実行・結果は teams.json。チームリーダーは `nexarch_team` の道具（`team_status`・`write_plan`・`research`・`delegate`・`wait`・`read_result`・`run_checks`・`integrate`・`ask_user`・`wait_answer`・`report`・`finish`）で動かす。上限で止まったら別の endpoint（チームリーダーのセカンダリ）が `team_status` から続ける。
- **段の表（Opus の流れの模倣。役割の名前＝段の名前）**:

| 段 | 仕事 | 既定の割り当て（例） | 書くか |
|---|---|---|---|
| explore | 探す・読む・調べる（安い） | OpenCode（ローカル） | 読むだけ（チームのブランチの worktree） |
| implement | ふつうの実装・試験を書く | Claude sonnet | 仕事ごとの worktree＋ブランチで書く |
| review | 評価・レビュー（別の系列が望ましい） | Codex | 読むだけ |
| hard | 難しい設計・判断の要る不具合 | チームリーダー自身 | worktree で書く |

- **worktree**: チームのブランチ `nexarch/<team>`・仕事ごとに `nexarch/<team>-<n>`（git は `/` で入れ子にできないため `-`）・作業場所の `.nexarch-wt/` に置く。統合は `merge --no-ff`、競合は implement へ戻す。「取り込む」は元から git の作業場所だけ（PR は T4）。git でなければ `git_init`（既定 true）で `git init` して同じ形にする。
- **自動の流れ**: 依頼 → 調べる（explore）→ 計画（write_plan）→ 並列の implement → 結果と確認の合否を見る → integrate → 全体の確認（run_checks team）→ review → finish。finish しなければ最大 3 回再開、上限・落ちたときも再開。
- **設定**: `autonomy`（high＝聞かず判断を記録／ask）・`web_research`・`git_init`。呼び出しの履歴は道具の名前と成否だけ（本文・token は残さない）。

## 出典

- Claude Code agent teams: https://code.claude.com/docs/en/agent-teams
- Claude Code cross-session messaging: https://code.claude.com/docs/en/cross-session-messaging
- MCP Agent Mail: https://github.com/Dicklesworthstone/mcp_agent_mail
- Agent Deck: https://github.com/asheshgoplani/agent-deck
- OpenCode agents: https://opencode.ai/docs/agents/
- Codex subagents: https://developers.openai.com/codex/subagents
- Gemini CLI remote agents: https://geminicli.com/docs/core/remote-agents/
- Anthropic multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- ローカル LLM の道具の失敗: https://arxiv.org/pdf/2510.00229v3 、https://arxiv.org/pdf/2602.18968 、https://dev.to/thlandgraf/four-failure-modes-youll-hit-running-a-local-llm-in-a-multi-step-agentic-loop-3kd9
- （追加調査）多モデルの実測と委譲 0 回: https://akitaonrails.com/en/2026/04/18/llm-benchmarks-part-2-multi-model/
- （追加調査）3 社の CLI で 1 つの案件: https://dev.to/wilddog64/i-ran-three-ai-agents-from-three-different-companies-on-one-complex-project-heres-the-909
- （追加調査）21 エージェントの失敗 3 つ: https://dev.to/seakai/the-3-failure-modes-we-hit-running-21-ai-agents-on-one-codebase-p09
- （追加調査）cross-model review: https://zackproser.com/blog/codex-reviews-claudes-diffs 、https://claude-codex.fr/en/advanced/cross-model-workflow/
- （追加調査）Chorus: https://github.com/homilabo/Chorus 、PAL MCP（clink）: https://github.com/beehiveinnovations/zen-mcp-server
- （追加調査）oh-my-opencode のカテゴリ別の振り分け: https://dev.to/rosgluk/oh-my-opencode-review-honest-results-billing-risks-and-when-its-worth-it-3ea
- （追加調査）スマホから操作: https://dev.to/alexeiled/ccgram-control-ai-coding-agents-from-your-phone-via-telegram-and-tmux-4jjd


## 使いながら見つけた直し（2026-11-28〜。`docs/ROADMAP.md` 方針 3）
ControlDeck の開発を本番の AI チームから回して、詰まった所・手間だった所をここに書き、開発機能の直しとして入れる。
- 2026-11-28: 実装の担い手に OpenCode の GLM（Zen・Go）を当てられなかった（選択肢に出ない・ゲートウェイでしか起動しない）→ チームの画面のない実行で Zen のモデルを使えるようにした。コンソールは未対応（断る）。
- 2026-11-28: 依頼が 4000 字までで、本物の仕様（5638 字）が入らなかった → 20000 字にそろえた。
- 2026-11-28: チームリーダーの Codex が 1 秒で落ちた（RLIMIT_FSIZE が Codex 自身の記録ファイルに効いて SIGXFSZ）→ 出力のファイルだけを見張る形に直した。
- 2026-11-28: GLM の担い手がローカル LLM の同時の枠に数えられ、調べものが終わるまで実装が始まらなかった → モデルで判定するように直した。
- 2026-11-28（チームリーダーの要望）: 担い手の出力の末尾が空のままで、進み具合が見えにくい（`_result`・`read_result` が実行中の live を読んでいなかった）→ wait・read_result に実行中の最後の行・道具・経過を返すようにした。待ちの仕事には、配り手が始められなかった理由（wait_reason）を返す。
- 2026-11-28（チームリーダーの工夫）: 確認のコマンドは 1 行・パイプや && なしなので、backend の中で流すには `env -C backend …` を使った。→ 確認のコマンドに作業フォルダを指定する欄を足すか、道具の説明に書く（未対応）。
