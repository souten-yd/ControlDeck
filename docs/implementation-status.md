# 実装状況

## 2026-11-23 OpenRouter のモデル選択: 一覧を取り直すボタン・アカウントの設定で除外された件数・数の言葉

- 利用者「フリーモデルを、制限しないにしてるにもかかわらず 200 件しか取れず、無料は 1 件」「モデル設定を更新した。モデルリストが更新もされない」。
- 出どころ: OpenRouter はアカウントの設定（Privacy・ZDR・Guardrails）に合わないモデルを鍵つきの一覧から除く（鍵つき 271 件・無料 0、鍵なし 477 件・無料 12）。「選択 1/200」は選べる上限で件数ではないが件数と読まれた。一覧は 5 分覚え、取り直す手段が無かった。
- 直し: `GET /models/cloud/{id}/remote-models?refresh=true`（ZDR の 1 時間の覚えも使わない）・`cloud.remote_models_report`（鍵なしの公開の一覧との差を `excluded` に。Authorization は付けない。取れなければ null）。画面に「一覧を取り直す」・最後に取得した時刻・「アカウントの設定で除外: n 件（うち無料 m 件）」（折りたたみ・灰色・openrouter.ai/settings/privacy へのリンク）、「該当 n 件（全 m 件） · 選んだ k 件（最大 200）」。
- 試験: `backend/tests/test_cloud_models.py`（差の数え方・無料の数・取り直し・公開の一覧の失敗・OpenRouter 以外）、`frontend/e2e/frontier-models.spec.ts`（書いただけ・未実行）。

## 2026-11-23 会話モードの読み上げ: 「Web で調査中」のような進み具合の文を読まない。先頭が入れ替わっても読んだ文を読み直さない

- 利用者「生成開始時に『ウェブで調査中』が出て、音声でも読む。その後に本文を読み、表示が消えて 1 行目から入れ替わると、一通り読んだ後にもう一回読む」。
- 出どころ: AssistantChat の send が、Web・学術・Deep・Research の送信直後に回答の content へ「🔎 検索中...（サーバー側で継続）」などを仮に置き、生成中の delta はその直後へ足され、確定でサーバーの本文に置き換わる（backend の progress は meta で、本文には入らない）。これを本文として読み、消えた後は位置合わせが外れて最初から読み直していた。
- 直し（`frontend/src/features/workflows/speakable.ts`）: 仮の文は content に入れず `Msg.placeholder`（chatState.ts）へ置き、ChatMessage が本文が来るまでだけ出す（読み上げ・コピー・保存の対象外。文の形では見分けない）。`nextSpoken` は位置ではなく文ごとの指紋（文字と数字だけ。空白・記号・出典の [n]・Markdown を除く）の数え方つきの集合で、読んだ文を飛ばす。読んでいない文は先頭に差し込まれても読む。
- 試験: `frontend/e2e/assistant-voice-modes.spec.ts` に「progress lines are not read…」を追加（関数だけの試験）。
## 2026-11-23 Home: 通知の流れるテロップ・LLM カードの 1 行 ctx

利用者の要望（通知は収まらなければ右から左へ自動で流す・複数なら 1 件ずつ流してめくる・LLM は見出しを大きく上に・ctx は 1 行で棒は右に半分幅）。共通部品 `components/Marquee.tsx`（items を 1 件ずつ流す→縦にめくる。1 件だけなら流し続ける。収まる項目は 4 秒見せる。触れる/押すで一時停止。reduced-motion は省略表示）。`HomeNoticeLine` は全通知を重要度順に回し、札は項目に合わせて切替・「n/total」表示・× は表示中の件。

## 2026-11-23 Home をモバイルで 1 画面に詰め、一番上に通知の 1 行（利用者「モバイルのホームは 1 画面で今の情報とニュースを」「最終的な通知を一行で、重要度で題名と色を変えて」）

- モバイル（768px 未満）: CPU+RAM・GPU+VRAM を各 1 枚（2 本の線・凡例「CPU 9%」）で横 1 段。電力の 4 項目を 1 行。LLM カードは見出しの行に「ローカル · モデル名/未ロード」「OpenRouter · モデル名」、前処理と生成・平均と今日を横並び。PC は従来のまま。
- 「履歴 15分・生データ・n 点」の行は PC・モバイルとも Home から外し、設定 → 表示「Home のグラフの履歴」へ（localStorage `control-deck-home-history-minutes`。以前は保存なしで毎回 15 分）。
- 通知の 1 行（`HomeNoticeLine`、backend `alerts/home_notice.py` の `GET /home-notices`・`POST /home-notices/dismiss`）: 監視の警報・失敗したアプリ・更新の失敗・承認待ちを集め、エラー > 警告 > 注意 > 情報 → 新しさの順の先頭 1 件。元が解決すれば消えて次が出る。「閉じる」は本人だけ（解決後は印を掃除）。お休みモードとは無関係。
- 試験: `backend/tests/test_home_notice.py`、E2E `frontend/e2e/home-compact.spec.ts`（390・320・1280、ライト・ダーク）。
## 2026-11-23 作業場へ渡す USB 機器（シリアル・adb）の第 1 段（設計は `docs/design-device-dev.md`）

- 利用者の要望: 「USB の装置（Android・M5Stack・ラズパイ）を OpenCode から開発できないか。作業場に環境を作ってよい。安全に」。
- `maintenance/devices.py`: /dev/serial/by-id と sysfs を読むだけの一覧（シリアルは ttyUSB*・ttyACM* の実体だけ、adb はインターフェース ff/42/01 とシリアルを持つ機器だけ）。起動のたびに実体へ解決し、無ければ渡さず警告。
- 作業場の meta `devices`（既定は空・箱の外・読み込み時に検査）。`podman_argv` が選んだ機器だけを `--device` と `--group-add keep-groups` で渡す。API: `GET /opencode/devices`・`PUT /opencode/workspaces/{name}/devices`（settings.manage）。監査 `opencode.workspace.device_attach/detach`。画面: 作業場の結果のカードの「USB 機器」（危険の説明つき）。
- テスト: `test_dev_workspace_devices.py`。実機の USB をつないだ起動は未確認（Opus が確かめる）。Wi-Fi・承認・ツールチェーンは第 2 段以降。
## 2026-11-23 ニュース: 記事の画像を取る・画像が無い記事はアクセントラインだけ・プリセット（200 件弱）・自分のリポジトリを出さない・チャンネルの語を変えたら古い検索の結果を出さない・ホームのカードにテロップとまとめの概要

- 利用者の要望: 画像の無い記事の大きな箱をやめ極力画像を取る／自分のリポジトリは出さない／取得の失敗の件数をニュースのページに出さない／分野ごとの無料のソースのプリセット（ローカル AI は厚く）／チャンネル 02 に前の語の検索結果が残る／ホームのニュースに流れるテロップ・まとめの概要・記事の数の設定。
- 画像: `app/news/images.py`。新着で画像の無い記事だけ、ページの og:image・twitter:image（RSS の media・enclosure・本文の img は従来どおり先に使う）。1 回 60 件・同時 4・1 件 8 秒・先頭 256KB・guarded_client（私的 IP は断る）・取れなくても `image_tried` で再試行しない。画像の無い・読めない記事は 5px のチャンネル色の線（5 つのデザインとホームのカード）。
- 自分のリポジトリ: 既定から外し、保存済みの設定は読むとき 1 回だけ移行（`migrated`）。`github:souten-yd/` の記事は一覧・ホーム・書き出し・チャンネル・まとめから除く（`store.visible`・`channels.matches`）。
- 取得の失敗の件数はニュースのページから消し、設定 → ニュース → ソースの「ソースの状態」へ。
- プリセット: `app/news/presets.py`（9 分野・実際に GET して読めたものだけ・確かめた日つき）。設定 → ニュース → ソースの「プリセット」。ソースの上限を 30 → 200。GitHub は API の上限（認証なし 1 時間 60 回）に当たったら releases.atom で取る。
- チャンネル: `matches` が Google ニュースの検索結果を無条件に通していたのをやめ、今の語が題・抜粋に無ければ出さない。チャンネルの保存で語・言語が変わったら、そのチャンネルの検索だけを裏で取り直す。
- ホームのカード: テロップ（流す項目の配列。今は先頭を流す仮の形で、共通の Marquee へ Opus がつなぐ）・まとめの概要（今日の「今日の 3 行」、無ければ昨日、古い形なら最初の 3 行）・記事 N 件（設定 → ニュース → 表示「ホームに出す記事の数」1〜10、既定 5、利用者ごと）。
- 試験: `backend/tests/test_news_images_presets.py`、E2E `frontend/e2e/news-sources-images.spec.ts`（書いただけ・未実行）。
## 2026-11-23 並列のスロット数に応じた役割分担（設計 `docs/design-parallel-roles.md` と第 1 段）
- 利用者「5 並列なら 1 本で会話を続け、残り 4 本で調査の要約など。並列 1 は従来どおり。OpenCode でも計画は大きいモデル・実装は Radiance の並列に」。
- 第 1 段（レビューで修正）: ページの読み取りは HTTP と reranker だけで LLM を使わないので、スロットと切り離して 4 本のまま（観点が並んでも総数 4）。スロットで決めるのは LLM を呼ぶ処理の同時数（`models_mgmt/parallel_slots.py`）。`investigate` が観点をまたいで並べ、`digest`（観点ごとの LLM 処理。接続は第 2 段）を n-1 本（上限 8）までに絞る。1 なら順に、Strata・Maya-S は 1、読めないローカルは 1、外部 API は 4。設定 → API キー → 調べる量の「LLM を同時に使う本数」（0 は自動、1〜8 は上書き）。
- 設計書に案 A（会話の LLM がルーター）・B（決まった割り当て、採用）・C（OpenCode の計画 → 並列実装 → 確認）の比較、第 2 段以降のファイルと測ること、載せ替えの費用の扱いを記した。試験 `tests/test_parallel_slots.py`。
## 2026-11-23 ニュースの画像生成に精度の段（小・中・大）: 設定で選べる

- 利用者の要望: SDXS の試し 16 枚（人物の崩れ・題に無い人物・文字のような模様・構図が単純）を見て「もうちょい精度の高いモデル使えないか？もう少し遅くても良い。このモデルを残しておいて、小中大みたいな感じで、選べるようにしてほしい、設定で」。
- 調べ・実測（CPU 16 スレッド・OpenVINO・torch 無し・512x288 / 1024x576・8 本の見出し）:
  | 段 | モデル | 解像度・ステップ | 1 枚目（読み込み込み）/ 2 枚目以降 | RAM の山 | 取得 | ライセンス |
  |---|---|---|---|---|---|---|
  | 小 | SDXS-512 DreamShaper + TAESD | 512・1 | 0.4 秒 / 0.23 秒 | 2.9GB | 約 1.7GB | OpenRAIL 系（既存） |
  | 中 | LCM-Dreamshaper v7 int8（OpenVINO/ 公式変換）+ 本物の VAE | 512x288・4（w=8 の埋め込み） | 2.0 秒 / 1.5 秒 | 5.1GB | 約 1.0GB | MIT 表記（元は CreativeML OpenRAIL-M） |
  | 大 | SDXL-Lightning 2 ステップ int8（rupeshs 変換）+ TAESD-XL | 1024x576・2 | 4.3 秒 / 3.8 秒 | 8.9GB | 約 4.2GB | OpenRAIL++（個人利用可） |
  選ばなかった候補: SDXL-Turbo int8（同じ SDXL 系で大と重複・ライセンスが非商用）、SD-Turbo（5GB・fp16 版のみで重い）、FLUX.1-schnell int4（9GB・T5 と 12B で CPU では 1 枚が分単位・別の台本が要る）、SANA-Sprint・FLUX.2 klein（専用の部品が要り、今の部品のままでは動かない）。
- 実装: `imagegen_worker.py` に `TIER_SPECS`（段ごとの部品・ステップ・解像度・ガイダンス・目安の時間/大きさ/RAM・時間の上限・1 回の件数の上限）と `_Pipeline`（SDXS 1 ステップ・LCM の手順〔LCMScheduler と同じ時刻・境界条件〕・SDXL の Euler trailing を自前で実装。diffusers・torch は使わない）。1 回の中でモデルは 1 度だけ読み込み、終わったら子プロセスを終える。seed は記事の id。
- プロンプト（全段）: CLIP は「no ○○」を理解せず書くと描くので、否定語をやめ、物・風景・静物の言い回し（concept illustration of objects and scenery, still life, wordless）にした。否定のプロンプト（`NEGATIVE`）はガイダンスのある段用に持つが、今の 3 段は蒸留でガイダンス無し（LCM は w の埋め込みで否定を受けない）なので使わない。
- 設定: `imagegen.tier`（small・medium・large。既定 small）。1 回の件数は設定の上限と段の上限（大 20・小中 30）の小さい方。未導入の段を選ぶと何も作らず、設定の画面と API（`imagegen_available`）で導入へ案内する。
- 機能と拡張: 「ニュースの画像生成（CPU）」の詳細に段ごとの導入・削除（`POST /features/news-imagegen/models/{tier}/install-jobs`・`DELETE .../models/{tier}`。小は機能のアンインストールでだけ消える）。モデルは版固定（Hugging Face の公式のみ）で `data/models/news-imagegen/<名前>/`。
- テスト: `test_news_imagegen.py`（段の選択・未導入・台本の引数・段ごとの削除・設定の既定・LCM の時刻。子はモック）、E2E `settings-news-imagegen-tiers.spec.ts`（書いただけ・未実行）。比較の見本: scratchpad の `imgtiers/compare.png`。

## 2026-11-23 ニュースの画像生成（CPU）: 画像の無い記事に、ニュース専用の軽いモデルでイメージ画像を作る

- 利用者の要望: 「画像が取れない記事は本文の意図を汲んで AI で生成…CPU と RAM だけ・時間がかかってもよい・作ったらすぐアンロード・連続は一気に順次・品質は 2 の次・メディアフォージを使わずニュース独自の生成 AI・容量は軽く・速度が重要」。
- 調べ・実測（CPU 16 スレッド）: SDXS-512 を OpenVINO（CPU）で 1 ステップ・TAESD 復号。DreamShaper 版は 1 枚目 0.4 秒（読み込み 0.4 秒）・2 枚目以降 0.25 秒・RAM の山 2.8GB・ディスク約 1.7GB。SDXS-0.9 版は 0.3 秒・4.2GB・2.7GB。スレッド 2/4/8 で 0.63/0.41/0.29 秒。DreamShaper を採用（小さく速く絵も良い）。ライセンスは openrail++（上流 IDKiro、TAESD は MIT。変換版は第三者 XXX2333 のもので出どころは版固定のみ）。
- 実装: 機能「ニュースの画像生成（CPU）」（`registry.py` の python-lib。専用 venv に openvino・tokenizers・numpy・pillow・huggingface_hub を版固定。導入ボタンでモデルも `data/models/news-imagegen` へ取得、削除でモデルも消す）。台本 `news/imagegen_worker.py`（子プロセス。モデルを 1 回読み、渡された記事を順に生成し、終わったら終了＝アンロード）、`news/imagegen.py`（LLM を使わないプロンプト・nice・スレッド/件数/時間の上限・id だけでパスを組む配信と削除）。取得のあと `service._after_save` から呼ぶ。
- 画面・API: `GET /news/images/{id}.webp`（ログイン必須・その人に見える生成画像だけ）。記事に `image_ai`、カードに「AI 生成のイメージ」。設定 → ニュースに入切（既定は切）・スレッド数（既定 8）・1 回の件数（既定 30）、未導入なら案内。古い記事を消すと画像も消える。
- 限界: SDXS はガイダンス無し（CFG 0）なので否定プロンプトが効かず、文字のようなものが絵に出ることがある（肯定文の言い回しで避けるのみ）。
- テスト: `test_news_imagegen.py`（子はモック）。実際の生成は専用 venv で 5 枚実測。

## 2026-11-23 ニュース: Release の機能を切った Codeberg のリポジトリ（Radiance）はタグから更新を読む（本番で HTTP 404 だった）。
## 2026-11-23 ニュースの「意味で探す」に並べ替え（reranker）を通し、見出しが同じ記事を 1 件に（本番で、ぴったりの記事が 4 位以下・同じ記事が 2 件出ていた）。日本語の問いから英語の記事を探す件は U35。

## 2026-11-23 機能と拡張に「Playwright のブラウザ」と、追加で入れるもの（apt・Maya の tokenizers）の項目

- 事故: Playwright は 3 つの版が同じ置き場 `data/cache/ms-playwright` を使っていた（E2E の @playwright/test 1.61・本体の venv の playwright 1.62・Playwright MCP の playwright-core 1.66）。`playwright install` は自分の版が知らない版のブラウザを片付けるので、2026-11-23 に 1.66 で `install ffmpeg` したら E2E の chromium-1228 などが消えた。
- 置き場を分けた: E2E は `ms-playwright`（従来どおり。箱・deck.sh e2e の指定は不変）、本体の Python（Workflow のブラウザ操作）は `ms-playwright-py`、Playwright MCP（録画用 ffmpeg だけ）は `ms-playwright-mcp`。MCP の設定に `environment.PLAYWRIGHT_BROWSERS_PATH` を足し、箱へは読み取り専用で見せる（`sandbox._tool_dirs`・provider の readonly）。既存の置き場の中身は消さず・移さない（新しい置き場へは導入ボタンで入れる）。
- 項目 `playwright-browsers`（分類「検索と収集」、`features/playwright_browsers.py`）: 状態＝各置き場の中身と、各版の browsers.json が求める revision の比較（「足りない: …」）。導入・更新＝足りない分だけを版ごとに argv（shell なし）で `install`。MCP が未導入なら飛ばす。外部取得なので利用者のボタンだけ（自動更新の対象外）。アンインストールは無し。
- `./deck.sh e2e` は E2E のブラウザが足りないとき、勝手に入れず「設定 → 機能と拡張 → Playwright のブラウザ を導入してください」で止まる。`ensure_playwright_browser` は `ms-playwright-py` へ入れる。
- 追加で入れるもの（利用者「追加で入れないといけないものは機能と拡張に導入ボタンを」）: OS のパッケージ（poppler・tesseract=`system-ocr`、`system-zstd`、`system-gh`）は sudo が要るのでボタンでは入れず、足りないものだけの `sudo apt install -y …` をコピーさせる（`features/system_packages.py`）。gh も案内だけにした（Release の tar の版固定・検証・ログインまで要り、deck.sh の apt と二重になるため小さい方）。Maya の tokenizers は Maya-S の更新・準備の中でエンジンの venv へ入れる。各エラーの文は「設定 → 機能と拡張 → <項目名> から導入してください」に直した。
- テスト: `test_playwright_browsers_feature.py`、E2E `settings-playwright-browsers.spec.ts`（API は page.route）。実際の `playwright install`・apt・pip は走らせていない。
## 2026-11-23 ニュースのまとめに有料のモデルも選べる形へ戻す（利用者「有料モデルをニュースに使っても良い。わざわざ消さないで」）。有料は「有料」の警告つきで選べる。

## 2026-11-23 画面の撮影・操作テストの動画をチャットに出し、個別・一斉に消せる（ROADMAP U34・U28）

- 撮る: 作業場の ui-verifier の Playwright MCP に `--caps=devtools`（`browser_start_video` / `browser_stop_video` / `browser_video_chapter`）を足し、出力先を `repo/.controldeck/media` にした（`sandbox.opencode_config`。中に `.gitignore`（`*`）を置き、自動保存・差分・関門に混ざらない）。ui-verifier の指示に操作テストの録画の手順を、主の指示（`agent-notes.md`）に「画面を変えたら ui-verifier に頼める」を足した。
- 一覧と配信（`maintenance/workspace_media.py`）: `.controldeck/media` と `test-results` の画像（png・jpg・webp）・動画（webm・mp4）。箱の AI が書ける場所なので、各段を dir_fd・O_NOFOLLOW で開き、symlink は一覧にも配信にも削除にも使わない。パスは一覧に出た相対パスだけ（`..`・絶対パス・置き場の外・他の拡張子は 404）。300MB 超は返さない。content-type は拡張子で決め、`nosniff`・`Content-Security-Policy: sandbox`・Range 対応。
- API（持ち主だけ・terminal.use）: `GET /opencode/workspaces/{name}/media`・`.../media/file?path=`、`DELETE` の 1 件・作業場ぶん、`GET/DELETE /opencode/workspaces-media`（自分の全作業場）。削除はホストで dir_fd の unlink。監査 `workspace.media.delete`（件数と大きさ）。設定 `workspace_media_auto_delete`（既定は切）がオンなら、取り込み（PR）・マージの後に自動で消す。
- チャット: ターンの終わり（`autosave_turn`）で、ターンの始めより新しい記録を最大 12 件 `meta.media` に。完了カードの下に縮小画像（押すと大きく）と `<video controls preload="metadata">`。消された記録は「消しました」。
- 消す画面: 作業場の結果のカードの「画面の記録」（一覧・1 件ずつ・この作業場の全部を確認つき）と、設定 → 開発（作業場）の「画面の記録の容量」（合計・作業場ごと・一斉削除を確認つき・自動で消す設定）。
- テスト: `test_workspace_media.py`（一覧・symlink・`..`・上限・Range・削除・持ち主・自動削除・meta.media・config）、`test_opencode_dev_tools.py`（caps）、E2E `assistant-workspace-media.spec.ts`（320・1280）。箱の中での実際の録画は未確認（本番で確かめる）。
## 2026-11-23 ニュースの表示のデザインを 5 つに（マガジン・新聞・経済紙・テキスト一覧を追加）

- 放送局（既定）は据え置き。The Verge・日経・FT の色・HN の型を参考に、色だけでなく並びも変えた（`DesignHomes.tsx`。共通のデータ・部品、並べ方だけ別）。旧「紙面」「コンパクト」は経済紙・テキスト一覧へ移し替え（backend の正規化と frontend の両方）。設定に見本の縮小図つきの選択。
- 確認: 型検査・build・news の E2E 17 件・backend の news テスト 72 件。撮影は frontend/test-results/news-theme-*.png。
- ホームのニュースのカードを作り直した（`NewsHomeCard.tsx`）: 先頭に今日の日刊のまとめの最初の 1〜2 文（無ければ出さない）・おすすめ 1 位を画像つきの小さなカード・残り 4 件はチャンネル色の点つきの見出し。利用者の表示のデザイン（`/news/prefs`）の色・書体に合わせ、テキスト一覧は画像なしの番号つき。`/news/items` の各記事に所属チャンネルの id を付けた。

## 2026-11-23 ニュースの 2 段目（日刊・週刊のまとめ・別 DB・RAG・LLM の個別設定）

- ニュースの表を本体の DB（Alembic の移行は削除）から `data_dir/news/news.sqlite3` へ分けた（`news/db.py`）。取った記事は知識基盤の分野 `news`（public）へ取り込み、横断検索・「意味で探す」に使う。本文の取り込み・古い記事を消すときの RAG の扱いは設定。
- まとめ・一言要約の LLM を用途ごとに選べる（ローカル／OpenRouter。無料を上に印つき、有料は警告）。外部送信は呼び出し元 `news` の許可がある時だけ。許可が無いときはまとめを作らず理由を残す。LLM 失敗時は印つきの見出しの一覧。更新は取得の間隔・ホームのフィード・日刊・週刊・取得しない時間帯を個別に。通知 `news_digest`。
- テスト: `test_news_rag_llm.py`（新規）ほか。E2E: `news.spec.ts`（まとめ・設定・意味で探す。撮影は `frontend/test-results/news-digest-*.png` ほか）。詳細は `docs/design-news-channel.md` 2b。
## 2026-11-23 会話モードの二重読み上げと、Web 調査で読むページ数

- 読み上げ（利用者「会話モードにしたら、TTS が 2 回くらい同じ文字を読んだ」）: `speakable.nextSpoken` は、回した文（queued）が今の文の頭と食い違うと「食い違った所から」読み直していた。生成中と確定で文が変わると（出典の番号・空白や記号・本文に出た `<tool_call>` の文字の除去・`strip()`・再接続の snapshot）、先頭近くの違いで読んだ文をもう一度読む。直し方: 文字と数字だけにして比べ、回した分の終わり（24・12・6 文字）が今の文のどこにあるかを探して、その続きから読む。見つからないとき（丸ごと差し替え）だけ食い違った所から読む。`<tool_call>` は読み上げから除く。テスト: `assistant-voice-modes.spec.ts`（出典の番号・空白・道具の呼び出し・丸ごと差し替え・文の途中・細かい刻み）。実際の会話で文がどこで変わったかの記録は無く、原因は経路の読みと再現からの推定。
- Web 調査の件数（利用者「3 件くらいしか文献を調査していない。エビデンスを増やせるか」）: `READ_PER_ASPECT = 3` で深さに関係なく観点ごとに 3 件しか読んでいなかった。今は設定の「観点ごとに読むページ数」（2〜10・既定 5）を「ふつう」とし、速いは 2 少なく・深いは 3 多く（既定で 3・5・8）。時事（`freshness == "latest"`・`date_sensitive`）は最低 5 件。同時に読む数は 3 から 4。「出典の上限」（5〜30・既定 20）を超えたら期間に合うものから残す。設定は 設定 → API キー →「調べる量」（`research_keys.limits`・`data/research/keys.json`）。
- 調べる時間: 調査の進み具合（`meta.progress`）の各段に `elapsed`（調べ始めてからの秒）を足した。
- テスト: `test_research_tools.py`（深さ・設定・時事の下限・出典の上限・設定の範囲）。
## 2026-11-23 AI アシスタントの会話をまたぐ記憶（守りを中心に）

- 利用者「会話の永続記録は？有用なら採用。設定で選べるように。保護された会話・特許の記憶は検索できる会話に露呈しないように」。設計と比較は `docs/design-chat-memory.md`。
- 置き場: `data_dir/memory/memory.sqlite3`（本体 DB と別。本文は暗号化）。`app/memory/`（store・policy・service・router）。API `/api/v1/memory/*`。
- 守り: protected（秘密の会話・特許の流れ・「守る」）は秘密の会話の中でだけ入る／外部の LLM・OpenCode には既定で入れず、設定でも normal だけ／答えを作る段にだけ入れ、検索語の段と道具の引数には渡さない（写されたら道具の前で止める）／取り出しは必ずローカルの LLM（無ければ作らない）。
- 画面: 設定「AI の記憶」（設定・一覧・編集・守る・削除・全消し・候補の承認）、答えの下の「記憶を n 件使いました」、AI 設定の会話ごとの切り替え、会話の削除時の「記憶も消す」。
- テスト: `test_chat_memory.py`、E2E `chat-memory.spec.ts`（API は page.route）。未検証: 本物のローカル LLM での取り出しの質・Patent 画面から `patent_case_id` を付ける導線（API だけ）。

## 2026-11-23 event loop が数秒止まる原因（同期処理）を loop の外へ

- 診断（直近 24 時間）で、loop のスレッド上の重い同期処理が数秒ずつ画面を止めていた。①Add-on の health poll（`registry.list_addons` → `_load_path` が manifest を毎回 JSON 読み・pydantic 検証、最長 6.1 秒）②`recover_paused_workflows_once`（1 秒ごとの SQLAlchemy クエリ）③`runtime_environment`（`ldconfig`・`systemctl` の subprocess、`httpx.AsyncClient` 作成時の SSL コンテキスト生成、`data_dir` の mkdir）④llama の `runtime_status`（systemctl）と providers の `_candidates`・probe。
- 直し方: manifest は inode・mtime・size が同じ間は前回の検証結果を使い回す。health の poll・再確認、pause の回復、`runtime_environment`、`/llama/status`、providers の候補は `asyncio.to_thread` へ。httpx の SSL コンテキストは `app/http_tls.py` で 1 つを使い回し、起動時に thread で温める。振る舞いは変えていない。
- 直さなかったもの: `chat_persist.on_status`（メモリ上の `job.log` だけで同期 I/O は無く、機械の負荷で出た見かけ）・`compression`・`swap_scheduler.acquire`（待っているだけ）。
- テスト: `test_event_loop_offload.py`（重い関数を遅くしても loop の tick が進む・manifest キャッシュ・SSL コンテキストの再利用）。
## 2026-11-23 評価で見つかった AI 開発環境の不具合 5 件（ROADMAP U22〜U26）

- 作業場の欄の「未保存の変更あり」が古いまま: 一覧（`/opencode/workspaces`）は AI アシスタントで 30 秒キャッシュされ、ターンの終わりの自動保存のあとに取り直していなかった。さらに自動保存がコミットしたあとも、コミット前に覚えた probe（dirty）が 30 秒残っていた。回が終わったら一覧を invalidate し、`workspaces._autosave_commit` が probe の覚えを捨てる。
- AI が自分でコミットして clean のターンに「この時点に戻す」が付かなかった: `autosave_turn` がターンの始めの HEAD を覚え（`_box_head` 1 回）、終わりが clean で HEAD が動いていれば、その HEAD を `meta.checkpoint` にする（箱への問い合わせは 1 ターン 2 回まで）。`workspaces.checkpoints` は起点からの全コミットを返すので戻す側は変えていない。
- 見た目の関門（`visual_review.py`）: 画像を断る（400「does not read images」など）モデルは飛ばして、読める登録済みの次のモデルで見る。無ければ「画像を読めるモデルがありません（例: Inkling は Vision の部品をモデルの隣に置くと読めます）」で未実施（不合格にしない）。見たモデルと切り替え元を結果に出す。
- ヘッダの札: 作業場を選んだ OpenCode は作業場のモデルの並び（`/opencode/workspaces` の各作業場に `model_chain` を追加）の先頭を「GLM-5.3（作業場）」と出す。
- プランの回（変更ファイル 0）の完了カードに「レビューして」を出さない。
- テスト: `test_dev_workspace_persist.py`・`test_opencode_workspace_persist.py`・`test_visual_review.py`、E2E `assistant-workspace-header-and-plan-card.spec.ts`（書いただけ。未実行）。
## 2026-11-23 ニュースチャンネル（1.5 段目: 放送局のデザイン・チャンネル＝検索テーマ・詳細な設定）

- 利用者「https://www.ozy.com/ こういうページのデザインを参考に。設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」。設計は `docs/design-news-channel.md`。OZY のロゴ・文言・画像は使わず、色・書体の雰囲気・構成の型（モニターの枠の ON AIR・CH 切り替え・テロップ・「CH 0N · 名前」の札・3 列カード・大きな数字）だけを取り入れた。
- チャンネル＝検索テーマ（`app/news/channels.py`、設定は `data_dir/news/channels.json`）: 名前・色・含める語・外す語・ソースの絞り込み（記事の種類）・言語・件数・検索するか。並び順＝CH 番号。既定は ローカル LLM／AMD・GPU／特許／開発ツール／使っているソフトの更新（kind=release）。所属は問い合わせ時の判定（外す語 → 種類 → 検索で取った記事（source_key `search:<id>:…`）→ 含める語）。
- 検索の取得（`sources.search_sources`）: Google ニュースの検索 RSS（日本語 `hl=ja&gl=JP`・英語 `hl=en-US&gl=US`。配信元を題の末尾から分ける。既存の `http_get`＝安全な出口）・HN の Algolia の `search_by_date`・設定済み Discourse の `/search.json`・Reddit の `/search`（キーがあるときだけ）。日本語だけのチャンネルは HN・Reddit を引かない。取得の間隔とアーカイブの残す件数も全体の設定（`channels.json`）。
- API: `GET/PUT /news/channels`（PUT は settings.manage）、`GET /news/items?channel=`、`GET /news/home`（ON AIR・テロップ・NOW/TODAY/THIS WEEK・チャンネルごとの上位・興味・話題の動き・数字を 1 回で）、`GET/PUT /news/prefs`（利用者ごと: 表示のデザインと好み）、`GET /news/export?format=json|csv&channel=`（CSV は式の無害化と BOM）。
- 好み（`prefs.py`）: おすすめの強さ（0＝新しさだけ、0.5＝従来、1＝興味を 2 倍）・半減の日数・チャットの話題を使うか・ミュートの語とソース（一覧・ホームに効く。検索では探せる）・既読を薄く。
- 画面（`pages/News.tsx`・`features/news/`・`styles/news.css`）: 放送（ON AIR のモニター＋CH 切り替え・テロップ（止められる）・NOW/TODAY/THIS WEEK・チャンネルの区画・興味と話題の動き・数字・アーカイブへ）／新着（チャンネル・種類で絞る）／アーカイブ。デザインは 放送局・紙面・コンパクト（CSS の変数で切り替え。外部フォントなし）。携帯は 1 列でモニターが先頭。動きを減らす設定と OS の設定でテロップ・点滅が止まる。
- 設定: 設定に「ニュース」の節（チャンネル・ソース（設定 → 表示から移動）・表示のデザイン・好み・アーカイブと書き出し）。ニュースのページの「⚙ 設定」から開ける。
- テスト: `test_news_channels.py`（所属判定・外す語・RSS 整形・検索のソース・/home の形・設定の検査・好みの強さ・ミュート・書き出し）、E2E `news.spec.ts`（放送局の各区画・CH 切り替え・デザイン変更・チャンネル追加・携帯 390/320・撮影）。

## 2026-11-23 ニュースの専門ページ（1 段目）

- 利用者「ニュース機能もつけて…私専門のニュースチャンネルを開設して。よく開くニュースとか傾向を把握してカスタマイズして。グラフィカルな感じで。検索機能が不足であれば拡張して」「有料は使わない」「ホームのニュースフィードとは別に専門ページ。過去のニュースはアーカイブにして、いつでも閲覧できるように」。画面は `/news`（メニューの新しいまとまり「情報」）、ホームに「ニュース」の小さなカード（おすすめ上位 5 件）、設定 → 表示に「ニュースのソース」。
- DB（Alembic `a3c5e7f9b1d2`）: `news_items`（kind・source・source_key・title・url 一意・summary・image_url・published_at・fetched_at・topics・user_id＝event の宛先だけ）、`news_actions`（open・like・dislike・hide・star。1 人・1 記事・1 種類で 1 行）、SQLite の FTS5（trigram）の仮想表とトリガー（`app/news/fts.py`。日本語は 3 文字以上。短い語・FTS5 が無い DB は LIKE）。
- 取得（`app/news/`）: ソースは `data_dir/news/sources.json`（既定: GitHub Release 4・Codeberg radiance・GIGAZINE と Phoronix の RSS・Hacker News 上位・Hugging Face の話題の GGUF（90 日以内）・Discourse 2 つ・r/LocalLLaMA（Reddit のキーがあるときだけ））。通信は既存の `research_tools`（guarded_client）・`knowledge/sources.http_get`・`hf_search` を使い回し、新しい生の httpx は無い。30 分ごとに見張りが取り、ソースごとに 45 秒で打ち切り・失敗は記録して他を止めない。同じ url は更新だけ（published_at は動かさない）、同時に 1 回だけ（`POST /news/refresh` は 30 秒の間隔）、外から来た文は平文・url と画像は http/https だけ。
- 出来事: `notify_event` が、種類の有効・お休みに関係なく宛先の人ごとに kind=event の行を残す（伏せた文のまま。秘密の会話は「秘密の会話」）。その人にしか見えない。
- 保存期間: 消さない。50,000 件を超えたら古い順（star は残す）。
- 好み: 開いた・👍・☆ は正、👎・隠すは負（30 日で半減）、チャットの題（直近 30 日・秘密の会話を除く・本人のもの）を少し足す。おすすめの点 = 3 日で半減する新しさ ×（1 + 興味）。語ごとに外せる（`muted.json`）。`GET /news/profile` は画面の「あなたの興味」。
- テスト: `test_news.py`（ソースの整形・設定の検査・失敗の分離・event の宛先・好みの重み・アーカイブ／検索／trends・50,000 件の切り捨て・移行の up/down）、E2E `news.spec.ts`（書いただけ。未実行）。2 段目（日刊・週刊の AI のまとめ、Bluesky・Mastodon）は未着手。

## 2026-11-23 調査の道具に Reddit の API・Discourse・Stack Exchange の入口

- 利用者「Reddit の API を登録するところを作って、入っていたらそれを使って検索できるように。XDA など技術的に有望なフォーラムも検索したい」。設定 → API キーに群「調査（フォーラム・検索）」（Reddit の Client ID・Secret・ユーザー名、Stack Exchange のキー（任意）、足す Discourse のドメイン）。キーは `workflows/research_keys.py` が暗号化して `data/research/keys.json` に置く（値は応答・ログ・監査に出さない）。
- `research_tools`: Reddit はキーがあれば OAuth（application-only、トークンは期限の 60 秒前まで使い回す）を先に使い、失敗・キーなしは今の経路（未認証 → RSS → SearXNG）。Discourse 系（既定 10 件＋足したドメイン）は `search.json`・`/t/{id}.json`、Stack Exchange は search/advanced・質問と回答（票順）。XDA など API の無いサイトは今までどおり SearXNG の `site:`。
- 「つながりを確かめる」（`POST /settings/keys/{id}/test`）: Reddit はトークンと検索 1 件（401・403「承認が要る可能性」・429 を平易に）、Stack Exchange は残りの回数。
- テスト: `test_research_forums.py`（HTTP はモック）、E2E `settings-keys-research.spec.ts`（書いただけ。未実行）。詳細は `HANDOFF-web-collector.md`。

## 2026-11-23 Strata で Inkling 2 Mini を登録できるように

- 利用者「Strata や Radiance で inkling がサポートされているか最新の情報を確認して、サポートされていれば候補として追加できるように」。
- 調べた結果（2026-11-23）: **Strata** は 0.2.19（11/22）から Thinking Machines の Inkling 2 Mini（総 26.4B・活性 3.4B）に専用エンジン `strata-inkling` で対応（R9700 で 99〜106 tok/s と公称）。手元は 0.2.20。系列名 `inkling-2-mini`、量子化は Strata の SQ-20（劣化なし 19.3GB）・SQ-15・SQ-10 と Unsloth の UD-*（0.2.20 で GGUF を直接）。`strata launch --family inkling-2-mini --model sq-20 --plan` で確かめた。**Radiance** は対応の PR（arch/inkling2、11/22）が Codeberg に出ているだけで、Release（手元 2.2.0）には未。**llama.cpp** は b12600 で対応（手元の b12388 は未対応）。
- `strata_gateway._FAMILIES` に `inkling-2-mini`（sq-20・sq-15・sq-10・ud-q8_k_xl・ud-q4_k_xl・ud-iq4_xs）。Qwen とは別のエンジンなので `--ple-io`・`--kv-resident` と思考の設定を渡さない（strata-inkling は `--set` をそのままエンジンの引数へ足す。Strata の `inkling_kind.py`）。思考を持たない（`HAS_THINKING=False`）ので思考の既定は断り、起動後の `/settings` も送らない。投機デコードは自動（内蔵 DSpark）か使わないだけ。
- 画面: Strata の登録の系列に「Inkling 2 Mini（Thinking Machines）」。選ぶと思考の欄・KV の VRAM 保持長・RAM 余裕を隠す。HF の検索で Inkling の置き場は inkling-2-mini 系列として出す。
- テスト: `test_strata_gateway.py`、`test_hf_search.py`、E2E `strata-memory-settings.spec.ts`。実際の起動（エンジン約 94MB とモデル約 20GB の取得）は利用者の起動操作まで行っていない。

## 2026-11-23 メニューの「メイン」をなくす

- 利用者「メインメニューのファイルズとリモートを管理に回して、メインという謎の項目を消して」。`navigation.tsx` のまとまり「メイン」を消し、Remote・Files を「管理」へ。Home・Apps は既定で「よく使う」に固定されていて、外したときは「管理」に出る。並びは変えない。
- テスト: E2E `navigation-layout.spec.ts`（「メイン」が無く Files が「管理」の下）。
## 2026-11-23 設定 → 表示でメニューの項目ごとに表示・非表示

- 設定 → 表示に「メニューの表示」。サイドバー・ドロワーの項目を 1 つずつスイッチで隠せる（端末ごと、鍵 `cd-navigation-hidden-v1`、既定は全部表示）。上の「すべて表示に戻す」で戻る。
- 項目の一覧は `useNavigationCandidates()`（`navigationEntries.tsx`）へ 1 か所に切り出し、AppLayout と設定の欄が同じものを使う。鍵は NavEntry.key（本体は行き先のパス、Add-on は `addon-<id>-<cid>`、プラグインは `plugin-<id>`）。新しい機能・Add-on・プラグインはこの一覧に出れば自動で選べ、初めから表示される。
- 隠した項目は、まとまりからも「よく使う」からも外れる（空になったまとまりは見出しごと消える）。Settings は隠せない。検索（Ctrl K）など、メニュー以外の入口は変えていない。「よく使う」の候補には隠した項目に「（非表示）」と出る。
- 何も隠していないときの AppLayout の組み立ては前と同じ（まとまりの並び・拡張機能の位置・「よく使う」の重複排除）。
- テスト: E2E `navigation-visibility.spec.ts`（Patent・Files・Add-on を隠す／よく使うからも消える／Settings は無効／戻す／再読み込みで残る）。

## 2026-11-23 AssistantChat.tsx を分ける（ROADMAP Q1 の画面側）

- `AssistantChat.tsx`（3,239 行）から、発言の表示の部品（`GenStatsBadge`・`ThinkingBlock`・`CodeTimeline`・`RoleModels`・`MessageBubble`・`UnsettledNotice`・`QualityBadge`・`SpeakerToggle` ほか）を `ChatMessage.tsx` へ、コンポーネントの外の補助（`uploadWithProgress`・`extendTimeline`・`settledStatus`・`restoreMessage`・`readHistoryCollapsed`・`loadSettings`・`MODES`・`Msg` などの型）を `chatState.ts` へ移した。本体は 2,381 行。
- 移しただけで、振る舞い・DOM・文言は変えていない。import は ChatMessage → chatState、AssistantChat → 両方で、循環は無い。`MarkdownMessage` の遅延読み込みは使う側（ChatMessage）へ。外から AssistantChat を import するのは default export のみで変更なし。
- ROADMAP Q1 は backend 側（chat_persist.py）が残るので ☐ のまま。
## 2026-11-23 chat_persist.py を分ける（ROADMAP Q1 の backend 側・途中）

- `backend/app/workflows/chat_persist.py` から、添付（`_archive_path`〜`get_attachment`）を `chat_attachments.py`、送信待ちの要望（`_queue_out`〜`dispatch_queue_now`）を `chat_queue.py` へ移した。移しただけで、URL・応答・ルートの数と順序は変えていない（ルート一覧と OpenAPI を分ける前後で比べて同じ）。
- 各モジュールは自分の `APIRouter` を持ち、chat_persist の router が元の位置で `include_router` する（prefix `/chat`・tags `chat-persist` は chat_persist が付ける）。元の名前は chat_persist からも使える（再 export）。
- chat_queue は chat_persist に残る関数（`_start_send`・`opencode_image_problem` など）を `cp.<名前>` で実行時に参照する。chat_attachments の文書登録は chat_persist の `register_chat_document` を実行時に引く（テストの差し替えが効く）。
- テスト: 件数は分ける前と同じ（2421 件通過・3 件スキップ）。`test_knowledge_rebuild.py` の `_collection_embed_override` の差し替え先を chat_attachments へ直した。

## 2026-11-23 作業場を箱の中で動かして preview（U12）

- Project Lab の「作業場」に「動かす」欄。箱（podman）の中でアプリを動かし、待ち受けたら preview（iframe・別タブ）を出す。
- `sandbox.lab_start/lab_stop/lab_state`（ControlDeck の複製と同じ並び・同じ公開ポート `box.port`）。command は箱の bash へ位置引数（`$1`）で渡し、`{port}`・`{host}`（0.0.0.0）・`{base}` だけを置き換える。複製が動いているときは断る。ログは `<箱>/lab/run.log`（開始ごとに作り直す）。
- API: `GET /opencode/workspaces/{name}/lab`、`POST .../lab/run`、`POST .../lab/stop`（terminal.use・持ち主だけ・監査 `workspace.lab.run`）。
- 中継: `/project-view/w/{name}/{token}/...`（HTTP・WebSocket）。鍵は起こすたびに変わり、鍵違い 404・停止中 409・待ち受け前 425。Cookie・認証は上流へ渡さず、応答に CSP `sandbox`（allow-same-origin なし）を付けるので、別タブで開いても箱の中のアプリは ControlDeck の API に触れない。`_proxy_http`・WebSocket の中継は送り先のポートを受ける形にした（run の入口の振る舞いは同じ）。
- テスト: `test_workspace_lab.py`、E2E `project-lab-workspaces.spec.ts`。実際の箱での通しは未。
- Opus の監査で直した点: lab のログ（箱の AI が書ける `lab/run.log`）をホストが空にし・読んでいた。symlink にされるとホストのファイルを空にされ・読まされる。空にするのは箱の中で、読むのは `_box_file_tail`（フォルダもファイルも O_NOFOLLOW・ふつうのファイルだけ）。前からあったミラーの server.log の読み方も同じに。

## 2026-11-23 取り込みの PR の題（U18）

- 前は最後のコミットの件名を PR の題にしていたので、ターンの終わりの自動保存（「作業途中の保存 #N（会話 … のターン）」）が題になっていた。
- `workspaces.suggest_title`: 差分の起点（`change_base`）から head までの、自動保存・マージ以外のコミットの件名（新しいもの）→ 自動保存に記録された会話の題 → 「作業場 <名前> からの変更」の順。1 行に畳んで 120 字。`changes` の `suggested_title` で画面へ。
- 取り込みの確認（本体の PR）に「PR の題」の欄。候補が入っていて書き換えられる。
- テスト: `test_dev_workspace_adopt.py`（自動保存を飛ばす・会話の題・既定）、E2E `assistant-dev-workspace-result.spec.ts`。
## 2026-11-23 作業場の関門を信頼できる版のコードで動かす（U21）

- 前は本体の作業場の関門（dev_check）を箱の中のコピーで動かしていたので、箱の AI が関門の仕組みそのものを書き換えて「合格」にできた（守りはマージの L2 の警告だけ）。
- `workspaces.trusted_gate_code()`: ホストの本体の `origin/main` から `backend/app` だけを `git archive` で取り出し（`data/gate-code/<sha>`、新しい 3 版を残す）、関門の実行に読み取り専用で見せる。`python -s -P` で箱の backend と HOME の user site を import に入れない。調べる対象は `CONTROL_DECK_DEV_CHECK_REPO`（箱の作業ツリー）。
- 作業場のテスト・ビルドは作業場のもの（子へは関門の PYTHONPATH を渡さない）。テストの書き換えは、これまでどおり L2 の守る場所の警告が見る。
- 手元で、取り出した版から作業ツリーの差分を調べられることを確かめた。実際の箱（podman）での通しは未。
- テスト: `test_dev_workspace_adopt.py`（読み取り専用・環境・取り出し）、`test_dev_check.py`（子の環境）。

## 2026-11-23 お休み中は緊急の警報も送らない（既定）

- 利用者「お休み中の通知は届かないようにしてほしい」。お休みモードの「緊急の監視の警報はお休み中も送る」の既定を無効にした（`events.QUIET_DEFAULT`）。有効にすれば今までどおり通る。本番の souten の設定は先に無効へ書き換えた。
- お休みモードを通らない Push は設定画面の「試しに送る」（利用者が押したときだけ）のみであることを確かめた。
## 2026-11-23 設定に「開発（作業場）」の節（U10）

- 設定 → 開発（作業場）（`/settings/dev`、節の id は `#dev-workspace`、`DevWorkspaceSettings.tsx`）。新しい設定値は持たず、既存の API をまとめた。
- 編集できる: 隔離（`isolation`）・自動レビュー（`auto_review`）= `/opencode/workflow`、AI アシスタントの権限モード = `/chat/opencode/permission-mode`、ディスクの空きの下限（`workspace_min_free_gib`）= `/opencode/settings`。OpenCode 画面と同じ値。
- 要約とリンク: 役割とモデル（指定のある役割を一覧し、編集は OpenCode 画面へ）・開発対象と取り込み先（本体は PR、ほかは元のリポジトリへブランチ）・関門（dev-check）の説明・自動保存（ターンの終わりごと・常に有効。ディスクの下限との関係を説明）。
- テスト: E2E `dev-workspace-settings.spec.ts`（320・1280）。
## 2026-11-23 自動保存の時点へ戻す（U15）

- 自動保存（1 ターンの終わりのコミット）の時点へ、コード・会話・両方を戻せる（Claude Code の /rewind と同じ考え）。保存した回の発言の「⋯」に「この時点に戻す」（`RestoreDialog.tsx`: コードだけ / 会話だけ（既存の分岐）/ 両方。両方はコードが戻せてから分岐）。作業場の結果のカードに「保存の時点」の一覧（初め 5 件・もっと見る。「戻す前」の行は「戻す前に戻す」）。
- backend（`workspaces.py`）: `autosave` が `sha` を返す。`checkpoints(box)`（起点の revision から HEAD まで最大 50 と過去の「戻す前」）・`restore(box, sha, username)`・`take_restore_note(box)`。戻す前に未保存を先に保存し（保存できず変更が残るなら戻さない）、戻せるのは HEAD の祖先で起点の子孫の保存と過去の「戻す前」だけ（sha は 40 桁の 16 進）。今の HEAD は `refs/controldeck/before-restore/<n>` に残し、メタの `restores`（最大 20）に記録。`git clean` はしない。自動保存と同じロックで直列化。
- API: `GET /opencode/workspaces/{name}/checkpoints`、`POST /opencode/workspaces/{name}/restore`（body `{sha}`。持ち主だけ・その作業場で生成中の会話があれば 409・audit `workspace.restore`）。
- AI へ伝える経路: メタの `restore_note` に 1 文を入れ、次の OpenCode のターンの先頭（「## コードを戻しました」）へ一度だけ付ける（`chat_persist.py`。画面の利用者の発言は変えない）。保存した回の発言の meta に `checkpoint {sha, count, workspace}` を記録する。
- テスト: `test_dev_workspace_persist.py`（戻す・やり直し・拒否・API）、`test_opencode_workspace_persist.py`（checkpoint と注記）、E2E `assistant-restore-checkpoint.spec.ts`（320。未実行）。

## 2026-11-23 通知の作り直し: 種類ごとの有効・無効とお休みモード

- 通知の入口を 1 つにした（`backend/app/alerts/events.py::notify_event`）。利用者ごとの種類の設定 → お休みモード → `webpush` の順に判断する。既存の 3 か所（Project Lab の実行・監視の警報・更新）も通す。設定は利用者ごとの JSON（`data_dir/push/notify-prefs.json`。DB の移行なし）。
- お休みモード: 既定は有効・22:00〜07:00（サーバーの地域の時刻・日またぎ・開始は含み終了は含まない）。休み中は捨てる。緊急の警報（app_down・app_health_failed・gpu_temp_c・cpu_temp_c）だけは「お休み中も送る」で通す（既定は 2026-11-23 に「送らない」へ変更）。
- 種類は 16（AI アシスタント 7・作業場 4・生成 1・システム 4）。AI アシスタントの完了・質問待ち・許可待ち・失敗・中断・調査・長い会話を新しく通知する。一覧と検出の場所は `docs/design-notification-kinds.md`。秘密の会話は題も要約も載せない。通知を押すと `/assistant?conversation=<id>` でその会話を開く。
- API: `GET/PUT /api/v1/notification-settings`（本人の分だけ）。画面: 設定 → 通知に「お休みモード」「通知の種類」の節（`NotificationSettings.tsx`）。
- テスト: `test_notify_events.py`、E2E `notification-settings.spec.ts`（320・1280、ライト・ダーク。撮影 `frontend/test-results/notification-settings-*`）。
- 確かめられなかった点: 実機の Push の到着（外へ送らない決まりのため差し替えで確認）。

## 2026-11-23 AI アシスタント: プランの質問を選択肢と自由入力の答えの画面に（U13 の続き）

- 質問の書き方を SKILL.md で変えた: 質問ごとに選択肢（2〜4 個、推奨は末尾の `（推奨）`、複数可は題に `（複数可）`）。古い書き方（`→ 推奨：○○`）も読める。選択肢も推奨も無い質問は自由入力だけの質問として出す（今までは推奨の無い質問が 1 つでもあると全部を捨てていた）。`planQuestions.ts` が `{n, question, options[{label, recommended, note}], multi}` を返す（見出しが無ければ空、LLM 不使用）。
- 画面は新しい `PlanQuestionForm.tsx`（`AssistantChat.tsx` は差し替えだけ）: ラジオ／チェックボックス（44px）・「推奨」の札・推奨は最初から選択・質問ごとの「その他」の自由入力（書けばそれを使う）・全体への補足。主ボタン「この答えで作る」はビルドに切り替えて（`setCodeAgent`・`rememberPrefs`）答えを付けて送る（プランの回でなければ「この答えで進める」で同じ agent）。副ボタン「答えてプランを直す」はプランのまま答えだけ送る。答えの無い質問があれば主ボタン・副ボタンを押せず、番号を示す。
- 質問があるプランでは、答え無しで始める単独の「このプランで作る」を出さない（答え無しで実装が始まっていた）。質問が無いプランでは従来どおり。
- 送る文は `buildPlanAnswer` の 1 か所: 「決めたこと:\n1. 題 → 答え（推奨どおり）…\n補足: …\nこの答えでプランどおり実装してください。」。
- テスト: E2E `assistant-question-buttons-model-pill.spec.ts`・`assistant-plan.spec.ts`（320・1280、ライト・ダーク。撮影 `frontend/test-results/question-buttons-*`）。
## 2026-11-23 会話の一覧に作業場の状態の印（U9）

- 会話の一覧（`ConversationHistory.tsx`）の各会話に、作業場を使う OpenCode の会話だけ「作業中・質問待ち・完了・中断」の小さな札を出す（失敗・サーバー停止は「止まった」）。色と文字の札で、題は先に切り詰める（320px でも読める）。
- 出どころは Project Lab の作業場タブと同じ `GET /opencode/workspaces/progress`（同じ query キー＝キャッシュを共有。動いているものがあるときだけ 10 秒ごとに見張る）。会話との結び付きは進み具合の `conversation_id`（作業場ごとの最新の会話）。API には `interrupted`（#561 の interrupted。failed と区別）を 1 項目足しただけ。`WS_STATES` は ProjectLab から `DevWorkspace.tsx` へ移して共有。
- テスト: `test_dev_workspaces.py`（interrupted の区別）、E2E `assistant-chat-list-workspace-badge.spec.ts`（320・1280、ライト・ダーク。撮影 `frontend/test-results/chat-list-ws-badge-*`）。
- 注意: 印が出るのは各作業場の最新の会話だけ（作業場に結び付く会話が複数あっても、進み具合は作業場ごとに 1 件のため）。
## 2026-11-23 AI アシスタント: 差分の行から「ここを直して」・「レビューして」ボタン（U7、L1）

- 差分の行（会話の「変更」＝`OpenCodeChanges`、作業場の結果の「変更」＝`WorkspaceDiff`/`DiffLines`）を押す（スマホはタップ）と、行の下に小さな入力が開く。送ると「<ファイル>:<行> の <その行の内容> について: <文>」を同じ会話へビルド（`code_agent: build`）で送る。hunk の見出し行も押せる。削除の行は旧側の番号に「（削除）」。
- 送る口は `AssistantChat.send` を `DiffAskContext`（新規 `DiffAsk.tsx`）で渡す。「このプランで作る」と同じくビルドへ切り替えて送る。生成中・入力中は値を入れないので、行もボタンも出ない（ProjectLab の結果カードにも出ない）。
- 「レビューして」: 完了カード（`CodeDoneCard`）と作業場の結果のカードに出し、「今の変更をレビューして（reviewer の係を使って）。問題があれば直して。」を送る。reviewer は `provider.py` が常に agents へ入れる子エージェント。
- テスト: E2E `assistant-diff-ask-review.spec.ts`（320・1280、ライト・ダーク、生成中は出ない。撮影 `frontend/test-results/diff-ask-*`）。
- 確かめられなかった点: 実モデルが reviewer を呼ぶか。見た目の視覚ゲートは今夜は外した。

## 2026-11-23 完了カードが短い答えを繰り返していた（#565 の直し）

- #565 の完了カードは、「結果・まとめ」の節が無いと本文の最初の行をまとめにしていたので、短い答え（「作りました」）に同じ文が 2 つ出た。E2E（assistant-plan・assistant-claude-layout の 14 件）も同じ文が 2 つで落ちた（#566 のマージの時点で Opus が E2E の結果を見落とした）。まとめは「結果・まとめ」の節があるときか本文が 400 字以上のときだけ出す。
## 2026-11-23 AI アシスタント: 推奨どおりで進めるボタン・ヘッダの auto に実モデル名（U13・U11）

- U13: 完了した OpenCode の回の本文に「決めてもらいたいこと」の見出しと番号付きの質問（すべてに「推奨：」）があれば、本文の下に推奨の一覧と「推奨どおりで進める」を出す。押すと「推奨どおりでお願いします（1: …、2: …）」を同じ会話・同じ agent（プラン/ビルド）へ送る（既存の `send`）。検出は `planQuestions.ts`（本文の書き方だけ、LLM 不使用。推奨の無い質問が 1 つでもあれば出さない）。SKILL.md の「利用者に決めてもらうこと」に書き方を 1 行足した。OpenCode の question の道具は従来どおり deny。
- U11: `/workflows/llm-endpoints` の managed の項目に `auto_resolves_to`（`gateway.resolve_instance` と同じ解き方）を足し、ヘッダの札を「auto（モデル名）」に（長ければ省略、title に全体、押すと既存の設定＝モデルの選択）。OpenCode の会話では設定に「役割ごとのモデル」（既存の `/opencode/workflow` の `role_info`。`RolesPanel` の `ROLE_LABELS` を共有）の一覧。
- テスト: `test_assistant_auto_model.py`、E2E `assistant-question-buttons-model-pill.spec.ts`（320・1280、ライト・ダーク。撮影 `frontend/test-results/question-buttons-*`・`model-pill-roles-*`）。
- 注意: 320px 未満 360px の札は既存の仕様で文字を隠す（アイコンと title のみ）。

## 2026-11-23 AI アシスタント: いまの作業の 1 行・子エージェントの見える化・完了カード（U4・U5・U6、L1）

- 利用者の通し: 道具の羅列が長く今の作業が分からない／子エージェントが 1 時間近く動いても係・モデル・進みが見えない／完了の要約が無い。
- U4: `TodoFloat` の小窓の 1 行を「いま: <作業中の項目> · m:ss · 計画/実装/確認」に（段は `workStage`。プランの回は計画、項目の言葉で確認系を推す目安）。押すと ToDo 全体（既存）。道具の羅列は `CodeTimeline` で 8 件ごとに `<details>` へ畳み、生成中の最後の段だけ開く。
- U5: `ToolRow` の task（subagent）に係・モデル・経過・最後に動いた時刻（`SubagentInfo`）。10 分動きが無ければ「止まっているかもしれません」と、既存の停止（`stopRunning`、`StopContext` 経由）の入口。backend `chat_server.py`: 係のモデルを設定から tool に（`model`）、裏の子のイベントを受けたら 10 秒に 1 回 `last_active_at` を送る（`child_activity`）。
- U6: `CodeDoneCard`（`OpenCodeChanges.tsx`）。本文から要約を抜き（「結果・まとめ…」の節、無ければ最初の見出し。LLM 不使用）、ToDo の完了数・変更数、作業場の結果のカード（`workspace-result`）への入口。ファイル一覧は既存の `OpenCodeChanges`。
- テスト: `test_opencode_chat_server.py`（2 件追加）、E2E `assistant-live-status.spec.ts`（320・1280、ライト・ダーク。撮影 `frontend/test-results/live-status-*`・`done-card-*`）。
- 未了・確かめられなかった点: 実際のモデルでの子の `last_active_at`（イベントの形はテストの模擬）、前景で動く子（task が裏でない場合）は動き時刻が付かない、U6 の撮影の縮小画像、U5 の「待たずに進める」・レビュー係の既定モデル。見た目の視覚ゲートは今夜は外した。
## 2026-11-23 E2E の既知の失敗 5 件を 0 件に（Q3）

- 原因: 作業フォルダの選択（`AssistantChat.tsx` の `__folder__` → プロジェクト名への直し）は、プロジェクト一覧と作業場の一覧（`/opencode/workspaces`）の両方が届くまで直さない（#560 以降）。`assistant-plan.spec.ts`（4 件）と `assistant-claude-layout.spec.ts`（1 件）は作業場の一覧を模擬しておらず、選択が `__folder__` のまま残り `code_project` が空で送られた。画面の不具合ではない。
- 直し: 両 spec に `/opencode/workspaces`（空の一覧）の page.route を足しただけ。確かめる中身は変えていない。画面のコードは触っていない。

## 2026-11-23 マージの L2 の承認をボタンだけに・区分を PR の差分で判定（U20）

- 利用者「マージの際は文言を入力させずにボタンタップで承認できるようにして」。L2 の赤い警告と対象のファイルの一覧は残し、確定のボタンを「L2 を確かめてマージ」にした（押すと `acknowledge_l2: true`）。サーバーは印の無い L2 のマージを今までどおり断る（古い確認の文言も互換で受ける）。押した人は監査に残る。
- U20: 区分（L0/L1/L2）を作業場を作った時の revision から見ていたので、作業場が取り込んだ main の分まで数え、画面だけの PR（＋メニュー、10 ファイル）が「L2・40 ファイル」と出た。結果のカードと同じ `change_base`（今の main との共通の祖先）から判定する。
- テスト `test_dev_workspace_merge.py`（ボタンの印で承認・印が無ければ断る・区分の起点）、E2E `assistant-dev-workspace-diff-merge.spec.ts`。

## 2026-11-22 AI アシスタントの＋メニューを作り直す — モード・権限をフラットアイコンに・全体をコンパクトに（L1）

- 利用者「『OpenCode のモード（ビルド／プラン）』と『OpenCode の権限（Auto／確認）』は、文字ではなくフラットデザインのアイコンで表示してほしい（どれを選んでいるかは分かるように）。メニュー全体をもっとコンパクトにして、横幅も小さく、スタイリッシュに。スマホ（幅 320px）でも使いやすく」。承認（同日）: 札（＋の隣）も同じアイコンに揃える・グループの小ラベル（モード／権限）は残す。追加: 「ファイルを添付」と機能タイルの行の 2 行目を無くして 1 行に。
- `PlusMenu.tsx`: モード・権限はアイコンだけの 44px ボタン（選択中は既存の TONE 色 — プラン=青・ビルド/Auto=アクセント・確認=琥珀。名前と説明は aria-label と title へ、小さな名前の行と sr-only の注記は残す）。添付・操作の行と機能タイルを 1 行に（説明は title へ）、開発対象の説明を 1 行に。幅 336px → 288px（`place()` と className の両方を一緒に）。区切り・見出しの余白を詰めた。
- `icons.tsx`: `IconHammer`（ビルド）・`IconClipboardList`（プラン）・`IconFastForward`（Auto）を追加。確認は既存 `IconPause`（＋の隣の札の ⏵⏵／⏸ と同じ形を継承）。`AssistantChat.tsx`: 札の 📋・⏵⏵／⏸ を同じフラットアイコンへ（sr-only の `data-pill-label` はそのまま）。
- E2E: 新規 spec を作らず既存を更新（TDD: まず赤を確認）。`assistant-dev-targets`（メニュー幅 ≤300px・機能タイルのアイコンとラベルの中心 y が同じ・モード/権限ボタンに svg と aria-label・**タイルのラベルは「…」省略なし**）、`assistant-claude-layout`（添付の行の textContent がラベルだけ = 1 行化）。
- ui-verifier（実ブラウザ・320/390/768/1280×明暗・操作フロー・ダークのコントラスト計測）: 判定 partial（critical 0・major 0・minor 2・info 3）。minor を直した: ①「Generate Flow」タイルのラベルが 288px で省略 → `ModeOption` に `tile`（短縮名「Gen Flow」。ピルと説明は元の名前のまま）を足し、spec に省略なし検査を追加して赤→緑 ②ライトの 10px 薄色（zinc-400 ≈ 3.65:1）が AA 未達 → zinc-500（≈ 4.9:1。ダークは 5.36 のまま）。info（ビルド/プランが同じ青系・矢印キーの空間移動・WebSocket の再接続）は既存の色の決まり・既存のキー移動の挙動・隔離インスタンスの特性なので変えない。
- 検証: E2E `assistant-dev-targets` 5、`assistant-claude-layout` ほか assistant 関連（`assistant-plan`・`assistant-mobile`・`assistant-message-actions`・`assistant-secret-lock-topbar`・`assistant-offline-research`・`assistant-opencode-progress`・`assistant-queue-voice`・`assistant-code-copy`）32 通過、320／1280・ライト／ダークの撮影、`npm run build`。
- reviewer（差分レビュー）: 判定 partial（重大・要修正なし・minor 3・info 4）。minor に対応: ①「Generate Flow」タイルの省略（ui-verifier 指摘と同じ。上の `tile` 修正で解消）②未使用になった `PERMISSION_MODES` の `icon` 文字列（⏵⏵／⏸）を削除し、権限の描画は `PlusMenu` の `PERMISSION_ICONS` に一元化 ③メニュー幅の 288px と 18rem の単位不一致 → `place()` が根の文字サイズから 18rem を px で測る（既定でない文字サイズでも位置と実幅がずれない）。info は `.gitignore` へ `.controldeck/` 追加と冒頭コメントの食い違い（説明は title/sr-only へ移った）を解消。info の残り 2 件（添付行の折り返し未検査・タイル span の非 null 断言）は ui-verifier の実ブラウザ確認（全幅 1 行・44px）と「構造が変われば検査が TypeError で落ちる＝意図どおり」の理由で見送り。
- 最終回帰: `assistant-dev-targets`・`assistant-claude-layout` 25 通過（失敗は上の既知 1 件のみ）。
- 確かめられなかった点: 実機の指での操作感（E2E の実ブラウザ 320px で代用）。既知の既存失敗（変更前の HEAD でも同じことを stash で確認）: `assistant-claude-layout` の「thinking split」1 件・`assistant-plan` 4 件 — 検証用インスタンスに CodeDEV の app が無い（2026-11-22 の記録どおり）。box の中では `./deck.sh e2e` が venv 作成で止まるため、元の venv（app-strata）と `PLAYWRIGHT_BROWSERS_PATH` で同じ流れを手動で回した。
- main の視覚ゲートの中継（428bafa）を merge して dev-check を再実行（--base origin/main）: 「画面の見た目」は中継経由で走った（qwen-flash-next・12 枚）が high 3 件を指摘。うち 2 件（320px でチップが画面幅を超える・モード行が下端と重なる）は E2E の幾何検査（メニューは 320px に収まる・はみ出し無し）と ui-verifier の実ブラウザ確認に反する。1 件（「コーディングエージェントが作業」などの長い補助ラベルが「…」で切れる）は 288px 化での切り詰めが実態。対応は次の判断待ち。

## 2026-11-22 Maya の設定に Hy4-Flash（Tencent）の対応（L1）

- 利用者「Maya の設定に hy4 の対応を追加して」。Hy4-Flash（Tencent の `tencent/Hy4-flash`、220B・最大 1M コンテキスト・画像対応・AMD Linux の R9700 で動く）を Maya の登録の一覧・登録・個別設定へ足した。
- Maya 本体（v1.1.73、GitHub の mw00/project-maya を読んで確かめた。この箱からは実機のエンジンが見えない）:
  - family `hy4`、setup id `hy4-flash-maya-xs/s/m/l`（QUANTS に 4 サイズ）、config 名 `maya-hy4-flash-maya-xs.json`、専用エンジン `maya-hy4`（setup が自動でビルドする。ControlDeck 側の引数は不要）
  - VRAM の余裕は `--reserve-gb`、**GB = 10^9 バイト**（本体 set_vram_reserve: `mib * 1048576 / 1e9`。MiniMax と同じフラグで単位が違う。#1026）。`_apply_vram_margin` に hy4 の分岐と `with_margin_arg` に gb10 モードを足した
  - **1 度に 1 要求**: 本体 check_parallel の PARALLEL_FAMILIES は (glm, deepseek, minimax) で、hy4 に `--parallel` を渡すと setup が fail する → `_register` の argv で hy4 のときだけ `--parallel` を付けない
  - 登録は `--model hy4-flash-maya-xs` 型（ds4/mm3 と同じ）、manifest は `tools/hy4/download.json`（`maya.HY4_MANIFEST`）。サイズ（trunk 44.8〜89.5 GB）＋共通の n-gram テーブル 63.4 GB（全サイズで同じ 1 ファイル）。S・M・L の setup は本体の hy4_places（--models-dir 直下の正式名フォルダ `Hy4-Flash-Maya-*` か登録済み Hy4 の config の model_dir）から hard-link するので、**XS を先に登録しておくのが最短**（XS 未登録で S を登録するときは n-gram をモデルのフォルダへ手で置く）
  - 画像は mmproj 1 個（全サイズ共通、XS のフォルダ、Maya 自身の Q8_0）。pack の `hy4.txt` の `vision.dim` で判定し `--vision off` だけが切（`_config_vision` に hy4 の分岐を追加）。メモリの目安（manifest の memory_gb）: XS 52・S 64・M 76・L 100 GB。ライセンス Tencent Hy License（画面の説明に、n-gram の大きさと一緒に 1 行）
- 変えた所: `maya_probe.py` に `hy4_entry`（mm3 型 + 共通テーブルの所在どおり）を足し maker map へ登録（Hy4 は GLM/DeepSeek/MiniMax と違い maker が無いと一覧から黙って落ちる）。`maya_s.py` は FAMILY_LABEL・別名 `maya-hy4-<tier>`（_NEW_ALIAS）・`_register` の hy4 分岐（--parallel を外し、mmproj が在れば --no-vision 無し）・`_hy4_pack_has_vision`。画面（MayaRegisterPanel）は family に hy4・ライセンスの 1 行。取得は既存の「利用者の明示の操作で」に乗せ、空きの確認（XS で trunk+テーブル ≈ 108 GB）は `_download_plan` のまま。
- レビュー（別のモデルの reviewer）: critical 2 件（hy4 に `--parallel 1` を渡すと本体 setup が fail・余白の引数が `--vram-reserve-mib` ではなく `--reserve-gb` GB 単位）を指摘され、一次資料（本体の check_parallel・set_vram_reserve・hy4_main.cpp）で裏を取って直した。再レビューで解消を確認（partial 判定: 残るはこの箱で流せない pytest・dev-check・実エンジン）。major（S・M・L の n-gram の hard-link は XS 先登録が前提）と minor（登録までの早期ヒント）は: `_register` で「n-gram 表がこのサイズのフォルダに在るか、登録済みの Hy4 から hard-link できるか」を先に確かめ、どちらでも無ければビルド（10-30 分）の前に「先に Maya-XS を登録するか、テーブルをこのフォルダへ」で断るようにした。
- テスト: `test_maya_probe.py`（新規・pytest に依らず流せる構造で 3 個この箱でも合格）、`test_maya_register.py`（登録の引数〔--parallel 無しを含む〕・別名・表示名・画像の判定・DeepSeek 専用の設定に出ないこと・ダウンロードの見積もり）、`test_vram_margin.py` の parametrize に hy4（`--reserve-gb`・GB 単位の変換）、E2E `maya-register.spec.ts`（320・1280、Hy4 の 4 サイズ・ライセンス行・不足の案内）。
- 確かめられなかった点: この箱に Python 依存（venv）が無いため `test_maya_register.py`・`test_vram_margin.py` は実行していない（コードを精査しただけ。実機で `cd backend && env -u CONTROL_DECK_CONFIG ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_maya_register.py tests/test_vram_margin.py tests/test_maya_probe.py` を流す）。E2E・dev-check も同じ理由で未実施（フロントエンドの tsc と npm run build は合格）。実際の登録と起動（`--model hy4-flash-maya-xs`・--parallel 無しでの setup・n-gram テーブルの hard-link・mmproj の読み取り）は Opus が利用者の了承を得て本番で確かめる。

## 2026-11-23 特許のテストの順番依存を直す

- `test_patents.py::test_search_and_claim_chart_use_the_shared_knowledge` が「検索の 1 件目が特許の文書」と決めつけていて、関門が選んだ順（先に `test_knowledge_rebuild`）で流すと、残った一般の文書が 1 件目に来て落ちていた（全体で流すと通る）。入れた特許の文書が結果に含まれることを確かめる形に直した。#561 は、この揺れで関門が不合格のまま Opus の手順の誤りでマージされた（変更そのものとは無関係と確かめた）。

## 2026-11-22 Project Lab の「作業場」の切り替えと進み具合、AI アシスタントの作業フォルダの選択（U1・U3）

- 利用者「Project Lab で現在の進捗とかって確認できないんだったっけ。CodeDEV でコード開発するフォルダと、今回のプロジェクト側の開発（作業場）はどういう扱いになるのか…参照先がテンポラリーなフォルダなら、今あるドロップダウンの一覧とは別にした方がいいと思う」。作業場の OpenCode（GLM-5.3）が途中（時間切れ）まで作った差分を Claude が読み直して仕上げた。
- **Project Lab**（`ProjectLab.tsx`）: 一覧の上に「プロジェクト｜作業場」の切り替え（SegmentedTabs）。作業場のタブには動いている（作業中・質問待ち）件数の印。寿命が違う（作業場は一時の写し、CodeDEV は残る）ので混ぜない。作業場のカードは元・開発対象の札・進み具合（作業中・質問待ち・完了・止まった、ToDo の n/m、最後に動いた時刻）・会話を開く・既存の結果のカード（変更は未コミットも数える・関門・差分・取り込み・マージ）。実行して確かめるのは「準備中」と小さく出す（箱の中で動かす仕組みが要る）。
- **backend**: `GET /opencode/workspaces/progress`（terminal.use。自分の作業場と進み具合）。会話の最新の OpenCode の回答の meta と会話サーバーの状態から組み立てる（自分の会話だけ。生成中でもサーバーが止まっていれば「止まった」）。変更の数は、先に入れた U2 の `changes`（未コミットを含む）を使うので、GLM が足した別の `uncommitted_stats` は捨てた。
- **AI アシスタントの作業フォルダの選択欄**: 利用者の決定により、「🧪 ＋ 新しい作業場（ControlDeck を直す・試す）」は選択欄の中の「➕ 新規作成」のすぐ下に置く（選択欄の外の別のボタンは作らない。GLM の「🧪＋ 作業場」ボタンは捨てた）。その下に `<optgroup>`「CodeDEV のプロジェクト」「作業場（一時）」。値（`__ws_new__`・`ws:<名前>`）は不変。
- GLM の差分から捨てた範囲外: トーストを画面の上へ移す変更（全画面に効く）、作成欄の文言の変更。
- テスト: backend `test_dev_workspaces.py`（進み具合 API）、E2E `project-lab-workspaces.spec.ts`（切り替え・カード・選択欄の並び、320・1280、ライト・ダーク）、`assistant-dev-workspace.spec.ts`。
- 確かめられなかった点: 実機の箱での会話サーバーの状態（meta と状態ファイルで代用）。

## 2026-11-22 作業場の結果のカードの数え方（U2・U16）

- 利用者の通し（Opus が利用者として AI アシスタントから作業場で開発を頼んだ）で、結果のカードの数が 2 つ違った。作業場の OpenCode（GLM-5.3）が出した差分を Claude が読み直して仕上げた（他の 2 つ、見た目の検査と既定のランタイムは main の #555・#557 を残し、GLM の側は捨てた）。
- **U2 未コミットが「0 ファイル +0 −0・未コミット」**: `workspaces.changes` が取り込んだ ref（コミット済み）の差分だけを数えていた。未コミットの分を箱の中の git（`git diff --numstat HEAD` と `git status` の未追跡）で数え、ファイル数・増減に入れる（`_uncommitted_stats`。`git add -N` は index に副作用が残るので使わない。ホストの git は箱の作業ツリーで動かさない）。表示は「3 ファイル +32 −5（うち未コミット 2）」。箱の git が読めなければ従来の「・未コミット」の印。
- **U16 「17 ファイル」が取り込んだ main の分まで数える**: 起点を作った時の revision から、今の main との共通の祖先（merge-base。`change_base`）に変えた。export.git へ本体の origin/main を fetch して取る（箱の作業ツリーは触らない）。main が読めない・CodeDEV の作業場は元の revision に戻す。マージの前提の区分判定（L0/L1/L2）は、関門を狭めないよう元の revision のまま（広い側）。
- テスト `test_dev_workspace_adopt.py`（未コミットを含む数・main を取り込んだ作業場は merge-base から数える・main が読めないときの退路）、`test_sandbox_gateway.py`（中継が /models の vision・loaded を通す）、E2E `assistant-dev-workspace-result.spec.ts`（320・1280、ライト・ダーク）。
- 確かめられなかった点: ホストの origin/main が古いと、取り込んだ main の新しい分が数に混ざる（自動更新が保つ前提）。実機の箱での通し。
## 2026-11-22 作業場の「保存と中断・再開」のルール整備

- 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。開発が終わってから移動されるのか、ルールは適切に整備されているのか。途中で停電や再起動した場合はどうなるのか。中断して再開できるのか。他にも考えられる割り込みのシチュエーションを想定した上で、問題がないように実装してほしい」。
- **自動保存（区切り）**: 作業場の OpenCode の回が終わるたび（成功・失敗・止めた・例外、どれでも）に箱の中の箱の git でコミット（`workspaces.autosave`・`chat_persist` から呼ぶ）。メッセージ「作業途中の保存 #n（会話 <id> のターン）」・変更が無ければしない・作者は箱の設定済みの作者・保存したら export.git へ取り込み結果のカードの差分に見える。PR の本文に途中の保存の回数。取り込み（adopt）は今までどおり 1 つの PR にまとめる（squash）。
- **起動時の立て直し**（`workspaces.restore_interrupted`、起動時に 1 回裏で）: 残った `.git/index.lock` を箱の中に git の process が無ければ消す（pod top で確認）。生成中のまま止まった作業場の会話のメッセージを「中断（ControlDeck の再起動）」へ（`interrupt_unfinished` と同じ形）。未保存の変更があれば自動保存。
- **続きから**: 中断・止めた・失敗した OpenCode の回のメニューに「続きから」（44px）。`POST /chat/conversations/{id}/opencode/resume` が同じセッションへ「前回は中断されました（理由）。git log・git status と ToDo を確かめて、残りを続けてください」を送る（セッションが無ければ新規で前回の ToDo を本文へ。会話サーバーは ensure が起こし直す）。
- **ディスクの空き**: 新しい作業場を作る前・自動保存の前に `data` のある所の空き（`statvfs`）。下限（設定 `workspace_min_free_gib`・既定 15 GiB）を下回れば作るのを断り、自動保存は飛ばして会話へ「ディスクの空きが少ないので保存できません（残り n GiB）」。下限の 2 倍（30 GiB）未満は結果のカードに注意。
- **main の進み**: 結果のカードに「元の main が n 件進んでいます」（export.git で元の origin/main と差、10 分キャッシュ・fetch は外へ出る）。取り込みの前に箱の中で main を取り込む段（export.git から bundle を箱の /tmp へ置き箱の中で fetch+merge・ホストの git は export.git で動かすだけ）。ぶつかったら merge --abort で取り込みを止め、カードに「OpenCode に直させる」ボタン（同じ会話へぶつかったファイル一覧つきで送る・bundle は /tmp/main.bundle に残す）。merge できたら関門を流し直させる。
- **モデルの切り替わり**: 使用量の上限・残高切れ・レート制限（`sandbox.LIMIT_PATTERN`）で回が失敗したら、モデルの順番（chain）の次へセッションを切り替え（`chat_server.switch_model`）、会話に「glm-5.3 が使えなくなったので hy4-flash-free で続けます（理由）」の 1 行を出して同じ依頼を再送（chain が尽きるまで・前の回で進めた所から）。
- **作業中の作業場を消す**: 一覧に「・OpenCode が作業中」の印（会話サーバーの unit）。消すときの確認の文言を強く（止めてから消す）。
- テスト: `test_dev_workspace_persist.py`（21）・`test_opencode_workspace_persist.py`（14）・E2E `assistant-workspace-resume.spec.ts`（7・320/1280/ダーク）。目録に載せた。レビュー（reviewer）の major 指摘「merge の bundle の置き場所が箱の AI に書ける場所で、symlink を仕込まれるとホストの git がそれを辿る」に対応: bundle は export.git の隣（ホストだけが書く）へ作り、`in_box_result` の readonly マウントで箱へ読み取り専用に見せる。minor も対応（マージの解決中は自動保存しない・fetch と merge を分けて必ず abort・origin の `ext::` 拒否・model_index の常時保存・再送へ「状態を確かめて」の注記・resolve-conflict の権限を送信と同じ workflows.run に・起動後の新しい回は「中断」にしない・再開し終えた回へ印）。既知の制限: 全体実行でだけ落ちるフレーキー（test_patents の 1 件、単独では合格・HANDOFF 課題 4 に記録）が今回も出た。画面の見た目の LLM での確認は本番ゲートウェイへ箱から中継で届かず未実施（E2E の撮影は 7 枚取れている）。
- 未確認（Opus が実機で確かめる）: ControlDeck の再起動をまたいだ起動時の立て直し（index.lock・生成中の中断・未保存の保存）・停電相当・実際の GitHub への fetch（main の進み）・本番のモデルでの切り替わりの 1 行・実サーバーの OpenCode の `POST /api/session/{id}/model` の挙動。
- Claude による見直しで直した点: ① ぶつかりを OpenCode に直させる指示が、常駐の OpenCode の箱には見えない bundle のパスを伝えていた → 取り込んだ main を箱の中の `refs/controldeck/main` へ残し、それを merge させる（bundle は fetch の間だけ readonly で見せて必ず消す） ② 取得元（meta の origin）の `ext::`・`-` 始まりを拒む ③ 取り込みの前の main の進みは覚えた値でなく都度取る ④ 同じ作業場の自動保存を 1 つずつに（番号の重なり・index.lock の取り合い） ⑤ 起動時の立て直しを別スレッドにし（サービスの起動復旧を待たせない）、中断にした・lock の残った作業場だけ保存する（止まった箱を全部起こさない） ⑥ モデルの順番の位置は同じセッションのときだけ引き継ぐ ⑦ 同じ回の「続きから」の二重を 409 で断る ⑧ merge --abort の失敗を伝える。テストを追加（readonly の bundle・取得元の拒否・保存の直列・起動時に触らない・二重の再開・セッション違いの位置）。

## 2026-11-22 作業場の関門: 画面の見た目の誤検出を、管理者が人の目で確かめて外せる

- 利用者として作業場から＋メニューの作り直しを頼んだ通しで、画像を読むモデルが重大の指摘を 3 件出した（320px で札がはみ出す・下端と重なる・文字が切れる）。撮影を Opus が見て 3 件とも誤検出と判断したが、人が確かめた後も外す口が無く、正しい変更が取り込めなかった。
- 直し: 最後の関門が「画面の見た目」だけで落ちたとき、管理者（settings.manage）に結果のカードの「見た目は確かめた（外して流す）」を出す。`POST .../check {skip_visual: true}` で `dev-check --skip-visual` を流し、外した人を関門の結果（`visual_skipped_by`）と監査（`skip_visual`）に残す。見た目以外も落ちているときは外せない（409）。
- テスト `test_dev_workspace_visual_override.py`、E2E `assistant-dev-workspace-result.spec.ts`（見た目だけの不合格で出る・skip_visual 付きで流れる・他も落ちれば出ない）。

## 2026-11-22 作業場の中から画面の見た目の検査が流れない（Q2）

- 利用者として作業場から＋メニューの作り直しを頼んだ通しで、関門が「画面の見た目: ゲートウェイへ届きません（Connection refused）」で不合格になり、画面を変えた作業を取り込めなかった。箱から届くのは中継だけ。
- 直し: `visual_review._gateway` は、明示の `CONTROL_DECK_GATEWAY_URL` → 箱の中（`CONTROL_DECK_SANDBOX`）なら中継（`OPENAI_BASE_URL`）→ 本番の既定、の順。ローカル LLM を借りない作業場（中継が無い）は未実施（不合格にしない）。
- 気づいた設計の課題: 関門は作業場の中のコード（作業場を作ったときの main の写し）で流れるので、関門の直しは既存の作業場に効かない。作業場で main を取り込む段（U8・resil）で扱う。
- テスト `test_visual_review_in_box.py`。

## 2026-11-22 既定のランタイムが要求の載せ替えで書き換わる不具合（P1）

- 利用者（画面の写真つき）「モデルを一旦 FlashNext に切り替えるがまた Maya-XS の DS に戻ってる。競合しないようにうまくハンドリングできないか」。
- 原因: 各ランタイムの起動（4 か所）と 60 秒ごとの `reconcile_selection` が、要求による載せ替えのたびに利用者の既定の選択（selected_runtime）を動いているランタイムへ書き換えていた。既定（auto）の要求の行き先が最後に載ったモデルで揺れ、Maya-S と Strata の間を行き来した（本番のログで 90 分に 5 回）。
- 直し: 共通の起動の入口 `local_llm.ensure_ready` を「要求による起動」の区間（`large_model_lifecycle.on_demand_start`、contextvar）で包み、その中では `follow_selection` が選択を変えない。60 秒ごとの合わせ込みは回さない。選択が変わるのは、利用者が Models の画面でロードしたときだけ。
- テスト `test_selection_only_explicit.py`。

## 2026-11-22 AI アシスタントの調査: サイト指定・依頼の理解・今日の日付の確認（L2）

- 利用者「Reddit の中で〜、公式ページの機能の使い方、GIGAZINE の記事をもとに調べて」「『調べて』でいきなり検索して関係ないものを選ぶ」「『今日の横浜の天気』で過去の天気を調べる」「検索が不十分にならないよう幅と件数を担保」。
- 新規 `backend/app/workflows/research_tools.py`（道具 `web_search` の拡張＝sites・time_range・limit 既定 10/最大 30、新しい道具 `read_page`）、`research_intent.py`（依頼の理解 → 検索語 → 選別 → 本文を読む → 足りない観点だけ追加）、`research_time.py`（今の日時・「今日」の解決・ページの日付の照合）。`chat_tools.py` は道具の登録と MAX_ROUNDS 4→12 だけ、`chat_persist.py` は呼び出しの配線。
- サイトごとの入口: Reddit（search.json → 拒否されたら search.rss → SearXNG `site:`。スレッドは thread.json → .rss）、GitHub（search/issues・repositories・README・issue・blob）、Hacker News（Algolia）、Hugging Face は `huggingface_models` を勧める文だけ、その他は SearXNG の `site:` ＋ `read_page`。
- 件数の担保: 少なければ ページ送り → 語の言い換え（引用符・短い語・前半・長い 2 語）→ 期間の解除の順に、`site:` を保ったまま再検索。複数サイトは交互に合わせる。
- `read_page`: 既存の `collect`（SSRF・PDF・文字コード）。長ければ段落に分け、focus に近い段落（reranker＝既存の再ランク、無ければ語の一致）を上位から。題名・日付・リンク一覧（同じサイト先に最大 30）。ロボット確認・本文がほぼ空のページは「読めない」と返す（回避しない）。robots.txt は 1 ページずつの取得では見ない（既存の収集の決まり。巡回は未実装）。
- 依頼の理解（LLM 1 回・思考なし）: 観点 2〜4・検索語（日英）・時（今日→日付）・sites・simple・clarify。発言の文そのものは検索語にしない。`rule_plan` の「調べて」の近道は検索語を作らず mode だけ決める。web・academic・research のモードは全部この流れを通り、調査の進みに「調べること」「検索語」を出す。
- 今の日時は `research_time.now_text`（Asia/Tokyo）1 か所から、理解・十分か判定・答えの資料・チャットの道具の指示へ。時期が大事な依頼（時の言葉・天気・価格・ニュースなど）は上位を読んでページの日付を依頼の期間と照合し、合わないものを捨て、合うものが無ければ語を変えて探し直す（簡単 1・通常 3・深い 5 回まで）。チャットの `web_search` も同じ部品（日付つきの語での再検索と本文の日付の確認）を通る。
- 安全: 通信は `guarded_client` だけ、通信の直前に `ensure_external_allowed`、取得した文は「資料」の注意書き付き。
- テスト `test_research_tools.py`（19）ほか。限界: この機械からは Reddit の JSON・RSS が 403/429 で、スレッドの本文・コメントは取れず検索の要約で扱う（SearXNG 経由の検索は可）。

## 2026-11-22 ゲートウェイの「載せ替えの調停役」（L2）

- 利用者「モデルの載せ替え機能をインテリジェントにして…載せ替えの待ち時間は最短に、安全に。載せ替えなくていいタスクを先に、次に載せ替えの回数が最少になるように」。同日の追加「断ると要求が消える。待ち時間で優先度が少しずつ上がるように」「並列は Radiance だけ」。
- 不具合: 要求ごとに `gateway_chat` → `prepare_switch` → `ensure_ready` が走り、別々のモデルの要求が互いを降ろそうとした（解放の見送り、Strata の操作ロックが取れず即失敗）。待てば通る要求が 300〜660 秒待って 503・500（箱の OpenCode が開発 Strata と調査 Maya-S を交互に呼んだ）。理由もログに無かった。
- 直し: `backend/app/models_mgmt/swap_scheduler.py`。chat（ストリーム含む）と `/load` は調停役に並び、載せ替えは実行中が 0 のときに 1 回だけ（`gateway._swap_load`: `_model_startup` → lease → `prepare_switch` → `ensure_ready`）。載っているモデルは即実行（Radiance だけ `max_num_seqs` まで並列、他は 1 本）。点数 = (待ち数 + 最古の待ち秒/aging) ÷ 載せ替えの見込み秒（続ける点数は下限 10 秒で割る）で、続けるか・どのモデルへ切り替えるかを決める。載せ替え直後は目的の要求を先に通す（行き来の防止）。失敗は理由つき 503（ログにも）、2 回続くと 30 秒の間。次のモデルの重みは、MemAvailable が重み+8 GiB 以上のときだけ `posix_fadvise(WILLNEED)` で先読み。補助のモデル（embedding・reranker）は対象外。調停役が例外になったら要求を止めず通す。
- 見込み秒: 調停役自身が測った載せ替えの所要（warm/cold）→ load_profiles の実測 → 60 秒。メモリ上（停止後 15 分以内・先読み済み）なら warm。
- 見える化: `GET /api/v1/models/swap-queue`、載せ替えのたびにログ 1 行、Models の「切り替え待ち: …」。設定は LLM 共通設定の「モデルの載せ替え」（`runtime_policy.swap_scheduler`: `aging_seconds`・`prefetch`）。
- テスト `test_swap_scheduler.py`（実行中を切らない・点数・待ちで優先度が上がり必ず実行・503 にしない・失敗の理由と間・交互の要求で載せ替えが到着順の 15 回に対し 4 回以下・先読み・補助の対象外・`/load`・二重ロックで止まらない・API）。

## 2026-11-22 AI アシスタントの道具 huggingface_models（Hugging Face のモデル検索）

- 利用者「『Qwen の最新のモデルで Strata に対応しているモデルを抽出して特徴とともに教えて』のような検索で、Hugging Face 内を検索して教えてくれる機能が欲しい」「検索の幅・十分な件数を担保して」。
- `backend/app/models_mgmt/hf_search.py`: 語の揺れ（Qwen3.8 / Qwen-3.8 / Qwen3_8 / Qwen38）・GGUF の別作者（unsloth・ISTA-DASLab・bartowski・lmstudio-community・mradermacher・ggml-org）・ベースモデルからの派生（filter=base_model:）を合わせて、Link ヘッダの cursor でページをたどり、重複を除く。件数は既定 50・最大 200。10 分キャッシュ。各モデルの更新日・DL・いいね・pipeline・license・ベース・パラメータ数（出所つき。名前からは推定と明記）・文脈・画像入力・GGUF の量子化と大きさ（tree）。
- 答えの単位はベースモデル（公式の作者のモデルを作成日の新しい順。公式の作者は語から検出）。それぞれに Strata で動かせる GGUF の置き場を最大 3（UD・GSQ が先、Q8_0 は後。作者の信頼: 公式>unsloth・ISTA-DASLab>bartowski・lmstudio-community・ggml-org>その他）。個人の派生は公式の行の下に「派生」の印で続く（include_derivatives 既定 true）。既定はテキスト生成だけ（pipeline 引数で TTS・ASR など）。
- Strata 対応の判定は `check_runnable` と同じ決まり: Unsloth の UD-*（IQ1〜Q6_K_XL、作者 unsloth）・ISTA-DASLab の GSQ-RCO（Q2_0・IQ2_XS・IQ3_XXS・IQ3_S）・Q8_0。ファイル名からの判定で最終確認は起動時（表にも明記）。現在の登録画面で選べる量子化（`strata_gateway._FAMILIES`）も別に出す。登録済み（Strata・llama.cpp の model_path）には印。
- 道具 `huggingface_models`（`workflows/chat_tools.py`、`chat_persist.py` で web_search と同じ条件＝機能「自動」・道具対応モデルで渡す）。秘密の会話では道具を渡さず、呼ばれても `ensure_external_allowed` で通信しない。
- テスト `test_hf_search.py`（判定・語の揺れ・別作者と派生で増え重複が消える・ページをたどる・キャッシュと登録済みの印・秘密の会話・道具の登録と答えの形）。

## 2026-11-22 OpenCode 設定: モデルを選ぶ場所を「役割とモデル」の 1 か所へ

- 利用者「ロールの開発は OpenCode の設定に載せるとあるが、他の設定と競合しないように改めて設定の項目と文言を見直して。ユーザー側に余計な操作はさせずに自動で適用されるように」。
- 設定シートのモデル選択を「既定のモデル」と役割の表（7 役割）だけにした。上の「モデル」→「既定のモデル」（役割の表と同じ枠の先頭。「役割でモデルを選ばなければ、このモデルで動きます」）。「開発担当のモデル」「相談先のモデル」の欄は廃止。
- 開発担当（子エージェント developer）は役割「開発」（build）のモデルで動く（常に明示）。Auto Advisor の相談先は新しい役割「相談」（`advise`、④ 行き詰まったとき。未設定ならセッションと同じモデル）。Auto Advisor を使わないときは相談の行を薄くし「（相談を使うときだけ）」と出す。
- 移行（`provider._migrate_role_models`、読むときに移す。保存で旧項目は空）: `developer_model` → `roles.build`（build が空のときだけ）、`advisor_model` → `roles.advise`（空のときだけ）、`review_model` → 従来どおり最終検証として読む。古い API の値（`developer_model`・`advisor_model`）も同じ役割へ移り、壊れない（`advisor_model` は画面が `advise` を送らないときだけ上書き）。
- 自動の適用: 秘密の会話・特許の流れは全役割が自動でローカルへ（画面に 1 行）。外部が使えなくなった役割は自動で既定へ（画面は「いまは使えないので既定で動きます」）。
- 文言: 開発ワークフローの各項目を日本語の働きを主に（Superpowers・Adversarial Review・Auto Advisor・Playwright MCP は括弧へ）。古い「同じ段のローカルは…」の記述は無し。
- テスト: `test_opencode_workflow.py`（移行・build 優先・advise が相談先・developer が build・秘密でローカル・旧 API）、`test_opencode_roles.py`。E2E `opencode-roles.spec.ts`（選択欄の数・配置・相談の行、320/1280・ライト/ダーク）。

## 2026-11-22 特許の外部検索の「手動の確認」を自動の検査へ（L2）

- 利用者「ユーザー側に余計な操作はさせずに自動で適用」「特許の検索自体はインターネット」「制限で検索が不十分になることは避けたい」。毎回のチェックボックス・`approved_public` を廃し、サーバーが送る直前に自動で検査する。
- 検査（`backend/app/patents/egress.py`）: 検索語・検索式が案件の未公開の文章（請求項・claims_draft・hearing）と **連続 25 文字以上**（`BLOCK_MIN_CHARS`。幅・改行・句読点の揺れは `judge._flat` で許す）一致するときだけ 422 で止める。キーワード・短い句は止めない（8 文字の重なりは survey の参考表示に留め、選択も送信も止めない）。止めたら理由・重なった部分（20 字まで）・言い換えの案（その塊を読点・助詞で区切ったキーワード列）を返し、画面の「この言い換えで送る」で 1 タップで送れる。
- 入口: 手動取得 `POST /patents/sources/{source}/fetch`（案件を指定しなければ全案件と照らす）、ウォッチの作成・更新（止める操作は検査しない）、ウォッチの実行（手動・定期。実行のたびに検査し、止めたら `last_status` に理由）、検索語の案の採用 `approve`（25 字以上の写しだけ断る。安全な語は `default_selected` で最初から選ばれる）。`approved_public` は互換のため受けて無視、「確認待ち」表示は無し。検索の件数・取得元は変えていない。
- 監査: `patents.source_fetch` / `watch_create` / `watch_run`（送った: 長さのみ）と `*_blocked`（止めた: 長さ・理由・重なりの長さ。本文は残さない）。
- テスト `test_patent_egress.py`（境目 24/25 字・全案件・ウォッチの実行ごと・監査）、E2E `patents.spec.ts`（チェック無し・止めたときの理由と言い換え、320/1280、ライト・ダーク）。

## 2026-11-22 役割ごとのモデルの制限を外す（全部同じ・全部違うも可）

- 利用者「ロールの件、個別にモデルを設定できるようにして。同じモデルを使った別の役割を実行するときは境目で載せ替え・載せ直しが起きないように。全部同じにも全部違うモデルにもできるようにし、その場合でも OpenCode の開発が問題なく進むように。動作も確認して。変な制限は設けなくて良い」。
- 同じ段に別のローカルでも保存できる（422 をやめ、待ち時間が増える旨のお知らせだけ）。`roles._settle` はお知らせだけ。
- 全ての役割の agent にモデルを明示する（今までは会話のモデルと同じなら空で、子は呼んだ側のモデルを引き継いでいた＝計画から調査を呼ぶと調査が計画のモデルで動いた）。ホスト（`provider._workflow`）と作業場（`sandbox.opencode_config`）の両方。
- 作業場: 役割で選んだローカルのモデルを箱の controldeck の一覧へ足す（無いと「Model unavailable: controldeck/<モデル>」で止まった。全部違うモデルの実機で見つけた）。
- 実測（本番のゲートウェイ）: 別々のローカルへ同時に 2 つ → 両方 200（順番に載せ替え。qwen 1 秒・maya 71 秒）。長い生成の最中に別のモデルを割り込ませる → 生成中の方は stop まで返り、割り込んだ方は待ってから載せ替えて 200（89 秒）。同じモデルが続くときは起動済みをそのまま使う（`local_llm.ensure_ready` は health が通れば載せ直さない）。
- **Strata 同士の載せ替えの不具合を直した**（全部違うモデルの実機で見つけた）: 開発＝qwen-flash-next、簡易検証＝qwen-flash-next-uc（どちらも Strata）にすると、簡易検証の要求がゲートウェイで 300 秒待って 503 を 3 回返し、作業が止まった。Strata の起動は「別の Strata を黙って降ろさない」作りで、`large_model_lifecycle.release_for` も Strata を起こすときは Strata を降ろさなかったため、誰も降ろさず起動が失敗していた。`release_for(runtime, keep=alias)` で、頼まれたもの以外の Strata も降ろす（推論中の要求は `prepare_switch` が待ってから）。`strata_gateway.ensure_ready`・`prepare_switch`・明示のロードから keep を渡す。テスト `test_large_model_lifecycle.py`。
- 実機（全部違う 6 つのモデル、直す前）: 開発 qwen-flash-next → 調査 maya-deepseek-xs（Strata ⇄ Maya の載せ替え）→ 最終検証 hy4-flash-free → 簡易検証 qwen-flash-next-uc まで、それぞれ指定のモデルで答えた。uc で上の不具合。直した版での通しは本番の更新の後に確かめる。
- テスト: `test_opencode_roles.py`（どの組み合わせでも保存・お知らせだけ・全ての役割に明示・箱の一覧）、`test_opencode_workflow.py` の期待を明示に。E2E `opencode-roles.spec.ts`（どの組み合わせでも保存できる）。

## 2026-11-22 OpenCode の「役割ごとのモデル」（役割の表）

- 利用者「計画を実行するモデルと、調査、開発、検証、評価など。同時に 1 モデルしかできないので、同時に実行する可能性のある作業は同じモデルにしたい。どうしても無理なら順番を決める」。
- 役割 6 と段 3: ① 計画 `plan` / ② 作る `build`・`research`・`quick_check` / ③ 確かめる `final_check`（reviewer 系）・`evaluate`。既定は ② が開発と同じ、③ が計画と同じ（計画が未設定なら外部の最初のモデル、無ければ開発と同じで注意）。段をまたぐ載せ替えは許す（ロードはゲートウェイが呼ばれたときに行う今の方式のまま）。
- 決まり（`backend/app/integrations/opencode/roles.py` の 1 か所）: 同じ段のローカルは 1 つだけ（保存は 422 で理由、設定を作る時は揃えて続行）・外部は自由に混ぜられる・最終検証＝開発は注意（保存は止めない）・秘密（confidential の scope か `local_only`）は全役割をローカルへ落とし外部モデルを並べもしない。
- 設定: OpenCode の設定に `roles`。旧 `review_model` は最終検証として読み、roles を保存したら空にする。生成は `provider._workflow`（本番の session）と `sandbox.opencode_config`/`box_roles`（作業場。`model_chain`・機能 `llm`・Zen の可否を守る。`reviewer_model_for` は最終検証の既定に統合）。子エージェント `researcher`・`quick-checker`・`evaluator` を足した（編集不可・重い道具なし）。
- API: `PUT /opencode/workflow` に `roles`、`POST /opencode/roles/check`（保存せず検査）。画面: 設定の「役割とモデル」（段ごとの枠・既定の明示・その場の理由・保存不可・注意）。
- テスト `test_opencode_roles.py`、E2E `opencode-roles.spec.ts`（検証用インスタンスは OpenCode の API が無いので check は模擬）。

- Opus の監査と実機（2026-11-22）:
  - 本物の箱の OpenCode で、開発役（build）はローカル、最終検証役（reviewer の子エージェント）は GLM-5.3 が答えた。子エージェントは設定のモデルが効く。
  - **主のエージェント（plan・build）は、設定の `agents.plan.model` が効かず、セッションのモデルのまま答えた**（`opencode2 run --agent plan` と serve で実測）。会話サーバーでエージェントを切り替えるとき、`POST /api/session/{id}/model` でセッションのモデルも役割のモデルへ切り替える（`chat_server._agent_model_ref`。serve で model-switched が記録されることを確認）。
  - ホストの AI アシスタントでは、既定の役割に外部のモデルを入れない（外部は役割で明示したときだけ）。作業場は了承済みのモデルの順番どおり。

## 2026-11-22 ＋メニューのモードと権限を左右に 1 行・見た目の道具は呼ばれたらロード（利用者の指摘）

- 利用者「OpenCode のモードと権限は、横幅が半分になり、上下に並んでいるものが左右に並んで 1 行になるイメージ」。モード（ビルド／プラン）と権限（Auto／確認）を半分の幅で左右に並べた 1 行にした。押せる高さ 44px、320px では絵文字を外して文字を切らない（E2E で数値と文字切れを確かめる）。
- 利用者「アンロードしていても呼ばれたらロードしてから答える方式は変えないでほしい」。#541 で見た目の道具（`ui-review`）を起動中のモデルだけにしていたのを戻し、ほかの呼び出しと同じく呼ばれたらロードする（ロードさせたくないときだけ `--no-load`）。AI アシスタントなどの動きは元から変えていない。
- テストの直し: `test_dev_workspace_targets.py` を単独で流すと、本物の `ensure_gateway` を掴む前にほかの準備が差し替えていて落ちていた。読み込みの時に掴む。

## 2026-11-22 開発の作業場 4 段目: 行ごとの差分と画面からの「マージ」

- 目的（利用者）: マージの判断を Claude（Opus）に頼らず、利用者が画面で行えるようにする。外部（GitHub）への書き込みと権限に関わる変更（L2）。
- 差分: `GET /opencode/workspaces/{name}/diff?path=`（terminal.use・持ち主のみ）。元の revision から export.git の ref `refs/ws/<名前>/head` までを `git diff` で取る（箱の作業ツリーでホストの git を動かさない。`_host_git` のガードを通る）。path なしはファイル一覧（増減・バイナリ）、ありはそのファイルの unified diff（1 ファイル 256KB で行の区切りまで打ち切って `truncated`、バイナリは中身を出さない）。差分にないパスは 422（オプション注入も同じ）。未コミットは箱の中の `git status` でファイル名だけ（「取り込む前にコミットされます」）。
- マージ: `GET .../merge`（前提の確認。何も書かない）・`POST .../merge`（`confirm` 必須、**settings.manage**）。前提は全部満たすときだけ、満たさなければ 409 と理由: (1) 本体の作業場で PR を取り込み済み・PR の URL がホストの origin のリポジトリのもの、(2) PR が OPEN、(3) `mergeable` が MERGEABLE（CONFLICTING・UNKNOWN は別の理由）、(4) PR の `headRefOid` が export.git の ref と同じで、その tree が関門で確かめた tree と同じ（取り込み後に箱が動いた・PR に別の commit が足されたら不一致）、(5) 最後の関門が合格、(6) 未マージ。区分は `change_policy.classify(export.git, 元の revision, PR の head, controldeck)`（`./deck.sh change-class` と同じ仕組み）。**L2 は確認の文言「L2 を理解した」の一致が必要**（画面は強い警告と当たったファイルを出して入力させる）。マージは `gh pr merge <番号> -R <owner/repo> --squash --delete-branch --match-head-commit <確認した head>`（cwd は export.git のある所）。Release はしない（画面に案内だけ）。監査 `opencode.workspace.merge`（PR の URL・区分・head・L2 に当たったファイルのパス。差分の中身は残さない）。結果は meta の `merged` に保存し、`changes` に `merged` を足した。
- 画面: `WorkspaceDiff.tsx`（新規。ファイル一覧 → 行ごとの差分: 追加は緑・削除は赤・新旧の行番号・横スクロール・打ち切り・バイナリ・未コミットの印）、`WorkspaceMerge.tsx`（新規。PR のリンクの横の「マージ」（管理者だけ）→ 前提の確認（PR・関門・区分・理由）→ 確認 → 結果）。`DevWorkspace.tsx` は import と 2 か所の差し込みだけ。CodeViewer.tsx は全文の表示用で差分の行の種類に合わないので使わず、差分専用の小さな表にした。
- 設計から外れた点: (1) `gh pr merge` に `--match-head-commit` を足した（確認した head 以外はマージしない。確認からマージの間の競合対策）。(2) 区分は `./deck.sh change-class` を呼ばず、同じ `change_policy.classify` を export.git に対して直接呼ぶ（箱の外で動く・サブプロセスの二重起動を避ける）。(3) PR の head の比較は、ref を取り込み直さず今ある export.git の ref と比べる（作業場が取り込み後に変わっていれば 409。取り込み直しが要る）。(4) 差分は `--no-renames`（リネームは削除と追加）。(5) 未コミットは名前だけで、差分の中身は出さない。(6) `deck.sh e2e` は venv を作って pip する（元の venv を使う分岐が無い）ため、同じ手順を元の venv で手で流した。
- 検証: `tests/test_dev_workspace_merge.py`（新規 15: 差分の取り方・箱の中でホストの git を動かさない・打ち切り・バイナリ・パス注入・マージの前提 6 種で 409・箱が動いた/関門が不合格/tree 不一致・URL のすり替え・L2 の文言・権限・監査・Release しない）、`test_dev_workspaces.py`（ルート数 10・merge だけ settings.manage）、E2E `assistant-dev-workspace-diff-merge.spec.ts`（差分 320/1280・マージ L1/L2 320/1280・前提不足のダーク 320）。
- 確かめられなかった点: 実機の GitHub への `gh pr merge`（モック。実際のマージはしていない）、`gh pr view --json mergeable` の実際の値（UNKNOWN になる時間）、`--match-head-commit` と `--delete-branch` を cwd が git でない所で使ったときの gh の挙動。

## 2026-11-22 開発の作業場: 開発対象（機能の組み合わせ）と＋メニューのコンパクト化（L2: 権限と隔離）

- 利用者の言葉: 「ControlDeck と普通の開発の違いを明確にして、＋ボタンのところに開発対象を記載して選択できるように。違いは特に LLM をメイン PC から借りるところ。汎用的にするには、ControlDeck の改修かどうかではなく、機能的な改修対象によって権限を分ける方がよい。プランとビルド・権限もトグルで、表示面積を小さく、＋メニューをコンパクトに」。
- 決めたこと: 作業場の meta（`.meta`）に `capabilities`（`llm` ローカル LLM を借りる・`tools` ControlDeck の道具＝MCP・`mirror` ControlDeck の複製・`gpu` P4 未実装で受け付けるだけ）・`target`・`adopt`（`pr`/`branch`）。開発対象 `app`（tools・branch）・`llm_app`（llm・tools・branch）・`controldeck`（llm・tools・mirror・pr）。`controldeck`・`mirror`・`pr` は元が ControlDeck 本体のときだけ。古い作業場（capabilities 無し）は、元が本体なら controldeck、それ以外は llm_app 相当。作成 API は `target` と任意の `capabilities`。権限は controldeck の作成と gpu が `settings.manage`、他は `terminal.use`。監査に target・capabilities。
- 強制（画面で隠すだけにしない）: 中継 `sandbox_gateway` の起動引数 `--allow-llm`（models・chat・load）/`--allow-tools`（MCP）が無いと該当パスは 403（healthz に llm/tools を出し、機能が違う古い中継は `_gateway_healthy` で作り直す）。`llm` 無しは `model_chain` からローカルを外し、外部も無ければ「使えるモデルがありません」で断る。`llm` ありは箱の環境に `OPENAI_BASE_URL=http://127.0.0.1:<port>/api/v1/llm/v1`・`OPENAI_API_KEY=sandbox`。`tools` 無しは箱の OpenCode の MCP 設定も入れない。`mirror` 無しは `mirror_start` を断る。取り込みは branch なら元へのブランチだけ（PR は作らない）、pr は本体のときだけ。meta の capabilities/target/adopt は改ざん検査の対象。
- 画面: ＋メニューに「開発対象」（3 つの切り替え・1 行の違いの説明・「詳しく」で機能の一覧）。作るときだけ選べて、作業場を選んでいるときは表示だけ。権限の無いもの・元に合わないもの・gpu（準備中）は押せず理由を出す。「プラン／ビルド」「権限」は 1 行ずつのトグル（Shift+Tab・`PUT permission-mode` は変えない）。行の高さ・余白を詰めた（押せる範囲は 44px）。作業場の札は「作業場・本体」「作業場・LLM アプリ」「作業場・開発」。取り込みの文言は開発対象（adopt）で決まる。
- 検証: pytest `tests/test_dev_workspace_targets.py`（新規。preset・組み合わせ・権限・監査・中継の 403・環境変数・順番・mirror・取り込みの振り分け・古い作業場）、E2E `assistant-dev-targets.spec.ts`（1280/320・ライト/ダーク・撮影）。
- 設計から外れた点: ＋メニューの高さの目安（320px で画面の 60% 以下）は、会話の引き継ぎなど「そのときだけ出す行」（各 44px）を除いて測る（含めると 700px の画面で約 500px）。
- 確かめられなかった点: 実機の箱（podman）の中での OPENAI_BASE_URL の効き・中継の再起動（モック）。E2E の `assistant-plan`（4 件）・`assistant-claude-layout`（1 件）・`assistant-dev-workspace`（時々）の失敗は、この変更の前の HEAD でも同じに起きる（e2e 環境に CodeDEV の app が無い・既存のフレーキー）。

## 2026-11-22 画面の見た目の関門（画像を読めるローカルのモデルに撮影を見せる）

- 目的: OpenCode（GLM は画像を読めない）が作った画面を、Claude が目で見なくても仕組みで確かめる。設計は Opus。
- `./deck.sh ui-review <png...> [--spec 説明]`（`app/maintenance/visual_review.py`）: 撮影の PNG を本番のゲートウェイ（loopback）の、画像を読めるローカルのモデルへ送り、`{"issues":[{severity,where,what,fix}],"summary"}` を返させる。観点は ui-ux-guidelines から（文字の切れ・重なり・はみ出し、横はみ出し、押せる大きさ、ライト/ダークの色、空白の偏り、英語の混在、エラー表示）。読めない答えは失敗（終了 2）。
- モデルの選び方: `/v1/models` に足した `vision`・`loaded`（`gateway.instance_sees_images` と同じ判定）で、cloud:・external・auto を除き、画像を読めるものだけ。起動中を優先。無ければ「画像を読めるローカルのモデルがありません」で未実施。古いゲートウェイ（項目が無い）は手元の登録から同じ判定で補う。機密の印（x-control-deck-confidential）を付け、cloud: へ流れても断られる二重の守り。
- 画像: 1 回 4 枚まで、既定で最大 16 枚。Pillow を使わない（依存を足さない）ので縮めず、4 MB 超・PNG でない・読めないものは飛ばして報告に出す。
- dev-check の「画面の見た目」: frontend（e2e を除く）を変えたときだけ。`frontend/test-results/**/*.png` のうち frontend の変更より新しいものが無ければ不合格（「撮影がありません（E2E で撮ってください）」）。high があれば不合格、medium は合格のまま注意を表示。モデルが無ければ未実施。見させるのに失敗（通信・答えが読めない）は不合格。`--skip-visual` で外せる（報告に出る）。backend だけの変更では未実施。
- スキル `controldeck-development` に「画面を変えたら E2E で 320・1280、ライト・ダークを撮り、dev-check の見た目の項目を通す」を足した。
- 検証: `tests/test_visual_review.py`（新規。モデルの選び方・cloud を使わない・未実施・4 枚ずつ・答えの検め・dev-check の項目の合否）、`tests/test_llm_gateway.py`（vision・loaded）。実機: 本番の起動中の qwen-flash-next に 320 の撮影 2 枚を見させ、形どおりの JSON が返った（17 秒）。
- 確かめられなかった点: 本番のゲートウェイはまだ新しい項目（vision）を返さない（次の更新後）。それまでは手元の登録から補う（実機ではこの経路で確認）。縮小は未実装。dev-check 全体を frontend 変更つきで実機に流すこと。

## 2026-11-22 開発のスキルに「要望から指示書（計画）を書く」段

- 利用者「Claude が要る部分の開発にまず注力」。2026-11-22 の試験で、成果を一番左右したのは Opus が書いた指示書の質だった。その型（目的・まず読むもの（手本の実在を確かめる）・作るもの・守ること・テスト・完了の条件・利用者に決めてもらうこと）を `controldeck-development` のスキル（1.1.0）の最初の段にした。
- 指示書が渡されていなければ `.controldeck/plan.md` に書く。プランのモードでは要点を見せて止まり、ビルドに切り替えたらその指示書どおりに進める。ビルドのモードで決めてもらうことが無ければそのまま進む。大きな作業は段に分けて段ごとに関門とコミット。
- 検証: `tests/test_dev_skill_planning.py`（1）。

## 2026-11-22 ミラーの LLM（P3）: 上流の LLM を中継経由で使う

- 目的: 箱のミラー（`CONTROL_DECK_ISOLATED=1`）はモデルの登録が空で、AI アシスタント・Patent の AI・ナレッジの分析が LLM を使えなかった。ミラーに「上流の LLM」を設定し、箱の中の中継（127.0.0.1:<gateway_port>）経由で本番のローカルのモデルへ送る。
- 設定: `config.llm_upstream`（`mirror_config` が `http://127.0.0.1:<gateway_port>/api/v1/llm/v1` を書く）。`gateway.upstream_base()` が読むのは ISOLATED のときだけ（本番では無視）。loopback の http・ポート付き・パスに `/api/v1/llm/v1` を含むものだけ受け、それ以外は空扱い（設定ミスで箱の外へ出さない）。
- 振り分け（既存の関数に分岐を足しただけ）: (1) `gateway.resolve_internal_target`（`RuntimeChatRequest` 経路・`resolve_target`）: 上流があればゲートウェイ宛を上流へ（モデル名・`cloud:` はそのまま）。(2) `ai_gateway.resolve_ai_target`: 上流の `/models` を確かめ（接続できない・空なら `AITargetUnavailable`）、`AITarget(上流, "auto", gateway_managed=True)`。`require_local_target` は上流の URL なら通す（先は本番のローカルのモデル。外部は中継が断る）。`resolve_local_target`（Patent）などはこれで動く。(3) ミラーのゲートウェイ `GET /models`・`POST /chat/completions`: 上流へそのまま中継（ストリーム含む）。機密の印・呼び出し元・accept を渡す。
- 中継（`sandbox_gateway.py`）: 機密の印 `x-control-deck-confidential` を上流へ渡すよう `FORWARD_REQUEST_HEADERS` に 1 語足した（以前は落ちていて、`cloud:` を許可した箱で印が効かない）。外部のモデルは既定で断る（今までどおり）。
- 設計から外れた点: 中継は呼び出し元を常に `opencode` に付け替えるので、アシスタントの `assistant` は本番へ届かない（外部のモデルの許可は箱では効かず、既定の断りが効く）。モデル名は `auto` 固定（本番の Models で選んだものへ流れる。ミラーの画面でモデルを選ぶ操作は上流の一覧のとおり）。
- 検証: `tests/test_mirror_llm_upstream.py`（新規 8。ISOLATED のときだけ読む・loopback 以外を拒否・内部の振り分け・AI target と local_only の検査・上流が空/落ちている・RuntimeChatRequest のストリームと機密の印・ゲートウェイの models/chat の中継）、`test_dev_sandbox.py`（mirror_config が中継の URL を書く）。
- 確かめられなかった点: 実機のミラー（podman）から中継経由で本番のモデルへ会話できるか（AI アシスタント・Patent・ナレッジの分析）。実際の長い生成での中継のタイムアウト（中継は read 900 秒）。

## 2026-11-22 E2E 直し: モデル管理の画面 2 件（HANDOFF 不具合と課題 14）

- 対象: `frontend/e2e/model-provider-common.spec.ts` と `frontend/e2e/resource-load-profile-ui.spec.ts` が main のまま落ちる。まず `frontend` の build 後に検証用インスタンス（`cmd_e2e` と同じ手順、箱では `./deck.sh e2e` が PyPI 取りに行き使えないので python だけ既存 venv に差し替え）で再現してから直した。
- 原因と根拠（どちらも「画面が変わったのに E2E が古い」で、画面のバグでは無い）:
  - `model-provider-common`: `getByRole('button', { name: '閉じる' })` が部分一致で、画面shell刷新 ad73f23（☰ サイドバー）で増えた `aria-label="サイドバーを閉じる"` と衝突（失敗時の aria スナップショットに「サイドバーを閉じる」と「閉じる」の 2 件）。→ 個別設定 sheet（dialog 名 `fake-model`）とモデル取得 sheet（dialog 名 `モデル取得`）の中だけに絞った。確かめる中身はそのまま（保存の PUT、取得ジョブの POST、320px の横溢れ）。
  - `resource-load-profile-ui`: 退避(yield)機構を 4deb560（2026-09-04「画像生成をRAMへ載せてLLMと共存させ、退避(yield)機構を廃止する」）で廃止、GPU Broker パネルごと `frontend/src` から削除済み（`今すぐ退避`・`実測（暫定）`・`load_profiles` が grep ゼロ、失敗時の aria スナップショットでもダイアログは開きつつ該当欄が無い）。→ 後継の「Add-on への GPU の譲り方」パネル（`AddonGpuSettingsGroup`）に読み替え: 方針の説明（自動）、載せ直し制御と貸す時間の欄、載せ直し目安の実測文、方針を「LLM 優先」にすると載せ直し制御が消え説明が連動すること、320px の横溢れなし、を PC/モバイル両幅で確かめる。
  - 保証の変化: 旧 spec の cold/warm 実測・退避根拠・「処理時間が未申告」の説明は、対象機能が製品から削除されたため立て直せない（削除は機能の意図的な廃止で、画面の退行ではない）。同じダイアログのモバイル/デスクトップ表示と 320px 非溢れの保証は保ったまま、現存する後継機能の説明表示へ読み替えた（理由を spec 先頭の目的・根拠にも書いた）。
- 変更: `frontend/e2e/model-provider-common.spec.ts`（閉じる 2 箇所を dialog スコープに）、`frontend/e2e/resource-load-profile-ui.spec.ts`（全面書き換え。`/api/v1/resources` の mock と yield 欄の policy フィクスチャは未消費なので削除）、`docs/test-catalog.md`（spec の目的を反映して作り直し）。コード・画面は変えていない。
- 検証: 対象 2 件 pass。関係 E2E（`frontier-models`・`model-display-name`・`runtime-preferred-model`・`llama-runtime-settings`・`vram-margin`・`gpu-settings` の 15 テスト）を一緒に流し、13 pass / 2 fail。**落ちた 2 件（`frontier-models`・`llama-runtime-settings`）は今回の差分と無関係な既存の main 落ち**（それぞれ別種の画面 drift。不具合と課題 15 として記録）。
- レビュー役（別のモデル）: critical 0・major 0・minor 3。minor は直した — (1) 目録が旧題名のままだった → 作り直してこの差分に含めた (2) 320px の横溢れ確認が方針を「LLM 優先」に切り替えた後だけになっていた → 切り替える前（載せ直し制御が出ている状態）でも確かめた (3) コメント誤記。info は見送った: toHaveCount(0) を combobox にも足すのは同一の条件分岐で等価、`gateway_only`・`drain_timeout_sec` の interface 残存と waitLabels の yield_* は画面側の変更になるためこの PR のスコープ外（実害なし）。
- 確かめられなかった点: GitHub Actions の E2E 一式（箱のため）。`./deck.sh dev-check` の backend pytest は箱の `.venv` が空で流せない（外部取得禁止のため依存を入れられない）→ frontend build と差分に基づく静的検査まで。詳細は下の関門の行。

## 2026-11-22 知識マップ・技術仮説のリンク（K3）: 1〜2 hop の読み取りと inferred の明示登録

- 目的: K1・K2 で文献ごとに抽出・承認した知識エッセンスを、**文献をまたいだ構造**で見られるようにする。
  PatentBuddy Draft #34（知識マップ）・#35（技術仮説のリンク）の考え方を ControlDeck の書き方で
  作り直した（コードは写していない）。読み取りのマップは LLM を呼ばない。リンクの作成も
  AI に推論させず利用者が明示して結ぶ（`basis=inferred` 固定・理由のメモ必須）。
- 知識マップ（読み取りだけ）: `app/knowledge/map.py`（新規）。`GET /api/v1/knowledge/documents/{ref}/map?hops=1|2`。
  中心の文献から書誌の関係（documents.related_documents の引用・別の版）で 1〜2 hop の文献を広げ、
  それぞれの**承認済み・今の版の**エッセンス（K1 の list_essences の判定を使い回す）をノードと
  エッジで返す。上限はノード 61・エッジ 60（PB の 36/52 を広げた）、1 文献の知識は 8 件まで。
  超えたら打ち切りの印（truncated）。引用の再照合は行わない（K2 の横断検索と違い、2 hop の文献数ぶんの
  全文読みは重いため。エッセンスは保存時に検めてある）。
- 技術仮説のリンク: `app/knowledge/links.py`（新規）。承認済みのエッセンスどうし（別の文献）を
  利用者が明示して結ぶ。作っただけでは「未確認」、別の操作（承認・却下）で確かめる。レビューは
  追記だけ（K1 と同じ）。どちらかの知識が今の版で承認済みでなくなったらリンクも stale
  （判定は K1 の list_essences に任せる）。リンクのレビューには両側の版の番号（epoch）を
  記録し、番号が変わったら前の判断は効かない（知識の承認をやり直しても同じ文面でも戻らない。
  K1 と同じ決まり）。保存はナレッジの shard DB に表（knowledge_links・knowledge_link_reviews。
  K1 の表の足し方）。リンクの行は source 側の分野に置き、target は「分野:番号」で持つ。
  文書の差し替えでは `store.py` の `_carry_links`（同分野）・`_carry_links_other_shards`
  （コミット後に他分野）で付け直し、削除では `_delete_link_rows`・`_delete_link_rows_other_shards`
  で消す（annotations・essence と同じ考え）。後から足した列は `store._LATE_COLUMNS` と同じ
  作りで ALTER で足す。
- API・権限: マップとリンク一覧は閲覧（workflows.run）、リンクの作成・レビューは workflows.edit、
  監査は作成・レビューに id と判断だけ記録（K1 と同じ）。endpoint は `def` で threadpool へ
  （AGENTS.md ルール 9）。
- 画面: DocumentSheet（`features/knowledge/`）に「知識マップ」（`KnowledgeMap.tsx`。SVG で手書きの
  放射図・依存を足さない。hop 1/2 の切り替え・打ち切りの印・ノードを押すとその文献を開く・
  320px で読めるよう題名は 8 字に切って全文は title 属性）と「知識のつながり」（`LinkSection.tsx`。
  エッセンスのカード（`EssenceSection.tsx`）に「別の文献の知識と結ぶ」（`LinkMaker.tsx`。
  相手は K2 の横断検索から選ぶ → 理由 → 作成）。
- テスト: `backend/tests/test_knowledge_map.py`（8 件: 1/2 hop・承認済みだけ・stale・上限と打ち切り・
  形の悪い指定・権限・threadpool）・`test_knowledge_links.py`（10 件: 作成の門・未確認から承認への
  2 段階・追記だけ・source/target それぞれの失効・同じ文面へ戻しても戻らない・両側からの一覧・
  重複・分野をまたいだ差し替え・権限・threadpool）。knowledge 系の回帰 57 件。
  E2E `knowledge-map.spec.ts` 2 passed（API は page.route で差し替え。1280・320、ライト・ダーク、
  撮影 4 枚）+ knowledge・knowledge-essence・knowledge-essence-cross の回帰 13 passed。
- PatentBuddy から変えた点: PB の GraphRAG（PR #23 の graph_nodes/graph_edges）を正本にせず、
  ControlDeck の書誌の関係（identifiers）+ K1 のエッセンスの投影として作った。PB の
  evidence_graph.save/review・`_connection` トランザクション拡張・`knowledge_link_origins` 表・
  deterministic な edge id（write0）を作らず、K1 の表の足し方に合わせた。レビューは
  PB の graph_revision 409 ではなく K1 と同じ「追記だけ・stale では付けられない」に寄せた。
  上限 36/52 → 61/60、`include_unreviewed` の opt-in は作らない（未確認の知識は出さない割り切り）。
  素の JavaScript の画面 → React の SVG（部品は既存のものだけ）。
- レビュー役（別のモデル）の指摘を直した: critical 1（`_delete_link_rows` の SQL バインド数ミスで、
  リンクの表がある分野で文書・コレクションの削除が 500 になる。一覧を開くだけで全分野に表が
  作られるので最悪の発動条件だった）・major 4（一覧の重複除去を id だけでしていた（分野違いの
  同番号が消える）→「分野と番号」の組へ。文書削除でほかの分野に残る target のリンクも消す
  `_delete_link_rows_other_shards` を足した（残ると id の使い回しで無関係な文書へ「復活」する）。
  LinkSection の「メモを添える」の note を送っていなかった→直した。SVG の 2 段目の知識の円が
  viewBox の外にはみ出して押せなかった→viewBox 420・半径を詰めて収まるようにした）。
  minor も直した: リンクのレビューに両側の版の番号（epoch）を記録し、知識の承認をやり直しても
  前の判断が復活しない（K1 と同じ考え）・`HOP_DOCS` で切ったときも打ち切りの印・
  `_carry_links_other_shards` の connect を try の中へ。テストも 5 本足した（削除×リンク・
  分野またぎの同番号・epoch の復活禁止・HOP_DOCS・E2E で 2 段目の知識の押下とメモの送信）。
  1 件落ちた knowledge.spec の「審査基準のコレクション」は使い回した検証インスタンスに
  guidelines-e2e が残っていた環境起因と確かめた（POST /knowledge/collections 422。
  使い捨ての新インスタンスでは 15/15 通過）。
- 再レビュー（同じモデル・2 回目）の major を直した: `knowledge_link_reviews` に後から足した
  列（source_epoch・target_epoch）の追加移行が無く、列の無い DB で一覧・レビューが 500 に
  なっていた → `links.connect` で `PRAGMA table_info` を見て ALTER で足す（`store._LATE_COLUMNS`
  と同じ作り。テスト `test_connect_adds_missing_review_columns_to_existing_tables` で旧スキーマを
  再現して確認）。minor も直した: モジュールの説明文を実装に合わせた・レビュー後にメモを
  クリア（K1 と同じく成功したとき。403・失敗時は入力を残す）・`LinkSide` 型に epoch・
  `add_document` の他分野への付け替え・削除の呼び出しを try の外（本体が登録できたのに
  500 にならない）へ・`essence.epoch_of` を公開の形に・スイープの `shard_path` も try の中へ
  （storage が壊れている分野は警告を残して続ける）。SVG も微調整（ラベル 7 字・viewBox 440・
  各段の半径を見直して、どの段の要素も枠に収まり段どうしが重ならない）。残した軽微な点は
  報告のとおり（一覧の N+1・並び順・逆向き重複の許容など。一覧は 1 文献 100 件までで実測
  39 件 81ms）。3 回目のレビューは合格（critical 0・major 0・minor 2 → minor を直して確定）。
- 確かめられなかった点: 撮影 PNG の目視（このモデルは画像を読めない。E2E の要素単位の検査と
  320px の横 overflow 無しは機械的に確認済み）。本物のローカルモデルでの抽出を含む通し
  （検証用インスタンスにモデルが無いため、エッセンスの応答は E2E で差し替え）。

## 2026-11-22 開発の作業場 3 段目: 結果のカードと「取り込む」

- 目的: 作業場の会話に「作業場の結果」のカード（変更の要約・関門・取り込む）を足し、頼む → 作る → 見て試す → 取り込む、を画面で通す。本番を変えるのは利用者の一押しの後だけ。
- API（`/opencode/workspaces/{name}/…`、持ち主のみ、権限は terminal.use）: `GET changes`（元の revision からの差分の要約。中身は返さない）、`POST check`（関門 `dev_check.run_all(base=元の revision, repo=箱の repo)` を Job で流し、結果・時刻・HEAD・作業ツリーの tree を meta に保存）、`GET check`、`POST adopt`（confirm 必須）。
- 取り込む: 最後の関門が合格で、その後に変更が無いことが前提（満たさなければ 409 と理由）。未コミットがあれば `commit_message` でホスト側でコミット（関門が当てた内容と同じ tree のときだけ）。本体は ホストの git で push（`ws/<名前>`）→ `gh pr create`、マージはしない（`settings.manage`）。CodeDEV は元のリポジトリへ `fetch` でブランチ `ws/<名前>` として取り込むだけ（外へ出さない）。監査 `opencode.workspace.check/adopt`（名前・ブランチ・PR の URL のみ）。
- 脅威と対策（2026-11-22 監査で追加。L2・安全）: 箱の中の AI は `box.repo/.git/config` とフックを書き換えられる。ホストの git を box.repo の中で動かすと、`core.fsmonitor`・`credential.helper`・`core.sshCommand`・`core.askPass`・`url.*.insteadOf`/`pushInsteadOf`・`http.proxy`・`include.path`・フックで、ホストの権限で箱の中のスクリプトが動きうる（`-c` での打ち消しは漏れが出るので採らない）。対策: **ホストでは box.repo の中で git を動かさない**。(1) ブランチ・未コミット・未 push・tree は箱の中の箱の git に調べさせ結果の文字だけ受け取る（`sandbox.probe` / `in_box`。一覧用に 30 秒は meta に覚える）。(2) 箱のコミットは、ホストが管理する bare の `<data>/workspaces/export.git` へ `fetch --no-tags <box.repo> +HEAD:refs/ws/<名前>/head` で取り込み、差分・コミット一覧・tree の比較・push はそこから行う。(3) 未コミットのコミット（`commit_message`）は箱の中で `git add -A && git commit --no-verify`（作者は箱の git の名前）。(4) push は `git -C export.git push <ホストの本体リポジトリの origin> refs/ws/<名前>/head:refs/heads/ws/<名前>`。push 先は、箱の AI が書ける meta ではなくホストの本体の origin から取る。`gh pr create` は export.git のある所を cwd にして `-R owner/repo` を明示。(5) CodeDEV は `git -C <元> fetch <export.git> refs/ws/<名前>/head:refs/heads/ws/<名前>`（box.repo から直接 fetch しない）。(6) 関門は**箱の中で流す**（`sh -c 'cd backend && exec <元の venv の python> -m app.maintenance.dev_check --base <rev>'`）。理由: export.git からの一時 worktree でホストで流すと、箱の AI が書いたテスト・コードがホストの権限で動く。箱の中なら隔離が保たれ、未コミットの変更も関門が見る。代わりに、箱の AI が dev_check 自体を書き換えて「合格」にしうるので、取り込み（PR）の前に pushed したコミットの tree が関門で確かめた tree と同じことを確かめ、マージ前の関門は本番側で改めて通す。(7) 2 段目の `unsaved`・`summary`・`list_for`・`sandbox.status`・`list_all` も同じ理由で箱の中の調べに切り替えた。`sandbox.create` の clone 直後の checkout・remote set-url は、まだ箱の AI が触れる前なのでそのまま。
- 設計から外れた点: (1) push は `origin` の名前でなく、ホストの本体の origin の URL へ直接（`push origin` と違う）。(2) 「関門の後に変更が無い」は HEAD の一致に加えて作業ツリーの内容の tree で判定（未コミットをここでコミットしても、関門が当てた内容と分かる）。取り込む内容は export.git の tree が関門の tree と一致することも確かめる。(3) 同じ作業場で再度取り込むと、PR は重ねず既存の PR の URL を返す（push は更新）。(4) 差分の要約はコミット済みの分（未追跡のファイルは一覧に出ず、未コミットの件数と印だけ）。
- 脅威と対策 2（同日・L2・安全）: 箱の設定 `sandbox.json`（owner・origin・source・revision・overlays・git_common_dir・ポート）が箱の書ける `box.path` の中にあり、箱の AI が書き換えると、次にホストが作る podman の引数で overlays に `~/.ssh` などを足して見せられた。対策: meta を箱の外 `<sandboxes root>/.meta/<名前>.json`（ディレクトリ 0700・ファイル 0600、`.relay/` と同じ考え。箱へはマウントしない）へ移し、`load`・`_save_meta`・ポートの一覧を切り替えた。新しい箱は最初から .meta へ書く。古い箱（`box.path/sandbox.json`）は load のときに一度だけ、**中身を信用せず**、引き継ぐ項目（名前・source・git_common_dir・revision・origin・ポート・owner・overlays・workspace_source など）だけを検査して移し、古いファイルを消す（関門の結果・取り込みの記録・調べた状態は引き継がない）。load のたびの検査: name が箱の名前と一致・source が箱の中を指さない・git_common_dir が source から求め直した値と一致・overlays は `overlay_pairs`（作る時と同じ規則: `source/.venv`→同じ場所、`source/frontend/node_modules`→`box.repo/frontend/node_modules`）の 2 種類だけ（それ以外は捨てる）・ポートは決めた範囲の整数だけ・owner などは文字列。通らなければ「この箱の設定は改ざんの恐れがあるので使えません（作り直してください）」で断る（消す操作だけは元のリポジトリを見ずに通す）。箱の中から古い形のファイルを置き直されても使わず消す。`.meta` の中の関門の結果・取り込みの記録は箱から書けない。
- 画面: `DevWorkspace.tsx` の `WorkspaceResultCard`（作業場の札の下。OpenCode の回が終わると読み直して開く。ファイル一覧・未コミットの印・関門の合否と不合格の行・関門を流す（3 秒ごとに進みを見る）・取り込む（確認、未コミットならコミットメッセージ））。ボタンは 44px。
- 検証: `tests/test_dev_sandbox.py`（meta を箱の外へ: +9。.meta の 0700/0600・箱へマウントしない・古い箱の移行で足した overlays が podman の引数に入らない・git_common_dir などの改ざんで断る）、`tests/test_dev_workspace_adopt.py`（新規 10。箱の .git/config に fsmonitor・credential.helper・sshCommand・insteadOf・フックを仕込んでも、一覧・状態・差分・関門・取り込み・削除でホストで動かない実測を含む）、`test_dev_workspaces.py`（ルート数）、E2E `assistant-dev-workspace-result.spec.ts`（320・1280・ダーク）。
- 確かめられなかった点: 実機の GitHub への push・`gh pr create`（モック）。ホストの git 認証・gh のログイン。実機の箱（podman）の中での `probe`・コミット・関門（テストでは箱の中の実行を同じ作業ツリーでの実行に置き換えている）。一覧が箱を起こす重さ。

## 2026-11-22 知識エッセンス（K2）: 承認済みの横断検索・別文献の比較

- 目的: K1 で文献ごとに抽出・承認した知識エッセンスを、**文献をまたいで**探せるようにする。
  PatentBuddy Draft #36（K4）の考え方を ControlDeck の書き方で作り直した（コードは写していない）。
  どの文献を探すかは画面の「探す」と同じ検索の入口（`documents.search_documents`）に任せ、
  ここでは承認済み・今の版・引用が原文に一致する item だけを集める。比較は並べて見せるだけ
  （AI の判断・意味の一致・新規性は足さない。PB #36 の「side_by_side_literal」と同じ割り切り）。
- backend: `app/knowledge/cross.py`（新規・読み取りだけ。書き込み・監査・Job は無し）。
  `search(q, kind, limit, cursor)`: search_documents（consumer=local。エッセンスはローカル専用の
  推論で、この検索は後のローカルの AI 支援へ知識を渡す入口でもあるため、利用条件で AI に使えない
  文献（ai_use=prohibited）は出さない）→ `essence.list_essences`（K1 の版・レビューの判定をそのまま
  使い回す）→ 引用を原文の [start, end) ともう一度突き合わせる（合わない item は出さない）。
  1 回の走査は 40 文献まで、応答は 50 件まで、続きは next_cursor（PB #36 の 240 イベント相当の
  打ち切りを、文献単位の走査の上限として作り直した）。`compare(refs)`: 別の文献 2〜3 件を
  種類ごとの表に並べる（同じ再照合を通る）。
- API: `POST /api/v1/knowledge/essences/search`・`/essences/compare`（router.py）。読み取りだけなので
  権限は既存の閲覧（workflows.run）と同じ。endpoint は `def` で threadpool へ（AGENTS.md ルール 9）。
- 画面: Knowledge に 4 個目のタブ「知識の横断」（`frontend/src/features/knowledge/EssenceCross.tsx`）。
  検索の欄 + 種類の絞り込み → 結果のカード（文献の題名から文献の画面へ行ける・引用の折りたたみ・
  承認の日時）→「比較に加える」（別の文献 2〜3 件まで。選択を変えたら古い比較は捨てる）→
  「比較する」で種類ごとの表（320px では横スクロール。AI の判断を足していないことを画面にも明記）。
  何も見つからないときは「文献の画面で知識を抽出し、承認すると、ここで横断して探せます」と案内。
- テスト: `backend/tests/test_knowledge_essence_cross.py`（9 件。承認済みだけ・stale・引用の破壊・
  prohibited の門・種類の絞り込み・上限とページ送り（limit=1 でも文献の途中で切らない）・走査の上限・
  比較の形・権限・threadpool）。knowledge まわりの回帰 40 件、frontend build、E2E `knowledge-essence-cross.spec.ts`
  5 passed（API は page.route で差し替え。1280・320、ライト・ダーク、撮影 6 枚。検索・絞り込み・引用の開閉・
  比較・もっと見る・空の案内）+ knowledge・knowledge-essence の回帰 8 passed。
- レビュー役（別のモデル）の major 指摘を直した: 当初の limit は item 単位で切れており、文献の途中で
  limit に達すると残りの item がページ送りの先で永久に出なかった。**同じ文献の item はページの途中で
  切らない**ようにした（応答は limit を最後の 1 文献の分だけ超えうる。欠落・重複なしを優先）。
  minor も直した: 比較の表のセルの型を実応答に合わせた（文献の情報は列の見出しへ）、「もっと見る」は
  最後に検索した条件で続きを取る（検索欄を変えても結果に混ざらない）、種類の選択肢は最初から出る
  （`ESSENCE_KINDS` 固定。検索応答で上書き）、走査の間に消えた文献は検索全体を落とさず飛ばす、
  cursor は 500 まで（超えたら 422）。全文を 1 文献あたり 2 回読む点（版の照合と引用の照合）は
  K1 の判定を使い回す優先で残した（ユーザーが明示的に押す検索で、1 回の走査は 40 文献まで）。
  再レビューは合格（critical・major なし）。残った軽微な指摘も直した: 応答上限の docstring を
  「その文献の承認済み全件数」の趣旨に直した、1 回の走査で承認済みが 1 件も無いページでも
  「もっと見る」で先へ進める（空の案内は本当に尽きたときだけ）、ページングのテストに
  重複検出（件数の検査）を足した。
- 決定として記録: 横断検索は `search_documents` に `query_vector` を渡さない（全文索引のみ）。
  画面の「探す」と比べると意味の近い表現の当たりは弱くなるが、埋め込みサーバーの可用性に
  検索結果が左右されず、走査（40 文献・冪等）が予測できる。埋め込みを足す判断は残す。
- PatentBuddy #36 から変えた点: PB 独自の認証（qlk_ トークン）→ ControlDeck の RBAC（workflows.run）。
  レビューイベント 240 件の keyset 走査 → search_documents の文献候補 40 件の走査（検索の入口を
  1 つに保つ決まり §10.2 の 5）。casefold 部分文字列照合 → search_documents（FTS・番号の一致）。
  PB の 9 分類 → K1 の 8 種。素の JavaScript の画面 → React の部品。
- 確かめられなかった点: 本番のデータ（実際に承認したエッセンス）での動作。撮影 PNG の目視は
  このモデルではできない（画像を読めない）。
  開発: 箱の OpenCode（GLM-5.3）が作り、Opus が監査（撮影は Opus が確認）して採用。

## 2026-11-22 モデル管理の既知の不具合 4 件の点検（件1・2は直っていた／件3・4の死んだ設定を整理）

- 対象: `docs/HANDOVER-model-management-v2.md`「既知のバグ」の 4 件。文書は 2026-08 で止まっていて古いため、
  今のコードで 1 件ずつ起きるかを確かめた。
- 件1 `save_instance` が既定チャット先（`selected_alias`）を常に奪う: **直っていた**（追加のコード変更なし）。
  今の実装は「新規登録時か、まだ既定が無いときだけ引き継ぐ」（`llama.py` の `save_instance` 末尾。
  966f1d7 で入った）。既存モデルの保存で奪わないことは `test_llama_runtime.py::test_multi_instance_catalog_uniqueness_and_unit_names`、
  削除で変わらないことは `test_delete_instance_keeps_selection_when_other_model_removed` が確かめている。
  利用者の明示的な切り替えは `select_instance`（`POST /llama/instances/{alias}/select`）。
- 件2 `llama_delete_instance` の `gguf_deleted` が常に False: **直っていた**（追加のコード変更なし）。
  今の router は `llama.delete_instance(alias, delete_file=…)` の実結果を透過し（`{"ok": True, **result}`）、
  `delete_instance` は GGUF 本体を実際に消したときだけ `gguf_deleted: True`（共有参照・権限外は理由つきで False）。
  966f1d7 で入った。`test_llama_runtime.py::test_delete_instance_can_remove_gguf_but_protects_shared_files` と
  API 透過は `test_rag_db_nodes.py` が確かめている。
- 件3 `RuntimePolicy.default_model_ref` が定義だけで参照ゼロ: **使われていない定義として整理（削除）**。
  既定のモデルの決め方は今のコードでは ① gateway の `resolve_instance`（`auto`・未登録時: 選択中ランタイム →
  起動中 → `preferred_models` の優先 → 登録順）② ⚙️共通設定の `selected_runtime` と `preferred_models`
  （ランタイム別の優先モデル。保存時に起動可否を検証）に置き換わっており、単一の `default_model_ref` を読む箇所は
  バックエンドに 1 件も無い（grep で確認）。読む場所を新しく作ると `preferred_models` と二重の既定になって
  挙動が変わるので、定義を削除（`runtime_policy.py`）し、フロントの型と e2e の mock からも該当キーを落とした。
  古い `model-runtime-policy.json` にキーが残っていても pydantic が読み捨てるのでそのまま読める
  （`test_runtime_policy.py::test_default_model_ref_is_dropped_from_policy` で担保）。設計書（A フェーズ）の実用化は
  他仕組みで代替済みとして行わない。
- 件4 `ollama-settings.json` の `idle_unload_enabled` / `idle_unload_minutes` が書けるが効かない:
  **画面は既に RuntimePolicy（⚙️共通設定）を指している**（Models の「アイドル時に自動アンロード」は policy を編集。
  ollama・llama.cpp・Maya-S/Strata のアイドルループはすべて 32e5c7b 以降 RuntimePolicy を読む）。
  残っていた ollama 側の死んだキーを整理: `ollama.DEFAULT_SETTINGS` と API の `SettingsBody` から削除し、
  `PUT /models/settings` に渡っても設定ファイルへ書かれないようにした（モジュール docstring にも「⚙️共通設定が正」を明記）。
  すでに古い `ollama-settings.json` に残ったキーは読み込み時に混ざるだけ（どのループも読まない）で実害なし。手動掃除は不要。
  テスト: `test_models_local_register.py::test_ollama_settings_has_no_dead_idle_unload_keys`。
- 検証: 関連 backend テスト（`tests/test_llama*.py`・`tests/test_*model*.py`・`tests/test_runtime*.py`・
  `tests/test_llm*.py` で 277 件、`tests/test_rag_db_nodes.py` 22 件）すべてパス。新規テスト 2 件は修正前に落ちることを確認。
  `cd frontend && npm run build` 成功。`./deck.sh dev-check` 合格。
- 確かめられなかった点: e2e `model-provider-common.spec.ts` と `resource-load-profile-ui.spec.ts` はこの作業ツリーの
  **変更前からも失敗**（変更を退避して再実行して同一の失敗を確認。側ナビの「閉じる」ボタンの strict 一致と、
  LLM 共通設定ダイアログの文言 drift が原因で、今回の変更とは無関係）。実画面での ⚙️共通設定の操作確認は未実施。

## 2026-11-22 開発用の箱の npm・pip をオフラインに

- 実機: 箱の OpenCode（GLM-5.3、K1b の作業中）が `npx tsc` を流し、npm からパッケージを取ってきた（外部からの取得の禁止に反する。本人の報告で判明）。箱の環境に `npm_config_offline=true`・`PIP_NO_INDEX=1`。手元（キャッシュ）に無いものは取りに行かず失敗する（`npx` で `only-if-cached` の失敗を確認）。
- 検証: `tests/test_dev_sandbox.py`（+1）。

## 2026-11-22 知識エッセンス（K1b）: 文献の画面のカードと人のレビュー（frontend のみ）

- 目的: K1a（backend。`essence.py` の抽出・検め・レビュー・版の失効）を、文献の画面（DocumentSheet）から
  使えるようにする。PatentBuddy Draft #32・#33 の「知識カード・人のレビュー」の考え方を ControlDeck の
  書き方（React の部品・権限・Job の見せ方・`docs/ui-ux-guidelines.md`）で作り直した（コードは写していない）。
- `frontend/src/features/knowledge/EssenceSection.tsx`（新規）: DocumentSheet の「AI の分析」の下に
  「知識エッセンス」の節を足した。まだ無ければ「知識を抽出（ローカルの AI）」（`workflows.edit` の利用者のみ。
  AI に使えない文献（ai_use=prohibited）は始める前からその理由を出す）→ 開始（`POST /documents/{ref}/essences`）→
  Job の進み具合（pages/Knowledge.tsx にあった RagJobProgress を features/knowledge/JobProgress.tsx へ移して共用）→
  終わったら一覧を読み直す。503（ローカルのモデルが無い）・422（利用条件）は返ってきた理由の文をそのまま出す。
  Job が「前回と同じ内容」（unchanged）を返したときは「既存の結果とレビューをそのまま使います」と出す
  （backend が同じ内容の再実行を新しく書かないのと合わせる）。
- 一覧は抽出（版）ごとに行、行の中を種類（課題・原因・原理・作用機構・実装・効果・制約・応用）ごとにまとめたカード。
  各カード: 要約・「AI の推論（未確認）」の印・レビューの状態（未確認・承認済み・却下・差し戻し）・引用の折りたたみ
  （開くと原文からの引用）・前回のレビュー（誰がいつどうした。API が返す最新の 1 件）。
  権限のある利用者は承認・却下・差し戻しを押す（メモは任意で折りたたみの中）。「押したら API → 一覧を読み直して状態が変わる」。
  原文の版が変わった行（stale）は「原文が変わりました（前の承認は効きません）」の印を出し、レビューの操作を出さない
  （backend が古い版へのレビューを断るのと同じ）。検めで捨てた件数（形違い・上限超過）も小さく出す。
  取り消し（canceled）の Job は「抽出を取り消しました」と通常の通知に、失敗・再起動中断は赤のまま。
  一覧の初回取得中は操作を出さない（取得前に「抽出」が一瞬出るのを防ぐ）。
- API は `frontend/src/api/knowledge.ts` に essences（一覧）・extractEssences（開始）・reviewEssence（レビュー）を追加。
  権限の出し分けは既存の画面（DocumentSheet の分析・ClaimJudge）と同じ `can("workflows.edit")`。
- E2E `frontend/e2e/knowledge-essence.spec.ts`（新規。API は page.route で差し替え、本物の LLM は呼ばない）:
  抽出の開始と Job の進み具合・一覧の表示、カードの引用の開閉、承認・却下・差し戻しで状態が変わる、stale の印・捨てた件数、
  503・422 の理由の表示、見るだけの利用者（/auth/me を差し替え）にボタンが無い、1280・320 で横に崩れず
  ライト・ダークの撮影（test-results/knowledge-essence-*.png）。5 passed。
  あわせて knowledge・patents の E2E も回帰（DocumentSheet を共有しているため）: 17 passed。
  途中で patents の spec が落ちて `essences` キーの無い応答で `refetchInterval` が TypeError を出すのが見つかったので
  `?.` で頑健に直した（差し替え幅の広い既存の stub でも壊れない）。
- 確かめられなかった点: 本物のローカルモデルでの抽出（検証用インスタンスにはモデルが無いので開始は差し替え）。
  撮影した PNG の目視はこのモデルではできない（画像を読めない）。実機の ControlDeck での確認は利用者向けの案内に残す。

## 2026-11-22 開発の作業場 2 段目（AI アシスタントの OpenCode の会話から、作業場を選ぶ・作る・消す）

- 目的: 「Claude なしで、AI アシスタントに要望を出せば OpenCode が開発を進められる状態に」（利用者）。今まで端末の `./deck.sh sandbox` だけだった開発用の箱を、AI アシスタントの画面から使えるようにする。
- 箱の meta に `serve_port`（19000〜、古い箱は load 時に割り当てて保存）。pod が `-p 127.0.0.1:<serve_port>` を公開する（pod の spec が変わる）。`ensure_pod` は、古い spec でも pod の中に動いているコンテナがあれば作り直さない（会話サーバーの起動だけ「古い形の pod」のエラー）。
- 会話サーバー（`chat_server`）: プロジェクトが作業場（`<sandbox root>/<名前>/work/repo`）なら、箱の pod の中で `opencode serve` を systemd のユーザーユニットで起こす（`_start_workspace`）。設定は `sandbox.opencode_config(chat_server=True, default_model=順番の最初)`（中継を向く・鍵なし・スキル・レビュー役）に権限プラグイン（箱のホームへ写す）・ask・question deny・todo を足して箱のホームへ。パスワード（と使えるなら Zen のキー）は 0600 の env-file（argv に出さない）。持ち主（meta の owner）と会話の利用者が違えば断る。`network` 引数は将来の秘密の会話（none）用の余地（今は `gateway` のみ）。
- 作業場の API（`/opencode/workspaces` の GET・POST・DELETE、権限 terminal.use、監査 `opencode.workspace.create` / `.delete`）。作成は ControlDeck 本体（`origin/main`）か CodeDEV の git のプロジェクト。削除は未コミット・未 push があれば `confirm=true` 無しでは 409。新モジュール `app/maintenance/workspaces.py`。
- 画面: AI アシスタントの OpenCode の作業フォルダの選択に「開発の作業場」の組と「新しい作業場…」。作業場の印（バッジ）・元・ブランチ・未保存の表示・確認つきの削除（`DevWorkspace.tsx`）。作業場は作業ツリーのパスを `code_project_path` で送る。
- 秘密の会話は今までどおり断る（段 2 で別に）。
- テスト: `test_dev_workspaces.py`（15 件）、`test_dev_sandbox.py` 更新、E2E `assistant-dev-workspace.spec.ts`（320・1280）。
- 確かめられなかった点: 実機の podman・systemd での起動、古い箱の pod 作り直し、実際の OpenCode との会話（模擬のみ）。

## 2026-11-21 開発の作業場 1 段目（関門 dev-check・開発のスキル・別モデルのレビュー役・目録の道具の修正）

- 目的: 箱の OpenCode（ローカル LLM・GLM）が、Opus の監査に頼らず間違いの少ない開発を自分で進められるようにする。試験で Opus の監査が見つけた機械的な問題（目録の説明の消失・venv の作成と PyPI 取得・箱の git の作者が不定・子のレビュー役が気づかない）を道具で潰す。設計は Opus、実装は Sonnet。
- `./deck.sh test-catalog`: 作り直しても既存の `docs/test-catalog.md` の「目的:」と「 — 説明」を、コードに説明が無いものについて名前で引き継ぐ（docstring があればそれが正。消えたテストの説明だけ消える）。venv を作らず PyPI へも行かない（`.venv` → `CONTROL_DECK_SOURCE_VENV` → 元のリポジトリの `.venv` の順に既存の Python を使う `existing_python`）。
- `./deck.sh dev-check [--base <ref>] [--full]`（`app.maintenance.dev_check`）: base（既定は origin/main との merge-base）からの差分に対し、変えた module を import するテスト（`--full` で全体）、frontend の build（node_modules が無ければ「未実施」と明記し不合格にしない）、依存の差分、新しいテストの目的・根拠と目録、目録の説明が減っていないか、実装状況の更新、秘密の形（`diagnostics/report.py` の正規表現を再利用。例外は同じ行に `dev-check: allow`）、`shell=True`・`os.system`（AST）、Alembic の head が 1 本、を確かめる。合否は日本語で 1 行ずつ、終了コードは合格 0・不合格 1。
- スキル `controldeck-development`（同梱）: 共通（手本探し・テスト・関門・報告の型）と ControlDeck 固有（読む順・守ること・完了の条件）。箱の `opencode_config` は導入の有無に関わらず箱の skills へ写す。
- レビュー役のモデル: 作業がローカルなら順番の最初の外部（`opencode-go/glm-5.3` など。キー無し・`allow_zen` off ならローカルのまま）、作業が外部なら `controldeck/auto`（`sandbox.reviewer_model_for`）。`agents/reviewer.md` に「`./deck.sh dev-check` を流してその結果も見る」を追加。
- 箱の作業ツリーの local の git に user.name「ControlDeck Sandbox (<箱の名前>)」・user.email「sandbox@controldeck.local」。
- テスト: `test_dev_check.py`・`test_test_catalog_preserve.py`・`test_dev_sandbox.py`（4 件追加）。

## 2026-11-21 知識エッセンス（K1a）: 文献からの根拠付き抽出と人のレビュー（backend のみ）

- PatentBuddy Draft #29〜#33（K1a〜K2）を ControlDeck の決まりで作り直した。`backend/app/knowledge/essence.py`:
  種類 8 つ（課題・原因・原理・作用機構・実装・効果・制約・応用）・1 文献 12 件まで。各件は kind・
  summary（1〜400 字）・引用 quote（1〜1200 字）・引用の位置（原文の Unicode の start・end）。
  モデルの答えは `validate`（純粋な関数）で検めてから保存: 引用が原文の [start, end) に一字一句
  一致すること。位置が出せていない（無い・形が違う・範囲外）なら、引用が原文にちょうど 1 回だけ
  現れるときに限って位置を補う（2 回以上・0 回は捨てる）。形違い・上限超過は件を捨てて捨てた数
  （dropped）を行に記録。`basis=model_inference`・`review_status=unreviewed` は固定（提案が
  source_fact/approved と主張しても上書きできない）。
- 実行は analysis.py と同じ呼び方: ローカル専用（`local_only=True`・Jobs・`resolve_local_target`）。
  全文がモデルの窓に入らなければ骨格だけ渡し、引用の照合は全文に対して行う。読めない JSON・
  形違いは失敗として行に記録（extract 全体を失敗にする）。同じ文献・同じ版・同じ内容の再実行は
  新しく書かない（内容の sha256 で既存の成功行と照合し、今回の予約行を消して前回の id を返す）。
  ai_use=prohibited の文献は始める前に断り、実行側でも同じ門を通る。
- 人のレビュー: 承認・却下・差し戻しは消さず追記だけ（連番）。最新のレビューがその件の状態。
  stale（原文が変わった）抽出へのレビューは断る。版の失効: 文書を置き換えるたびに版の番号 epoch を
  単調に +1 する（`store.py` の `add_document` で、annotations と同じようにエッセンス・レビューを
  新しい文書 id へ持ち越す）。同じ文面へ戻っても番号は減らないので、前の承認は戻らない
  （レビューの効きは「抽出が今の版」かつ「レビュー時の epoch が今と同じ」で判定）。
  文書・コレクションを消したらエッセンス・レビュー・番号も消える（annotations と同じ。
  番号が削除で 0 に戻る経路はあるが、レビューも一緒に消えるため旧承認が復活する抜け道は無い。
  会話の分岐の copy_collection はエッセンスを写さない — 写し先は別の文書参照なので）。
  起動時に queued/running のまま残った行を「中断」にする（judge/survey と同じ、`features/registry.py`）。
- API（knowledge の router に追加。監査には id・判断だけを残し、文書の本文・summary・引用の全文は
  書かない）: `POST /api/v1/knowledge/documents/{ref}/essences`（201。Job の id・essence の id・
  「分野:番号」の essence_ref）・`GET /api/v1/knowledge/documents/{ref}/essences`（版ごとの一覧・
  各件のレビューの状態・stale・種類の表示名・各行に分野）・
  `POST /api/v1/knowledge/essences/{essence_ref}/items/{index}/review`。レビューの宛先は
  「分野:番号」で指す（番号は分野ごとに振られるため。分野を付けないと別の分野の同じ番号へ
  人の承認が誤着するのを防げない。reviewer から指摘されて修正）。閲覧は workflows.run、
  開始・レビューは workflows.edit（analyze と同じ区分）。
- 検証: `tests/test_knowledge_essence.py` 15 件（LLM は偽物・外部へは通信しない）: validate（位置の
  一致・不一致・補い 1 回/2 回/0 回・版の不一致・上限・固定）、start/run（保存・同じ内容で増えない・
  JSON 不通の失敗記録・prohibited の断り・**run の中の DB 書き込みは asyncio.to_thread を通して
  event loop の外で行うこと**。reviewer から指摘されて修正）、review（追記だけ・最新が状態・版が
  変わると stale で承認が効かない・同じ版へ戻っても戻らない・**分野をまたいで同じ番号があっても
  承認が誤着しないこと**）、API の権限（workflows.run は見るだけ・edit が要る・viewer は全断り・
  prohibited 422・ローカルのモデルが無ければ 503・無い文書は 404）。
  実行環境: `cd backend && env -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/app-strata/.venv/bin/python
  -m pytest -q -p no:cacheprovider`（開発環境の指示により `./deck.sh test` は不使用。CI も不使用）。
  回帰: `tests/test_knowledge*.py` 6 ファイルと `test_patent_judge.py`・`test_patent_survey.py`・
  `test_patents.py`・`test_feature_boot_restore.py` を合わせて 85 件、すべて合格。
- 画面（frontend）はこの段では作っていない（K1b）。実画面・本番での評価は未実施。
  開発: 箱の OpenCode（GLM-5.3）が作り、Opus が監査して採用。新しい関門 `dev-check`（開発の作業場 1 段目）にもかけて合格。

## 2026-11-21 入力欄の秘密の会話の印をフラットなアイコンに

- 利用者「入力欄の、周りがオレンジの鍵を、他のアイコンと同じフラットに」。絵文字（🔒）と琥珀色の枠の札をやめ、マイク・スピーカーと同じ枠の無いボタンに `IconLock`（線の色だけ琥珀色）。押すと設定のシートを開くのは今までどおり。
- 検証: E2E `assistant-secret-lock-topbar`・`assistant-offline-research`・`assistant-claude-layout` 27 passed、撮影（320・ダーク）を確認。

## 2026-11-21 AI アシスタント: 上の行に「秘密の会話」の鍵のボタン

- 利用者「Web 検索などの通信ロックは重要なので、分かりやすいところに置きたい。右上の『auto』の右側に、鍵が開いているマークを。押すと警告、OK でロック」。
- 上の行（`data-assistant-topbar`）のモデルのボタンの右に鍵（44px）。開いた鍵＝押すと既存の確認ダイアログ→OK（既存の会話は `POST /chat/conversations/{id}/confidential`、まだ無い新しい会話は「秘密の会話として始める」と同じ `newSecret` を立てる）。閉じた鍵（琥珀色）＝押しても短い説明のトーストだけで解除の操作は無い。`IconLock`・`IconLockOpen` を icons.tsx に追加。
- 設定のシートの既存のボタン・チェック・入力欄の 🔒 は残し、状態（`confidential`・`newSecret`）を共有する。
- E2E: `assistant-secret-lock-topbar`（1280・320px、ライト・ダーク）。`assistant-offline-research` は鍵の aria-label と部分一致しないよう `exact: true` に。

## 2026-11-21 リモートデスクトップの有効・無効（設定）

- 利用者「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。オフになったら、通信そのものやサーバーの起動や、ホームのアイコンも消すように」。L2（特権の操作）。
- 設定 `remote_desktop.enabled`（全体で 1 つ、既定は有効）。保存は `data/remote_desktop.json`、API は `GET/PUT /api/v1/settings/remote-desktop`（settings.manage だけ、監査 `remote_desktop.enable|disable`）。画面は 設定 →「機能と拡張」の先頭。
- 強制は ControlDeck 側: `/api/v1/remote/*` と tunnel の WebSocket を、router の依存で 403（WebSocket は 4403）「リモートデスクトップは無効です（設定で有効にできます）」。オフにした瞬間に開いている中継も閉じる。`/api/v1/meta` の `enabled_features` から `remote-desktop` を外し、ナビ・「よく使う」・コマンドパレットの Remote を消す。`/remote` は無効の表示。
- サーバー: 特権補助の `system-service` に `enable`・`disable` を追加。一覧には id `remote-desktop`（xrdp.service）と `remote-desktop-session`（xrdp-sesman.service）を組み込みで足した（actions: start・stop・enable・disable）。オフは stop+disable、オンは enable+start。
- **補助の更新が要る**: 補助・一覧が古い間も設定の保存と ControlDeck 側の強制は効くが、xrdp は止まらない。画面に「補助の更新が要ります（sudo ./deck.sh service を 1 回）」を出す。流した後にもう一度切り替える。
- 検証用インスタンス（E2E）は xrdp を操作しない。テスト: `test_remote_desktop_toggle.py`（7 件）、E2E `remote-desktop-toggle`（1280・320px）。

## 2026-11-21 AI アシスタント: 会話ごとの「この会話は秘密」（外部への調査・外部の AI を使わない。一方通行）（段 1）

- 利用者「AI に利用を制限させるのではなく、根本的につながらないように」。続けて「途中から秘密の話をしたくなって押すことはよくある。一度押した会話の文脈には秘密が入っているので、解除して外部へ調査に出られてはいけない」。設計は Opus、実装は Sonnet。全体の設定ではなく**会話ごと・解除なし**。
- 印: `conversations.confidential_at`（Alembic `b5e1c7a93d20`）。`POST /chat/conversations/{id}/confidential`（一方通行。`confidential:false` は 409。PATCH に外す欄は無く、ORM の `before_update` も付いた印を消す更新を拒む）、`POST /chat/conversations` は `{confidential:true}` で最初から秘密。監査 `chat.conversation.confidential`（`at: create|midway`・止めた Job 数）。分岐した会話も秘密を引き継ぐ。途中で押すと、動いている生成の Job を取り消し（OpenCode の回もこの取り消しで interrupt される）、会話に「秘密にしました」の一言を残す。
- 強制の本体は通信の直前 `app/security/offline.py`（`ensure_external_allowed` → `ExternalResearchBlocked`）: `web_collector/egress.resolve_public`、`workflows/nodes.node_web_search`（SearXNG の起動も止める）、`workflows/external_search._client`（学術・特許・市場の全 API）、`workflows/github_research.inspect_repository`。止めるのは contextvar `confidential_scope()` の中だけ。入口は `chat_persist._run_chat_job`（会話の実行。DB の印を 1 回見る）と `chat_router.chat_search`（`conversation_id` つき）。特許の公報の取得・Workflow・自動更新などはスコープの外なので止まらない。
- 外部の LLM: 秘密の会話は送信時（`_start_send`・`/chat/route`）に `local_target` で、`cloud:` や外部の endpoint をローカルの既定のモデルへ替える（無ければ 409「ローカルのモデルが使えないため答えられません」。外部へ流さない）。`RuntimeChatRequest` に `confidential` の欄があるとき（gateway の変更）だけ、`x-control-deck-confidential` が付く（`confidential_request_kwargs`）。
- 補助: 自動の会話は web_search の道具を出さない。明示の Web・学術・調査モードは普通の会話にして断り書き。Deep Research は rag・local_code だけでレポート冒頭に断り書き。OpenCode へは任せない（送信は 409「秘密の会話の OpenCode は次の版で対応します」、自動の引き継ぎもしない）。
- 画面: AI 設定のシートに「この会話を秘密にする」（確認つき）、新しい会話では「秘密の会話として始める」、秘密の会話には入力欄に 🔒 の札。解除の操作は無い。
- 未対応（段 2）: OpenCode の箱（host network・プロジェクト×利用者で会話の間共有）。`docs/HANDOFF-opencode-dev-environment.md` の課題 12。
- 検証: `backend/tests/test_offline_research.py`（19 件）、E2E `assistant-offline-research`（1280・320。撮影は `~/ControlDeck-評価/2026-11-21-秘密の会話-画面/`）。

## 2026-11-21 発明の内容を外部の LLM へ出さない守り（ゲートウェイの層・Patent の流れ・埋め込み）

- 利用者「知財の、私の方から出したアイデアは、ローカル LLM 以外の LLM へのアクセスを拒否する形に」「Patent の特許フローに
  従ってやる場合は基本的にローカルでしか使えないように。会話ごとの判定はやらない（重くなる）」。
- ゲートウェイ: 要求のヘッダ `x-control-deck-confidential: 1` があると、`cloud:` へ出る前（`gateway_chat` の振り分けと
  `_cloud_chat`）に 403「発明の内容を含むため、外部のモデルへは送れません」。呼び出し元の許可（egress）より優先。
  拒否は `control_deck.audit` のログに呼び出し元とモデル名だけ残す（本文は残さない）。
- `RuntimeChatRequest` に `confidential`（既定 False）。`local_only=True` も機密扱いで、ゲートウェイ宛にだけ印を付けて送る。
  どちらも `_prepare` で `cloud:` を断る。
- Patent の流れの LLM 呼び出し（`patents/service.py`・`judge.py`・`survey.py`、`knowledge/analysis.py`、`pdf_ingest.py`）は
  すべて `local_only=True` 済み（変更不要）。テストで列挙して固定した。
- 埋め込みの宛先（`workflows/rag.py` の `embed_base_url`）はループバックだけ。`set_config`・`create_collection` で断り、
  `embed()` でも保存済みの外部 URL へ送らず `RagError`。
- AI アシスタントの既定モデルはローカル（`AssistantChat.tsx`: 既定は 127.0.0.1、外部は選んだときだけ）。変更なし。
- 範囲外（やらない）: 会話の自動判定・会話のモデルの強制・OpenCode の会話サーバー。AI アシスタントで外部のモデルを
  選ぶと、会話・添付はそのモデルへ送られる（画面に警告あり）。
- テスト: `backend/tests/test_confidential_local_only.py`（13 件）。

## 2026-11-21 会話のコードブロックにコピーボタン

- 利用者「コードブロックの右上ぐらいにコピーボタンを付けて、押すと全体がコピーできるように」。`frontend/src/features/workflows/MarkdownMessage.tsx` の `pre` を `CodeBlock` に置き換え、上の細い帯の右端にコピーボタン（`aria-label` 「コードをコピー」、見た目 32×28px・押せる範囲 44px 程度）。ボタンは横にずらす `pre` の外にあるので、横スクロールしても右上に留まり文字に重ならない。押すとアイコンがチェックと「コピーしました」に約 1.8 秒変わる。コピーする中身はコード全体（``` の行は含まない・末尾の改行 1 つは落とす）。既存の `lib/clipboard.ts` の `writeClipboard`（http では textarea + execCommand）を使い回し、アイコンは `components/icons.tsx` に `IconCopy`・`IconCheck` を追加。生成中のコードブロックでも押せる。MarkdownMessage を使うほかの画面（ナレッジ・Patent）にも付く。
- 検証: `npm run build`、E2E `assistant-code-copy`（新規 2 件・1280px／320px、クリップボードの中身・押せる範囲・横スクロールで動かないこと）、`assistant-message-actions` が成功。ライト/ダークを `frontend/test-results/code-copy-*.png` に撮影。

## 2026-11-21 AI アシスタント（OpenCode）: 「途中で伝える」で固まる不具合

- 原因: plan の OpenCode が `question`（利用者へ質問して答えを待つ道具）を呼んで待っている最中に「途中で伝える」を
  押すと、`/prompt` の `delivery: steer`（今の道具が終わったところで届く）が 200 で受理されるが、`question` は画面に
  答える欄が無く終わらないので実行が先へ進まず固まった（本番の会話の最後の道具が running の question、20:42:51 に
  steer が 200、20:44:20 に利用者の停止で interrupt）。
- 直し: AI アシスタント用サーバーの権限で `question` を禁止（`provider._apply_chat_server`。確認は本文で聞かせる）。
  すでに動いている古いサーバー向けに、`question` が running の間の「途中で伝える」は 409 で理由を返し、送信待ちは残す
  （`chat_persist.py` の `steer`）。テスト 2 件を `test_opencode_chat_timeline.py` に追加。

## 2026-11-21 Patent に「調査」: 検索語の案（ローカル LLM）→ 外へ送る語の確認（P1 の最初の部分）

- 案件の調査テーマ（1〜2000 文字、空なら請求項 1 の本文）から、ローカル専用のモデルで検索語の案を作る
  （`backend/app/patents/survey.py`）。案は構成要素ごとの ja/en の語（1〜80 文字・前後の空白を取り重複を除き
  各 20 個まで）・IPC（`^[A-H]\d{2}[A-Z] ?\d{1,4}/\d{1,6}$` に合うものだけ。捨てた数を記録）・各取得先の検索式
  （EPO・USPTO・OpenAlex）。judge.py と同じ形: start → Jobs で LLM（`resolve_local_target`・
  `_complete_with_host_admission` をそのまま使用）→ 出力を検めて保存 → 人の確認。読めない JSON・形違いは
  失敗として記録。保存は judge と同じ専用 sqlite（patents shard の `patent_surveys` 表）。ローカルのモデルが
  無ければ 503 で始まらない（記録も作らない）。
- 未公開の内容の検出: 語・検索式が案件の請求項・ヒアリングの記録と 8 文字以上重なっていたら
  `risk: "unpublished"` を付ける（全体が含まれる場合に加え、8 文字以上の一部分が写されている場合も。
  NFKC・小文字・空白・句読点の揺れは judge._flat で許容）。検索式は語を組み合わせたものなので同じ窓検査。
  利用者が入力したテーマ自身は判定対象に含めない（指示書の文言が「請求項・ヒアリング」としているため。
  テーマが空なら請求項 1 がテーマになるので、既定の流れでは検査対象に含まれる。テーマだけに書かれた
  内容の語は、利用者の approved_public の確認が最終関門）。
- 外へ送る語の確認: `approve` は利用者が選んだ語だけを確認済みとして保存。`approved_public` が無ければ断る
  （watch.py と同じ文言）。risk の付いた語・案に無い語・まだ出来上がっていない（または失敗した）案は断る。API: `POST /patents/cases/{id}/surveys`（201、
  job と survey の id）・`GET .../surveys`・`GET .../surveys/{sid}`・`POST .../surveys/{sid}/approve`。閲覧は
  workflows.run、開始・確認は workflows.edit。監査には id・件数だけ（テーマ・語の本文は書かない）。
- 組み込み: 案件を消したら案も消す（案件番号は使い回されるため。`store.py` の delete_case）。起動時に
  queued/running の案を「中断」にする（`features/registry.py`。judge と同じ）。
- 検証: `tests/test_patent_survey.py`（9。LLM は偽物・外部へは通信しない）: 正しい JSON の保存・読めない JSON と
  形違いの失敗・語の長さ/重複/上限・IPC の検査・請求項とヒアリングに含まれる語や一部を写した語の risk・
  approve の断り（確認無し・未完成・risk・案に無い語）と保存・API の流れと権限（viewer 403）・案件削除での
  消去と再起動での中断。関連する `tests/test_patent*.py` と `tests/test_feature_boot_restore.py` は 45 件通過。
  レビュー（reviewer 子エージェント）の major「語と検索式で risk 判定の感度が食い違う」は窓検査に統一して
  直し、minor「未完成の案の approve」は status で断るように直した。
  実機（ローカル LLM での本物の実行）は未確認。画面はまだ無い（P1 の後続: 取得・関連順・調査結果と一緒に作る）。
  開発: 箱の OpenCode（GLM-5.3、Go プラン）が作り、Opus が監査して採用。同じ指示をローカル LLM（Qwen3.8 Flash-Next）にもやらせて比べた（`~/ControlDeck-評価/`）。

## 2026-11-21 箱の OpenCode のモデルの順番（使い切ったら次へ）

- 利用者「GLM を使い切り、無料で高性能なものを順次」（Go プランのキーを Zen の欄に登録済み。箱から GLM-5.3・Zen の無料 2 つが答えるのを実機で確認）。
- `sandbox.py`: 既定の順番 `DEFAULT_MODEL_CHAIN`（`opencode-go/glm-5.3` → `opencode/hy4-flash-free` → `opencode/deepseek-v4.1-flash-free` → `controldeck/auto`）。Zen のキーが無い・箱で `allow_zen` off なら `opencode*` を飛ばす。箱の meta `model_chain` で置き換え、CLI `models <名前> [モデル ...]`（引数なしで表示、`--reset` で既定へ）。`local` は `controldeck/auto` の別名。
- `opencode --run`: JSONL をそのまま標準出力へ流しつつ読み、`type: error` の行に limit / quota / exceeded / insufficient / rate / 429 / 402 / usage / billing があれば次のモデルで同じ指示をやり直す（sessionID が取れていれば `--session` で続き）。切り替えは標準エラーに 1 行。上限以外のエラーでは切り替えない。尽きたら最後の終了コード。`--model` 明示はその 1 つだけ。対話（`--run` なし）は順番の最初の使えるモデルで開く（切り替えなし）。
- 最後に使ったモデルを meta `last_model` に保存し `status` に出す。
- 検証: `tests/test_dev_sandbox.py`（+4）。

## 2026-11-21 使われていない OpenCode の会話サーバーを数分おきに止める

- 実機: AI アシスタントの OpenCode の会話サーバー（プロジェクトの箱、host network・gh の設定を読み取り専用で見せる）が、17.5 時間使われていないのに 21 時間動いていた。止める処理 `chat_server.reap_idle` を、次の会話を始めたとき（`ensure`）にだけ呼んでいたため、最後に使ったサーバーは止まらなかった。
- `chat_server.idle_reap_loop`（5 分おき、`main.py` の機械全体の常駐に登録）。1 回の失敗で止めない。止めたら記録に出す。動いていた 1 つは既存の `reap_idle` で止めた（次に会話を開けば起き直る）。
- 同じ確認で、podman の記録にだけ残り実体の無い Radiance のコンテナ（状態 Removing）も消した（次の起動で名前がぶつからないように）。
- 検証: `tests/test_opencode_chat_server.py`（+1）、backend 全体 1959 passed（1 回目は既知の全体実行フレーキー 1 件）。

## 2026-11-21 テストの一時フォルダと箱の /tmp を溜めない

- 利用者「data/sandboxes が 400GB くらいある。不要なら消して、増えないように」。実際の使用は約 3.4GB で、見かけ 372GB は pytest の一時フォルダ（`test_disk_streamed_moe_reservation` が作る 90GB の見かけの GGUF、`test_llama_large_vision_request` の 2GB）だった。pytest は最近 3 回分を残すので、箱の共有 `/tmp`（空のルートの `.rootfs/tmp`）・OpenCode のプロジェクトの一時フォルダ・ホストの `/tmp`（見かけ 93GB）に溜まっていた。中身を詰めないバックアップでは本当に書き出されうる。
- 消した: 3 か所の `pytest-of-*`（見かけ 372GB → 2.1GB）。
- 直した: `pyproject.toml` に `tmp_path_retention_policy = "failed"`（通ったテストの一時フォルダは残さない。backend 全体を流した後も 217KB）。開発用の箱は箱ごとの `/tmp`（`<箱>/tmp`、0700。箱を消すと一緒に消える）。
- 検証: `tests/test_dev_sandbox.py`（+1、既存 2 件の「書けるのは箱だけ」に箱の中の /tmp を足した）、backend 全体 1958 passed。

## 2026-11-21 設定の「API キー」に OpenCode Zen（無料モデル）の登録欄

- Zen の無料モデルは OpenCode の中からしか使えず他の道具は 403（ControlDeck のゲートウェイは通せない）。箱の OpenCode が opencode.ai へ直接繋ぐ。
- 保存: `app/integrations/opencode/zen.py`（cloud.py と同じ暗号化・0600）。設定 → API キーに組「開発」と項目「OpenCode Zen（無料のモデル）」（`dev:opencode-zen`）。値は返さず末尾 4 文字。
- 箱: `opencode_argv` だけが `OPENCODE_API_KEY` を渡す（0600・箱の外の一時 `--env-file`、使用後に削除。argv・opencode.json に値なし。shell・exec・mirror には渡さない）。meta `allow_zen`（既定 true）、`sandbox new --no-zen`、`zen <名前> on|off`。`--model` は `provider/モデル` ならそのまま（例 `opencode/deepseek-v4.1-flash-free`）、無ければ `controldeck/` を付ける。`opencode` の対話も execv をやめ子として動かす（後始末のため）。
- 未公開の情報（特許など）を扱う箱では使わない。実機は未検証（キー登録後に確認）。

## 2026-11-21 ControlDeck の MCP に Project Lab の実行の道具（P2a）。中継で箱にも見せる

- 道具（`app/addons/agent_mcp.py`）: `control_deck.lab_projects`（プロジェクトと profiles の id/label/type）、`lab_run_start`（project_id・profile_id・timeout_seconds。web は preview の鍵付き相対パス `previewPath` と `http://127.0.0.1:<ControlDeck のポート>` 付きの `previewUrlAbsolute`）、`lab_run_status`（`run_out`）、`lab_run_logs`（tail_kb 既定 32・最大 256、秘密の伏せは `run_logs`）、`lab_run_stop`。中身は `project_lab.runs` / `service` の既存関数を呼ぶだけ（新しい実行の仕組みは無し）。見る道具は `project_lab.view`、始める・止めるは `project_lab.run`（権限の無い道具は一覧に出さず、呼んでも 403）。開始・停止は `project_lab.run.start` / `.cancel` を監査（`via: agent_mcp`）。他人の run は 404（管理者 = `settings.manage` は全員分）。
- 中継（`sandbox_gateway.py`）: `GET /api/v1/addons/agent-mcp/tools` と `POST .../call` を通す。MCP のトークンは箱の外の中継だけが持ち（環境変数 `CONTROL_DECK_SANDBOX_MCP_TOKEN`）、上流へ `Authorization: Bearer` を付ける。箱から来た Authorization は捨てる。`/call` の道具名が許可の一覧（lab_* 5 つ・report・research）に無ければ 403、`/tools` も一覧に絞る。上流の更新トークンの頭（`X-Control-Deck-MCP-Token`）は箱へ渡さない。`/healthz` に `mcp` の有無を出し、MCP の無い古い中継は作り直す。
- 箱（`sandbox.py`）: 持ち主 `sandbox new --owner <ユーザー名>`（省くと有効な管理者が 1 人ならその人、複数・0 人はエラー）。meta の `owner` に保存。古い箱は中継を起こす時に同じ規則で決めて保存。トークンは `issue_opencode_token(owner, "sandbox-<名前>")` で中継の起動ごとに作り、`<sandboxes>/.relay/<名前>.env`（0600、ディレクトリ 0700、箱の外）へ書いて `--property=EnvironmentFile=` で渡す（argv・ログに出さない。`destroy` で消す）。期限は、ControlDeck 側が loopback からの呼び出しでは見ない（`allow_expired=local`、`x-requested-with` は中継が付ける）ので作り直し不要。
- 箱の OpenCode の設定: `controldeck_addons` を、箱の中の bridge（`addon_mcp_bridge.py` を箱の `home/.controldeck/` へ写して `/usr/bin/python3` で実行。箱はホストの /data1tb を見られないので写す）に差し替え、URL は中継、トークンは意味の無い値 `sandbox`、結果のフォルダは箱の中。本番の鍵に加え、中継のトークンが設定に入るなら書き出しを中止。
- 検証: `tests/test_lab_mcp_tools.py`（7）、`tests/test_sandbox_gateway.py`（+6）、`tests/test_dev_sandbox.py`（+5）、`tests/test_addon_agent_mcp.py`（一覧の期待に lab_* を追加）。実機（podman・systemd・箱の OpenCode から呼ぶ）は未確認。

## 2026-11-21 Project Lab の見張りモード（ライブ確認。Vite 以外にも）

- 目的: OpenCode（AI）と利用者が、保存のたびに作り直された結果をすぐ確かめる。Web アプリ（Vite・Vite 以外・Python のサーバー）、Python のスクリプト・テスト、C++（ビルドして実行・ctest）。利用者は preview を触り、操作と気づきを記録して AI に渡せる。
- manifest の profile に任意の `watch`: `{paths, ignore, build（argv）, mode: restart|rerun|native, debounce_ms 100〜5000}`。検証は command と同じ（相対パスだけ・`..` 禁止・shell の -c 禁止、build の実行ファイルは許可 SDK だけ）。`.controldeck/`・`.git/`・`node_modules/`・`__pycache__/`・`build/` は常に見張らない。mode の省略は web=restart、ほか=rerun。
- 見張り役 `app/project_lab/live.py`: run の unit の中で command の代わりに動き（watchfiles、debounce でまとめて 1 回）、build → command。子は同じ unit の中（`web_preview_ready` が見える）。変更で前の子を SIGTERM→SIGKILL してから回す。build 失敗では command を起こさず failed。状態は `<project>/.controldeck/live/<run_id>.jsonl`、出力は journal にも出る。指示（spec）は data の下に 0600（鍵付き {base} を含むため）。
- 実行のメモリ上限を設定 `run_memory_max_mb`（既定 2048、512〜16384）に。unit の MemoryMax に使う。
- 画面: 鍵付き入口の text/html にだけ `__cd/live.js` の script を 1 本差し込む（identity で頼み直し・8 MiB 超は差し込まない・content-length を直す）。`__cd/live.js` と WS `__cd/live` は上流へ送らず ControlDeck が答える（ビルド中・再起動中でも答えられる）。WS は状態の `status`、restart の起き直し後の `reload`、AI の知らせ（`<project>/.controldeck/live/<run_id>.notes` に 1 行書く）の `note` を送り、画面の記録（クリック・入力の長さだけ・送信・遷移・エラー・fetch の失敗・気づき）を `<project>/.controldeck/feedback/<run_id>.jsonl` に追記する（1 件 16KB・1 秒 50 件・1 run 20MB）。
- 検証: `tests/test_project_lab_live_config.py`（20）・`_live_supervisor.py`（7）・`_live_preview.py`（16）。実機（本物の systemd・Vite・iPhone Safari・C++）は未確認。
- 実機（Opus、使い捨ての ControlDeck・ポート 18791・一時の CodeDEV）: Python の Web（restart）で HTML に live.js が 1 本入り、WS で status・note・reload が届き、保存で v1→v2 に変わった。画面からのクリックと気づきが `.controldeck/feedback/` に残った。C++（cmake＋ctest、rerun）で、成功（終了 0）・テスト失敗（終了 8、変わったファイル `src/main.cpp`）・コンパイル失敗（failed、エラー位置つき）を記録した。止めた後にユニット・子のプロセスは残らなかった。
- 実機で見つけて直した: (1) 見張り役を `python -m app.project_lab.live` で起こすと、作業フォルダ（プロジェクト）の利用者の `app.py` が ControlDeck の `app` より先に読まれ、起動しなかった → `-P`（作業フォルダを import の探し先に入れない）。単体テストも本番と同じ作業フォルダに `app.py` を置く形に直した。(2) 終わり（exited）の記録に変わったファイルが入っていなかった。

## 2026-11-21 開発用サンドボックスにネットワークの境界（pod と LLM 中継）

- 問題: 箱は `--network host` で、本番のゲートウェイが loopback＋`x-requested-with` なら鍵なしで通すため、箱の AI が本番のモデル切り替え（POST /api/v1/llm/v1/models/{m}/load）まで呼べた。opencode.json に本番の鍵も平文で書いていた。
- 箱ごとに pod（`cd-sandbox-<名前>`、`pasta:-T,<gateway_port>`、公開はミラーのポートだけ）。箱からホストへ届くのは中継のポートだけ。プロジェクトの箱（OpenCode）は従来の host network のまま。
- 中継 `app/maintenance/sandbox_gateway.py`（ホストの systemd ユーザー一時ユニット `cd-sandbox-<名前>-gateway`）: models 一覧・chat・（`allow-load` のとき）load だけ通し、頭は捨てて決まったものを付け直す。本文 32 MiB・同時 4。
- opencode.json は中継を向き、鍵は入れない。`new --allow-load` / `allow-load <名前> on|off`。古い箱は load 時に gateway_port を割り当てて保存。
- 箱の OpenCode はローカル LLM だけ（2026-11-21 利用者）。中継は外部のモデル（`cloud:`）への chat を既定で断る（meta の `allow_cloud` で許す）。
- 検証: `tests/test_dev_sandbox.py`（+4）、`tests/test_sandbox_gateway.py`（7）。backend 全体 1932 passed（1 回目に既知の全体実行フレーキー 2 件、2 回目 0 件）。
- 実機（Opus、podman 4.9.3）: 箱から本番 8765 へ直接 000、中継のモデル一覧 200、load・他の API・cloud の chat は 403、インターネット 200。ローカル LLM の chat がストリームで返る（起動待ち込み 56 秒）。箱の OpenCode が中継越しに道具を使って答えた。opencode.json に鍵なし。pod と中継を止めても次の利用で起き直る。ミラーはホスト・箱の両方から 200。destroy で pod・コンテナ・ユニットが残らない。

## 2026-11-21 Strata が動かせない GGUF を登録の時点で断る

- 本番で Uncensored の Q4_K_S を Strata に登録し、ロードが「a GGUF Strata cannot run」で 4 回失敗した。Strata が動かすのは ISTA-DASLab の GSQ-RCO・Unsloth の UD-*・Q8_0 だけ。
  `strata models import --plan` と `launch --plan` は量子化を確かめない（「plausible」と出る）ので、ControlDeck が GGUF の先頭（general.file_type・general.quantized_by）を読んで断る。
- 案内: ほかの量子化は llama.cpp で登録する。llama.cpp b12388 は qwen4exp に対応（本番で Q4_K_S を llama.cpp に登録し直し、12.9 tok/s。エキスパートの 32 層を CPU）。
- 検証: `tests/test_strata_gateway.py` に 1 件、全体 1923 passed、本物のファイル 2 つで判定を確認。

## 2026-11-21 モデルの表示名

- 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。Strata は系列名を表示名にしていたので、同じ系列のモデルが同じ名前になっていた。
- Models の各モデルの個別設定（llama.cpp・Strata・Radiance・Maya）に「表示名」。一覧と AI アシスタントのモデル選択に出る。空で既定に戻す。
  ゲートウェイでの名前（alias）は変えない（既定のモデル・会話・Workflow・OpenCode の設定が alias を覚えているため）。置き場 `data_dir/model-display-names.json`。
- Strata の手元の GGUF の登録は、既定の表示名をファイル名と量子化にした（系列名ではなく）。
- 検証: `tests/test_model_display_names.py`（2）、全体 1922 passed、E2E model-display-name 1。

## 2026-11-21 Settings に「API キー」（外部のサービスのキーを 1 か所に）

- 利用者「キーを設定の中にまとめてほしい。OpenRouter のキーも。今あるキーは消さないように」。
- Settings →「API キー」: クラウドの AI（OpenRouter・OpenAI・Anthropic・Gemini・OpenAI 互換）、特許・論文の公開データ（EPO・USPTO・特許庁）、Hugging Face、Workflow のシークレット。
  保存場所は変えていないので、これまでのキーはそのまま出る。値は表示しない（設定済みか・末尾 4 文字）。
- 検証: 全体 1920 passed、E2E settings-keys 2 ほか。

## 2026-11-21 検索基盤の強化 W1（共通の取得・本文抽出・SSRF・機能と拡張・特許庁 API）

- 利用者「検索基盤の強化を実施して」「必要なコードやプラグインは機能と拡張に導入・更新ボタン（個別管理・一括導入・更新）」「特許庁などの API の設定はどこ？」。
  調査・計画・引き継ぎは `docs/research-web-collector.md`・`PLAN-web-collector.md`・`HANDOFF-web-collector.md`。
- 取得の共通化: Deep Research・Workflow の web.scrape・公開データの取得が同じ安全な取得を使う。名前を解決した IP にそのまま接続し（DNS rebinding 対策）、リダイレクトは各段で検査。以前は web.scrape が内部のアドレスへ届いた。
- 本文抽出: 機能「Web 本文抽出（Trafilatura）」を導入すると、見出し・表・リンクを Markdown で取り出す（別プロセス・メモリ上限付き）。未導入なら従来の抽出（見出し・段落の区切りは残すようにした）。
- 機能と拡張: 分類「検索と収集」、pip・npm の機能にも「更新あり」、分類ごとの「まとめて導入」、公開データの取得先（EPO・USPTO・特許庁）の設定への入口。
- 特許庁 特許情報取得 API を取得先に追加（利用登録の後に通知されるトークン取得の URL・ID・パスワードで、出願番号から経過情報を引く）。
- 検証: 全体 1910 passed、本物の Trafilatura で抽出（実サイト 3 件）、E2E 15 passed。

## 2026-11-21 ボタンの動作の確認と、見つけた不具合 2 件

- 利用者「各ボタンなどの機能動作も確認して」。Patent と Knowledge のボタンを入力つきで押して結果まで確かめる E2E（`button-sweep.spec.ts`、27 項目）。
- 直した: 埋め込みのサーバーに届かないと資料の取り込みが丸ごと失敗していた（本文と全文検索は残し、埋め込みは作り直しで後から）。作り直しの一部が失敗しても Job が「成功」と出ていた（失敗にして件数と理由を出す）。
- 検証: 全体 1896 passed、E2E button-sweep 1・patents 9・knowledge 3。

## 2026-11-21 AI クレームチャートの判定（R3-3）

- Patent の請求項の節で、請求項 N と 1 件の文献（登録した先行文献・クレームチャートの候補）を選び「判定」。ローカル専用のモデルが構成要件ごとに ○（開示）・△（一部・示唆）・×（開示なし）・?（判断できない）を付ける（Jobs）。
- ○・△ には原文からの引用が必須。引用が原文に見つからなければ「?」に落とし、その旨を出す（モデルの作った引用を根拠に残さない）。
- 人が行ごとに判定を直せる（モデルの判定も残す）。確認済み・不採用。請求項の版や文献の原文が変わったら「古い判定」と出す。
- 直した不具合: Patent の入力欄に幅を指定しても w-full が勝ち、「クレームチャートを作る」のボタンが縦に潰れていた。
- 検証: `tests/test_patent_judge.py`（5）、全体 1894 passed、E2E patents 9（PC 幅・320px、横はみ出し無し）。

## 2026-11-21 Patent を ControlDeck のページに・特許の分析・公開データの取得とウォッチ・ナレッジの作り直し

- 利用者「特許の開発フローが浮いた窓になっている。ControlDeck と統合した UI/UX に（Fable を使ってよい）」「傾向・書誌・請求項・類似特許をグラフで見る機能は」「RAG もデータの再構築が必要ではないか」「結果は日本語で、それをリポジトリのルールに」。
- Patent: `/patents`（案件・分析・公報のタブ）と `/patents/:id`（PC は左に節の一覧、右に次の期限と次にやること。携帯はタブ）。節は 概要・ヒアリング・請求項・文献・ウォッチ・期限・AI 支援・分析。画面は Fable（子エージェント）が作り、手で確認した。
- 特許の分析: 年別・請求項の数・出願人・IPC・出願人×IPC のバブル・出願人×年のヒートマップ（空白の年も出す）・国と種別・類似マップ（近いほど内容が似ている）。書誌が読めた件数と偏りの注意を必ず出す。案件ごとに請求項に近い公報（X/Y/A で登録）。書誌は文書の画面で手直しできる。グラフは手書きの SVG（ライブラリを足していない）。
- 公開データの取得（EPO OPS・USPTO ODP・OpenAlex・RSS・GDELT）と特許ウォッチ（新着だけを候補に、先行文献へ登録・対象外）、期限の通知（60/30/7 日前、案件名は書かない）。外へ送る検索語は利用者が「公開してよい」と確認したものだけ。鍵は暗号化して保存し、画面に値を出さない。
- RAG の作り直し: 会話の資料はその会話の中だけで使い、別の会話の雑談や AI の回答を横断検索に混ぜない。AI の回答は推論として保存。`./deck.sh knowledge-rebuild`（Knowledge → 資料の管理 →「作り直し」）で、旧データの AI の回答の区別・埋め込みの作り直し・原本 PDF からの取り込み直し。
- 直した不具合: 案件を消してもウォッチ・候補・通知の記録が残り、番号が使い回されて次の案件に出ていた。
- 決まり: 結果・報告・PR 本文・Release ノートは日本語（`AGENTS.md`「結果は日本語で」）。
- 検証: 全体 1887 passed、E2E patents 7・knowledge 3・frontier-models 4（PC 幅・320px、横はみ出し無し）。外部への実際の取得は行っていない（鍵が未登録）。

## 2026-11-21 統合後の再設計 R3-1: 文献ごとの AI 分析・審査基準と分類辞書・MCP のナレッジ検索

- 利用者「各リポジトリの計画を確認し、ControlDeck 側の計画に反映し、統合したときに整合性を持つように再検討してから進める」。棚卸しと統合の決まり（3 層・根拠の版・外部へ出る 4 経路・ローカル専用・検索の入口は 1 つ）は `docs/PLAN-knowledge-platform.md` §10。ResearchDataHub main にあって v0.102 で抜けていた機能（ウォッチ・期限の通知・分類辞書・審査基準・文献ごとの分析）を見つけ、R3 に入れた。
- 文献ごとの AI 分析: 文書を開いて「要約を作る」「請求項の確認を作る」。この PC のローカルモデルだけで全文（入らなければ骨格）を読む。結果は未確認の推論で、確認済み・不採用を付けられる。原文を取り込み直したら消さずに「原文が変わった」と表示する。利用条件で AI に使えない規格は分析しない。
- コレクションの種類: 資料・審査基準と法令・分類辞書（1 行 1 コード）。Patent の AI 支援は審査基準を [基準n] で引き、検索式の設計・新規性・アイデア・ヒアリングは分類辞書から候補を受け取る。資料の作成画面の分割・検索の方式・埋め込みモデルは「詳細設定」の中へ。
- MCP（OpenCode など）に `control_deck.knowledge_search` を足した。相手のモデルは分からないので cloud とみなし、公開・AI 利用可の文書だけを返す（最大 3 万字）。
- 検証: `tests/test_knowledge_r3.py`（4）、全体 1869 passed（落ちた 1 件は既知の不安定なテスト）、E2E `knowledge`（3）・`patents`（2）。

## 2026-11-21 文書単位の RAG・規格検索・PDF の取り込み・発明の音声ヒアリング

- 利用者「特許・規格は書誌全体が関係する。上位何件に絞れない。コンテキストが十分なら全文を入れて回答」「PDF から画像・文章・波形グラフ・表を抽出して記録し、体系的に引き出す規格検索」「発明を音声で長く話し、AI が本質・請求項に分解するヒアリング」「知らなくていい情報は隠す」。調査と採用した方式は `docs/research-patent-standards-rag.md`、引き継ぎは `docs/HANDOFF-knowledge-platform.md`（引き継ぎ資料の決まりを `AGENTS.md` に追加）。
- 文書単位の検索（断片を文書ごとに集める、番号の完全一致を先頭）、入るなら全文・入らなければ骨格（要約・請求項の全文・適用範囲・引用規格）と該当箇所を元の順で（`knowledge/documents.py`・`context.py`）。予算はモデルの窓から（`context_budget` の徹底の量）。
- 書誌の関係: 公報番号・規格番号・DOI を規則で取り出し（`biblio.py`）、引用する・引用される・別の版で結び、検索で 1 hop 広げる。LLM の GraphRAG は使わない。
- 規格: Knowledge →「規格の管理」。入手経路から本文の保存・AI 利用を判定して見せ（理由は既定で隠す）、条項見出しで分割、番号で引く、新しい版で旧版を自動で旧版にする。購入した ISO/IEC/JIS は番号と題名だけ。
- PDF: ページの文章（Poppler）、文字の少ないページは OCR（Tesseract jpn+eng）、図のページの画像と埋め込み画像、ローカルの VLM で図・表・グラフ（軸・単位・値）の説明（モデルの推論・未確認）。原本は保存先の設定どおり。Knowledge の資料に PDF を選ぶと自動でこの取り込みになる。
- 文書の表示（本文・図のタップ拡大・関係する文書・詳細は折りたたみ）。Knowledge →「探す」で資料・特許・規格を横断。
- AI アシスタントの道具 `search_knowledge`: ControlDeck 管理のローカル GGUF なら機密も、そうでなければ公開・AI 利用の許されたものだけ。
- Patent: ヒアリング（話して記録。区切りごとに保存、AI で本質・構成要件・請求項案・確認したいことに整理）、文献はナレッジの文書単位で探す、AI 支援の根拠は全文か骨格で渡す。
- 直した不具合: SQLite の表を作ったかの覚え書きがファイルの置き換え（復元・移動・作り直し）で古くなり、表が無いまま使っていた → inode も鍵に。
- 検証: `tests/test_knowledge_documents.py`（8）ほか、全体 1866 passed（落ちた 2 件は既知の不安定なテストで単独では通る）。E2E `patents`・`knowledge`（1280/320、横はみ出し無し）。実 PDF（特開2026-116114、15 ページ）で 2.8 秒、35 断片、OCR 1 ページ、図のページ 12、番号一致・骨格（要約・請求項）を確認。VLM の説明はローカルの画像モデルがある本番で動く（検証用インスタンスには無い）。
- 未: 座標（bbox）・WebP・レイアウト解析（Pillow・Docling の導入は了承待ち）、特許ファミリー・IPC の関係、保存先の設定画面。

## 2026-11-21 Patent 画面（特許調査・検討）と PatentBuddy Add-on の撤去、ナレッジの保存先の設定

- 利用者「ControlDeck に統合する件を進めて。PatentBuddy のアドオンは不要になるので、アドオンと機能追加画面の PatentBuddy 導入項目などを一式削除し、特許調査検討用ツールである Patent 機能をつけて」。計画は `docs/PLAN-knowledge-platform.md`（K-PR3 を前倒し）。
- Patent 画面（ナビの AI → Patent、`/patents`）: 案件（11 段階）、概要（技術分野・課題・解決手段・効果・実施例）、出来事から期限の目安（審査請求 3 年・公開 18 か月・拒絶理由の応答 60/90 日・特許料など）、請求項の版と構成要件の分解、クレームチャート（構成要件ごとにナレッジ全体を検索）、先行文献（ナレッジの検索から X/Y/A/P/E で登録、番号の直接入力）、AI 支援 12 種（発明ヒアリング・検索式・新規性・進歩性・請求項ドラフト・拒絶理由への応答など）、公報・論文の取り込み（特許の分野。既定は機密、公開済みだけ公開にできる）。
- AI 支援は ControlDeck が管理するローカルの GGUF だけで動かす（`ai_gateway.require_local_target`）。使えなければ 503 で止め、クラウドへ回さない。結果は「モデルの推論・未確認」で保存し、確認済み・不採用を付けられる。Jobs で動き、GPU は既存の順番待ちに従う。
- 保存は `data_dir/knowledge/patents.sqlite3`（特許の分野）。中身は ResearchDataHub の `qnaplibrary/patents.py` から移植（期限・構成要件・指示文）。
- PatentBuddy: 機能追加のカタログ（`trusted-catalog.json`）から外し、導入済みなら起動時に片付ける（`RETIRED_FEATURES`。サービス・Add-on の登録・実行ファイルを消し、`data/feature-data/patent-buddy` は残す）。設定の検索語・アイコンの割り当て・フロンティアモデルの説明文から名前を外した。
- ナレッジの保存先・保存対象・バックアップ（`./deck.sh knowledge-storage`）: 保存先を名前で登録（NAS はマウント先）、DB（分野ごと）・GraphRAG・原本・図・抽出結果の置き場所を別々に、SQLite はネットワーク上の FS に置かせない、目印で外れた NAS に書かない、バックアップの世代・照合・空の場所への復元・blobs の移し替え・DB の移動。設定画面と定時バックアップは未着手。
- main に入っていた reranker の選択・時間の上限・小さい会話の資料を全部入れる処理（#475〜）を、統合ストアの窓口へ移した。
- 検証: `tests/test_patents.py`（7）、`tests/test_knowledge_storage.py`（4）、`tests/test_knowledge_store.py`・`tests/test_rag_db_nodes.py` ほか。E2E `patents`（PC 幅・320px、作成→期限→請求項→AI 支援の案内、横はみ出し無し）、`frontier-models`、`feature-auto-update`。`release-bundle-settings`・`feature-boot-restore` は導入済みの環境が要るので未実施。

## 2026-11-19 統合ナレッジストア（PatentBuddy・ResearchDataHub の統合 K-PR1）

- 利用者「すべて ControlDeck に統合する。PatentBuddy・ResearchDataHub のデータを回収し、使わなくなったリポジトリを閉じる」「DB は分けるが ControlDeck からは意識しない」「横断検索したい。守りたいのはアイデアの流出」。計画の正本は `docs/PLAN-knowledge-platform.md`。
- `backend/app/knowledge/`: 分野別 SQLite（general / patents / standards）、日本語 2-gram の全文検索（ResearchDataHub から移植。2 文字の語も当たる）、RAM 上の行列によるベクトル検索、RRF、機密区分（confidential / public）と cloud 向け横断検索の除外、原本・図のハッシュ名保存。
- `rag.py` は窓口だけにし、関数と戻り値の形を保った。直したこと: 2,000 チャンクで黙って切り詰める（埋め込みは 64 件ずつ送る）、検索のたびに全件を読み 1 件ずつ計算する、async の中の同期 SQLite。GraphRAG の三つ組はコレクションごとの別ファイル。
- 旧 `data/rag/*.db` はコレクションを初めて使うときに取り込む（旧ファイルは書き換えない・消さない。消したコレクションは復活しない）。`./deck.sh knowledge-migrate [--apply]` で一括取り込みと ResearchDataHub の取り込み（patents 分野、原本 PDF・図・ローカル AI の要約は未確認の推論として）。
- 規格の利用判定（`standards_license.py`）と根拠 `docs/standards-license-policy.md` を ResearchDataHub から移した。
- 検証: `tests/test_knowledge_store.py`（13）、`tests/test_standards_license.py`（15）、既存の RAG・会話・Deep Research・ワークフローのテスト。本番データの複製で取り込みのリハーサル（45 コレクション・158 文書・1,144 チャンク、旧ファイル不変）。100k チャンクで warm のベクトル検索 15.5 ms（旧方式 431 ms）。
- 未実施: 本番への取り込み（Release 後に行う）、画面の変更なし（Knowledge 画面の分野・機密区分の表示は K-PR2/3）。


## 2026-11-21 通知が端末に届いたかを確かめられるようにする

- 利用者「通知が届いていない気がする」。
- **本番の記録で分かったこと**
  - iPhone（Safari のホーム画面版）は 1 台、登録されていた。
  - 「試しに送る」は 3 回とも、Apple の push service が受け取っていた（2xx、`sent: 1`）。
  - つまり送る側は動いていた。届かなかったのは Apple より先（端末への配達か、端末での表示）。サーバーからは区別できなかった。
- **調べて分かったこと**（AGENTS.md「分からないことの調べ方」）
  - Apple の文書：すぐ届けたいなら `Urgency: high`。`normal` で送っていた。
  - Apple Developer Forums の報告：
    - 201 でも届かなかった例の原因が、集中モード（おやすみモード）だった。
    - 200 でも Service Worker の push が呼ばれない例がある。
- **直したこと**
  - `Urgency` を `high` にした。
  - 送るたびに番号（rid）を入れる。Service Worker は表示した後、その番号で `POST /push/receipts` を返す。表示に失敗したときは理由も返す。
  - 画面の「試しに送る」は、30 秒まで返事を待って結果を出す。
    - 「端末が受け取りました」：見えないなら、集中モードと iPhone の 設定 → 通知 → ControlDeck を確かめる。
    - 「返事がありません」：古い Service Worker が残っているか、端末がネットに繋がっていない。
  - 端末の一覧に、端末が最後に受け取った時刻と、表示に失敗した理由を出す。
  - sw.js の版を v23 にした。
- **検証**
  - pytest `test_web_push`：番号は 1 回だけ使え、知らない番号は記録しない。Urgency が high。
  - E2E `web-push`：Service Worker が番号を添えて返事をする。
  - `./deck.sh test`：1820 passed。
- 出典：
  - [Apple: Sending web push notifications](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers.md)
  - [Apple Forums 770749（集中モードが原因だった例）](https://developer.apple.com/forums/thread/770749)
  - [Apple Forums 848080（200 でも push が呼ばれない例）](https://developer.apple.com/forums/thread/848080)

## 2026-11-21 Project Lab の preview で HMR を通す・端末への通知（Web Push）

利用者から渡された 2 つの提案（WS プレビュー、push 通知）を、実測とコードで確かめてから作った。

- **preview の WebSocket（HMR）**
  - 提案の見立てで合っていたこと：HTTP の proxy しかなく、WebSocket は通らない。認証には Cookie ではない方式が要る。Host を上流のものにする。
  - 実測で違ったこと：
    - preview は sandbox の iframe（allow-same-origin なし）なので、中のページの生まれは null になる。Cookie も Referer も付かず、Vite は module script が CORS で 1 本も読めなかった。WS 以前に、HTTP の時点で動いていなかった。
    - HMR は「preview と同じ接頭辞」ではなく、Vite の base（既定は `/`）へ繋ぎに来た（`ws://host/` で 403）。
    - サブプロトコル `vite-hmr` を中継しないと、Vite は握手に応じない。
    - ping は ASGI から見えないので、中継しない（区間ごとに返る）。
    - `authenticate_websocket` は Cookie と Origin を見るので、null の生まれでは使えない。
  - sandbox を外す案は採らなかった。外すとプレビューの JS が ControlDeck の API を本人の Cookie で叩ける。
  - 作ったもの（`project_lab/webview.py`、`runs.preview_*`）：
    - run ごとの鍵を入れた入口 `/project-view/r/{id}/{token}/`。HTTP と WS の両方を受け、`Access-Control-Allow-Origin: *` を返し、Cookie は受け取らない。
    - command の `{base}`。Vite の `--base {base}` で、資産と HMR の両方が同じ接頭辞へ来る。
  - 実測：実際の Vite 5.4 を Project Run として起こし、Project Lab の画面（390px、sandbox のまま）で表示できた。ファイルを書き換えると、HMR で VERSION-1 が VERSION-2 に変わった。
- **端末への通知（Web Push）**
  - 提案の見立てで合っていたこと：`notify.py` に `push` を 4 つ目の通知先として足すと、既存のアラートと自動更新がそのまま push で届く。
  - 提案から変えたこと：
    - 依存を足さない。pywebpush を使わず、cryptography と httpx で RFC 8291（aes128gcm）と RFC 8292（VAPID）を組んだ。RFC 8291 付録の例と 1 バイト違わず一致する。
    - endpoint は端末が渡す URL なので、そのままでは内側の任意の URL へ POST させられる（SSRF）。既知の push service（FCM・Mozilla・Apple・Windows）だけ受ける。
    - VAPID の sub は ControlDeck を開いている https の origin にする。Apple は mailto: か https: 以外を拒む。
    - 実行の終わりは、前は画面で読むまで分からなかった。見張り（5 秒ごと）を足して、閉じていても通知する。画面と見張りが同時に見つけても、通知は 1 回にする。
  - 通知の中身：
    - Project Run の終わりは、起動した人の端末へ送る。取り消したときは送らない。
    - 通知を押すと `/project-lab?project=…&run=…` が開き、その実行結果を表示する。
    - 404・410 の端末は消す。endpoint と鍵は暗号化して持ち、一覧には出さない。
  - iOS は 16.4 以降で、ホーム画面に追加した ControlDeck からだけ受け取れる。返信はできないので、押したらその画面を開くところまでを 1 本の導線にした。
  - 画面：設定 → 通知 の「この端末への通知」（受け取る・試しに送る・止める・端末の一覧）と、通知チャンネルの種類「この ControlDeck の通知（端末へ）」。
- **検証**
  - pytest `test_project_view_websocket`（5 件）が通過。
    - 中継：文字・binary・サブプロトコル・close code。
    - 拒否：鍵違い・終わった run は通さず、上流へも繋がない。
    - 漏らさない：Cookie と Origin は上流へ渡さない。Host は上流のもの。
    - `{base}`：接頭辞を付けたまま送る。記録に鍵を残さない。
    - HTTP：`Access-Control-Allow-Origin: *` を返し、Set-Cookie は捨てる。
    - サブプロトコルと CORS の行を消すと、テストが落ちることも確かめた。
  - pytest `test_web_push`（10 件）が通過。
    - RFC 8291 の例との一致と、受け手の側の復号。
    - VAPID の JWT が公開鍵で検証できる。
    - SSRF の拒否、410 の端末を消す、届いたら最後に届いた時刻を残す、外への url を出さない。
    - 実行の終わりで 1 回だけ送り、取り消しでは送らない。
    - チャンネル push が監視を見られる人の端末へ送る。
  - E2E `web-push`（2 件）が通過。
    - 購読して一覧に端末が出て、止めると消える。
    - DevTools から Service Worker へ push を届け、通知の中身と行き先を確かめた。
    - 通知の link の印は、読んだら消える。
- 実機で未確認：iPhone のホーム画面版で受け取れるか、押すと開くか。
- **外の資料での確認（2026-11-21、AGENTS.md「分からないことの調べ方」）**
  - **Apple の文書**（[Sending web push notifications in web apps and browsers](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)）：
    - 合っていたこと：sub は URL か `mailto:`、aud は push service の origin、exp は 1 日以内。届いたら必ず表示する。中身は 4KB まで。410 は失効。
    - 食い違っていたこと：JWT を「1 時間に 1 回より頻繁に作り直さない」とある。送るたびに作っていたので、push service と sub ごとに、期限の 1 時間前まで使い回すよう直した（テストを追加）。
  - **WebKit のソース**（`PushManager.cpp`）：
    - `subscribe` は、許可が「未定」のときだけ操作の直後か（transient activation）を確かめて消費する。
    - 画面では、押した直後・最初の await より前に `Notification.requestPermission()` を呼んでいる。許可済みになった後の `subscribe` は操作を要らないので、今の順で iPhone でも通る。
  - **コミュニティの報告**（[taOS #2166](https://github.com/jaylfc/taOS/pull/2166)、Stack Overflow の転載、web-push の README）：
    - Apple は sub に `.local` や localhost を使うと拒むらしい。Apple の文書には書かれていない。
    - ControlDeck の sub は、開いている https の host を使う。iPhone の Web Push は正しい証明書の https（Tailscale の ts.net など）でしか動かないので、ふつうは当たらない。
  - **端の戻る（フリック）**（[W3C manifest の議論 2025-05](https://lists.w3.org/Archives/Public/public-webapps-github/2025May/0270.html)、[pqina](https://pqina.nl/blog/blocking-navigation-gestures-on-ios-13-4)、WebKit Bugzilla）：
    - ホーム画面の Web アプリで、戻る操作を止める標準の手段は無い。
    - 端の touchstart を止めるのが唯一知られた回避策で、v0.99 の作りと同じ。慣性スクロールの途中で端から引くと、iOS が先に取って戻る（WebKit 側の制限で、JS では止められない）。

## 2026-11-20 メニューを指で引き出す（v0.98 のフリックの作り直し）

- 利用者（v0.98 の後）「思ったのと違う。Claude は真ん中から右へ引いても、左から右へ引いてもメニューが出る。いまは左端から右へ引くと戻る、右端から左へ引くと進むになる」。
  - 原因: v0.98 は「60px 以上・0.6 秒以内に離したら開く」で、引いている間は何も動かなかった。ゆっくり引くと開かない。左端 16px は使わないことにしていたので、端から引くと iPhone の「戻る」「進む」になっていた。
  - 対策（`hooks/useNavSwipe`）:
    - 画面のどこから引いても、メニューが指に付いて出てくる。離したときの速さと引いた量（幅の 35%）で、開くか戻るかを決める。開いているときに暗い所で引くと、同じように指に付いて閉じる。
    - 左右の端 20px で始めた指は touchstart を止めて、iPhone の「戻る」「進む」にさせない。その指を縦に動かしたときは、こちらで縦にスクロールする。端のボタンなどは止めない。
    - 縦に動かし始めた指は、ふつうのスクロールのまま（横の動きが縦の 1.2 倍を超えたときだけ引く）。横にスクロールできる所などで始めた指は、前と同じく使わない。
    - 設定 → 操作性で切ると、端の「戻る」「進む」も元に戻る。
    - `NavDrawer` に `peek`（引いている間だけ置く）を足した。
- 検証: E2E `ux-swipe-and-metrics-gap` が通過（真ん中からゆっくり引くと開く、少し引いて離すと戻る、左端から開く、左右の端の指は止めて真ん中は止めない、切ると端も止めない）。ドロワーに関わる E2E 10 本は 3 件を除いて通過。落ちた 3 件（addon-v2-ui・file-archive-preview・workflow-runner の携帯の承認）は main でも同じように落ちる。
- 実機で未確認: iPhone で端の「戻る」を本当に止められるか（standalone では効くとされる）。

## 2026-11-20 操作性（フリックでメニュー・画面を消さない・題名で先頭へ）とホームのグラフの欠け

- 利用者「左上のメニューを、左から右へフリックで出し、右から左で閉じたい。オンオフは設定に操作性のような項目を作って。便利機能が他にも思い浮かべば追加して」。
  - 設定 → 操作性（端末ごと、`stores/uxPrefs`）。どれも既定でオン。
    - フリックでメニューを開閉（`hooks/useNavSwipe`）。携帯幅だけ。横の動きが縦の 2 倍以上・60px 以上・0.6 秒以内のときだけ働く。横にスクロールできる所・入力欄・ターミナル・キャンバス・`data-no-nav-swipe` で始めた指、左端 16px（iPhone の戻る）は使わない。閉じるのは暗い所での右 → 左（ドロワー自体を引き戻す操作は前からある）。
    - 足した便利機能: 読み上げ・音声入力・会話モードの間は画面を消さない（`hooks/useWakeLock`、Screen Wake Lock。iPhone は画面が消えると読み上げが止まる）。題名を押すと先頭へ戻る（本文、本文が動いていなければ中で一番動いている一覧）。
- 利用者「ホームの CPU・GPU・RAM のグラフが、ブラウザを閉じてまた開くと、閉じていた間が更新されない」。
  - 原因: 画面を閉じている間は受け取りを止める（電池のため）。戻ると手元の点の続きに新しい点を足すだけで、閉じていた間を詰めて描いていた。サーバーの履歴は Home を最初に開いたときに一度読むだけだった。Home を離れていた間（受け取りは続くが、手元には直近 120 点＝4 分だけ）も同じ穴ができる。
  - 対策: 前の点から 15 秒以上空いたら手元の点を捨てて数え直し（`useMetrics.gapEpoch`）、Home はサーバーの履歴を読み直す。読んだ履歴の終わりと手元の最初の点の間が空いているときも読み直す（30 秒に 1 回まで）。読み直す間は前の履歴を出したままにする。
- 検証: E2E `ux-swipe-and-metrics-gap`（フリックで開く・閉じる・縦では開かない・横スクロールの所では開かない・切ると働かず端末に覚える、題名で先頭へ、受け取りの時刻を 2 分飛ばすと履歴を読み直す）。関係する E2E 40 件（navigation-layout・mobile-viewport・mobile-reconnect・dashboard-fans・assistant-mobile・assistant-voice-modes・assistant-queue-voice・update-history・a11y）。
- 未確認: iPhone の実機（フリックの感触・画面を消さない）。

## 2026-11-20 本体の自動更新に気づけるようにする（履歴と一度だけの知らせ）

- 利用者「自動更新（使っていないとき 5 分後）で更新されたことがない気がする。気づいてないだけ？」。
- 調べた結果: 動いていた。11/17 以降、systemd の記録で自動 38 回・手動 19 回（今日は 02:28・12:20・16:41・19:41 が自動。v0.94 は Release の約 30 分後に自動で入った）。気づけなかった理由は 2 つ。自動更新の結果は通知先へ送る作りだが、本番には通知先が 1 つも無い。画面にも前回の更新が出ない。加えて、新しい Release は 30 分ごとに確認するので、見つけてから 5 分空くまで最大約 35 分かかり、その前に手で更新している回が多い。
- 対策:
  - 本体の更新が終わるたびに履歴へ残す（`self_update.history()`、`data/updates/history.json`、30 件。API `GET /system/control-deck-update/history`）。履歴を残し始める前の版から上がった直後は、状態ファイルの直前の 1 回を見せる。
  - 設定 → 更新とバックアップに「更新の履歴」（時刻・自動/手動・版・成否、失敗は理由）。
  - 自動で更新した後、次に画面を開いたら一度だけ知らせる（`AutoUpdateNotice`、管理者だけ。見たかどうかはこのブラウザに覚える）。
  - 待っている理由を、更新があるときだけでなく常に出す（新しい Release が無いときは「30 分ごとに確認し、見つけたら 5 分空いた時点で適用」）。
- 利用者「更新の画面が数値の羅列で、何から何へ更新するのか分からない。Release の名前で出して」。いまの版・最新の正式 Release・確認の文を Release の名前（v0.95 など）で出す（`self_update._release_names`、`git ls-remote --tags` でコミット → 名前。同じコミットに複数あれば版の大きい方）。コミットは小さく添える。更新状態も英語（scheduled / running など）を日本語にした。
- 検証: `test_control_deck_self_update.py`（履歴の順・自動/手動・30 件・失敗も残る・移行直後・コミットから Release の名前）、E2E `update-history`（320px・1280px、API は模擬。「いまの版: v0.97」「v0.97 → v0.98」・確認の文）。本物のリポジトリで eeea699 → v0.95、18a0d94 → v0.96 になることを確かめた。

## 2026-11-20 「今すぐすべて更新」が 500 になる

- 本番（v0.95）: 「今すぐすべて更新」で「エラー (500)」。ログは `RuntimeError: no running event loop`（`jobs.create`）。
- 原因: API を同期関数で書いたので、FastAPI はスレッドで動かす。`jobs.create` はイベントループの上で Job を走らせるので、ループの無いスレッドからは作れない。テストは `start_job` を差し替えていて見逃した。
- 対策: API を async にした。テストは本物の `jobs.create` を通して Job が走り終わるまで見る（直す前の形に戻すと同じ例外で落ちることを確かめた）。毎日の自動の開始は元からループの上で動いていて、この不具合は無い。

## 2026-11-20 オプション機能の毎日の自動更新

- 利用者「設定の機能と拡張のオプション機能を、1 日 1 回ぐらい時間を決めて自動でバージョンアップできるように。ボタンと時間設定を付けて」。
- 設定 → 機能と拡張 → オプション機能の先頭に「毎日自動で更新」（既定はオフ）・時刻（既定 04:00、サーバーの時計）・「今すぐすべて更新」・前回の結果を置いた（`FeatureAutoUpdate.tsx`）。
- 判断と更新はサーバー（`app/features/auto_update.py`、API `GET/PUT /features/auto-update`・`POST /features/auto-update/run-jobs`）。時刻を過ぎたら、その日のうちに 1 回だけ一括更新の Job（`feature.auto_update`）を始める。使われている間（本体の自動更新と同じ `busy_reasons`）と本体の更新の予定・実行中は待つ。
- 対象は ControlDeck が導入した、壊れていない機能。音声エンジン（開発終了）は除く。GPU ランタイムは新しい Release があるときだけ更新し、npm・release bundle は各自の更新に任せる。1 つが失敗しても残りは続け、結果を保存して画面と通知先へ出す。Strata はモデルが稼働中だと更新を断るので、その日は「失敗（稼働中）」になる。
- 検証: `tests/test_feature_auto_update.py`（1 日 1 回・時刻前は走らない・使用中は待つ・対象の選び方・失敗しても続ける・API のパスが `/{feature_id}/{action}` に取られない）、E2E `feature-auto-update`（320px・1280px、API は模擬）。
- 検証用インスタンスでは自動では動かない。

## 2026-11-20 読み上げの読み飛ばしと、止めても鳴り続ける不具合

- 利用者「多少文言を読み飛ばす（前の版から）」「読み上げが始まって止めたのに、音が小さくなってまだ鳴り続けた」。
- 読み飛ばしの原因（再現した）: 生成中の切り出し（`speakable.stablePrefix`）が、太字・斜体・コード・リンク・URL の中の「！」「？」でも切っていた。閉じていない書式のせいで、切った所の前後で読む文がずれ、次の文の頭を飛ばし、記号を読んでいた（「**結論！** これは大事です」→「**結論！」「れは大事です」。リンクでは「を見て」が消えた）。
  - 対策: その行の書式が閉じている文の終わりでだけ切る（`lineIsClosed`）。読んだ分は文字の数ではなく文そのもので覚え、今の文の頭と食い違ったら、食い違った所から読み直す（飛ばさない）。
  - 無音の削り方も控えめにした: Supertonic 3 の前後の無音はちょうど 0 なので、0 でない音は削らない（1% で削ると、小さく消えていく語尾を最大 0.08 秒削っていた）。
- 鳴り続けの対策: 回答ごとの音量の通り道（GainNode）に音をまとめ、止めたら外す。予約済みの音も含めて、すぐ消える（iPhone の Safari で、まだ鳴り始めていない音の stop() が効かない場合も消える）。止めた回答は、続きや終わりが届いても読み直さない（前は、止めた直後に届いた続きで最初から読み始めることがあった）。OpenCode などの「止める」は、読み上げも止める。
- 検証: E2E `assistant-voice-modes`（書式の多い 10 通りの回答を 1 文字ずつ流して、抜け・重なり・記号の読み上げが無い。読み上げを切ると通り道を外し、続きが届いても読まない）、`assistant-queue-voice`・`assistant-claude-layout`・`assistant-plan`・`assistant-mobile`。
- 長文を通しで確かめた（利用者「長文を生成して、音声にして、ASR で読み取って確認して」）: 本番の Strata で書式つきの小説（1,283 字・738 個の断片）を流し、画面と同じ切り出しで合成に回す文を作った。上限（1,200 字）の手前まで、全文から作った読む文と 1 字も違わない（直す前は「**」を読み、次の文の頭を飛ばしていた）。上限では文の途中で切っていた（「ため息を続きは画面…」）ので、文の終わりで切るように直した。合成した 8 つの区切りを whisper で読むと、元の文との一致は 0.90〜0.98（同じ文でも回ごとに 0.90〜0.98 で揺れる）、どの区切りも末尾まで読めていた。残る違いは珍しい言葉の発音と聞き取り（「ズォーン」→「ゾーン」）で、受け渡しの抜けではない。
- 文の間: 区切りの間を 0.18 秒から 0.3 秒にした。Supertonic 3 が 1 つの音声の中で文の間に空ける長さ（実測 0.32 秒、SDK の既定 0.3 秒）にそろえた（利用者「文と文の間は適切に開けて」）。
- 未確認: iPhone の実機。

## 2026-11-20 iPhone で読み上げの後にマイクを開けない（AudioSession）

- 本番（iPhone、v0.92）: 読み上げの後に音声入力を押すと「AudioSession category is not compatible with audio capture.」。#485 で読み上げをマナーモードでも鳴らすため `navigator.audioSession.type = "playback"`（再生専用）にし、そのままにしていた。再生専用ではマイクを開けない。
- 対策（`lib/audioSession.ts`）: マイクを開く前に "play-and-record"、閉じたら "auto"。読み上げは、マイクを使っていないときだけ "playback"。会話モード（マイクを開いたまま読む）は "play-and-record" のまま鳴らす。AI アシスタントの音声入力（`useAssistantAsr`）と、Add-on の録音（`EmbeddedAddonView`）の両方。
- 検証: E2E `assistant-voice-modes`（偽の audioSession で「再生専用ではマイクを開けない」を再現し、読み上げの後に音声入力が始まり、閉じたら auto に戻る）・`assistant-queue-voice`。`addon-v2-embedded` の 1 件は変更前の main でも同じ所で落ちる（偽の Add-on の接続表示を待つ所）。
- 未確認: iPhone の実機。

## 2026-11-20 AI アシスタントで添付した画像を OpenCode に渡す（読めないモデルは送る前に知らせる）

- 利用者「OpenCode って画像見られなかったっけ？」「VLM を使う予定なので OpenCode にそのまま渡すのがいい。VLM が使えないものはモデルの判定で分かるので、使えませんと示して」。
- 調べた結果: OpenCode は画像を読める。ControlDeck は OpenCode のモデルを画像入力ありで登録しており（`provider.py` の capabilities）、いまのモデル（ゲートウェイの auto → Strata qwen3.8-flash-next）も画像を読めた（赤い画像に「赤」）。OpenCode v2 の発言の API は `files`（`data:` の URI）を受け付ける。外へ出ない設定（モデル一覧の取得なし・外向きの通信は存在しないプロキシへ）で本物の OpenCode を偽のモデルの窓口へつなぎ、添付した画像が `image_url` でモデルへ届くことを確かめた。渡していなかったのは ControlDeck の側（画面が「画像は OpenCode に渡せません」と止め、自動の引き継ぎも画像つきでは任せなかった）。
- 変更: 添付した画像を OpenCode の発言に `data:` の URI で付ける（`chat_persist._image_files`、`chat_server.run_turn(files=)`）。OpenCode のモデルが画像を読めるかは Models の表示と同じ判定（`gateway.instance_sees_images`: llama.cpp は mmproj、Strata は vision が off でない、Radiance は常に、Maya は vision）。読めなければ送信・送信待ちの時点で 422「OpenCode のモデル（名前）は画像を読めません」。外部のモデルなど分からないときは送ってみる。プランも画像つきで OpenCode へ。自動の引き継ぎは、画像を読めるモデルなら画像つきでも任せる。
- あわせて直した不具合: 回答の枠を作る前に送信が断られると、エラーがどこにも出ず送れたように見えていた。送れていない発言を取り下げて入力へ戻し、理由を出す。
- 検証: `tests/test_opencode_chat_timeline.py`（画像が data: URI で OpenCode へ・読めないモデルは送信も送信待ちも 422・判定が Models と同じ）、`tests/test_chat_tools.py`、E2E `assistant-plan`（OpenCode を選んで画像を送る・読めないモデルの知らせ・入力へ戻す。320px）・`assistant-claude-layout`・`assistant-mobile`・`assistant-voice-modes`・`assistant-queue-voice`。
## 2026-11-20 読み上げをつなぎ目なしで順に鳴らす（長文が飛び飛びになっていた）

- 利用者「長文を TTS で連続的に読ませたら飛び飛びになった。ストリーミング再生のように順番を守って再生して」。
- 原因（実測）: Supertonic 3 の音声は前後に約 0.4 秒ずつ無音が付く（15〜138 字で 0.31〜0.44 秒）。それを 1 文ずつ audio 要素で鳴らしていたので、文の切れ目ごとに約 1 秒途切れていた。生成中は 1 文ずつ合成に回るので、切れ目が多かった。合成自体は速く（15 字 0.6 秒・47 字 0.9 秒・138 字 1.9 秒）、順番は崩れていない（文の切り出しは 1・3・7・20 文字ずつ流しても抜けも重なりも無いことを確認）。
- 対策（`useAssistantTts`）: Web Audio で、合成した音声の前後の無音を削り（`speakable.voicedRange`）、前の音声の終わりに続けて予約する（文の間は 0.18 秒）。生成中は 160 字ほどたまってからまとめて合成する。ただし予約した音声の残りが 3 秒を切ったら、たまった分だけで合成する。最初は 60 字か 0.4 秒待った分で早く鳴らし始める。iPhone のマナーモードで消えないよう `navigator.audioSession.type = "playback"`。
- 検証: E2E `assistant-voice-modes`（長い回答を順番どおり・抜けなく合成し、鳴らした音の間が 0.25 秒未満で重ならない。無音の削り方）・`assistant-queue-voice`・`assistant-claude-layout`・`assistant-mobile`。

## 2026-11-20 自動でも、会話の LLM の判断で OpenCode に移る

- 利用者「自動でも適切に OpenCode へ遷移する作りにできないか」「すぐ移る形で」。v0.90 で語・会話の流れ・圧縮ファイルによる自動の切り替えを止めたので、判断を会話の LLM に任せる。
- 「自動」のチャットで、道具を呼べるモデル（`tools_verified`）に `hand_off_to_opencode`（`chat_tools.TOOL_HAND_OFF_OPENCODE`）を渡す。説明で「ファイルを作る・直す・読む・コマンドを実行する作業の依頼だけ。質問・説明・比較・相談には使わない」と決める。呼ばれたら同じ回答の枠のまま OpenCode の回（build）として始め直す（`_run_chat_job` の再入。画面には `handoff` を送り、途中の本文を消して「自動で OpenCode に任せました」を出す）。その回で Web を調べていれば、結果を OpenCode へ渡す。
- 作業フォルダ: この会話で OpenCode が前に使ったもの、無ければ画面で選んでいる既存のプロジェクト。どちらも無ければ移らず、＋から OpenCode を選んで作業フォルダを選ぶよう伝える（新しいプロジェクトを勝手に作らない）。OpenCode が無効・画像つき・声の会話では道具を渡さない。
- 実機（Strata qwen3.8-flash-next、思考なし）: 新しい会話 7 件（M5・M6 の質問は Web 検索、違い・書き方・コツの質問は答え、「足して」「まとめて」「直しておいて」は OpenCode）と、OpenCode で作業した会話の続き 4 件（「WAL って何？」と M5・M6 の質問は OpenCode に回さない）がすべて期待どおり。判断にかかる時間は約 2 秒。
- 検証: `tests/test_chat_tools.py`（任せたら OpenCode の回へ・作業フォルダが無ければ伝える・渡さない条件）、E2E `assistant-plan`（handoff で OpenCode の回答に切り替わる。320px）・`assistant-claude-layout`・`assistant-voice-modes`・`assistant-mobile`。

## 2026-11-20 OpenCode は明示的に選ぶ・読み上げを生成中から・声の性別で話し方

- 利用者「Mac の M5・M6 チップはローカル LLM としてどうですか、と聞いたら勝手に OpenCode が開いた。OpenCode は明示的に選ぶ必要があるように」。
  「自動」の判定（`assistantMode.detectAssistantMode`）は OpenCode を選ばない。以前は作業の語（実装して・直して・作って など）、OpenCode で作業中の会話（以後の発言はすべて OpenCode）、圧縮ファイルの添付、OpenCode のプラン指定で自動的に OpenCode へ回していた。プランは＋か Shift+Tab で選ぶ明示の指定なので、自動・調査と組み合わせても OpenCode で計画する（調査を選んでいれば先に調べ、その結果で計画する。利用者「プランと検索を選んだら、検索結果でプランを立てる作りか」）。OpenCode を選んでいれば質問も OpenCode で答える（前と同じ）。
- 読み上げのボタンの点滅をやめた（利用者「チカチカしてやめて」）。読み上げ中かは `data-speaking` で分かる。
- 会話モードの話し方を、Models の TTS で選んだ声の性別に合わせる（`voice_style.GENDER_RULES`。F1〜F5 は女性、M1〜M5 は男性）。TTS の設定に「性別」（女性・男性）を足し、声の一覧をその性別に絞る。声と話し方がずれないよう、別の設定は持たない。
- 読み上げを生成中から始める（`useAssistantTts.feed`、`speakable.nextSpoken`）。文の終わりまで流れてきた分を順に合成し、鳴らしている間に次を作る。生成（毎秒数十文字）は読み上げ（毎秒 8 文字前後）より速いので途切れない。会話モードは読み終えてから聞き直す。上限（1,200 文字）で「続きは画面」。
- 検証: `tests/test_chat_persist.py`（声の性別の決まり）、E2E `assistant-voice-modes`（生成が終わる前に最初の文を合成する・重なり抜けが無い・点滅しない）・`assistant-plan`・`assistant-claude-layout`（自動では OpenCode を選ばない。OpenCode の会話でも同じ。プラン＋自動・プラン＋調査は OpenCode）・`speech-engines`・`assistant-queue-voice`。

## 2026-11-20 LLM の高速化の設定を見直す（llama.cpp・Radiance・Maya）・補助の起動順

- 利用者「LLM を載せた後、reranker と埋め込みは必ず起動する必要はない。起動するなら reranker が先、埋め込みが後。LLM を載せるときだけ一度下ろす」「llama・Radiance・Maya も高速化の設定があれば入れて」。
- 補助の起動順: 補助は使うときだけ載せる（LLM の後に必ず載せはしない）。GPU の埋め込みを載せる前に、GPU の reranker（ruri）を先に載せる（`cpu_reranker.warm_gpu_reranker`、`llama.ensure_ready` の埋め込み）。埋め込みだけを使う場面（添付の保存・調査結果の保存・Knowledge への追加）でも先に場所を取る。LLM を載せる前は両方を止める（前の項）。
- llama.cpp: 埋め込みにも flash-attn を入れた（bge-m3 の資料 64 件の保存 3.80 → 1.34 秒、ベクトルはコサイン 0.99999 以上で同じなので保存済みの資料はそのまま使える）。reranker は前から on。チャットの LLM は今は llama.cpp に登録が無いので測っていない（新しく登録するときは flash-attn を試す）。
- Radiance: 投機的デコード（dflash2・深さ 7＋prompt lookup）と VRAM の prefix cache は既定で有効。ホスト RAM の段（`prefix-cache-mib`）は無く、VRAM から外れた会話は最初から読み直していた → 8,192 MiB を設定（Radiance が動く間は Strata・Maya は止まり RAM は空いている）。`--check` で受け付けを確認。KV が 289,648 トークンで max-model-len 327,680 に届かない警告は速さではないので変えない。
- Maya（DS-XS）: 投機的デコード（dspark＋prompt lookup）は有効。遅さの原因は設定ではなく RAM の量: VRAM に入らない専門家 92.3 GB に対し RAM の空きは約 90 GB で、約 82 GB を先読みし残り約 10 GB はディスクから（1 つ 3.56 ms、RAM の 7 倍）。`--ssd-cache` などはあるが、起動 80 秒×組合せの実測が要るので今回は変えない。

## 2026-11-20 Strata の --ple-io を mmap に（質問の読み込みが長い質問で約 1.9 倍）

- 利用者「Strata は RAM に読んでから VRAM に読む流れで遅い気がする。flash-attn は入っているか」「mmap にしてどう変わるか比べて」。
- 流れは設計どおり（94 GB の MoE を RAM 55 GB に置き、よく使う専門家 19.6 GB を VRAM にコピー）。注意計算の高速化（matrix cores）は既定で有効。起動約 62 秒（RAM へ 55 GB 約 30 秒・GEMM の準備 14.5 秒）。
- エンジンのログに「--ple-io direct はこのディスクで遅い（4 KiB の読み出し 52〜58 MB/s）」の警告。同じ起動条件で毎回初めて読む質問を比べた: 約 800 トークン 1.0〜1.1 秒 → 約 1.0 秒（同じ）、約 3,000 トークン 2.1〜2.8 秒 → 1.7〜1.8 秒（約 1.4 倍）、約 2〜2.7 万トークン 1,400 → 2,620 tok/s（約 1.9 倍）。起動時間は同じ（62 → 61 秒）。
- 本番の `data/runtimes/strata/gateway-models.json` の qwen-flash-next に `engine_settings: {"ple-io": "mmap"}` を入れた（ControlDeck は起動時に --set で渡す）。コードの既定には入れていない（DeepSeek など別のエンジンの家族で使えるか未確認）。表は page cache に載るので、RAM が逼迫すると追い出され、そのときは direct と同じ速さに戻る。
- VRAM の余白（vram-reserve-mib 3072）は残す。全部載せた後の空き 3.2 GB に埋め込み（約 1.4 GB）と GPU の ruri（約 1.3 GB）が収まる。

## 2026-11-20 GPU の補助は LLM の前に止める・reranker を埋め込みより先に・Embed / Reranker の表示・Maya の自動起動

- 利用者と合意（「順番に頼らない方法」）: 埋め込み・reranker は Knowledge の検索・添付の保存・Workflow・拡張アプリなどで LLM より先に GPU に載ることがあり、載ったまま大きな LLM（Maya 31.5 GB・Radiance 31 GB / 34 GB）を起動すると OOM になり得る。
  - LLM を GPU に載せる前に、GPU の補助（GPU の ruri・GPU に載った llama.cpp の埋め込み / reranker）を止める（`large_model_lifecycle.release_gpu_helpers`。`release_for`・llama.cpp の LLM の起動・切り替えの前）。常駐でも止める。CPU だけのものは止めない。
  - 資料の検索では GPU の ruri を埋め込みより先に起動する（ruri は全部を GPU に載せないと動かないが、bge-m3 は空きに合わせて残りを CPU で動かせる）。
  - GPU の ruri は起動前に VRAM の空き（1.6 GB）を確かめ、足りなければ試さず CPU の控えを使う。使われなければ GPU は 3 分、CPU は 10 分で止める。
  - 控えの xsmall を公式の int8（AVX-512 VNNI / AVX2）に（0.15 → 0.04 GB、速さ同じ、精度ほぼ同じ）。
- Embed / Reranker の表示: 「使う reranker」・「埋め込み」・「reranker」に分け、CPU / GPU（ONNX）と GPU（llama.cpp）を同じカード（役割・CPU/GPU・おすすめの印、導入・ロード / アンロード・アンインストール・常駐）にそろえた。常駐は CPU / GPU の reranker にも付けた（`/models/retrieval/cpu/{id}/load|unload|resident`）。
- CPU / GPU の切り替え: 利用者「モデルのボタンで CPU / GPU を切り替えたい」で一度トグルを付けたが、続けて「CPU にするとかなり遅くなるものは切り替えを消して」。ruri（CPU 8〜12 秒 / GPU 0.4 秒）、bge-m3（資料の保存が約 20 倍遅い）、Qwen3-Reranker-4B（遅い）はどれも該当するので、トグルは付けない。ruri は GPU（fp16）だけを出し、控えは CPU の xsmall（int8、CPU のみ）。llama.cpp で n_gpu_layers 0（CPU）にしたものには `--device none` を付けて GPU に触らせない（-ngl 0 だけだと大きな計算を自動で GPU へ回し VRAM も使っていた。bge-m3 の資料 32 件: GPU を使わせないと 20.9 秒）。
- 音声入力: 波形の右の「文字に…」を消した（利用者「いらない」）。
- Maya: 止まっているときに AI チャットで送ると「Mayaが起動していないか」で失敗していた（他の runtime は自動起動する）。DeepSeek（maya-deepseek-xs）だけを登録した環境で、起動する名前に未登録の既定名 maya-s を使い、起動を試みていなかった。要求されたモデル（登録名）を起動するようにし、`running_alias` も登録済みの最初のものを返すようにした。
- 検証: `tests/test_rag_db_nodes.py`（補助を LLM の前に止める・reranker を先に・VRAM の確認・常駐・xsmall の int8）、`tests/test_maya_s_addon.py`（止まっている Maya を起動する）、backend 全体 1788 passed、E2E `speech-engines`・`assistant-queue-voice`・`assistant-voice-modes`。

## 2026-11-20 Models: 導入でエラーが一瞬出て「処理中…」が消えない・アンインストール

- 利用者（画面写真）「reranker と埋め込みなのに LLM の画面に出る」「導入を押すと一瞬エラーが出るが、導入されているような感じ。ずっと導入表示が出る」「TTS / ASR と埋め込み・reranker にアンインストールを」。
- 原因: v0.87 の導入の API（`/models/retrieval/cpu/{id}/install-jobs`）を同期関数で受けていたため、スレッドで動きイベントループが無く `jobs.create` が 500（no running event loop）。その直前にジョブの記録だけができ、走らないジョブが「処理中…」のまま残った。ジョブの種類が `model.` で始まるため、Models の上（LLM の画面）にも出ていた。
- 変更: 導入の API を async に。CPU / GPU の reranker のジョブは `retrieval.install`（カードに進み具合、画面を開き直しても `job_id` で続きを出す）。llama.cpp のプリセットの導入（`model.preset`）は「Embed / Reranker」の画面だけに出す。アンインストール: TTS / ASR（ASR はそのエンジンのモデルだけ、sherpa-onnx の venv は共用なので残す。Supertonic 3 は実行環境だけ）、GPU の埋め込み・reranker（登録と GGUF を消す）、CPU / GPU の reranker（変換済みのモデルは残す）。本番に残った走らないジョブは次の再起動で「中断」になる。
- 検証: `tests/test_rag_db_nodes.py`（導入の API がジョブを実際に走らせる・プリセットのアンインストール）、`tests/test_speech_engines.py`（TTS / ASR のアンインストール）、E2E `speech-engines`。

## 2026-11-20 ruri を GPU で動かす選択肢（fp16・MIGraphX）・Maya にも道具を渡す

- 利用者「ruri を GPU で動かす選択肢を足して」「GPU で int8 などに量子化して容量を下げられないか。精度と速度は維持」「Maya でうまく道具が使えないのはなぜか。呼び方が違うのか調べて」。
- GPU の ruri: ONNX Runtime の MIGraphX 版（PyPI の onnxruntime-migraphx 1.27.1、ROCm の MIGraphX を使う）で fp16 計算。ファイルは fp16（0.63 GB、fp32 の半分）で精度・速さは fp32 と同じ（日本語 0.877 / 0.964、英語 0.582 / 0.935、標準の長さ 32 件 0.37 秒）。int8 は GPU で 8.5 倍遅く VRAM もほぼ減らないので不採用。4 件ずつ並べて VRAM 1.27 GB。形は 4 × 1024 に固定（形を 2 つにするとコンパイル結果の取り違えで点数が壊れた）。コンパイル結果は保存し、2 回目以降の起動は約 3 秒。
- Models の「Embed / Reranker」に「ruri（GPU）」を追加。自動では GPU の ruri を最優先（会話・Knowledge とも）、無ければ従来どおり（会話は xsmall、Knowledge は CPU の ruri）。GPU の ruri が失敗したら（VRAM 不足など）入っている CPU のものへ回す。実行環境は CPU 用と別（`runtimes/reranker-gpu`）、外す印もモデルごと。変換が済んだら元の重み（1.26 GB）を消す。
- 容量（GPU の ruri）: モデル 0.62 GB＋実行環境とコンパイル結果 0.77 GB＝約 1.4 GB。隔離した data で導入 180 秒。
- Maya: 道具の呼び出しは DeepSeek の DSML で書かれ、Maya が OpenAI の tool_calls に直して返す（形式の違いは無く、ControlDeck は正しく受け取れている）。ストリームで挨拶 8 回とも呼ばず、天気 8 回とも Web を探した。最初の確認で挨拶に資料を探した 1 回は、起動直後の最初の要求だけで再現しなかった → `tools_verified` にした。天気で 1 回探して諦めることがあったので、Web 検索の道具の説明に「見つからなければ語を変えて探し直す」を足した。
- 検証: `tests/test_rag_db_nodes.py`（GPU の ruri を優先・失敗したら CPU へ・印と実行環境は別）、`tests/test_chat_tools.py`（Maya に渡す）、隔離した data で GPU の ruri を実際に導入して並べ替え。評価は `docs/reranker-evaluation.md`。

## 2026-11-20 Models の「Embed / Reranker」で CPU の reranker を導入・選択する

- 利用者「reranker と埋め込みモデルは適切に選定されているか。Models タブで導入できるか、どのモデルを使うか選べるか」。v0.85 では CPU の reranker が設定の「機能追加 → 検索（RAG）」にあり、Models の「Embed / Reranker」（GPU の bge-m3・Qwen3-Reranker-4B）と場所が分かれ、使うモデルを選ぶ画面も無かった。本番では未導入なのに空のフォルダが残っていた（状態を見るだけで `archives/`・`runtimes/` のフォルダを作っていた）。
- 変更: CPU の xsmall・ruri を Models の「Embed / Reranker」へ移し、タップ 1 つで導入・外す。「使う reranker」を会話の資料と Knowledge・Workflow で別に選ぶ（自動 / xsmall / ruri / GPU / 並べ替えない。未導入のものは選べない。選んだものが外れたら自動に戻る）。選択は `data/retrieval-settings.json`。API は `/models/retrieval`。機能追加の「検索（RAG）」は削除。状態を見るだけではフォルダを作らない。
- 道具（#479 の続き）: Strata（Qwen3.8 Flash Next）をゲートウェイ経由で確かめ `tools_verified` にした（挨拶・計算では呼ばず、番組表・天気は探して答えた）。Maya（DS-XS）は挨拶でも資料を探したので渡さない。
- 検証: `tests/test_rag_db_nodes.py`（導入・選択・外す・保管庫を残す・見るだけでフォルダを作らない・選択に従う）、E2E `speech-engines`（1280 / 320 で表示・選択、機能追加に出さない）。

## 2026-11-20 AI アシスタント: 会話でモデル自身が道具を呼ぶ（資料の検索・Web 検索）

- 利用者「ローカル LLM の会話でモデル自身が道具を呼ぶ仕組みを入れてほしい」「毎回チェックする必要はない。Claude と同じ方法で」。
- 利用者「範囲は B（Web 検索も道具にし、送信ごとの機能の判定をなくす）。実機確認は Radiance のみ。今ある機能（ゲートウェイ）で」。
- 機能の選択が「自動」のときは `web_search` も渡す。呼ばれたら SearXNG で検索し、出典を会話へ登録して [R番号] 付きで渡す（画面の出典表示は既存の `sources` イベント）。Web の道具を使った回答は会話の資料へ保存する。「対話」を選んだときは Web の道具を渡さない。
- `/chat/route`: 道具を使えるモデルでは、曖昧な依頼を LLM で判定せず対話として返す（`decided_by: tools`、送信ごとの 1〜1.5 秒をなくす）。明らかな依頼（論文・Deep など）はルールで即決のまま。
- 上限（4 回）まで呼んだ後の最後の回は、道具のやり取りを普通の資料にまとめて道具なしで答えさせる。残したままだと Radiance が 3 回中 2 回、呼び出しを本文に書いた（まとめると 5 回中 5 回とも本文で答えた）。本文に残った `<tool_call>` は保存前に除く。
- Radiance（ゲートウェイ経由、本物の SearXNG）: 「こんにちは」「ありがとう」「Python でリストを逆順に」は検索せず 0.6〜3.7 秒。「明日の横浜の天気」は 3 回言い換えて探し晴れ・14℃ [R3]（14.8 秒）、「今日の日経平均の終値」は 1 回で 57,687 円 [R3]（6.5 秒）。資料の検索も同じく、挨拶・お礼・計算では呼ばず、「さっきの番組表で夜 9 時」は「神奈川 地上波 番組表 夜 9時」で探した。Radiance を `tools_verified` にした。
- Claude と同じ方式: 会話の資料が小さければ全部を文脈に入れる（従来どおり）。大きければ `search_conversation_materials` を渡し、探すかどうか・何で探すかをモデルが決める（前もっては探さない）。1 回の回答で 3 回まで。道具の結果は `role: tool` で返し、続きを生成する。使った検索語と件数は回答の meta の `tools` に残す。
- 生成の層（`runtime_provider`）: `RuntimeChatRequest.tools` を渡したときだけ `tools` / `tool_choice: auto` を送り、断片で流れてくる `tool_calls` をつないで `tool_call` の chunk で返す。tools 付きの要求を 400 等で断る endpoint は `ToolsUnsupported`。
- 道具を受け付けないモデルは覚えて、従来の言い方の判定（#478 の `needs_materials`）で探す。道具を黙って無視する runtime に渡すと資料を一度も探さなくなるので、実機で確かめた runtime（`tools_verified`。今は llama.cpp）にだけ渡す。Radiance・Strata・Maya・Ollama・外部は確認まで従来の方式。
- 実機（llama.cpp b12388、Qwen3-4B-Instruct）: 挨拶・お礼・計算では呼ばず、「さっき調べた番組表で夜 9 時は？」では「神奈川 今日の地上波 番組表 夜9時」、「明日の横浜の天気、資料に何て書いてあった？」では「明日の横浜の天気」と具体的な語で探して答えた。
- 道具で統合・不要になるもの（利用者への説明）: 言い方の判定と直前の話題を添える処理は、道具を使えるモデルでは不要（控えとして残す）。会話の資料の点数の門と 3 秒の上限も道具の経路では使わない。次の段階の候補: Web 検索を道具にし、送信ごとの機能の判定（`/chat/route`、毎回 1〜1.5 秒の LLM 呼び出し）をなくす。
- 検証: `tests/test_chat_tools.py`（断片の結合・tools を送る/送らない・400 で ToolsUnsupported・検索語で探し点数で切らない・失敗を文で返す・道具を呼んで続きを生成・受け付けないモデルは覚えて従来方式・未確認の runtime には渡さない）。

## 2026-11-20 RAG の並べ替えを CPU へ（xsmall・ruri をタップ 1 つで）・会話で毎回探さない

- 利用者「CPU に入れる形で」「ruri の int8 化はなぜ動かないのか」「軽量・高速なら精度は多少落ちてもよい」「他の reranker も含めてタップしたら初期設定とモデルのダウンロードまで」「毎回チェックする必要はない。Claude と同じ方法で」。
- int8 が動かなかったのは評価の書き方の誤り（CrossEncoder の外側の箱を差し替え、入力の辞書が `input_ids` に渡っていた）。全部 int8 だと JQaRA 0.877 → 0.834、MLP の出力側（Wo）だけ fp32 に残すと 0.875。
- 遅さの調査: 実装の誤り・二重起動は無い（会話 1 発言で検索 1 回、worker の起動はモデルごとに 1 回）。短い資料での「32 件 1.1 秒」は、実際の断片（約 800 文字＝433 トークン）では計算量が約 4 倍で、ruri は 32 件 8 秒（CPU の計算の上限どおり）。GPU の 4B も同じ断片 32 件で 3.8 秒（会話の 3 秒を超えていた）。本番の 4B は flash-attn on で次に起動する（起動時に設定を書き直すことを確認）。
- 機能追加「検索（RAG）」: `xsmall-reranker`（japanese-reranker-xsmall-v2、公式 ONNX、0.15 GB・RAM 0.7 GB、800 文字 32 件 0.64 秒、精度は 4B と同等・おすすめ）と `ruri-reranker`（int8、0.73 GB・RAM 2.1 GB、精度最良）。導入はタップ 1 つで取得（版と SHA-256 固定）・ruri は一時の build venv で int8 へ変換し PyTorch と順位一致を確認・実行環境（onnxruntime・tokenizers・numpy、共有）・試しの並べ替えまで。取得・変換したものは `data/archives/` に残す。worker は常駐し 10 分使われなければ止める。
- 使い分け: 会話（3 秒まで）は xsmall、Knowledge・Workflow は ruri（片方だけならそれ）。ruri を会話で使うときは候補 top_k×2・各 256 トークン。点数の門は Qwen の点数だけ。並べ替えの質問に直前の発言を添える。
- 会話: 大きい資料でも、発言が資料・前に調べたもの・詳しい内容を指すときだけ探す（挨拶・お礼・雑談では埋め込みも並べ替えも動かない）。小さい資料は従来どおり全部入れる。
- 検証: `tests/test_rag_db_nodes.py`、E2E `speech-engines`（1280 / 320 で 2 つが並ぶ）、隔離 data で 2 つとも実際に導入（xsmall 19 秒・ruri 154 秒）と並べ替え。評価は `docs/reranker-evaluation.md`。

## 2026-11-20 AI アシスタント: 会話の資料の使い方を見直す（探さずに全部 / 関係の薄いものは入れない / reranker を速く）

- 利用者「reranker は RAG や調査結果を調べるときだけの想定。毎回会話ごとに調査するのか。資料を全部入れるのもありか。性能・機能を落とさず速度は速めに」。
- それまで: 会話の資料（過去の検索結果・添付）がある会話では、挨拶でも毎回、埋め込みで探し、reranker で並べ替え、上位 8 件を入れていた。保存した資料の先頭の回答の指示文（「以下の検索結果を根拠に…」）もそのまま入っていた。
- 実測: 会話の資料は 45 会話中 38 が 3.2 万文字以内（中央値 3,800 文字）。埋め込みの近さでは挨拶（0.72）と関係する質問（0.69〜0.81）を見分けられない。reranker の点数は見分けられる（挨拶 0.21・お礼 0.003・無関係 0.001、天気の質問 0.93・「さっきの番組表」0.47）。reranker が遅かったのは GPU の混雑ではなく `--flash-attn off`: 15 文書で 18.5 秒、on で 1.2 秒（点数は同じ。全層 GPU でも off は 18〜23 秒）。
- 変更: (1) 資料が 3.2 万文字以内なら探さずに全部、文書の順で入れる（埋め込み・並べ替えなし、探す時間 0。最初の指示の直後の同じ位置に置き、前回の読み込みを使い回せるようにする）。「関係するときだけ使う」と添える。(2) 大きい資料は探して並べ替え、点数 0.3 未満は入れない（挨拶なら何も入らない）。並べ替えは 3 秒まで。(3) reranker を flash-attn on で起動する。(4) 資料として渡すときに保存時の指示文を除く。
- 検証: `tests/test_rag_db_nodes.py`（全部入れる・順・指示文の除去・大きければ探す）、`tests/test_llama_runtime.py`（reranker は flash-attn on）。本番の資料で 38/45 が全部入れる方式、天気の会話は 9,078 文字。reranker の速さは一時ポートの別プロセスで測定（本番の reranker は次の起動から on）。

## 2026-11-20 AI アシスタント: 思考が始まるまでの待ち・読み上げの抜け・＋メニュー

- 利用者「文字で入力すると思考が始まるまですごく時間がかかる。仕様か、余計な処理か」。本番の記録: ジョブ開始から思考の最初の文字まで 24.8 秒、うち会話の資料（RAG）の再ランク 1 回が 22.6 秒。GPU を Radiance（Qwen3.8-27B）が使い切り（VRAM 32/34 GB）、reranker（Qwen3-Reranker-4B）が CPU で動いていた。挨拶でも資料のある会話では毎回走る。→ 会話の回答前の再ランクは 3 秒で打ち切り、検索の順で答え始める（Workflow の RAG ノードは従来どおり待つ）。探している間は「資料を検索中」と出す。打ち切りは自己診断「会話の資料の再ランク」に出す。
- 利用者「読み上げが行われるときと行われないときがある」。読まれなかった 4 回は、どれも 1 つの回答に接続が 3〜4 本（別のタブ・購読し直し）。生成中かどうか（busy）の切り替わりで判定していたため、購読し直しで busy が一瞬立つと始まった読み上げを止め、同じ回答は 2 度読まないのでそのまま無音だった。→ 回答ごとに「流れていた → 終わった」を見て 1 回だけ読む。止めるのは利用者が次を送ったときだけ。読むのはこのタブから送った回答か、表に出ているタブだけ。
- ＋メニュー: 行を「アイコン・名前・その下に説明」でそろえた（添付だけ説明が右にずれていた）。機能のタイルと入力欄の札・自動の表示は線のアイコンに。読み上げのボタンはマイクと同じ線のアイコン（スピーカー / 消音）、会話モードは吹き出しのアイコン。
- 検証: `tests/test_rag_db_nodes.py`（再ランクの打ち切りと診断）、E2E `assistant-voice-modes`（別のタブが同じ回答を購読しても、送ったタブが 1 回だけ読み、裏のタブは読まない）・`assistant-claude-layout`・`assistant-plan`・`assistant-mobile`・`assistant-queue-voice`。320px の明暗で＋メニューを確認。

## 2026-11-19 AI アシスタント: 会話モードの回答を話し言葉に・回答傾向

- 利用者「会話モードでは会話に即した回答に。不要な絵文字や箇条書きは控えめに。一般的な会話を想定して条件を決めて」「回答傾向を複数から選べるように（フレンドリーなど）。モデル選択のところで設定したい」。
- 会話モードで送った発言にだけ、最後の発言へ決まりを書き添える（`app/workflows/voice_style.py`、送る分だけで保存しない）: 絵文字・顔文字・書式（箇条書き・見出し・表・コード）を使わずつながった話し言葉で、2〜4 文、長い説明は要点と続きの確認、URL・出典番号・注記を言わない、数字は聞き取りやすく、相手の言語で答える、聞き取りの誤りは推し量るか短く聞き返す、質問は 1 つまで。
- 回答傾向: フレンドリー（既定）・丁寧・簡潔・英会話の先生・聞き役。AI 設定（右上のモデルのボタン）の「会話モードの回答傾向」で選び、端末に保存。
- 会話モードは待たせないため、思考を指定していなければ切る。
- 検証: `tests/test_chat_persist.py`（決まりと傾向が最後の発言に付く・文字の会話には付かない・画像つきでも付く）、E2E `assistant-voice-modes`（会話モードは傾向を付けて送り、AI 設定で変えると次から使う。文字の発言には付けない）、`assistant-plan`・`assistant-claude-layout`・`assistant-mobile`。

## 2026-11-19 ASR: 日英自動（区切りごとに言語判定 → Haas / Parakeet）

- 利用者「日本語と英語が混ざったとき・交互に繰り返すときはどう判断するのか。両対応の ASR を入れた方がいいのか」。
- 日本語の実音声（Common Voice・ReazonSpeech 各 100 文）・英語の実音声・混ざった文で 5 方式を比較（`docs/speech-input-filter.md`）。言語判定（Whisper tiny）で振り分ける方式が、日本語（放送）6.2%・英語 3.1% と専用のエンジンの精度を保ち、判定は 344/346 正解。日英を 1 つで聞く Qwen3-ASR は放送の日本語で 43%（韓国語の文や無い文を作った）、Whisper turbo は 19%・4 倍遅い。
- ASR の選択肢に「日英自動」を追加。導入は言語判定（0.1 GB、HF の版とハッシュを固定）＋Haas＋Parakeet。応答の言語・モデルは判定に従う。控えは whisper（言語が自動のときだけ）。
- 検証: `tests/test_chat_asr.py`（振り分けと応答）、E2E `speech-engines`。隔離インスタンスで導入（16 秒）し、英・日・雑音入りの英・日・混ざった文を交互に送って振り分けと雑音の除外を確認。

## 2026-11-19 音声入力のフィルターと英語の ASR、エンジンごとの設定

- 利用者「音が小さい・ノイズが大きい場合を除去するフィルター。有効無効と詳細を変えられ、初めは推奨」「選んだ TTS / ASR に対応する設定項目を出す」「英会話をしたいので英語の ASR を選定して導入評価」。評価の全体は `docs/speech-input-filter.md`。
- 英語の ASR: 5 候補を LibriSpeech の実音声 73 文 × 6 条件で比べ、Parakeet TDT 0.6B v2（int8、WER 3.1%、雑音 10 dB 3.3%、RTF 0.022、CC-BY-4.0）を採用。英語を選んだときは日本語専用の Haas へ控えとして回さない。
- 入力フィルター: Haas-ja は雑音だけの 96 本中 37 本に「うん。」「音楽」などを出した（会話モードではそのまま送られる）。Silero VAD v5 は雑音をすべて「声なし」、話した声 1062 区切りをすべて「声あり」とした → 推奨は「声の無い区切りを捨てる」。雑音除去は英語で悪化・日本語でわずかに改善だったので既定では使わない。音量の均しは悪化のみで置かない。音量だけの門は小さな声と雑音が同じ大きさで使えない。
- プリセット: 推奨 / 騒がしい場所 / 静かな部屋・小さな声、細かい値を変えるとカスタム。詳細: 声の判定・厳しさ・最短の長さ・音量の下限・雑音除去・低い音のカット・マイクの感度・話し終わりの待ち時間（会話モード）・ブラウザの雑音抑制。
- Models の TTS / ASR に、選んだエンジンの設定を出す（whisper は聞く言語、Haas・Parakeet は言語を表示、Supertonic は声・速さ・品質・読む言語）。
- 資材: Silero VAD v5・GTCRN（2.8 MB、SHA-256 固定）、Parakeet（0.66 GB、HF の版を固定）。既に Haas がある本番では次の音声入力で裏で入る。
- 診断: 「音声入力」に英語のエンジン、フィルター未導入、捨てた区切りの数を出す。
- 検証: `tests/test_chat_asr.py`（英語は日本語へ回さない・フィルターで捨てた区切りは控えにも回さない）、`tests/test_speech_engines.py`（プリセットとカスタム）、E2E `speech-engines`・`assistant-voice-modes`。隔離インスタンスで Parakeet を本物の取得・照合で導入（85 秒）し、英語の認識（往復約 1.2 秒）、雑音の区切りを捨てる（0.15 秒）、フィルターを切ると同じ雑音から「ん?」が出ることを確認。

## 2026-11-19 AI アシスタント: 読み上げの失敗・入力欄の札・説明の簡略化

- 本番で「音声合成に失敗しました」: 「いいお天気ですね☀️」の絵文字の後ろの異体字セレクタ（U+FE0F）を Supertonic 3 が扱えず、文ごと失敗していた（12 回中 1 回）。読む文言から絵文字と見えない文字・英字の出典の印（[RK] など）を除き、合成側でも扱えない文字があれば除いて 1 回やり直す。
- iPhone で読み上げが鳴らないことがある: 開き直した後は操作の中で一度鳴らすまで声を出せない。最初のタップ・キー操作で無音を鳴らしておく。
- 利用者「入力欄の札は文字なし、× も不要、タップで消える」「読み上げは＋の中ではなく右下に。会話モードでも変えたい」「＋の説明が長い」「プランの説明はいらない」。札はアイコンだけ（名前は読み上げとツールチップ）。読み上げはマイクの左の 🔊 ボタン（会話モード中も出す。切ると文字だけで返してすぐ聞き直す）。＋の項目の説明を短くし、プランの説明欄を削った（枠の色・📋 の札・案内文で分かる）。
- 検証: E2E `assistant-voice-modes`（絵文字・出典の印、右下のボタン、会話中の切り替え）、`assistant-plan`・`assistant-claude-layout`・`assistant-mobile`・`assistant-queue-voice`。本番の実行環境で絵文字入りの文の合成を確認。

## 2026-11-19 AI アシスタント: TTS 返答と会話モード、Models の表記

- 利用者「TTS 返答モードではチャットの主要な文言を音声で回答」「会話モードは一定の入力停止を検知して LLM が回答し、停止ボタンまで交互に会話が続く」「フロンティアも LLM / VLM、Embed のように表記を揃えて」。
- TTS 返答: ＋メニューで入れる（端末ごとに覚える。入力欄に 🔊 の札、押すと今の読み上げを止め、× でやめる）。回答が終わったら、コード・表・画像・URL・出典の番号と節・Markdown の記号を除いた本文を読む。1200 字を超えたら文の切れ目までで「続きは画面」と添える（`speakable.ts`）。文ごとに合成し、鳴らしている間に次を作る（`useAssistantTts.ts`）。
- 会話モード: ＋メニューから始める。聞く → 最後の声から 1.2 秒黙り、文字にし終えたら送る → 回答を読む → また聞く、を「停止」まで繰り返す。回答待ちと読み上げの間はマイクの音を捨てる（自分の声を拾って送らない）。読み上げ中は「飛ばす」ですぐ話せる。会話を切り替えたら止める（最初の発言で会話が作られたときは止めない）。
- 声と速さは Models の TTS で「既定にする」で保存し（`tts_voice`・`tts_speed`）、読み上げはそれを使う。
- Models のタブ名を「Frontier」に（「LLM / VLM」「Embed / Reranker」「TTS / ASR」と揃える）。
- 検証: E2E `assistant-voice-modes`（読む文言の抽出、会話モード 2 往復と停止、TTS 返答と記憶。320px / 1280px）、既存の `assistant-queue-voice`・`assistant-claude-layout`・`assistant-plan`・`assistant-mobile`・`frontier-models`。隔離インスタンスで本物の Supertonic 3 の読み上げが鳴り終わるまで確認。マイクは模擬（実機のマイクでの会話は未確認）。

## 2026-11-19 Models: TTS / ASR タブと Supertonic 3（TTS）

- 利用者「Supertonic 3 を TTS として使いたい。開発中止・サポート終了で更新も消去もありうるので、リポジトリを丸ごと保存し、機能追加から導入できるように。導入時は音声モデルも。Models に TTS と ASR を同じ項目で。各エンジンを選べるように」。
- 上流は 2026-11-19 時点で archived（`supertone-inc/supertonic` → `supertone-oss-archive`）。導入時に `data/archives/supertonic-3/` へ丸ごと保管する: ソース 3 本の `git --mirror`（全ブランチ・タグ・PR 参照）、重みの全 38 ファイル（`supertone-oss-archive/supertonic-3@aafc6e32`、サイズと SHA-256 を照合）、SDK（supertonic 1.3.1 の wheel・sdist）と実行に要る wheel、`MANIFEST.json`。一度取ったものは取り直さないので、上流が消えた後もオフラインで導入できる。アンインストールで消すのは venv だけ。
- 設定 → 機能追加に「音声（TTS / ASR）」の分類と、新しい種類 `speech-engine`（導入＝利用可能、更新なし、再読み込みなし）。
- Models に「TTS / ASR」タブ。ASR（Haas-ja / whisper.cpp）と TTS（Supertonic 3）を並べ、使うエンジンを選ぶ（`data/speech-engines.json`、`PUT /speech/engines`）。ASR は選んだものを先に使い、未導入・失敗なら入っている方へ回る。TTS は試し聞きできる（`POST /speech/tts`、WAV）。
- 診断: 自己診断に「音声合成」（合成の失敗、保管庫の欠け）。「音声入力」は選んだエンジンと実際に動いているエンジンの違いも出す。
- 実測（CPU 4 スレッド）: 日本語 6.5 秒の音声を API 往復 0.79 秒、最大 RSS 約 0.5 GB。合成した音声を Haas-ja で聞き戻して原文どおり。本番 data に保管庫を作成済み（479 MB、git fsck 問題なし・全ファイルの SHA-256 一致）。
- 検証: `tests/test_speech_engines.py`、`tests/test_chat_asr.py`（選択）、E2E `speech-engines`（320px / 1280px、隔離インスタンスで本物の合成）。

## 2026-11-18 設定: GPU 設定（クロック・電圧オフセット）

- 設定に「GPU 設定」を追加し、Models の共通設定にあった電力・静音を移した。「ハードウェア」は「温度管理」に改名し、「AI モデル」「OpenCode」の移動ボタンを消した。
- R9700（PCI の装置 ID で選別）の最大コア周波数オフセットとコア電圧オフセットを別々に変更できる。ドライバの範囲を表示し、適用後に読み戻す。失敗時は変更した項目だけ元へ戻す。全体のリセット（`r`）は使わない。起動時の自動適用はしない。
- 書き込みは固定の helper `gpu-od` だけ。導入には `./deck.sh service` が必要。
- 検証: `tests/test_gpu_tuning.py`（疑似 sysfs）、E2E `gpu-settings`。実機の適用と効果は別途。

## 2026-11-18 AI アシスタント: アクションリストが古いままのとき

- 本番: モデルが `todowrite` を最初の 1 回しか呼ばず、91 回の操作の後も 1 番目が作業中に見えた（画面の不具合ではなくリストが更新されていない）。
- 最後の更新の後に道具を 8 回以上使っていたら「更新後 N 操作」と出す。依頼の前に更新を頼む一文を添え、更新されないまま進んだ回は診断に出す。作業中の割り込みは、止めた後に動き出す恐れがあるので使わない。
- 検証: `tests/test_opencode_chat_timeline.py`、E2E `assistant-opencode-progress`。

## 2026-11-18 AI アシスタント: OpenCode の変更差分・取り消し・作業中の割り込み

- 回答の下に、その回で変わったファイルと差分を出す。最新の回は確認のうえ取り消せる（ファイルと OpenCode の会話の両方を戻す）。
- 送信待ちの要望を「今すぐ伝える」と、OpenCode の今の道具が終わったところでモデルに届き、回答の並びに残る。
- OpenCode 2.3.0 の実物と偽の LLM（ネットワークなしの箱）で確認: `prompt` の返す ID、`diff?from=` はその回だけ、`revert/stage`→`commit`、`delivery: "steer"` は道具の直後に届く。
- 検証: `tests/test_opencode_chat_timeline.py`、E2E `assistant-opencode-progress`。

## 2026-11-18 AI アシスタント: 機能の選択の交通整理

- 利用者「勝手に変わる。固定している場合は変わって欲しくない。開き直すと外れないか」。OpenCode で送ると「自動」が OpenCode の固定に変わり、圧縮ファイルの添付でも固定が書き換わっていた。機能の選択は画面の中だけで、再読み込みで自動に戻り、別の会話へも持ち越していた。
- 規則: 「自動」は毎回判定して選択を変えない。機能の固定は利用者の操作でしか変えない。固定・調査の深さ・プランは会話ごとに端末へ保存し、送った発言にも記録して別の端末でも戻す。「調べて OpenCode に渡す」は 1 回だけ調査にして元へ戻す。
- プラン中は、調査と自動のチャットを OpenCode の計画として扱い、作業フォルダの選択と表示を出す。OpenCode を使っていない会話へ、別の会話のフォルダを持ち越さない。
- 検証: E2E `assistant-plan`・`assistant-claude-layout`、`tests/test_opencode_chat_timeline.py`。

## 2026-11-18 OpenCode: プラン・作業フォルダ・PDF の読み取り

- 本番の会話「Plan15模型ジオラマ」で確認。プラン中の失敗は編集ではなく、`2>/dev/null` などを含む読むだけのコマンドの誤判定だった。出力を捨てる形・`git -C`・版の確認を読むだけとして扱い、Auto の箱の中では git の読み取りと Web の取得も通す。書き込みのリダイレクト・単独の `&`・`git remote -v add` は読むだけと認めない。
- 作業フォルダを変えても前のセッションを使い続け、箱の中の空の `conv` で動いていた。セッションはフォルダごとに続け、新しいセッションには会話の最初から引き継ぐ。
- PDF は文字が `/uni30E6…` のまま登録され、原本も残っていなかった。文字を復元し、原本・文字・ページ PNG を保存して OpenCode のプロジェクト `.controldeck/attachments/` へ置く（リンクはたどらない）。ページ画像は read で読む。調査の計画と回答にも添付の内容を渡す。
- プラン中に「調査」を選ぶと、ControlDeck の調査の結果を渡して OpenCode が計画する。
- 検証: `tests/test_pdf_import.py`、`test_opencode_chat_server.py`、`test_opencode_chat_timeline.py`、E2E `assistant-plan`。

## 2026-11-18 AI アシスタント: 会話の読みやすさ・上部のぼかし

- 利用者「チャットウィンドウの視認性を向上させて。上部がぼやけているのは修正して」。
- OpenCode の本文を作業の前後の位置に出す（段階ごとの文が 1 段落につながっていた）。続く思考と道具は 1 枚のカードにまとめ、灰色の淡い文字を濃くした。
- 上部のぼかしは CSS ではなく iOS 26 のホーム画面アプリの scroll edge effect と見られる。消す設定は無いので、公開情報の回避策（上端に不透明で全幅の fixed の帯、上の行を sticky）を入れた。
- 検証: E2E `assistant-opencode-progress`・`assistant-claude-layout`。ぼかしが消えるかは Playwright で再現できず、実機の確認が必要。

## 2026-11-18 AI アシスタント: ＋メニューを低くする

- 利用者「項目が多すぎてはみ出る。スライドも微妙にできない。高さを減らして」。説明つきの行を並べて約 1200px あった。
- 機能は 3 列のタイル、OpenCode のモードと権限は横並びの切り替え（短い説明つき）にした。幅 430px で 400px 以下。矢印キーは見た目の向きに動く。上端はステータスバーに重ねない。
- 原因: 会話の欄の自動スクロールやキーボードの開閉でメニューが閉じていた。＋ごと動くスクロールと回転のときだけ閉じる。
- 検証: E2E `assistant-claude-layout`（高さ 480px 以下・中のスクロール不要）、`assistant-mobile`（音声の古い文言を現在の波形へ更新）。実機の iPhone では未確認。

## 2026-11-18 音声入力: 既定を Haas-ja（CPU・日本語）へ

- 利用者「高速な日本語 ASR を採用したい。GPU は 1 GiB 以下が望ましい」「音声モデルの使用を許可」。GPU を使わない Haas-ja-v1.0 量子化版（Apache-2.0・0.46 GB）を sherpa-onnx で動かす。
- 手元の日本語 12 文（合成 2・話し言葉 10）: 処理時間÷音声の長さは Haas 0.042（4 スレッド）、whisper 0.327（8 スレッド）。明らかな誤り 5 か所対 7 か所で、句読点も付く。Parakeet-ja は速いが 1 文を丸ごと落としたので採らない。これは小さな比較で、一般の精度を示すものではない。
- 専用の venv（版固定）とハッシュ照合したモデルを `data/runtimes/asr-haas/v1` に導入。区切りごとに別プロセスで動かし（1 回 約 2.5 秒・約 0.86 GiB を一時使用）、同時には動かさない。whisper が入っていれば、それで使いながら裏で導入し、Haas が失敗したら whisper へ回す。自己診断「音声入力」。
- 不具合: v0.72 の whisper の高速化（`-ac` の下限 256）で、短い区切りの文字が繰り返された（手元の音声で再現）。下限を 768 にし、連続する同じ言い回しを 1 回にまとめる。Haas-ja は同じ音声で繰り返さなかった。
- 検証: `tests/test_chat_asr.py`、別の場所での実際の導入（55 秒・582 MB）と文字起こし、E2E `assistant-queue-voice`。

## 2026-11-18 AI アシスタント: OpenCode の会話中の質問をチャットへ回さない

- 本番: 作業の語が無い質問（「〜できるか？」）が自動判定でチャットへ回り、道具の無いモデルが `<tool_call>` を文字で書いて止まった。OpenCode の会話中は、調査の依頼と＋での選択を除き OpenCode のままにする。チャットには道具が無いことをシステムで伝え、自己診断「道具の呼び出しの文字」で検知する。
- 検証: E2E `assistant-plan`（新規 1 件）、`tests/test_opencode_chat_timeline.py`（新規 1 件）。

## 2026-11-18 AI アシスタントの OpenCode: 裏のレビューの結果・状況表示・アクションリスト・会話名

- 利用者「コードレビューを依頼すると出てから、終わっても何もない」。原因: レビュー用の子エージェントは裏で動き、親のターンは依頼時に一度終わる。ControlDeck はそこで聞くのをやめ、子の完了後に親が出す結果を受け取っていなかった（本番の記録で確認）。子が終わり親の続きが終わるまで聞き続ける。自己診断「OpenCode の裏のエージェント」。
- 入力欄の下を「応答待ち」だけでなく、モデル準備中・プリフィル中・思考中・生成中・Bash を実行中・裏のエージェント作業中と経過秒・速度で出す。プリフィルの % は実行環境から取れないので経過秒にした。
- 道具を開くと開始・終了の時刻と所要時間を出す。
- アクションリスト: OpenCode の `todowrite` は実験機能で既定は切だったので有効にし、リストがあるときだけ会話の上に浮かべる（件数・％・いまの作業。タップで一覧と時刻）。設定を実物が受け付けることは確かめた。モデルが実際に使うかは本番で確認する。
- 会話名: 送った時点で最初の文から付け、最初の回答の後に LLM で 20 字以内の題名へ置き換える（利用者が変えた題名は触らない）。
- 検証: `test_opencode_chat_server`（裏の子の待ち）、`test_opencode_chat_timeline`（題名・アクションリスト）、E2E `assistant-opencode-progress`（新規 2 件・320px／1280px）。

## 2026-11-18 ファン制御: 負荷中に回転を下げすぎない・重い局面への余白・熱の模型をプロジェクトに

- 利用者「またファンがオーバースピードした。運転中も室温が微妙に上がるのと、電力負荷が急変した際はフィードフォワードかけれないか」
  「制御周期が遅い気がする」「熱モデルもプロジェクトに書き込んで最適化に使えるようにし、更新したら新しいものに」。
- 熱モデルの範囲外をべき乗で延ばす（130 W の先読み 23% → 37%）。負荷中は先読みを床にし、すぐ上げる。回転を下げている途中は上乗せを減らさない。
  重い局面の先読み（直近 3 分の最大電力へ戻ると目標に迫るなら、その電力で目標を保てる回転まで先に上げる）。周期は 3 秒のまま（1 秒は模擬でかえって悪い）。
- 2 段の模型を `app/monitoring/plant_models/` に持ち、自動調整で Kp・Ti を選ぶのに使う。1 秒ごとの記録（直近 2 時間）と当てはめ直し
  （`app.maintenance.fan_plant_fit`）。1 分ごとの記録に床・余白の列（古い見出しのファイルは書き直してから足す）。
- 詳しくは `docs/design-amd-smart-fan.md` の「2026-11-18 負荷中に回転を下げすぎて上限に届いた」「熱の模型をプロジェクトに持つ」。
- 検証: `tests/test_fan_two_stage.py`（同梱の模型で 12 件。LLM らしい負荷・熱い GPU）、`tests/test_fan_plant.py`（新規 5 件）、`tests/test_fan_pid.py`。

## 2026-11-18 AI アシスタントの OpenCode: プラン（計画だけ）とビルドの切り替え

- 利用者「プランの切り替えを追加し、プランの際はビルドモードではないことが明確にわかること」。前はいつもビルド（build エージェント）だった。
- 送るたびに OpenCode のエージェントをそろえる（`POST /api/session/{id}/agent`。v2 は名前を確かめず未知の名前でも 204 を返すので、build／plan だけに限る）。回答の meta に `agent` を残す。
- OpenCode の組み込みの plan は編集だけを止め、シェルは止めない（実機の箱・ネットワーク無しで確かめた）。ControlDeck の権限プラグインで、プランでは編集・書き込みを止め、シェルは読むだけを通して他は止め、ControlDeck の道具は確認にする（権限が Auto でも確認でも同じ）。
- 画面: Shift+Tab と札は Auto → 確認 → プラン → Auto（権限を変えられない人はビルド ⇄ プラン）。＋の「OpenCode のモード」。プラン中は入力欄の上に帯「プラン — 計画だけ」と「ビルドに切り替える」、札「📋 プラン」、空色の枠、案内文。回答に「プラン（ファイルは変えていません）」、最後の回答に「このプランで作る」（ビルドで送る）。
- 権限の切り替えは先に画面を変える（返事を待つと、続けて押したときに古い状態から数えていた）。
- レビューの指摘（10 件）を直した: プランでは規則で allow に決まったもの（ビルドで「いつも許可」と答えたもの）も見直して止める（evaluate は allow/deny/ask のどれでも呼ばれ、effect を書き換えられる。型定義 `PermissionEvaluation` で確かめた）、子のエージェントを止める、読むだけの一覧を 1 つにして書ける引数（`-x`・`--pre`・`-o` など）と `fd`・`uniq`・`tree`・`file` を外す、確認モード・箱の外では読むだけのコマンドも確認、ControlDeck がプランのセッション ID を権限ファイルに書きエージェント名が無くても判断する、切り替えた後にセッションを読み直して確かめる、権限の切り替えは 1 つずつ順に送り失敗したらサーバーの値に合わせ直す、「このプランで作る」は OpenCode に決め打ち、新しい会話ではビルドに戻す。
- 利用者「Auto モードは箱の中であればなにしても許可不要で進めて良い」: Auto かつ箱の中は、設定の禁止リスト以外すべて確認なし（箱の外は従来どおり）。
- 2 回目のレビュー（10 件）: プランの主の会話にも重い道具を載せない、`grep -C` などの読む引数を止めない、git の読み取りはプランでは確認、下書きがある間は「このプランで作る」を出さない、＋から入ったプランを出ても権限を変えない、プラン札は OpenCode が使える間いつも出す、権限ファイルの変更検知に inode と大きさを足す、エージェントが同じなら切り替えない。
- 検証: `tests/test_opencode_chat_server.py`（プランの規則・切り替え 2 件）、`tests/test_opencode_chat_timeline.py`（plan が渡り記録される 1 件）、全体 1709 件、E2E `assistant-plan`（新規 3 件・320px／390px／1280px）と AI アシスタントの E2E 32 件。

## 2026-11-18 AI アシスタント: 生成中の要望を送信待ちに貯める・音声入力を長く、波形つきに

- 利用者「実行中に要望を送信したい。サーバー側で保存し、適切なタイミングで LLM に送る。複数の送信・削除・編集・取り消し」。
  - 生成中の送信は表 `chat_queued_requests`（移行 b5e1c7a93d24）へ貯める。回答が**正常に終わったら**サーバーが古い順に送る（画面を閉じていても届く）。送り方（モード・モデルなど）が同じ続きは 1 つの発言にまとめる。止めた・失敗した後は保留し、「今すぐ送る」で送る。
  - 入力欄の上に「送信待ち N 件」。編集・削除（10 秒「元に戻す」）・すべて取り消す。送った後は編集できない（409）。サーバーが送り始めたら画面が会話を読み直して購読する（ほかの端末で始まった生成も）。
  - 送信の本体を `_start_send` に切り出し、画面からの送信と自動送信で共通にした。
  - 自己診断「AI アシスタントの送信待ち」: 回答は正常に終わったのに 2 分以上残っている送信待ち（送り損ね）を警告。
- 利用者「Claude のように波形を出して確かめながら文字入力」「すぐ止まった。長時間を想定して」。前は 1.2 秒の無音で止まって送信・最長 30 秒だった。
  - ✓ を押すまで聞き続ける（最長 60 分。画面が消えないよう Wake Lock）。話の切れ目（0.7 秒の無音。12 秒からは短い無音、最長 15 秒）ごとに区切って文字にし、入力欄へ足す（送信しない）。✕ はこの回の文字を戻す。波形の棒と経過時間を出す。生成中も使える（送ると送信待ちへ）。
  - whisper.cpp は 30 秒に詰め物して読むため 6 秒の区切りで 14〜17 秒かかっていた。読む範囲（`-ac`）を区切りの長さに絞り、スレッドを 8 にして約 3 秒（GPT-SoVITS の日本語音声で文字は同じことを確かめた。20 秒超は繰り返しで崩れることがあり、区切りを 15 秒までにした）。
- レビューの指摘（10 件）を直した: 画面が前の発言を送っている途中（自動判定・ワークフロー）に貯めた要望はすぐ送らない（`hold`。順番が入れ替わっていた）、発言を書いた後に失敗したら「生成中」のまま残さない、送れないまとまりは「送れなかった」にして後ろを送る、まとめるのは添付 8 個まで、回答の後に戻した・直した要望はそのまま送る、自動送信はジョブが片付いてから（決まった秒数で待たない）、黙っている間は 3 秒ごとに録り直し区切りの長さは話し始めから数える、待っている間に取り消したら録音を始めない、波形は購読する部品だけが描き直す。
- 検証: `tests/test_chat_queue.py`（新規 10 件）、全体 1703 件、E2E `assistant-queue-voice`（新規 4 件・320px／1280px。WebSocket とマイクは模擬）、`assistant-claude-layout`・`assistant-message-actions`、390px の画面写しで見た目を確認。

## 2026-11-18 AI アシスタント: 発言の「⋯」のメニューが入力欄に隠れない

- 最後の自分の発言の「⋯」を押すと、メニューが下の入力欄に隠れて「編集して送り直す」を押せなかった（利用者の画面）。下に入るかを画面の下端で判断していて入力欄を勘定していなかったのと、メニューを会話の欄の中に置いていて入力欄より奥に描かれていたため。
- 下の端は入力欄の上端で判断し、入らなければ上に開く。メニューは body の直下（いちばん手前）に出す。
- スクロールで閉じていたのをやめ、ボタンに付いて動く（書いている途中の回答で会話の欄が動き続けても閉じない）。ボタンが画面から外れたら閉じる。
- レビューの指摘（10 件）を直した: 置ける範囲は会話の欄（スクロールの枠）と入力欄の上端で決める、ボタンが欄の外（上のバー・入力欄の裏）へ出たら閉じる、上下どちらにも入らなければ広い側に開いてメニューの中をスクロールする（ボタンに重ねない）、開いている間に項目が増えたら置き直す、見えていないボタンからは開かない、スクロールの追従は 1 フレームに 1 回、Tab で閉じてボタンへ戻す、aria-controls。
- 検証: E2E `assistant-message-actions`（新規 3 件・320px／430px／1280px、Tab で閉じる・ボタンに重ならない。3 回続けて成功）、`assistant-claude-layout`。

## 2026-11-18 Maya: DeepSeek（DS-XS）の画像の読み取りを ControlDeck が正しく扱う

- DS-XS に vision tower を入れた（Maya の設定し直し、DeepSeek の checkpoint の shard 1 と config.json、約 0.97 GB）。ところが ControlDeck は GLM の見方（config の `vision`）でしか判定しておらず、DeepSeek は画像を読めても「読めない」扱いだった（ゲートウェイの画像の解析が断る、モデル画面に印が出ない）。
- DeepSeek（maya-ds4）は pack に vision tower が入っていて `--vision off` でなければ画像を読む、と判定する（Maya の ds4_pack_has_vision と同じ見方）。個別設定に「画像の読み取り」の入／切を足した（tower が無いモデルでは選べない・入にできない）。
- レビューの指摘（9 件）を直した: 入／切を選んだときだけ `--vision` を書く（ほかの項目の保存で切を書き込まない）、断るときは何も保存しない、DeepSeek かどうかは登録の family で見る、相対の `--pack` はエンジンの作業フォルダから、ds4.txt は 1 回だけ読む、ゲートウェイは動いているエンジンの `images` を正とし、切にしているだけなら設定の入／切へ案内する、自己診断「Maya の画像の読み取り」（エンジンと判定の食い違い）、入／切は閉じない「画像の読み取り（vision）」のグループに出す。
- 検証: `tests/test_maya_register.py`（既存のファイルに 3 件）、全体、E2E `maya-ds4-vision`（新規 3 件・320px と 1280px）、本番の DS-XS で判定が「読める」・自己診断が一致になることを読み取りで確かめた。

## 2026-11-18 AI アシスタントの OpenCode: 思考を操作の前後で分ける・作業フォルダを会話で続ける・箱ごとの /tmp

- 思考が全部 1 つの窓に入っていた: 思考の断片を 1 つの文字列に足し込み、道具の一覧と別の枠に出していた。届いた順の並び（`meta.timeline`。思考・本文は文字の位置、道具は ID）を記録し、画面は「思考 → 道具 → 思考 → …」の順に、思考はまとまりごとの窓で出す。再接続の snapshot にも並びを付ける。
- 作業フォルダが外れる: 選択を画面の中だけで覚えていた。会話を開いたら最後の OpenCode の回答のプロジェクトを選び直す。指定が空で届いたら、サーバーも会話の最後のプロジェクトで続ける。
- proto1〜3 が Project Lab に無い: OpenCode が箱の `/tmp` に書いた下書きで、消えてはいない（`data/sandboxes/.rootfs/tmp`）。ただ全部の箱で `/tmp` を共有していた（別のプロジェクトの下書きが見えた）。プロジェクトごとに別の、箱の外に残る `/tmp`（`data/sandboxes/project-tmp/<名前>-<ハッシュ>/`）をつなぐ。OpenCode への指示に「残すファイルはプロジェクトの中に」を足した。Project Lab がプロジェクトのフォルダだけを出すのは仕様どおり。
- 箱の中で CodeDEV を空の場所（tmpfs）で覆い、その上にこのプロジェクトだけをつなぐ。共有の根（`.rootfs`）に前に開いたプロジェクトのつなぎ口が空フォルダで残り、conv の箱から書けない `CodeDEV/Conv` が見えて、OpenCode が取り違えて `Unable to write` になった（2026-11-18）。本物の podman で、隠れること・プロジェクトに書けること・`/tmp` がプロジェクト専用で箱の外に残ることを確かめた。
- レビューの指摘（8 件）を直した: 前回のプロジェクトは CodeDEV の直下なら名前で開く（フォルダの取り込みを通すと失敗・複製のおそれ）、消えたプロジェクトは選び直さない、生成の途中で開き直しても思考を重ねない（思考・並びがあれば snapshot を送る）、準備中に開き直しても並びを作る、プロジェクトの記録がある会話だけ空で送れる、プロジェクトを消すと箱の /tmp も消す、自己診断「OpenCode の箱」で /tmp と覆いを確かめ下書きの量を出す。
- 検証: `tests/test_opencode_chat_timeline.py`（新規 7 件）、全体 1693 件、E2E `assistant-claude-layout`（22 件）、本物の podman で覆いと専用の /tmp。

## 2026-11-18 AI アシスタント: 思考プロセスを見出しでも欄の中のタップでも閉じる

- 思考が長いと開きっぱなしで、上へ戻るのが大変だった（利用者の依頼）。描き直すたびに「本文が無く生成中なら開く」を入れ直していたので、考えている間は閉じても開き直していた。
- 見出しのタップで開閉、開いた欄の中のタップでも閉じる（文字を選んでいるときは閉じない）。閉じたら見出しの位置まで戻す。考えている間に自動で開くのは最初の 1 回だけ。
- 検証: E2E `assistant-claude-layout`（20 件。長い思考の開閉）。

## 2026-11-18 ファン制御: マージン 5°C で行き過ぎない（hotspot の跳ね・休みの後・上乗せの膨らみ）

- 本番（v0.68）で hotspot が 95°C に届き、緊急の最大回転のあと 86% で回り続けた。原因は (1) 1 回の読みで上限を判定していた（hotspot は 1 秒だけ +3〜+7°C 跳ねる）、(2) 負荷が止まると比例の分で回転を急に下げていた、(3) 緊急の後に上乗せが膨らみ、冷えたまま戻らなかった。
- 直し: 上限の判定を 3 回の中央値に、目標より下の比例を 0.6 倍に、上乗せは休み（生の電力）・緊急・下げている途中は止め、それ以外で冷えすぎなら 1/10 の速さで減らす、Ti を熱の時定数の 1.6 倍（80 秒）に。v0.68 で保存された Ti 50 秒は 80 秒へ読み替える。
- 本番の 1 秒ごとの記録（30 分、読むだけ）に 2 段の熱の模型を当てはめ（hotspot の誤差 1.3°C）、マージン 5°C・外気温 +10°C まで緊急 0 回になるゲインを選んだ。模型と実際の揺れはテストの資料にし（`tests/test_fan_two_stage.py`）、毎回確かめる。
- 検証: 全体 1684 件、`tests/test_fan_core.py`（新規 3 件）、`test_fan_control.py`（Ti の移行）、`test_fan_two_stage.py`（新規 6 件）。

## 2026-11-18 AI アシスタント: ボタンを押さずに OpenCode へ引き継ぐ・調べてから渡す

- 自動判定が会話の流れを見る（`assistantMode.detectAssistantMode` の context）。
  - OpenCode が作業している会話では、チャットへ判定し直さず OpenCode に送る。調べる語（調べて・最新・論文など）があれば、その 1 回の送信の中で ControlDeck の調査（Web・論文、調査の計画で検索語を決める）を先に行い、出典つきの根拠を OpenCode への依頼に付ける（`chat_persist._research_for_code`、SendBody.code_research）。OpenCode を手で選んでいても同じ。
  - チャットや調査の回答がある会話で「実装して」「アプリにして」「コードにして」などと送ると、OpenCode に切り替えて、それまでの会話を引き継ぐ（引き継ぎは #448 の handoff_context）。
- ＋の「この会話を OpenCode に引き継ぐ」「調べて OpenCode に渡す」は手で選びたいとき用に残す。
- 調べられなかったときは、調べた結果なしで OpenCode に渡す（作業は止めない）。
- レビューの指摘（9 件）を直した: OpenCode の会話中でも、作業の語の無い質問はチャット、調べる語だけなら調査で答える（結果は次に引き継ぐ）。画像があるときは OpenCode へ回さない（OpenCode を手で選んでいたら知らせる）。調べる LLM は OpenCode のモデル（別のモデルを起こして GPU から追い出さない）。調べた結果と引き継ぎで上限を半分ずつ。「ウェブアプリ」などの名詞だけでは切り替えない。調べた経過を残す。
- 検証: `tests/test_context_budget.py`（調べた結果の付け方・失敗時）、E2E `assistant-claude-layout`（16 件。チャット→「これを実装して」、OpenCode の会話で「調べて反映して」・通常の依頼）。

## 2026-11-18 AI アシスタント: 圧縮ファイルを添付して OpenCode のプロジェクトへ展開

- ZIP・7z・RAR・tar（.gz・.bz2・.xz）を添付できる（200 MiB まで）。添付の時点で中身の一覧を確かめ、`..`・絶対パス・リンク・特殊ファイル・パスワード付き・展開後 2 GiB 超・2 万ファイル超を断る（`workflows/archive_import.py`）。
- OpenCode に送るときに、プロジェクトへ展開する。空のプロジェクトなら直下へ、既存のファイルがあれば `imports/<名前>/` へ（上書きしない）。中身が 1 つのフォルダだけなら、その中を展開先にする。展開は一時フォルダで行い、書いた量で圧縮爆弾を止め、展開後にもう一度リンクと外への出口を確かめてから移す。7z と RAR は 7z コマンド（配列で渡す・時間の上限）。
- 依頼の前に「どこに何を展開したか」とファイルの一覧（200 行まで）を付ける。OpenCode 以外の機能で送ろうとしたら断る。画面は添付した時点で OpenCode に切り替える。文字を打たなくても送れる（中身の説明を頼む）。
- レビューの指摘（10 件）を直した: 7z と RAR は 7-Zip 21 以降だけで展開（外を指すリンクを作らない。p7zip は使わない）、展開中も合計を見張り 1 ファイルの上限（prlimit）をかける、リンクの印を一覧の項目でも見る、imports がリンクなら移す前に断る、展開したものだけ数える（.git を数えない）、展開後・取り消し・発言の削除で圧縮ファイルを消す、未対応の ZIP の圧縮方式と壊れた並びを分かる言葉で断る、送る前に画面で 200 MiB を確かめる、event loop で読み書きしない、展開先を 0755 に。
- 検証: `tests/test_archive_import.py`（新規 10 件。ZIP・tar・7z の展開、危ない中身、宣言を偽る圧縮爆弾（ZIP と 7z）、リンク、imports のリンク、添付・取り消し・送信の受け口）、E2E `assistant-claude-layout`（13 件）。RAR は作る道具が無いので 7z で代表（7z 23.01 は RAR/RAR5 を読める）。実際の OpenCode での通しは本番に入った後。

## 2026-11-18 AI アシスタント: ＋メニュー・OpenCode への引き継ぎ・コンテキスト長に合わせた量

- 入力欄の左下に＋を置き、添付・機能の選択・OpenCode の権限を 1 つのメニューにまとめた（Claude と同じ形）。選んだ機能と権限は＋の隣に小さな札で出す。
- 権限が消えた原因: 入力欄の下の行に「いまの判定が OpenCode のときだけ」出していたので、自動判定が文字を打つたびにチャットへ判定し直すと消えた。OpenCode が使えるなら、いつでも＋から変えられる。
- OpenCode への引き継ぎ: 前回の OpenCode の回答より後のチャット・調査（出典つき）を、OpenCode への依頼の前に付ける（`chat_persist.handoff_context`）。＋に「この会話を OpenCode に引き継ぐ」「調べて OpenCode に渡す」。
- コンテキスト長に合わせた量: Deep Research の根拠・レポート・出典、調査の検索回数と抜粋、Web 検索の件数、RAG の件数と量、OpenCode への引き継ぎの量を、モデルの窓から決める（`context_budget`、根拠は docs/design-context-budget.md）。モデル画面に一覧。
- 検証: `tests/test_context_budget.py`（新規）、全体 1703 件、E2E `assistant-claude-layout`（12 件）、実画面（390px・1280px）。`assistant-mobile` の 1 件は main でも同じく落ちる（検証用インスタンスで音声入力が使えない）。

## 2026-11-18 ファン制御 v2: PID だけ・診断の記録・記録から作り直す

- 実機（R9700）で PID と温度マップを比べ、温度を一定に保てた PID だけを残した（全力中の hotspot 90.0±0.5°C、温度マップは 89.3±1.05°C）。温度マップ・方式の選択を撤去。プリセットは無く、選べるのは「この制御」か「GPU 標準（デフォルト）」。
- PID: 上がる温度を遅らせない、上乗せ（積分）を休み中も保持して熱モデルへ保存（再起動・夏の外気温でも引き継ぐ）、効き具合を強めに（Kp 1.33 → 2.5）。旧設定の targets_c は上限へ移さない。
- 診断の記録: 制御中の値を 1 分ごとに `data/diagnostics/fan-*.csv`（1 日約 120 KB・2 日分）。逸脱（目標 +2°C が 60 秒）と緊急を自己診断・報告へ。「記録から作り直す」（`POST /api/v1/system/fan-control/refit`、数秒）で熱モデルと Kp・Ti を作り直す。
- 推移のグラフの下段を回転数（rpm）に。
- 検証: `tests/test_fan_core.py`・`test_fan_control.py`・`test_fan_log.py`（新規）、実機の比較（docs/design-amd-smart-fan.md）、`npm run build`。E2E と実画面は下記。

## 2026-11-18 OpenCode: 日本語のプロジェクト名で回答が作れない（UnicodeEncodeError）

- プロジェクト「コンバータシム」で OpenCode が「処理に失敗しました（UnicodeEncodeError）」。OpenCode の server へ送るヘッダー `x-opencode-directory` にプロジェクトのパスをそのまま入れていて、日本語が入ると HTTP のヘッダー（ASCII だけ）を作る前に落ちていた。
- OpenCode 自身と同じく URL エンコードして送る（server 側は decodeURIComponent で戻す。v2 本体で確認）。ASCII だけのパスは今までと同じ値。
- 検証: `tests/test_opencode_chat_server.py`（既存のファイルに 1 件）、backend 全体。

## 2026-11-17 AI アシスタント: 検索系の機能を「調査」1 つにまとめる（検索の統合 その 2）

- 機能の選択欄に Web Search・Academic・Deep Research・Research が並んでいた（#423 は自動判定の振り分けだけ）。「🔎 調査」1 つにし、深さ（自動・速い＝Web・標準＝Web と論文・深い＝Deep Research のレポート・論文だけ）を入力欄の下で選ぶ。
- 深さが自動のときはサーバーの振り分け（/chat/route）で決め、対話と判定されたら少なくとも Web を調べる。機能の「自動判定」は従来どおり 4 つのどれかへ振り分ける。
- 検証（sandbox `invest`）: E2E `assistant-claude-layout`（メニューに調査があり検索系 4 つが無い・深いを選んで送ると deep）・`assistant-mobile`（配置。音声入力の箇所は既知の失敗）、`npm run build`。
## 2026-11-17 Deep Research: 引用の中身を確かめる（検索の統合 その 3）

- これまでの引用の確かめは「番号が実在するか」「段落に引用があるか」の形式だけだった。`workflows/claim_check.py` で、引用の付いた文ごとに、文の語（日本語は 2 字組・英数字は単語）と数値が引用先の本文にどれだけ出てくるかを測る（LLM を使わない）。一致 15% 未満か、出典に無い数値を含む文を「支えが弱い」とする。
- 弱い文があれば、その文だけを一度 LLM に直させ、弱い文が減ったときだけ採る。残ったものはレポートの末尾「根拠の確認が弱い主張」に並べる。進み具合の表示に「出典の支え N%（弱い主張 M 件）」。
- 検証（sandbox `cite`）: `tests/test_deep_research.py`（既存のファイルに観点を足した）、backend 全体 1696 件。

## 2026-11-17 Maya: 登録したモデル（DS-XS など）の個別設定

- これまで設定画面は最初のモデル（GLM-5.3-Flash · Maya-S）だけで、後から登録した DS-XS などは何も変えられなかった。個別設定のシートに設定欄を足した（`maya_s.instance_settings` / `save_instance_settings`、`GET/PUT /api/v1/features/maya-s/models/{alias}/settings`、監査）。
- 共通: コンテキスト長（--max-context）、思考の深さ・思考の上限・temperature・top_p（エンジンの config）、GPU 番号、起動と停止（Maya 全体で共通の設定）。
- DeepSeek（maya-ds4）: 下書き（--dspark）、エキスパートを VRAM に置く割合（--vram-experts）、mmap（--mmap-experts）、SSD キャッシュ（--ssd-cache）、CPU スレッド数、先読みの単位（--prefill-chunk）。既定（自動）に戻すとオプションごと外す。
- 起動中は起動と停止の設定だけ変えられる。エンジンの設定は次の起動から効く。
- 検証（sandbox `maya-set`）: `tests/test_maya_register.py`（既存のファイルに観点を足した）、本番の設定ファイルを読み取りで確認、`npm run build`。画面の操作は未確認。

## 2026-11-17 RAM を正味の利用量で出す

- Home・履歴・RAM の警告の割合を、プログラムが確保した分に「プログラムが映して使っているファイル」（モデルなど）を足した正味の量にした（`collector.net_memory`、/proc/meminfo の Mapped）。誰も使っていないキャッシュだけ除く。DS-XS（Maya）でモデル約 75GiB を映して RAM をほぼ使い切っていたのに、15% と出ていた。
- 全体は搭載量 96GB（Linux が使える 91.8GiB を 8GiB 単位へ切り上げ）、差の約 4GB（内蔵 GPU の映像用・BIOS・カーネル）は常に使っている分として足す（利用者の依頼）。Home の欄は「使用 / 96 GB（うちモデル）」。
- DS-XS を動かしている間は RAM の警告（memory_percent）が出やすくなる（実際に使い切っているため）。
- 検証（sandbox `ram`）: `tests/test_memory_net_usage.py`、実機の /proc/meminfo で値を確認、`npm run build`。

## 2026-11-17 ファンの利用者プリセット・会話履歴のスワイプ・画面側の時間の診断

- ファン: 画面で調整中の設定に名前を付けて保存し、削除できる（`data/fan-user-presets.json`、20 件まで）。組み込みのプリセットは消せない。使っていたプリセットを消すと「カスタム」として残る。`POST/DELETE /api/v1/system/fan-control/presets`（settings.manage・監査）。
- AI アシスタントの会話履歴: 右へスワイプでアーカイブ、左へスワイプで削除。一定以上引いて離すと確認なしで適用し、続けて操作できる。数秒は「元に戻す」（削除は 6 秒後に実行、画面を離れるときはすぐ実行）。アーカイブした会話は履歴の下から開いて、右へスワイプで戻せる。`conversations.archived_at`（migration c7d2e9a14b60）、`GET /chat/conversations?archived=true`、`PATCH` に `archived`。縦の指の動きは一覧のスクロールに任せる。
- 診断: 画面側の時間（AI アシスタントを開いてから一覧・会話の取得・描画まで、その間の API の往復・待ち・大きさ）を記録し、レポートに p50/p95 を出す。会話の読み込みが遅い件は、サーバー側では 2 秒未満・詰まり 0 だったので、実機の数値で通信・API・描画のどこかを決める。
- 検証（sandbox `ux3`）: `tests/test_fan_control.py`（利用者プリセット）、`tests/test_chat_persist.py`（アーカイブ）、`tests/test_diagnostics.py`（画面側の時間）、E2E `assistant-claude-layout`（スワイプ・取り消し・アーカイブの一覧）と `fan-temperature-margin`（保存・削除）。

## 2026-11-17 決まり: 異常には診断を足す・テストは目的と根拠を書いて重複させない

- `AGENTS.md` に「不具合・診断・テストの決まり」を追加（利用者の決まり）。異常が起きたら検知できる診断を足す（軽微・一度きりは作らない、常時の見張りが重いものは診断のテストケースに）。テストは目的と根拠を書き、足す前に目録で重複を探して直す・広げる・統廃合する。
- `docs/diagnostics.md`: 今ある診断の一覧（何を・どう見るか・きっかけ）と足し方。
- `docs/test-catalog.md`: テストの目録（`./deck.sh test-catalog` が各ファイルの目的と各テストの 1 行目から作る）。backend 164 ファイル・1476 テスト、E2E 79 spec・132 テスト。目的の未記入 93 ファイルは `tests/test_test_catalog.py` の一覧に残し、新しいファイルには目的の docstring を必須にした。

## 2026-11-17 診断: 不調の記録と AI に渡せるレポート

- `backend/app/diagnostics/`: 常駐の記録係（`recorder.py`）が、警告以上のログ（traceback つき）、**event loop の 1 秒以上の詰まり**（loop の外の thread から、塞いでいた関数の stack を撮る）、2 秒以上かかった API と API ごとの所要時間（p50/p95）、30 秒ごとのメモリ（空き・swap・VRAM・RSS 上位と unit）と空きが少ないときの上位を `data/diagnostics/events-YYYYMMDD.jsonl`（7 日分）へ残す。既存の watchdog の見張り（12 秒で全 thread を stderr）では、画面の「遅い」の原因になる数秒の詰まりが残らなかった。
- `journal.py`: 本体の再起動とその理由（watchdog・OOM・異常終了・更新・手動）、管理下の常駐（control-deck-* / cdapp-* / cd-*）の異常終了、カーネルの OOM killer の記録（プロセス・RSS・unit）を journal から抜き出す。
- `selftest.py`: DB の quick_check、ディスク、メモリ、詰まり、失敗した unit、ローカル LLM、画面の build、必要なコマンド、メトリクス収集。backend のテストを本番のデータに触らずにジョブで流す（`CONTROL_DECK_CONFIG` を外す）。
- `report.py`: 一式を Markdown/JSON にまとめ、鍵・トークン・パスワード・メールアドレスを伏せる。API `/api/v1/diagnostics/{summary,events,memory,journal,selftest,tests,report}`（settings.manage）。OpenCode などの AI は MCP の道具 `control_deck.diagnostics` で同じレポートを全文読める（橋渡しで削らない）。
- 画面「診断」（ドロワーの システム）: 自己診断・再起動・OOM・常駐の異常終了・詰まり・遅い API・警告/エラー・メモリ・失敗したジョブ・テスト実行、「AI 用レポートをコピー」「ダウンロード」。
- 本番の記録から分かったこと（2026-11-17）: 11/16 に Strata（qwen-flash-next）が 65〜67GB で 2 回 OOM、並べ替えの llama.cpp（rerank-qwen3-4b）が 51 回異常終了、本体の watchdog 再起動 3 回（いずれも終了処理の途中で addons の待ちに塞がれていた。#419 で直した部分）。
- 伏せ字は JSON の値ごとにかける（文字列にかけると escape を壊す）。鍵らしい名前（token・secret・password・key・cookie・session…）の後の値、Bearer/Basic、JWT、cdk-/sk-/ghp_/hf_ などの形、メールアドレス。プロセスのコマンドラインは記録しない。同じ警告は 1 分に 20 件まで。
- テストの実行: 指定は `tests/…​.py(::名前)` だけ（pytest のオプションは通さない。`--basetemp` に本番の data を渡すと消える）、同時に 1 本、`CONTROL_DECK_NO_MACHINE_TASKS=1` と一時の HOME/XDG で、機械全体に効く常駐（ファン・常駐の復元・自動更新・モデルの自動起動/停止）を起こさない。
- 本体の終了処理の不具合 2 件を直した: 機械全体の処理を起こしていない（検証用・テスト）ときに `searxng` を参照して落ちる、本番のファンを自動に戻していた。
- 検証（sandbox `diag`）: `tests/test_diagnostics.py`（秘密伏せ・記録・journal の解析・詰まりの検出で塞いだ関数名が残ること・API）、`tests/test_addon_agent_mcp.py`、`npm run build`、E2E `diagnostics`（1280px・390px、実データ）。

## 2026-11-17 AI アシスタントを Claude と同じ形に・iPhone のホーム画面アプリで上下が欠けるのを修正

- 上下の欠け（iPhone のホーム画面アプリ）: 殻（`#root`）を `position: fixed` で画面へ直接貼った（`html.cd-app-shell #root`）。これまでは `html`/`body` に `overflow: hidden` を掛けた上で `#root` を 100vh にしていたので、iOS が再読み込みの後に報告する高さ（`body` の 100%）が Safe Area の分だけ縮むと（#40 で実測した現象）、`body` が殻の下端を切り、入力欄が画面の下へ欠けた。fixed なら `body` に切られず、文書がずれても上の行が上端から離れない。上の行（`SHELL_TOPBAR_CLASS`）は半透明＋ぼかしをやめて不透明にした（下へ中身が潜る作りではなく、ステータスバーの帯と重なって題名が曇って見えた）。AI 入力欄はホームバーに重ねない（`.assistant-composer-safe`: 状態の行 32px を Safe Area の帯へ置き、足りない分だけ余白）。下端の Safe Area は `--cd-safe-area-bottom` を境界にした（上端と同じく e2e から iPhone の値を入れられる）。
- ほかの画面も同じ殻なので同じ修正が効く。全画面の重ね表示（Files のエディタ・抽出ビューワ・Apps のビューア）に下端の Safe Area、右から出るパネル（`SidePanel`）は `h-dvh` をやめて重ねの枠の高さに合わせた。Service Worker のキャッシュは v18。
- ドロワーの入れ子: まとまりの名前「システム」の中に「System」があり、Settings を開くとカテゴリの一覧、その中にまた「システム」があった（☰ → 一覧 → 一覧 → 中身）。まとまりは「管理」に改名。設定は携帯でもカテゴリの一覧だけの画面を挟まず、横に流れる札（44px）で切り替えて中身をすぐ出す（`/settings` は最初のカテゴリを表示）。設定のカテゴリ「システム」は「更新とバックアップ」に改名。PC の左の一覧はそのまま。
- AI アシスタント（`features/workflows/AssistantChat.tsx`）:
  - 会話の履歴（`ConversationHistory.tsx`）: 「新しいチャット」、検索（題名で絞り込み）、今日 / 昨日 / 過去7日間 / それ以前（`updated_at` を端末の暦日で数える）、行ごとの「⋯」で名前を変更（その場で編集）・削除（確認つき）。いまの会話に印。PC（1024px〜）は会話の左の列で畳める（`cd-assistant-history` に保存）。携帯は上の行の履歴ボタンで左から出るシート（☰ はアプリ全体のドロワーのまま。外側・Escape・× で閉じ、Tab を中に留める）。
  - 上の行は殻と同じ 1 段: ☰・履歴・会話の題名（押してその場で名前を変更）・モデルの札（従来の AI 設定パネルを開く）。処理モードと会話切替の `<select>`、新規・削除ボタンは撤去。設定パネルの会話名の欄も撤去。
  - 機能の選択は入力欄の札（`ModeChip.tsx`）: 「✦ 自動 · Web Search」のように今の判定を出し、押すと 自動判定 と各機能（印・名前・一行の説明）のメニュー。権限（`workflows.edit`）と OpenCode の有無による絞り込みは従来どおり。入力カードは本文が上、道具の行（添付・機能・音声・送信）が下。OpenCode のときの権限モードの札とプロジェクトの選択は入力欄のすぐ下／上に残した。入力欄は中身に合わせて伸びる。
  - 回答は枠なしの本文、発言は右の吹き出し。幅は 48rem。送信・会話の作成のたびに履歴を取り直す（題名と並び順が変わる）。
- 失敗・停止・中断した回答: 本文が無いと「▶ 🧭 Chat」の札だけで何も出なかった。状態（回答を生成できませんでした / 回答を停止しました / 回答が中断されました）と理由（`/messages` の `error`）、「再試行」（直前の自分の発言を送り直す）を出す。失敗だけ赤。本文の無い回答にも「⋯」（分岐・削除）を出す。ストリームの `done` に発言へ記録した `error` を載せた（`chat_persist.py`）。
- 検証（sandbox `ui-claude`）: `npm run build`、`tests/test_chat_persist.py`。E2E（検証用インスタンス、API は差し替え）: `assistant-claude-layout`（履歴の見出し・検索・開く・名前の変更・削除・新しいチャットを 1280px と 320px、機能メニュー、失敗表示と再試行、`navigator.standalone` と Safe Area 59/34px を入れた 390px・320px で上の行・入力欄・ほか 10 画面の上下、ドロワーと設定）8 件、`assistant-message-actions` 3 件、`navigation-layout` 4 件、`mobile-viewport`、`a11y` 12 件が成功。`assistant-mobile` は新しい画面に合わせて書き換えた（配置の検査まで通る。音声入力の部分は検証用インスタンスに ASR モデルが無く従来どおり通らない）。320px・390px（Safe Area つき）・1280px、ライト/ダークを撮影して確認。未確認: iPhone 実機（上下の欠けの原因は実機の高さの報告のずれと推定したもので、Chromium では再現できない）、本物の LLM での再試行。
## 2026-11-17 AI アシスタント: 調査の振り分けを画面とサーバーで揃える（検索の統合 その 1）

- 自動判定の語を `frontend/src/features/workflows/routingWords.json` にまとめ、画面（`assistantMode.ts`）とサーバー（`assistant_planner.py`）が同じ表を読む。以前は画面が「調べて」を Web 検索と見るのに、サーバーは LLM に回してチャットにしていた（「グランフロントの店を調べて教えて」がチャットになり、検索されなかった）。
- 「調べて・探して・検索して」は Web、「詳しく調べて・網羅的に・レポートにまとめて」は Deep。LLM の判定の選択肢に deep を足した（以前は deep をルールでしか選べず、ほとんど使われなかった）。計画に調べる深さ `depth`（quick / standard / deep）を持たせた。
- 評価セット `backend/tests/fixtures/assistant_routing_cases.json`（26 件）と、LLM 込みで測る `python -m app.workflows.routing_bench --base-url … --model …`。実測（ローカル auto）: main 81%（21/26、LLM 判定 6/9）→ 100%（26/26）。
- 検証（sandbox `research-route`）: `tests/test_assistant_planner.py` 9 件、backend 全体 1676 件、`npm run build`。

## 2026-11-17 AI アシスタント: 会話の分岐・編集・削除、添付の進み具合

- 各メッセージの下の「⋯」（`features/workflows/MessageActions.tsx`）: コピー・ここから分岐・編集して送り直す（自分の発言）・削除。メニューは画面に固定して出し、行は 44px 以上、スクロール・回転・外側・Escape で閉じる。
- 分岐 `POST /chat/conversations/{id}/fork`（`include=false` はその発言の手前まで＝編集して送り直す）。元の会話は残す。添付画像・会話の資料（RAG コレクション、`rag.copy_collection`）・文献を写し、圧縮の要約の `covers_until` は新しい ID へ付け替え、許可待ちは expired にする。OpenCode のセッションは引き継がない（分岐先で新しいセッション。画面で通知）。編集の送り直しは書き直す発言の画像も載せ直す。
- 削除 `DELETE /chat/conversations/{id}/messages/{mid}`: 自分の発言を消すとその回答も消す。添付画像のファイルも消す。本当に動いているジョブの生成中だけ 409（再起動でジョブが消えた generating は生成中とみなさない）。
- 添付は `XMLHttpRequest` で送り、チップに % と下端の進捗バー、送り終わったら「保存中…」/「資料に登録中…」。http でも動くコピー（`lib/clipboard.ts`、ターミナルと共通）。
- 検証（sandbox `conv-edit`）: `tests/test_chat_persist.py` ほか 22 件、backend 全体 1668 件成功（1 回目は既知のフレーキー 3 件）、`npm run build`、E2E `assistant-message-actions`（1280px・320px、API は差し替え）と `terminal-copy`。独立レビュー: 重大 0、要修正 5 件を修正。未確認: 本物の LLM での送り直し、iOS Safari 実機。

## 2026-11-17 画面の殻を刷新: 左上の ☰ で開くドロワー／折りたためるサイドバー

- ナビゲーションを 1 つにした。携帯（〜767px）は左上の ☰ で左から出るドロワー、PC（768px〜）は ☰ で折りたためるサイドバー（状態は `cd-sidebar` に保存）。携帯の下部ナビと、同じ行き先を並べていた More / Quick Actions の一覧、PC の上の行の検索欄・電源ボタン・利用者名は撤去。`AGENTS.md` の「ボトムシート + 下部ナビ」の決まりも新しいナビの説明へ置き換えた。
- ドロワーの中身は PC と携帯で同じ部品（`AppLayout.tsx` の `NavPanel`）。先頭に「よく使う」（以前の下部ナビの選択 `cd-mobile-navigation-v1` をそのまま引き継ぐ。設定 → 外観の「よく使う機能」で最大 6 件）、続けて メイン / 開発 / AI / 拡張機能 / システム。同じ行き先は 1 回だけ出す。行の高さは携帯 44px。背景を押す・左へ払う・Escape・☰ で閉じ、開いている間は Tab を中に留め、閉じると ☰ へ焦点を戻す（`role="dialog"` `aria-modal`）。最下段は利用者（サインアウト・拡張機能の quick action・電源）。
- 上の行を全画面で統一: ☰・題名（h1）・主操作。`PageHeader` は殻の中では題名と操作を上の行へ描く（`layouts/shell.tsx` の `headerSlot`）。題名が上の行とページ先頭の 2 か所に出ていたのを 1 か所にした。`PageHeader` を使わない画面は殻が題名を出す。
- コマンドパレット（Ctrl/⌘K）の行き先を `NAVIGATION` から作るようにした（手で並べた 8 件だけだった。Files・Terminal・Models などが無かった）。
- AI アシスタントは全画面のポータルをやめ、殻の中の 1 画面にした。上の行は殻と同じ形で、同じ ☰ が同じドロワーを開く（PC はサイドバーが出たまま）。専用の「閉じる」は撤去。
- iPhone で会話を指で動かすと入力欄ごと画面が動く不具合: 殻の中では文書をスクロールさせない（`html.cd-app-shell` に `overflow: hidden` と `overscroll-behavior: none`）、動くのは会話の一覧だけ（`overscroll-contain`）。キーボードが出ている間は殻を visual viewport に合わせ、上の行が画面の外へ出ないようにする（`hooks/useKeyboardViewportFit.ts`）。
- レビュー後の修正: h1 は画面に 1 つ（`PageHeader` が届け出ている間、殻は予備の題名を描かない。Add-on の案内・エラー表示の見出しは h2）。`NAVIGATION` に権限を付け、権限の無い利用者のドロワーとコマンドパレットに出さない（Terminal・Files・Remote・Apps・GitHub・Knowledge・Models）。上の行を `z-30` にしてメニューが中身の下に潜らないようにした。公開ランナー（`/runner`）の題名・戻る・停止は上の行へ。ワークフローのエディタは殻の上の行を出さず自分の道具の行だけ。320px の Workflows はサンプルブックをメニューへ、GitHub のログインは短い表記。`main` に下端の Safe Area の余白。
- 検証（sandbox `ui-shell`）: `npm run build`、`tests/test_opencode_chat_server.py`。ミラーで 320px（ダーク）・390px（タッチ）・PC 幅（ライト/ダーク）を撮影。横あふれ 0、画面のエラー 0、h1 は各画面 1 つ。ドロワーの開閉（Escape・外側・左へ払う）と焦点の閉じ込め、サイドバーの折りたたみの保存、会話の一覧を指で動かしても上の行と入力欄の位置が変わらないことを確認。E2E（`navigation-layout`・`assistant-mobile`・`addon-v2-ui`・`workflow-runner`）は新しいナビに合わせて書き換えたが未実行。iOS Safari の実機（ゴム引き・キーボード）は未確認。

## 2026-11-17 AI アシスタントで OpenCode をフル機能で（段階 1）

- AI アシスタントの OpenCode モードを、発言ごとの `opencode run --auto --standalone`（本文の文字だけ受け取る）から、OpenCode v2 のサーバー（`opencode serve`）の API へ切り替えた。本文・思考・道具の実行（コマンド・編集・出力）・許可の問い合わせ・停止がチャットに出る。詳細と実測は `docs/PLAN-opencode-dev-environment.md` の「AI アシスタントの OpenCode」。
- 画面: 道具を 1 行のカードで出し、タップで出力を開く。許可は「今回だけ許可・常に許可（型つき）・拒否」の大きなボタン（高さ 44px 以上）。実行中は送信ボタンが「止める」になる。入力欄の下に権限モードの札（⚡Auto / ✋確認）。タップでメニューを開いて選ぶ（携帯）、PC は Shift+Tab。メニューは入力欄の枠に切られないよう画面に固定して出す。
- 権限モードの既定は Auto（利用者の指定）: 編集・読むだけ・テスト/ビルドは自動、危ない操作と判断できないものだけ確認。今までの `--auto`（全部自動）はやめた。
- 検証（sandbox `oc-chat`）: `tests/test_opencode_chat_server.py` 10 件（プラグインの判断を node で、設定の規則の順、秘密を argv に出さない、1 回分の対話・失敗・中断、権限モードの API と動いているサーバーへの反映、チャット API の許可の返答）、`pytest` 全件、`npm run build`。本物の OpenCode v2.3.0 を `chat_server` で起こした結合の確認（偽の LLM）。ミラーで 320px（ダーク）・390px・PC 幅を撮影（横あふれ 0、画面のエラー 0、権限モードをタップで選んで保存されることを確認）。本番のゲートウェイ・ローカル LLM での実行は未確認。

## 2026-11-17 AI アシスタント: Markdown・表・TeX の表示、添付の修正、画像の貼り付け

- 回答を Markdown で描く（見出し・太字・リスト・表・引用・コード）。TeX の式は KaTeX（`$…$`・`$$…$$`、LLM がよく出す `\(…\)`・`\[…\]` も変換）。生の HTML は描かない。表はセルの中で折り返し、列が多いときだけ表の中を横にずらす（画面全体は横に広げない）。ライブラリ（react-markdown 9.0.1・remark-gfm 4.0.0・remark-math 6.0.0・rehype-katex 7.0.1・katex 0.16.11、版固定、利用者の許可を得て導入）は回答を描くときに初めて読む別ファイル（gzip 134 kB）。
- 添付の不具合を修正: 📎で選んでも何もアップロードされていなかった。`onChange` で `attachFiles(e.target.files)` の直後に選択欄を空にしており、連動する `FileList` が空になってから読んでいた（本番のログで添付の要求が 0 件だったのはこのため）。`File[]` に写してから空にする。
- クリップボードの画像を入力欄へ貼り付けて添付できる。添付した画像は小さく表示し、本文が無くても画像だけで送れる。送った画像は発言の上に出し、会話を開き直しても出る（`meta.attachments`、`GET /api/v1/chat/conversations/{id}/attachments/{attachment_id}`、本人の会話・ID の形を検証）。会話を消すと添付の画像も消す。
- HEIC など対応しない画像は、文書として読もうとせず 415 で理由を返す。文書は読めたのに埋め込みモデルへ届かないときは、「文書を読み取れません」ではなく「資料の登録先（埋め込みモデル）に接続できませんでした」（503）と返す。アップロードのファイル書き込みと文書の読み取りを event loop の外へ。
- 初期設定（`./deck.sh`）と更新準備（`update-prepare`）で Node.js / npm を用意する: 無ければ apt で入れ、18 未満なら理由と入れ方を出して止める（更新準備は npm が無いと止まるだけだった）。`package-lock.json` が変わったら `node_modules` があっても `npm ci` で入れ直す（以前は `node_modules` が無いときだけ入れたので、依存を足した版で `./deck.sh` の build が落ちた）。
- 検証（sandbox `assistant-md`）: `tests/test_chat_persist.py` 11 件、`pytest` 全件、`npm run build`。ミラーで、📎の画像・文書、貼り付け、画像だけの送信、開き直し後の画像、回答の表・式・コード（回答本文は差し替え）を 320px（ダーク）・390px・PC 幅で確認（横あふれ 0、画面のエラー 0）。ミラーには埋め込みモデルが無いので文書の登録は 503 になる（本番では埋め込みモデルが稼働中）。iOS Safari の実機では未確認。

## 2026-11-17 Home の LLM カード: ローカル LLM のアンロード中の表示

- ローカル側の列は、workflows.run の利用者には常に出す。モデルがアンロードされている間は、最後に動いていたモデル名と、その前処理・生成の速さ・ctx を薄い色で出し、見出しの隣に「アンロード」の札を付ける。一度も動いていなければ「未ロード」と「—」。
- 「平均」は常に、今日 LLM が処理していた間の積算（電力量 ÷ 時間）から出し、¥/h もその平均で出す（以前は処理中だけ瞬間の電力、10 秒未満は「—」だった）。
- Backend: `app/models_mgmt/llm_last_seen.py`。容量 API が返した 1 台（処理中のものを先に）を覚え、`/models/llama/capacity` の `last_local` で返す（動いているモデルがあるときは null）。待機中に速さが 0 になる実行環境で直前の速さを 0 で上書きしない。ファイル（`data/llm-last-seen.json`）へ書くのはモデルが変わったときとアンロードされたときだけで、再起動後も前回の値を出す。
- 検証（sandbox `llm-unload`）: 集中テスト `tests/test_llm_last_seen.py` 7 件・`tests/test_electricity.py`、`pytest` 全件 1648 passed、`npm run build`。ミラー（API 応答を差し替え）でアンロード中・未ロード・稼働中を 320px（ライト/ダーク）と PC 幅で確認（横あふれ 0、画面のエラー 0）。

## 2026-11-17 Home に LLM カード（ローカル + OpenRouter）

- Home の「実行中のアプリ」の上に LLM カードを追加。ローカル LLM と OpenRouter を 1 枚に左右半分ずつ並べる（携帯でも半分ずつ）。どちらも無ければ出さない。OpenRouter 側は settings.manage の利用者だけ。
- 左右は同じ作り: モデル名 → 前処理（OpenRouter は初回応答の秒数）・生成 tok/s・ctx（使用 / 最大とバー）→ 区切り → お金の 2 行。ローカルは「電力（処理中は今の W、待機中は今日の処理中の平均）· ¥/h」と「今日の LLM 電気代 · 処理していた時間」。OpenRouter は「残りクレジット」と「今日の使用額 · 回数」。利用者との擦り合わせで、最近 7 日のモデル別・無料枠・トークン数・キャッシュ率・今月の行は出さない。
- ctx の出どころ: llama.cpp は KV の使用量。Strata と Maya-S はエンジンの `/metrics`（実行中の 1 本の「プロンプト + 生成」、無ければ直前の 1 本、最大は engine.max_context）。Radiance は ControlDeck が中継した直前の 1 本。OpenRouter は直前の呼び出しの「プロンプト + 生成」/ モデルのコンテキスト長（モデル一覧を取ったときに覚える。知らなければ 1 時間に 1 回まで一覧を取りに行く）。
- LLM の電気代: 利用者の選択で「LLM が処理していた間の PC 全体」（基本電力を差し引く推定はしない）。PSU の電力（コンセント側換算）を 2 秒ごとに積算する既存の仕組みで、ゲートウェイ・内部の中継が LLM へ要求を出していた区間だけを別に数える。ControlDeck を通さずポートへ直接来た要求は数えない。日別に `data/electricity-llm.json` へ保存（62 日分、DB の変更なし）。
- Backend: ゲートウェイの外部モデル中継で、所要時間・最初のトークンまでの時間・生成速度を `cloud-usage.jsonl` の行に足した（本文は従来どおり記録しない）。実行中の呼び出しはメモリだけで持つ（15 分で掃除）。`GET /api/v1/models/cloud-summary`（settings.manage）を追加。`/models/llama/capacity` に `power` と `ctx_active` を足した。
- 検証（sandbox `home-llm`）: `pytest` 全件、`npm run build`。集中テストで見つかった不具合（`CallTimer` の既定値が定義時の `time.monotonic` を掴み、時計の差し替えが効かず `ttft_s` が負になる）を修正。ミラー（API 応答を差し替え）で Home を PC 幅・375px・320px、ライト/ダーク、実行中/待機中で確認（横あふれ 0、画面のエラー 0）。本番で LLM が動いていない時間で、Strata / Maya-S の `/metrics` を実機では読めていない。

## 2026-11-17 Maya のモデルを登録画面から導入・削除・更新

- Models の一覧から Maya の「モデルが未準備です」カードを撤去。GLM-5.3-Flash · Maya-S も含め、Maya のモデルは「＋」→「Maya」から導入する（GLM Maya-S を登録すると従来の maya-s として戻り、調整値を引き継ぐ）。
- 登録画面: 選んだフォルダに足りない量子化は「ダウンロードして登録」。大きさ・保存先・空き・ファイル一覧を確認画面で見せ、押したときだけ Maya の一覧（エンジンのマニフェスト）の版に固定して、そのファイルだけを hf で取得（途中から再開可）。大きさと SHA-256 を確かめてから登録。DeepSeek は Maya が探すフォルダ名（DeepSeek-V4.1-Flash-1017-Maya-GGUF）を自動で作る。空きが足りなければ始めない。
- 削除: 「モデルのファイルも削除する」（既定オフ）で、その量子化の一覧に載ったファイルだけをフォルダの中から消す。ほかの Maya のモデルが使う共有ファイル（n-gram 表など）、フォルダの外、シンボリックリンクは消さない。
- Maya の更新後: 登録済みのモデルを新しいエンジンで準備し直す（ダウンロードしない。ポート・env の調整値・思考設定を維持）。新しい版のファイルが一覧に載ったモデルは「新しい版あり」と表示し、個別設定の「更新（ダウンロード xx GB）」で取得して準備し直す（古いファイルは残す）。
- 検証: backend 全体、Maya の集中テスト（ダウンロードの引数・版・include、空き不足、共有ファイルを残す削除、更新後の準備し直しと調整値の維持、GLM Maya-S の復帰）、e2e（320/1280、確認前に送らないこと）。実際のダウンロード・実機の準備し直しは未実施。

## 2026-11-16 OpenCode: 作業の種類を撤去し、自動レビューと相談へ

- OpenCode 画面のセッション開始から「作業の種類」（UI 検証・精密調査・レビュー）を撤去。道具（ui-verifier・精密調査・reviewer）は従来どおり載っており、AI が必要に応じて使う。
- 新しい設定「自動レビュー」（既定オン）: session の指示 `auto-review.md` を足し、コードを変えたら完了を報告する前に子エージェント reviewer が差分を読み、重大・要修正は直して再確認（2 回まで）。同じ失敗が 2 回続く・指摘が消えない・方針を決めきれないときは advisor()（Auto Advisor）で相談先のモデルに相談する。大きな変更では Adversarial Review の adversarial-reviewer も読む。
- 「レビューに使うモデル」が組み込みの reviewer にも効くようにした。レビュー・相談・開発担当には、OpenCode からの外部送信を許可した外部モデル（OpenRouter など）も選べる（Fable の作業を引き継ぎ）。開発担当の子エージェント developer を任意で足せる。
- 検証: 関連テスト、frontend build、e2e。

## 2026-11-16 UI の共通部品の刷新（見出しの操作を＋ボタンへ）

- ページ見出しの操作を丸いアイコンボタン（`IconButton`、見た目40px・タップ領域44px）へ統一。Models の「＋ モデル登録」は ＋ だけのボタンになり、隣に共通設定（歯車）。Apps・Workflows・Terminal・Remote・Knowledge・GitHub・OpenCode も同じ形（主操作は色付きの＋、補助は淡色）。読み上げ名とツールチップは従来の文言のまま（「モデル登録」「新規セッション」など）。フロンティアのタブで＋を出さない動作も維持。
- `PageHeader` は見出しと操作を必ず1行に収め、説明は行の下へ全幅で出す（Knowledge の説明が操作に押されて細くなる、GitHub のログイン表示が見出しに重なる問題を解消）。
- Models の一覧: 行を詰め、ロード中の行は緑の枠と輪つきの点で示す。ロード/アンロードは携帯では記号だけ・広い画面では記号＋文字（Apps の起動・停止と同じ形）。タブは共通の `SegmentedTabs` にし、幅を文字数に比例させて 390px で「フロンティア（外部）」が切れないようにした（380px 未満は「フロンティア」と表示、読み上げ名は同じ）。
- 共通: シート・ドロワーの閉じるボタンを44pxへ、確認ダイアログのボタンを44px・携帯では横いっぱいに、メニューの項目を44pxへ。キーボード操作時のフォーカスリングを全ボタン・リンクへ。補足文の色を一段寄せて読みやすくした（暗い背景の zinc-500 と明るい背景の zinc-400。色の変数の差し替えで、各画面のクラスは変えていない）。下部ナビの More は他の項目とラベルの行がそろう高さにした。
- 全画面の統一: 空の状態を共通の `EmptyState`（破線の枠 + 1行の案内 + 最初の1件を足す操作）へ（Apps・Workflows・Terminal・Remote・Knowledge・GitHub・Models）。画面内の切替を `SegmentedTabs` へ（Models、モデル取得、Knowledge の詳細・取り込み、OpenCode のプロジェクト選択）。一覧の行は枠・影・名前の太さ・補足文の色をそろえた（Apps・Workflows・Knowledge・GitHub・Remote・Models）。
- 変えていないもの: 経路・API・Backend・文言の意味・読み上げ名。Terminal の行の絵文字ボタン（E2E が形を固定している）、モデル登録シートの中身、各画面個別のフォームやシート内の配置、App Studio / Project Lab の独自の見出し。
- E2E: 各 spec の login helper が「顔認証・パスキーでログイン」にも当たって 81 件がログインで落ちていたため、ボタンの指定を完全一致にした（UI の変更とは無関係の既存不具合）。
- 検証: `tsc`・Frontend build。E2E は 5 spec 以下の組に分けて実行し、main（+ 上のログイン修正）と失敗の集合を比較 — この環境では main でも 55 件が落ちる（Add-on・App Studio・Terminal の実 tmux・Workflow の一部など）が、失敗の集合は一致し、刷新で新しく落ちたものは 0 件。a11y は 12 件すべて成功。320px/390px（ダーク）と 1280px（ライト/ダーク）の前後の画面写真で横スクロールなしを確認。

## 2026-11-16 Maya のモデルを複数登録（手元のファイルから）

- Models → モデル登録に「Maya」を追加。Maya の量子化（GLM-5.3-Flash / DeepSeek-V4.1-Flash / MiniMax-M3.1-Flash の各段階）を、選んだフォルダにファイルがそろっていれば登録できる。足りないときは不足ファイルと `hf download` のコマンドを出すだけで、ダウンロードはしない（セットアップは通信先を閉じたプロキシに向けて実行し、何か取得しようとすれば失敗する）。一覧と照合は Maya 自身の一覧（`tools/maya_quants.py`・`glm_downloads`・`tools/ds4|mm3/download_gguf.json`）を `backend/app/models_mgmt/maya_probe.py` が Maya の venv で読む。
- 登録したモデルは別の alias（`maya-<系列>-<段階>`、例 `maya-deepseek-xs`）・ポート（8100〜8139）・systemd unit・設定ファイルを持ち、一覧に別の行で出る。Maya のモデルは同時に 1 つだけ（起動すると他の Maya モデルを先に止める）。最初のモデル（alias `maya-s`・8099・設定・`MAYA_GLM_PCIE_SHARE` などの調整）は変えない。セットアップが他のモデルの設定ファイルを書き換えたら元に戻して登録を失敗にする。削除は登録（設定）だけで、モデルのファイルは残す。
- 表示名: ランタイムは「Maya」、モデルは「<モデル> · <段階>」（例 DeepSeek-V4.1-Flash · Maya-XS）。内部の識別子（runtime `maya-s`、経路、設定ファイル）は互換のため据え置き。
- 検証: backend 全体 1553 件、frontend build、e2e `maya-register.spec.ts`（320px・1280px、モック）。実ファイルへの照合は `/data1tb/LLM/GLM-5.3-Flash-Maya`（Maya-S24 の 3 分割がそろう）で読み取りのみ確認。実際の登録（エンジンのセットアップ）と DeepSeek の gfx1201 での動作は未確認（DeepSeek は約 282GB の取得と、Maya の venv への `tokenizers` 導入が先に要る）。

## 2026-11-16 Strataの並列設定とメモリ目安・起動内訳

- 個別設定に並列数（1〜8）、会話ごとのコンテキスト（4〜1024K）、KV量子化、Qwen系のKV VRAM保持長、起動時RAM余裕を表示。変更は次回ロードから適用し、稼働中の起動条件変更は既存の停止必須ガードを維持。旧クライアントの項目省略は保存値を維持し、空欄で自動へ戻せる。設定保存・概算表示で起動や重みダウンロードはしない。
- 実際の分割GGUF合計（ディスク）と、同モデルに対応するStrata packのnative experts容量（RAM常駐候補）を分離。Flash-NextのKV係数・量子化・セッション数・現在のRAM/最大VRAMカードの空きから、KV RAM/VRAM、RAM常駐時のコンテキストと並列数の概算を表示。並列時の主セッションを加算し、KVが全てVRAMに載る明示設定ではホストストリーミング領域を加算しない。自動保持長は幅で表示、未対応系列・packなしは不明。固定余裕は明示した概算で起動保証ではない。
- 起動中は管理されたStrataプロセスツリーのRAM RSS/DRM VRAMと、最新起動ログの用途別確保量を分けて表示。GGUF/エキスパート・KV・VLM・MTP/補助モデル・n-gram/履歴・Dense/作業領域をRAM/VRAM別に表示し、個別計測不可は不明。RSSの共有領域、プロセスとログの重複は合算不可。停止・再起動前の古いログを現在値として表示しない。
- ローカル検証: 関連テスト、Frontend build、隔離E2Eの320px/1280pxで6並列・256K・KV保持32K・RAM余裕8GiBの編集・保存と概算/起動中表示を確認。最終件数・CI・マージ/ローカル反映はPRで追跡。実機のUD-IQ4_XS packからエキスパート55.43GiBを取得し、6並列256K/int8/保持32Kの目安を計算できることを確認。
- 1〜6並列の統一条件でのプリフィル・生成速度評価は、設定機能をマージ・ローカル反映後に実施。長文で256K/512Kを埋める精度・最大長受け入れは未検証。復旧は追加項目を自動に戻す、または前Releaseへ戻す（設定・GGUF保持）。

## 2026-11-16 起動モデルの選択をランタイムカードへ配置

- Models → 共通設定 → 既定のランタイムで、各ランタイムの選択カード内に「起動するモデル」のドロップダウンを配置。複数モデルが登録されているランタイムで表示し、初期値は「自動」。選択したモデルはランタイムごとに保存する。
- ランタイム選択ボタンとモデル選択は同じカード内の別操作とし、selectをbuttonに入れ子にしない。未選択ランタイムのモデルを調整しただけで、そのランタイムへ切り替わらない。保存済みの選択・未準備候補の案内・「自動」への復帰を維持。
- 保存と起動先の解決には既存の設定を使い、Backendの起動処理は変更しない。未起動時の利用で選択モデルを起動する。
- 検証: 320px/1280pxで、各ランタイムカード内のドロップダウン・初期「自動」・モデル変更・保存・再表示と、単一モデルでは選択欄なしを確認（2件成功）。Frontend build成功、Backend全体1544成功・2skip（105.82秒）。最終CI・本番反映結果はPRで追跡する。

## 2026-11-16 ランタイムごとの優先起動モデル

- Models → 共通設定 → 起動と停止に、登録チャットモデルが複数あるランタイムごとの「優先起動モデル」を追加。Strata・llama.cpp・Radiance・Maya-S・Ollamaの設定を個別に保持し、「自動」で従来順へ戻せる。Strataの未初回起動モデルは自動選択対象にしない。
- モデル名を指定しないコールド起動と、Add-onのAI転送先が同じ優先設定に従う。稼働中のモデル、モデル名の明示指定、visionなど能力条件は優先する。削除・未準備のモデルが保存されていても、既存の適格候補へ戻す。
- 自動起動が有効なモデル同士の選択にも使用する。llama.cppでは共有エンドポイントのwinnerを設定し直し、排他運用では他エンドポイントのチャットモデルとの同時自動起動を避ける（embedding/rerankerや共存設定は維持）。優先設定だけを保存してもGPU電力設定の適用・モデル停止/起動・重みのダウンロードは行わず、自動起動OFFのモデルを勝手にONにしない。
- APIでランタイムに所属する準備済みの登録モデルか検証し、優先設定の部分更新は他ランタイムの選択を保持する。監査ログへ選択を記録。
- 検証: 関連148テスト成功、Frontend build成功。独立インスタンスのブラウザで320px/1280px、Strata/llama.cppの優先モデル変更・保存・再表示、単一モデルのランタイムでは選択欄なし、未準備候補の無効表示、横スクロールなしを確認。Backend全体1544成功・2skip（107.34秒）。最終CI・本番反映はPRで追跡する。
- 運用: 優先選択だけでは現在のモデルを入れ替えない。既存の自動起動ON/OFFは各モデルの設定で変更する。復旧は各ランタイムを「自動」へ戻して保存するか、直前Releaseへ戻す。実際のOS再起動による起動順・全ランタイムの重みロードは未検証。

## 2026-11-16 OpenRouter の費用・入力の扱い・残高

- Models → フロンティアモデルの OpenRouter で、モデルごとに費用（無料 / `$`〜`$$$$` と入出力の単価 USD/1M）と入力の扱い（保存しない経路あり〔ZDR〕・保存されることがある・学習・保存されうる〔無料〕）をアイコンと文字で表示。モデル選択は名前・安い順・高い順・新しい順・コンテキスト長で並べ替え、無料だけ・ZDR だけで絞り込み。
- データの扱い（ZDR だけ / 学習に使う提供元へ送らない〔既定〕/ 制限しない）を設定し、ゲートウェイが要求ごとに OpenRouter の `provider`（`data_collection`・`zdr`）を付けて強制する（呼び出し元の指定より優先）。緩める方向だけ確認を出す。
- 残高: OpenRouter の `/key` から残り・今月/今日/累計の使用額・無料モデルの残り回数。ControlDeck 側の月の上限とは別に効く。
- 検証: 全テスト 1536 件、frontend build、e2e（320px、モック）。実際の OpenRouter の値はキー設定後に確認する。

## 2026-11-16 ファンの上限温度・可変マージンへの追従

- 新しい「温度追従」モード: センサー設定値を上限とし、通常は `上限 − 温度マージン` にFF＋PIで追従する。温度マージンは0〜20°C（画面では0.5°C刻み、既定5°C）。既存のグラフ・PID・従来マップの選択は保持。
- 通常の加速は5〜120ポイント/分（既定30）、減速は既存設定で制限。生の実測値がいずれかの上限へ到達した場合は直ちに最大。上限未満でGPUが独自に急加速しないよう、hotspot上限まで平坦、上限+1°Cで最大となる5点カーブを使う（機器の温度範囲へ保守的に制限）。既存のセンサー不足・ファン停止検出・GPU自身の過熱保護は維持。新モードは自動の階段状チューニングを開始しない。
- 温度マージン・加速速度をAPI検証・永続化・監査ログへ追加。画面は上限と実際の追従温度を表示し、履歴にも両方を記録。設定保存時はPI状態を初期化し、同じGPUなら直前の指令から加減速を継続。
- 検証: 関連テスト（上限到達の即時最大、各センサー、マージン検証・保存、通常加速制限、機器範囲内のカーブ、合成熱負荷での5°C/10°Cマージン追従）、Frontend build、独立インスタンスで320px/1280pxのマージン7.5°C変更・送信を確認。合成200W熱負荷・上限90°C/マージン5°Cで最終84.95°C、最後の5分84.94〜85.07°C。これはシミュレーションで、実GPUの連続高負荷での保証ではない。全件テスト・CI・実機反映結果はPRで追跡。
- 適用は本体更新後に「温度追従」を選んで保存。今回の依頼対象GPUには、既存の上限値・最低回転・モデルを維持してマージン5°Cから適用予定。復旧は旧方式を選んで保存するか、直前Releaseへ戻して以前の設定を復元する。

## 2026-11-16 OpenCode の Tab / Plan 切替の復旧

- 原因: session の `cli.json` は共有設定への絶対 symlink だが、プロジェクトの箱へリンク先を渡しておらず、OpenCode はキー設定を読めなかった。稼働中の箱でリンク先が存在しないことを確認。
- 修正: `_launch` で共有 `cli.json` 1枚だけを読み取り専用マウント。integration ディレクトリ全体は公開せず、利用者の既存キー設定と session 側の設定を保持する。
- 実機: 稼働環境と同じマウント・環境変数の独立 Podman + OpenCode 2.3.0 で、設定読み取りと Tab による Build → Plan、Shift+Tab による Plan → Build を確認（128列）。実行中の利用者セッションは停止していない。関連45テスト成功、Frontend build 成功。Backend全体1525成功・2skip（133.36秒）。最終CI・反映結果はPRで追跡。
- 適用後、新しく開くターミナルから有効。既存の箱にはマウントを追加できないため、OpenCode ターミナルを閉じて開き直す必要がある。復旧は直前の Release へ戻して同様に開き直す。ブラウザの物理キーボード・モバイル補助キーからの操作は未検証。

## 2026-11-15 OpenCode v2 のオプションプラグイン読み込み修正（#395、L2承認前）

- 原因: Podman の管理ツール一覧に Superpowers / Adversarial Review / Auto Advisor がなく、ホストの npm 導入先を箱内から読めなかった。箱内に npm・node は存在する。
- 修正: ３件の feature ディレクトリを依存ごと同じ絶対パスへ読み取り専用マウント。features 全体や他の拡張は公開しない。
- 二次原因: Adversarial Review 2.0.2 は XDG_CONFIG_HOME ではなく OPENCODE_CONFIG_DIR をコマンド配置に使う。セッション専用 opencode ディレクトリを明示し、HOME 内の親ディレクトリ不在による初期化失敗と設定先不一致を防ぐ。
- 実機検証: 独立した Podman + OpenCode v2 に本番と同じ file:// 指定を渡し、/api/plugin で３件 active、/api/command で adversarial-review と constructive-review を確認。プラグイン読み取り可・書き込み不可、本番 DB 不可視を確認。関連37テスト成功、Frontend build 成功。ミラーの起動・health・認証後の OpenCode 画面を1280px/320pxで確認。独立レビュー（プラグイン修正・時計隔離修正）の重大・要修正指摘0件。
- 最初の最終CIで既存のworkflow復旧2テストが失敗。計測テストが標準 time.monotonic をプロセス全体で差し替え、別threadのasyncio復旧timerを遠未来にずらす干渉を特定。Python 3.11.18・稼働時間100秒を模した環境で修正前は同じ2件失敗、修正後は83件成功。6テストファイルのclock/sleepをモジュール内のtime参照だけへ限定。llama._throughput の重複importを除去し既存の先頭importを使う（通常動作は同じ）。workflow本体と復旧テストの期待値は変更なし。関連159件成功、短い稼働時間を模したPython 3.11全件1520成功・2skip（111.20秒）。
- 後続CIの Add-on discovery テストも、fixture の定期healthy応答が手動degraded観測を上書きする競合をローカルで再現。対象テストの recheck だけ観測保持へ差替え、注入前に既存呼出しをdrainし、注入後もpollを明示実行して競合を検証する。既存の利用不可フィルターの期待値は維持。関連Addonテスト成功、Python 3.12全件1520成功・2skip（108.63秒）、Python 3.11短uptime全件1520成功・2skip（114.88秒）。
- 既存セッションのマウントは変わらないため、適用後は OpenCode のターミナルを閉じて開き直す。復旧は本修正の直前の正式 Release へ戻して同様に開き直す。最終CI・L2承認・マージ・正式Release・本番適用はPRで追跡し、未達を完了扱いしない。

## 2026-11-15 OpenCode をプロジェクトの箱で動かす（#388）と本番の Release 追従化

- OpenCode（TUI・チャットの code モード・headless 実行）を既定で podman の箱で起こす（`provider._launch`、`sandbox.container_argv` を共有）。書けるのはプロジェクト・session の設定・UI 検証の写真・MCP の長い結果・OpenCode 自身の履歴だけ。本番の data・秘密鍵・他のプロジェクト・~/.ssh・ControlDeck のコードは見えない（実機で確認）。ast-grep・OpenCode・Chrome・git・LLM ゲートウェイ・Add-on の MCP bridge は使える。設定 `isolation: host` か podman が無ければ従来どおり。
- 本番 `app-strata` を main（正式 Release 追従）へ切り替え、ControlDeck_v0.13 を本物の更新機能で適用（約 30 秒、バックアップ・build・再起動・画面/meta/ゲートウェイの確認）。旧ブランチは `backup/deploy-strata-20261115`。

## 2026-11-15 本体の自動適用（L1、#387）

- `app/maintenance/auto_update.py`: 正式 Release があり、使われていない状態（LLM の推論・GPU の貸出・Job・OpenCode のセッション・Add-on の lease が無い）が設定の時間（既定 10 分）続いたら、管理者の操作と同じ `self_update.start` を始める。開始と結果（成功・巻き戻し）を通知先へ送る。検証用インスタンスでは動かない。
- `self_update`: 適用後の確認を health だけから、画面の配信・meta・LLM ゲートウェイまでに広げた（駄目なら従来どおり自動で前の版へ戻す）。自動の印と Release 名を途中の状態でも保つ。
- 設定 → システム「ControlDeck 本体の更新」: 自動適用のオン・オフ、待つ時間、いま待っている理由。API `GET/PUT /api/v1/system/control-deck-update/auto`。

## 2026-11-15 自律開発の方針と変更の区分（#386）

- `docs/POLICY-autonomous-development.md`: 役割（利用者・統括・実装・機械）、区分 L0（自動）/L1（自動＋事後報告、関門を通れば Release と本番の自動適用・自動の巻き戻し）/L2（利用者承認）、関門、適用の条件。
- `config/change-policy/{controldeck,patentbuddy,codedev}.json` と `./deck.sh change-class`（`app/maintenance/change_policy.py`）。場所と変更内容で判定、内容の規則は強制する backend だけ。今日マージした PR に当てて妥当性を確認（外部送信・権限・隔離に触れたものは L2、画面だけは L1）。
- AGENTS.md の「本体アップデーターは管理者の明示操作のみ」を、利用者の承認を得て区分による運用へ改訂。

## 2026-11-15 開発用の軽量サンドボックス（`./deck.sh sandbox`）

- podman の空のルートにホストの /usr・/etc・/opt と道具を読み取り専用でつなぐ。イメージ不要・起動 0.07 秒。書けるのは箱のフォルダだけ、依存は overlay。
- 作業ツリーは `git clone --shared`（数百 ms）。箱の中で frontend の build（18.8 秒）と backend のテストが通り、ホストは変わらない。
- 箱の中のミラー（`mirror-start`、2.8 秒で起動）と箱の中の OpenCode（`opencode --run`）。箱の中の AI に確かめさせ、本番の DB と gh の token が見えないことを確認。
- OpenCode から本番を止める deck.sh のコマンドを禁止。

## 2026-11-15 子エージェントが MCP を呼べなかった不具合（#384）

- v2 の agent の `system` は既定の指示（Code Mode の呼び方を含む）を置き換えるので、ui-verifier / reviewer は Playwright MCP や control_deck.report を見つけられなかった。子の指示の先頭に Code Mode の呼び方を足した。
- 実機: 修正後、ui-verifier が Playwright MCP で 17 枚を撮影し、仕込んだ不具合を検出。

## 2026-11-15 Context7・レビュー係・GitHub 連携（第 2・3 段階）

- オプション機能 `context7-mcp`（既定オフ。外部へ問い合わせる旨を画面に明記）と `ast-grep`（構文検索、session の PATH へ）。
- 子エージェント `reviewer`（ファイルを変えない）。「レビュー」の作業はこれに任せ、`control_deck.report` で指摘を返す。
- OpenCode の設定「GitHub 連携（この PC の gh を使う）」: オンのときだけ `GH_CONFIG_DIR` を渡す。token の表示や破壊的な gh の操作は常に禁止。
- 実機: Context7 でライブラリ ID 取得、reviewer が呼び出し側の取りこぼしを critical で検出（ファイル変更なし）、session の環境で gh がログイン済みとして動くこと。

## 2026-11-15 OpenCode から Deep Research（第 2 段階、精密調査）

- ControlDeck の MCP に `control_deck.research`（AI チャットの Deep Research をそのまま使う。新しい調査エージェントは作らない）。結果は「結果」に `kind: research` で残り、出典・取得日時・一次資料・確かめられなかったことを持つ。
- 既定はローカル（プロジェクトのコードと RAG）だけで、検索語は外へ出ない。OpenCode の設定「精密調査で外部の検索を使う」をオンにしたときだけ Web・GitHub・論文。オンにすると注意を出す（未公開の発明・請求項）。
- `project_dir` は CodeDEV 配下のディレクトリだけ受ける。
- 「精密調査」の作業の種類の指示は、まずこの道具で根拠を集めるよう変えた。
- 実機: CodeDEV/Hanabi で「描画ループと入力処理」を depth quick・ローカルのみで調査。113 秒、出典 5 件（main.js / GameScene.js ほか）、web は許可が無いので落として記録。

## 2026-11-15 OpenCode の作業の種類・結果の画面と、オプション機能の初期設定（第 1 段階 C）

- OpenCode 画面（#379、Fable）: セッション開始に「作業の種類」（ふつう / UI 検証 / 精密調査 / レビュー）。UI 検証は確かめる URL を受け取り、道具が未導入・Chrome 無し・設定オフなら開始前に案内する。「結果」の一覧と詳細（幅ごとの画面写真と拡大、重大度順の指摘、コンソールのエラー、出典の支持・反証・一次資料、確かめられなかったこと、削除）。
- 設定 → 機能と拡張（#381）: Playwright MCP と OpenCode プラグインの「詳細と操作」に初期設定（OpenCode で使う、外部通信、接続診断）。未設定なら一覧に出して開いておく。導入・更新・アンインストールは従来どおりこの画面。
- 確認: `./deck.sh e2e` で opencode-reports（一覧 → 詳細 → 拡大 → 反証）と、320/1280 × ライト/ダークの撮影。横スクロール無し。

## 2026-11-15 E2E を隔離した検証用インスタンスで（第 1 段階 D）

- `./deck.sh e2e [spec]`: 一時 data・別ポート（18765）・使い捨て管理者で ControlDeck を起こし、Playwright を流して片付ける。これまでの E2E は既定で本番 8765 を向いていた。
- `CONTROL_DECK_ISOLATED=1` のとき、機械全体に効く処理（ファン制御・通知・保守・Ollama/llama/大きなランタイムの idle unload と自動起動・SearXNG の停止・オプション機能の復旧）を起動しない。
- `app.cli create-admin <name> --password-stdin`。
- `@axe-core/playwright` 4.14.0 と `e2e/a11y.spec.ts`（WCAG 2.1 A/AA、critical で失敗、横スクロール検査）。HTML レポートを `playwright-report/` に残す。
- 実行結果: a11y 12/12 合格。ログイン helper の不具合（パスキーのボタンと重なる）を修正。OpenCode 未導入の環境では opencode-focus を skip。

## 2026-11-15 OpenCode の UI 検証（第 1 段階 B、計画: PLAN-opencode-dev-environment.md）

- オプション機能に種類 `opencode-mcp` と `playwright-mcp`（@playwright/mcp 0.0.84、版固定、ブラウザは取得せずこの PC の Chrome）。
- session に Playwright MCP を足し、道具は子エージェント `ui-verifier` だけへ（主からは deny）。headless・使い捨てプロファイル・ControlDeck 本体（8765）を遮る・既定は loopback だけ。`browser_run_code_unsafe` は deny。
- ControlDeck の MCP に `control_deck.report`（UI 検証・精密調査・レビューの結果を構造化して受け取り、画面写真を取り込む）と結果の API。
- セッション開始の「作業の種類」（ui_verify / research / review）で最初の指示に手順を付ける。
- 同梱スキル `controldeck-addon-development`。接続診断 API。
- 実機: 導入・診断（道具 25 個、about:blank を 164ms で開く）と、v2 が子エージェントの指示と権限を読むこと（`debug agents`）を確認。

## 2026-11-15 Add-on への GPU の貸し出し（Models「Add-on への GPU の譲り方」）

- 不具合: LLM の resource provider は全ランタイムの VRAM を申告するのに、退くときは llama.cpp しか降ろさなかった。Strata / Radiance / Maya-S が載っていると「0 バイト空けた」と答え、MediaForge / SonicForge は待つか RAM へ流れた（画像 cpu_offload 235 秒、GPU なら約 3 秒）。
- `large_model_lifecycle.release_for_addon()` で大きなランタイムも降ろす（常駐指定・接続中は断る）。別の LLM の要求では降ろさない（それは prepare_switch の役目）。
- 方針 `RuntimePolicy.addon_gpu`: `auto`（既定。LLM が推論していなければ貸す、推論中なら待たずに RAM）／`swap`（推論の終わりを待って必ず貸す。drain 中の新しい要求は取り消しの合図にせず待たせる）／`llm_first`（降ろさない）。明示の `/addon-runtime/.../ai/release` も方針に従う。
- broker は RAM や小さい枠で受ける前に、一度だけ GPU を借りられないか試す（`_try_lending_first`、provider の `would_step_aside`）。
- 貸している間は LLM 要求と `local_llm.ensure_ready` を待たせる（LLM の受け入れは空きに合わせて縮むので、待たせないと生成中の隣へ載って両方落ちる）。`lending_loop` が Add-on の lease と待ち要求が消えたのを見て返し、`restore_delay_sec` 後に broker を通して載せ直す。LLM 要求が待っていれば即座に返す。`max_lend_sec` で打ち切る。
- 画面: Models の設定に方針・載せ直し・間隔・上限、載せ直しの実測時間、貸出中の表示。

## 2026-11-15 ControlDeck_v0.12 リリース

- 対象: `origin/main`（PR #347〜#373 と本記録）。前回 `ControlDeck_v0.11` から 304 commit。
- 主な内容: パスキーログイン（#347）、Add-on のローカル限定推論とリソース確認（#349〜#354）、Strata（#355）・Maya-S（#359）・Radiance（#367）の任意導入と寿命管理（#364）、AMD GPU ファン制御（#360・#363）、Settings/Models の整理（#365・#368）、OpenCode v2 への一本化（#366）、Maya-S の画像入力（#369）、会話の自動圧縮（#370）、同時実行数の整理（#371）、Radiance の長いコンテキスト（#372）、ターミナルの長い履歴（#373）。
- 移行: 起動時に DB を移行（パスキー用テーブル `a19b48d207f1`）。依存 `webauthn>=2.8,<3` を追加（`./deck.sh update-prepare` / 本体更新で導入）。OpenCode v1 と OMo は撤去済み（`/opencode` は `/opencode-v2` へ移動）。v2 のプラグインは 設定 → オプション機能 で導入・更新・削除する。
- 検証: main（#373 マージ後）で backend **1426 passed / 2 skipped**、frontend production build 成功。全体実行で `test_addon_execution` の1件が一度だけ落ちたが、単独・再実行で通過（タイミング依存）。GitHub Actions は無く、ローカル検証。稼働中のデプロイ枝とコードが一致することを確認（差分なし）。
- 未検証: Models 画面の新しい設定項目と AI アシスタントの画像添付のブラウザ操作、物理スマートフォン。

## 2026-10-13 ターミナルの長い履歴を実 tmux から参照

通常 Terminal の wheel/touch/右端バーを tmux の保持履歴へ接続。大量出力で xterm の
ローカル履歴が数行にしかならない問題を修正。長距離は goto-line で直接移動する。
実サービス適用、PC1280/320px で 12,000 行前・入力復帰・再読込後の履歴を確認。
mouse 対応/非対応 TUI の回帰も通過。backend 全1175pass/2skip、frontend build成功。
ホストの Codex alternate_screen を never に変更（次回起動から、既存プロセスは保持）。
既存履歴の復元、物理電話、V2 Lab は未検証/対象外。[詳細](terminal-scrollback-20261013.md)。



## 2026-11-10 Add-on local-only AI契約（PR #349）

complete/streamに厳密boolean local_onlyを追加。Host管理llama.cpp・存在するGGUF・loopback宛先を要求し、GPU admission前後で検証。Ollama/未検証runtimeは拒否し、proxy環境変数/redirectを使わない。従来要求は既定falseで維持。capabilitiesは契約対応と利用可能性のみ返しprovider/model/pathを公開しない。

関連52 tests成功。全体初回は隔離worktreeに.venv入口が無くterminal automationの2 testsが失敗。依存hash一致を確認して既存venvへ参照を設定し、./deck.sh testは1225 passed/2 skipped（105.70秒）、frontend build成功（18.29秒）。稼働Hostの既存作業を保持し、base一致確認後に製品3ファイルだけを反映して通常user serviceを再起動。実APIで一時アドオンのservice tokenを認証しlocal_only=trueの合成請求項説明が成功（1.3秒/68文字、execution_policy=local_only）。テストアドオンを解除/削除。テストuser削除はaudit/session FKにより一度拒否されたため、停止→本人のsessionのみ削除・auditのuser FKのみNULL化（6監査レコード保持）→テストuser削除で後始末済み。既存userは変更なし。/tmp/pb-host-local-only-live.jsonl。UI変更なし、PatentBuddyのAIボタン/Jobs画面受入は次の統合で実施する。

## 2026-11-10 パスワード入力のみ／顔認証パスキーのログイン

前回ログインに成功したユーザー名だけをブラウザで記憶し、次回はパスワード入力から開始。
「別のユーザーでログイン」で切替可能。初回は従来どおりユーザー名とパスワードを入力する。
設定の「顔認証・パスキー」から登録・一覧・削除でき、ログイン画面にパスキー認証を追加。
WebAuthnのuser verificationを必須とし、Face ID／指紋／端末PIN等を使用する。顔画像は取得しない。
登録にはsessionと現在のpassword、有効な場合はTOTPを要求。既存の二要素認証ポリシーは保持。
公開鍵と5分の単回challengeをDB保存し、Origin/RP ID・署名・user handle・counter・sessionを検証。
schema revision `a19b48d207f1`、依存 `webauthn>=2.8,<3`。

稼働checkoutのbackend全体 **1209 passed / 2 skipped / 1 warning（121.18秒）**、最終パスキー回帰14件成功。
frontend本番build成功。Chrome仮想認証器による登録→logout→パスキーログイン→削除→
旧鍵拒否→ユーザー名再入力なしのpasswordログインを1280px/320pxで検証。
隔離preview **2 passed / 3.0秒**、本番localhost **2 passed / 3.5秒**。
既存Tailscale ServeのHTTPSでも **2 passed / 3.5秒**。
証跡: `/data1tb/ControlDeck/data/maintenance/passkey-login-20261110/`。
pageerror・横overflowなし。実端末のFace ID／指紋センサーはNOT TESTED。
本番Hostは通常 `./deck.sh` で再起動して適用済み、health 200。検証account/session/keyは削除済み。

PR #347は`origin/main`からログイン変更だけを分離。PR treeのbackendも
**1209 passed / 2 skipped（107.48秒）**、frontend build成功（21.16秒）。
初回は空のworktreeでbuildとpytestを並行開始し、SPA配信未登録のため既存Project Labの
404/405期待値が1件失敗。build後の単独再検証と全体再実行で通過。コード変更は不要だった。
GitHub Actions workflowは未設定。物理端末の生体認証は上記どおり未検証。

## 2026-10-19 自動復旧修正のリリース候補検証

`origin/main`の`749a971`から今回の修正を分離し、PR #346を作成。
実稼働側の既存terminal修正は取り込まず、今回の製品変更4ファイルと新規回帰テストは実稼働側とbyte一致。
候補で`./deck.sh test` **1195 passed / 2 skipped / 1 warning / 109.39秒**、
frontend production build成功（Vite 18.44秒、chunk size警告あり）。
GitHub Actions workflowは0件のため、CI成功ではなくローカル検証として記録する。
ブラウザ4ケースと停止からのHost起動復旧は下記の実稼働環境で確認済み。
候補専用の別Hostでのブラウザ試験は未実施。PC自体の再起動・新規生成の未検証境界も継続。
配布は既存規則に合わせて`ControlDeck_v0.11`のソースアーカイブ。
証跡: `/data1tb/ControlDeck/data/maintenance/feature-boot-restore-20261018/`
内の`controldeck-boot-release-tests.log` / `controldeck-boot-release-build.log`。

## 2026-10-18 Media Forge / SonicForge の再起動後サービス復旧

導入済みbundleとデータは残っていたが、両systemd unitが停止し、Hostにも起動復旧処理がなかった。
Host起動時に有効な導入済みサービスをworker threadで起動・health確認するよう修正。
無効化設定・権限・保存データを保持し、稼働中サービスは再起動しない。
設定画面にも「起動して復旧」を追加し、停止を再導入必須として扱う問題を修正。

実Hostを通常`./deck.sh`で再起動し、停止していた両サービスの自動復旧を確認。
Media Forge 0.33.24 / SonicForge 0.6.37のままhealthy、画像・音声・ジョブ・音声設定件数は不変。
backend **1195 passed / 2 skipped**、frontend build成功。
実Chromeの1280px/320pxで設定・Library画像・保存済み音声の再生を確認、E2E **4 passed / 19.1秒**。
PC自体の再起動・新規生成はNOT TESTED。SonicForgeのLibrary直接URLの404は別途残存。
[原因・変更・受入記録](feature-boot-restore-20261018.md)。

## 2026-10-18 ログイン保持のリリース候補検証

`origin/main`の`07d9a00`から`fix/persistent-login`を作成し、ログイン保持の変更を分離。
実稼働側の`7f63417`は既存のローカルterminal修正`dd0beed`を親に持つが、リリース候補には
terminalの製品変更を含めていない。認証の4ファイルは実稼働側とbyte一致。
既存Add-on cookie寿命テストのfake upstreamだけはMockTransportへ置き換え、実ネットワーク依存を解消。

候補そのものを検証し、`./deck.sh test` **1184 passed / 2 skipped / 1 warning / 107.57秒**、
`npm --prefix frontend run build`成功（Vite 18.83秒、chunk size警告あり）。
隔離DB・設定・systemd user unit・port18768で実起動し、候補のproduction buildを配信。
Google Chrome1280px/320pxで通常ログイン、365日Cookie、保存状態のcontext復元、再読込、
認証API 200、横overflowなしを確認。E2E **2 passed / 1.4秒**。
検証unitは停止し、一時資格情報は無効化・削除。実サービスはPID160578/active/NRestarts0を維持。
このリポジトリにはGitHub Actions workflowがないため、上記はローカル検証でありCI結果ではない。
リリースは既存の`ControlDeck_v0.x`命名とソースアーカイブ配布に合わせて`ControlDeck_v0.10`を用いる。
ログ: `/tmp/controldeck-release-tests.log`、`/tmp/controldeck-release-build.log`、
`/tmp/controldeck-release-browser.log`。実時間の長期放置・物理スマートフォンはNOT TESTED。


## 2026-10-18 ログインの期限切れを無効化

`security.session_timeout_minutes` の既定を480分から0（期限なし）へ変更。
有効な既存セッションは次の認証時に無期限へ移行し、HTTP応答でCookieを365日保存へ更新する。
既に期限切れ・取消済みのセッションは復活させず、ログアウト・パスワード変更・
端末セッション取消・ユーザー無効化による失効を維持する。正の値による未使用時間制限も継続対応。
ブラウザのCookie削除・保持制限・365日以上の未アクセスでは再ログインが必要。

検証: `./deck.sh test` **1184 passed / 2 skipped / 1 warning / 106.75秒**。
90日未使用は時計を進めたテストで確認。旧セッション移行・有期限設定への復帰・失効操作も通過。
Host Job/leaseが0の時点で通常`./deck.sh`を実行し、実サービスPID2277→160578、health 200。
既存設定ファイルはtimeout未指定のため新既定0が適用される。実HTTPで再起動前に発行した
8時間Cookieのセッションを再ログインなしで引き継ぎ、365日CookieとDBの無期限化を確認。
Google Chromeの1280px/320pxで通常ログイン・Cookie属性・保存状態を別contextへ復元・再読込を確認、
E2E **2 passed / 1.9秒**、横overflowなし。実logout後の旧token再利用は401。
検証用account/session/資格情報は削除。長期間の実時間経過と物理スマートフォンはNOT TESTED。

反映時、旧プロセスはgraceful shutdownの30秒超過とshutdown中のstallを記録した後に停止し、
新プロセスが起動。既存tmux端末の再接続を確認。検証終了後の一時session削除と処理中requestが
競合して`StaleDataError`が1件発生したため、無エラーの受入とはしていない。最終healthは200。
ログ: `/tmp/controldeck-session-tests.log`、`/tmp/controldeck-session-deploy.log`、
`/tmp/controldeck-session-browser.log`。仕様は[security model](security-model.md)。

## 2026-09-23 OpenCode endpoint監視修正を通常導入

実Hostのstall stackが定期監視→feature status→同期version実行で停止し、30秒watchdogで再起動。
Gatewayの不要な照会を省き、直結時の設定/feature/session/instance照会をthreadへ移す。
同じidle監視の同期I/Oも分離。関連38tests通過、隔離実HTTPで4秒待つversion照会2回中も
81health応答/8.072229秒、最大1.253ms。実モデル起動はstub。
初回全体1173pass/2fail/2skip。worktreeのdist参照を補った後、1174pass/1fail/2skip。
既存Job testのloop終了が未完のslot解放を取消していたためhelperで終端taskをawait。
assertion/製品Jobは変更せず、対象23testsを通過。最終全1175pass/2skip/1warning/105.71秒、exit0。
PR343/e3d374aをroot mainへ通常deck.shで適用、PID604536→674922/health ok。
実OpenCode unit/HostとAdd-onのactive Job/leaseが0の時点で更新し、設定/frontend/runtimeのhashを保持。
更新後130秒（idle監視2周期）/65回のhealthは最大4.432ms、PID不変/NRestarts0。
通常opaque iframeのLibraryで既存GLBと画像の表示/閉じる、GLB回転をPC1280/320pxで確認。
横overflow0/pageerror0。物理電話/実LLM推論中の再起動越し継続はNOT TESTED。
以後は記録のみ。追加モデル取得0。[詳細](opencode-revive-nonblocking-20260923.md)。

## 2026-09-23 実OpenCodeのモバイル明示停止を受入

PR341/e34c7caを通常導入、PID486604/health ok。開始時刻/実行IDで停止対象を識別。
自身の実OpenCode/Qwen検証Jobcfce488f01aa（unit PID487050）を通常APIから起動。
通常operatorのPC1280/320pxでID/開始時刻/停止を確認し、320pxから当該IDだけをタップで停止。
Jobs=canceled、unit=inactive/MainPID0、active lease0、商店街Git clean。
横overflow0/button44px/page error0。追加DL0、他実行への停止0、設定hash保持。
実OpenCode推論中のHost再起動/厳密なcold同時到着/物理電話はNOT TESTED。文書のみ更新。
[受入記録](opencode-recovery-20260923.md)。

## 2026-09-23 継続OpenCode修正を通常導入、cold起動を実測

PR340/31e3420を通常deck.shでrootへ適用、PID456446/health ok。Job/unit/lease空きを確認し更新。
1280/320px通常認証で実履歴25件と実行中の停止ボタン、旧interruptedの結果未回収表示を確認。
Qwen3.8-27B loaded=falseからJob2291014cbcf0を実行、77.058秒/succeeded、read2回/変更0。
Broker3要求はcold30,979,147,560B→warm0B→0Bで全granted/released、expired/retry0。
ただしlock前の同時到着時刻はNOT TESTED。実OpenCode再起動越し継続と同じ検証ではない。
同名タイトルを識別できる開始時刻/ID表示を追補し、同名3unitのPC/320px個別停止もsource実測。
追加モデル取得0。詳細[受入](opencode-recovery-20260923.md)。

## 2026-09-23 継続OpenCodeの観測と明示停止（source受入）

利用者の方針に従い、Host終了時にagentを自動停止する案を取り下げた。
永続opencode.runの実unitを確認し、継続中をrunning表示、通常owner/RBAC付きcancelで個別停止。
Webの「バックグラウンドの実行」を追加。利用者の依頼を受けたCodexや自身の検証cleanupも停止可能。
実systemd/HTTP/隔離Host再起動後の3unit継続、PC1280/320px/APIの3停止経路を確認。
他unitへの停止0、横overflow0、button44px、page error0。heartbeat fixtureであり実LLMではない。
./deck.sh test 1171pass/2skip/1warning/107.27秒、frontend build24.60秒、追加モデル取得0。
installed/実OpenCode再起動越しの受入はNOT TESTED。次は通常PR/merge/idle確認後のHost更新。
[詳細と失敗履歴](opencode-recovery-20260923.md)。

## 2026-09-23 Add-on失敗Jobの実MCP・通常ブラウザ受入

PR337/786e76cをroot mainへ適用し、全active Job/lease0でDB保存後に通常deck.sh。
PID394222/health ok、frontendとllama-runtime設定のhash不変、モデル追加取得0。
実OpenCode/Qwen Jobace32c6b7a89は意図的unsupported packを1回だけ要求して期待するtool error。
Host94bde79e1799/upstream job_0fe3e640c9c341b0a2c3ded01c01402b/failedがLLMへ届き、
通常認証付きJobs APIの保存errorと一致。再送0、元Asset不変、失敗Jobの成功化0。
更新後の通常Project Labで1280/390/320px、7モデル/15画像付き配置、移動/視点/help/再読込を確認。
PC実測59.971214fps、page error0、物理電話はNOT TESTED。以後の変更は文書のみ。
詳細[受入記録](addon-failure-job-context-20260923.md)。
更新前helperのunit列挙は旧cdapp prefixのみだったため、その部分の受入表現を訂正。
現行cdfeatureと旧prefixの両方へhelperを修正し、更新後に全状態のunit0を確認。追加再起動なし。

## 2026-09-23 Add-on agent失敗時のJob追跡（source受入）

branch fix/addon-failure-job-context。MCP失敗時に受理済みJob IDが失われる問題を汎用Hostで修正。
Host/upstream IDを区別し、短い符号と安全な参照だけを失敗Job・HTTP・stdioへ保持する。
isError/failedを維持、成功応答とowner/RBACを変更しない。Media固有コード追加0。
独立実HTTP/stdioと隔離Host DBでfailed3/succeeded1、内部パス除外、再送0を確認。
関連82tests/5.16秒、最終全1150passed/2skipped/1warning/105.75秒。
次はPR/merge後、稼働中の作業がない時に通常Host更新と実OpenCode失敗応答を受入する。
installed/実OpenCodeはNOT TESTED。詳細[受入記録](addon-failure-job-context-20260923.md)。

## 2026-09-23 Gateway同一モデルの同時cold start（修正・受入中）

実OpenCodeのタイトル要求と本体要求がモデル未起動時に2.021286秒差で到着すると、
後続がcold予算を保持したまま、起動済みの同じモデルに阻まれて300秒でexpired。
前者30979147560 B予約、後者のblocking ownerもllm:Qwen3.8-27B/23138320384 B。
slot idle/容量availableでも進まなかった。MediaForge側ではgatewayの見積り時点を
変えられないため、汎用Host修正としてこのリポジトリで対応。

- aliasとevent loopごとの弱参照lockで見積り→GPU lease→ensure_readyだけを直列化。
  続く同一モデルの要求はready後に常駐を見て見積もり直す。別aliasの起動、KV待ち、
  推論ストリームはこのlockに入れない。Brokerの予約/認証/退去条件は保持する。
- 実Brokerを使う再現試験: 修正前は後続gateway_chatがtimeout。
  修正後はcold/warmの順で見積り、両応答200・残lease/active request0。
- 切断499/取消/起動失敗後の再要求/別aliasの並行性を含む関連30 tests通過（2.17秒）。
- 実データや他の生成を停止していない。installed適用後のcold実機・ブラウザはNOT TESTED。
  全体gateと通常PRを進め、現在の自身のOpenCode工程が終端になってから適用する。
- 初回全体1138 passed/3 failedはworktreeのvenv/frontend dist不足。既存環境への
  一時linkを用意するとその3件は通過。次の全体は1140 passed/1 failed、既存の
  exclusive resource job待ちのflaky（queued/running、単独再実行通過）。
  製品コードや待ち期限を変えず、最後に./deck.sh testを再実行し全体通過。


最終更新: 2026-09-18

## Files: 置き場プリセットと Add-on ライブラリ（2026-09-18）

ファイルマネージャーの置き場選択に、許可ルートを全部並べるのをやめた。携帯では
`/home/souten`・`/data1tb/LLM`・リポジトリ本体・`data/models` まで毎回選択肢に出て読めない。
普段開く置き場だけを出し、MediaForge と SonicForge の生成物へ直接入れるようにした。

- `app/files/service.py` に `presets()` を追加。CodeDEV（`config.codedev_dir()`）と、
  導入済み Add-on のライブラリだけを `{id, label, path}` で返す。`GET /api/v1/files/presets`
  （`files.view`）。**許可ルートは絞っていない**ので、プリセットに無い場所へも
  パス指定では従来どおり届く（`files.allowed_roots` はモデル管理・ターミナル自動化・
  Add-on の file grant が共有しているため、ここを絞ると `/data1tb/LLM` のモデル
  ライブラリ設定の保存と GGUF 削除が落ちる）。
- `addon_libraries()` を追加し、`allowed_roots()` へ導入済み Add-on の生成物置き場を足した。
  MediaForge は `feature-data/media-forge/data/assets`、SonicForge は
  `feature-data/sonic-forge/assets`。**開けるのは assets の下だけ**にする。feature-data ごと
  開けると、モデルの重み・実行状態・`credentials.json` まで一緒に読めてしまう。
  置き場の形は版で変わるので `data/assets` と `assets` の両方を見る。
- 導入されていない Add-on の置き場は返さない。判定は `registry.installed_ids()`
  （管理ディレクトリと manifest の有無だけを見る軽い判定）。`resolve()` から
  1 要求に何度も走るため、解決のたびに manifest を JSON parse させない。
  表示名が要る `presets()` 側だけ `manifests()` を読む。
- frontend `pages/Files.tsx` は `/files/presets` を見る。ラベル表示になり、初期位置は
  CodeDEV。プリセット外のパスで来ても選択とパンくずが壊れないよう、現在地を
  一時的な選択肢として出す。`FilePicker` は `/files/roots` のままなので、Models や
  Knowledge のパス選択は従来どおり（Add-on ライブラリが選択肢に増える）。

実行証拠:

- `backend pytest -q`（全体）: 1137 passed / 2 skipped、106.26 秒、failure なし。
- `tests/test_files_api.py` へ3件追加（CodeDEV＋導入済み Add-on ライブラリだけが並び
  許可ルートとは別物であること、未導入なら一覧に出ず 403 で閉じていること、
  導入後も assets の外＝`credentials.json` の親は 403 のままであること）。
- `frontend npm run build` / `npx tsc --noEmit`: ともに exit0。
- 稼働 Host の実データで `presets()` の3件すべてを `list_dir` し、CodeDEV 15 件、
  Media Forge ライブラリ 2,604 件、SonicForge ライブラリ 606 件を確認。
- `./deck.sh` 再起動後、`/api/v1/health` 200、未認証 `GET /api/v1/files/presets` 401。

NOT TESTED: ブラウザからの実 UI 操作（PC 幅／320px 幅）での置き場切り替え。

## Files: プロジェクトアーカイブの取り込み（2026-09-18）

フロンティアAI（Claude／Codex等）が出力したプロジェクトをzip／tar.gzのまま
FileManagerへ渡し、展開して配置できるようにした。三点リーダーメニューの
「プロジェクトを展開して配置」から、アーカイブ選択 → 内容確認 → 配置の2ステップで完結する。

- backend `app/files/archives.py` に `inspect()` を追加。展開せずに形式・項目数・
  展開後サイズ・直下の項目・単一ルートfolder名を返す。既存の項目検査（traversal、
  symlink／特殊file、重複path、階層競合、圧縮率）をそのまま通すので、壊れたarchiveと
  危険なarchiveはここで落ちる。`POST /api/v1/files/archive/inspect`（`files.view`）。
- `extract()` に `strip_root` を追加。全項目が同じ1つのfolder配下にあるときだけ
  その階層を外す。AIが出すzipは直下が`MyProject/`なので、これがないと
  `MyProject/MyProject/…` と二重になる。単一rootでなければ展開前に拒否し、
  一時ディレクトリを残さない。既存の`POST /files/extract`へ`strip_root`（既定false）を追加。
- 展開処理はzip／tarのopenを`_read_entries()`へ集約した。inspectとextractで
  壊れたarchiveの判定が二重にならないようにするため。
- frontend `pages/Files.tsx`: アーカイブは`.cd-import-<size>-<名前>`という隠しfile名で
  現在のフォルダへ再開可能uploadし、一覧を汚さない。展開後は既定で削除し、
  「アーカイブも残す」を選べば元の名前へrenameする。キャンセル時も片付ける。
  ダイアログは配置先folder名（単一root名を初期値）、単一root除去、archive保持を出す。
  `apiUpload()`が保存名の上書きと確定pathの返却に対応した。

実行証拠:

- `backend pytest -q`（全体）: 1133 passed / 1 failed / 2 skipped、96.30秒。
  failureは既知flakyの`test_jobs_persistence.py::test_two_exclusive_resource_jobs_execute_serially`。
- `tests/test_files_api.py` へ5件追加（単一root検出、平坦archiveでの非検出、壊れたarchiveの403、
  strip_rootの二重階層回避、複数rootのstrip_root拒否＋一時dir非残留）。
  同fileの`test_viewer_cannot_write`はfile単位実行だと差分なしのHEAD worktreeでも同じく失敗する。
- `frontend npm run build`: exit0。`npx tsc --noEmit`: exit0。
- 稼働Hostで実データ設定（`CONTROL_DECK_CONFIG=config/config.yaml`）から
  `archives.inspect`／`extract`を直接実行し、`/data1tb/ControlDeck/CodeDEV`配下で
  単一root検出とstrip_root配置を確認、検証用ディレクトリは削除済み。
- `./deck.sh`で再起動後、`/api/v1/health` 200、
  未認証`POST /api/v1/files/archive/inspect` 401（routeは登録済み、認証を迂回していない）。

NOT TESTED: ブラウザからの実UI操作（PC幅／320px幅）とcookie認証経路でのupload〜配置。
adminパスワードを持たないため利用者による確認が要る。

## Add-on AI request-local thinking control candidate（2026-09-13）

base main `7cf5605`、branch `ux1/addon-ai-thinking`。Add-onが既存AI bridgeへ
要求単位の思考設定を渡す手段がなく、従来は常にfalseだったため、genericなstrict
boolean `thinking` をcomplete/text streamへ追加。省略false、後続要求・operator設定へ
持ち越さない。既存runtime providerへ変換し、route/model/port/credentialを増やさない。
capabilities各項のrequest_optionsでHostの入力契約を発見可能にする。モデルの能力や
品質の保証ではない。SSE thinking chunkは従来どおり非公開。設計は
`design-addon-ai-gateway.md` のper-request節。

実行証拠:

- `./deck.sh test -q tests/test_addon_runtime_ai.py tests/test_runtime_provider.py`:
  36 passed（exit0）。有効/無効・次要求の既定・complete/stream・private chunk抑制・
  不正型8種の実行前拒否を追加。既存providerの取消/structured-output testを含む。
- `./deck.sh test`: 1102 passed / 3 failed / 2 skipped / 1 warning、150.21秒。
  failuresはmodel_limits未知alias、KV snapshot空き容量、Project Lab encoded slashの
  404対405。未変更mainで同じ3件を実行し2 failed/1 passed（2.95秒）:
  model_limitsとKV snapshotは同じ失敗を再現。Project Labは候補worktreeでも
  frontend build完了後の再実行で1 passed（0.74秒）。初回はbuildと並列で実行し、
  main.pyのimport時DIST.existsによるSPA route登録条件が異なるため、以後はbuild後に
  全gateを実行する。初回full gateを成功へ読み替えない。
- `frontend/npm run build`: 1550 modules、54.76秒、exit0（chunk-size warning）。
- 稼働Hostへ専用受入userの通常発行service tokenでHTTP確認（秘密値非表示/未保存）:
  `GET ai/capabilities` 200、旧応答にはrequest_optionsなし。
  `POST ai/complete` のthinking=trueは422/extra_forbidden（推論開始前）。
  これは旧版の境界確認で、新機能の実機成功ではない。
- Host PID485004 / MediaForge PID537447 はactive、稼働checkout/サービス未変更。

NOT TESTED: 新版の実HTTP推論・思考有効時の実取消/lease返却・PC/320pxブラウザ。
2026-09-13追記: PR325 test-only修正を統合した全gateは1106 passed / 2 skipped /
1 warning、97.58秒、exit0。PR325は4765666でmerge確認。旧失敗記録は上記へ保持。
配布前のread-only live checkで共有LLM slot0 is_processing=trueを観測したため、
Host再起動は行わない。未認証jobs/resources APIは401で拒否、認証を迂回していない。
未配布。次は既存2失敗の扱いとbuild後の全gate、別PRのreviewと実機受入、
その後consumer側を接続する。新モデル・global設定変更・provider直結は行っていない。

## AI gate regression fixtures isolated（2026-09-13）

main7cf5605で再現した2件をtestのみ修正。gateway.resolve_instanceは未知aliasも
起動済み/登録順LLMへ転送するため、_model_limitsの候補最小窓という現在仕様に
古いNone期待値を合わせた。既存のauto/個別alias/分割KVの検査は維持。
KV snapshot成功fixtureは実PCのRAM/disk空きに依存させず、容量判定を明示mock。
容量不足時も別caseで保存POSTなし/metaなしを検査し、実capacity guardの既存拒否testを維持。
途中のnegative fixtureはmeta不在を読み込み失敗したため、不在を明示assertへ修正した。

`./deck.sh test`: 1094 passed / 2 skipped / 1 warning、108.83秒、exit0。
frontend/distは同じmainソースから前段でbuild済みの成果物を参照し、SPA route条件を固定。
production code/設定/サービス変更なし。これは単体test gateの修復で、推論・GUIの実機受入ではない。

## Project Lab にプロジェクト削除を追加（2026-09-13）

CodeDEV へフォルダを置けば自動で並ぶ一方、画面から消す手段が無かった。プロジェクト
情報（ⓘ）のメニュー最下部へ、線で区切って「プロジェクトを削除」を置いた。確認
ダイアログを挟み、成功したら選択を外して一覧を引き直す。

`DELETE /api/v1/project-lab/projects/{id}`、権限は新設の `project_lab.delete`。
`apps.delete` / `files.delete` と同じ命名・扱いで、preset では administrator だけに付く
（`ALL_PERMISSIONS` へ足したので、preset ロールは起動時の `seed_roles` で同期される。
実機の再起動後に administrator=True / operator=False / viewer=False を確認）。

境界の検証は既存の `resolve_project` を再利用する（CodeDEV の外・symlink 脱出・`..`・
先頭 `.` はそこで弾かれる）。その上で root 自身でないことだけ追加で確かめ、CodeDEV
直下が symlink の場合は link だけを外して指す先には触れない。先に 2 つを 409 で断る:
**実行中**（走っている systemd unit が消えたフォルダを掴んだまま残る）と**公開中**
（消すと GitHub 側の Pages と branch だけが残り、画面から取り下げられなくなる）。
監査ログは `project_lab.project.delete`。

新規 test 3 件で、フォルダが消えること・CodeDEV 外の実体へ触れないこと・`..` や
先頭 `.` を拒むこと・実行中／公開中を断ること・権限が administrator 限定であることを
固定した。backend 1092 passed / 1 failed（既存の `_model_limits` の失敗）、frontend
build 成功。実機で service 再起動後、未認証 DELETE が 401、CSRF ヘッダー無しが 403。

NOT TESTED: 320px/1280px の画面確認（ブラウザへ繋げず未実施）。実プロジェクトを
画面から削除する操作（API と service 層までは test と実機の応答で確認）。

## v2 の TUI 設定が保存されない問題を修正（2026-09-13）

v2 は TUI の keybind / theme を config directory の `cli.json` から読む。ControlDeck は
job ごとに config directory を作り替えるため、**利用者が keybind を変えても次の起動で
消えていた**（`XDG_CONFIG_HOME` を job 別 directory へ向けている副作用で、利用者自身の
`~/.config/opencode/cli.json` も読まれない）。`data/integrations/opencode/cli.json` を
共有の1枚とし、生成した directory からは symlink する形にした。TUI が設定画面から
symlink を実体ファイルへ置き換えた場合はそちらを尊重する。

併せて共有 `cli.json` の既定を **v1 の操作へ揃えた**。両 binary から既定キー表を抽出して
突き合わせると、v1 の 162 件中 16 件だけが変わっていた。そのうち v2 が割り当てを外した／
ずらしたものを v1 の値へ戻す: `agent.cycle=tab` / `agent.cycle.reverse=shift+tab`（plan 切替）、
diff viewer の `left` / `right` / `E` / `tab` / `enter,space`、入力欄の `home` / `end`。

`tab` は v2 で `prompt.autocomplete.complete` も使うが、autocomplete 一群は候補が出ている
間だけ効く文脈限定の割り当て（`next=down` / `prev=up` / `select=return` と通常操作に重なる
キーを並べている）なので衝突しない。v1 でも `tab` は `agent_cycle` と `diff_switch_focus` が
共有しており、`left` / `right` / `home` / `end` / `space` も同じ共存をしていた。

戻さないものが 2 つ: `theme.switch`（v1 `<leader>t`）と `session.child.first`
（v1 `<leader>down`）は、v2 が `terminal.toggle` / `terminal.select` へ割り当て直しており、
戻すと v2 の新機能が潰れるため v2 のままにした。実 binary で起動し、keybind の
エラー・衝突警告が出ないことを確認。

## OpenCode v2 に専用画面と、道具の結果の上限を追加（2026-09-13）

### 画面を系列ごとに分けた

`/opencode`（v1）と `/opencode-v2`（v2）の2枚にした。中身は同じ `OpenCodePage` で
`runtime` prop だけが違う。ナビ・コマンドパレット・クイックアクションにも項目を出し、
無効な系列の path は SPA fallback が 404 を返す（実機で v2 を disable → `/opencode-v2`
が 404、`/opencode` は 200 のまま、re-enable で 200 に戻ることを確認）。

**画面から開くセッションは常にその画面の系列で起動する**。`POST /opencode/sessions` に
`runtime` を足し、`tui_command` / `active_binary` が名指しを受け取るようにした。
設定値が v1 のままでも v2 画面は v2 で起動する（実機で確認: v1画面→`opencode-ai`、
v2画面→`@opencode/cli`、未指定→設定値）。設定画面の系列トグルは、画面を持たない
AIチャットとワークフロー `code.agent` の既定を決めるものへ役割を絞り、ラベルも変えた。
セッションIDの localStorage キーも系列ごとに分けた。

`lazyPage` は props を持たない画面（`ComponentType<Record<string, never>>`）に固定
されていたので、props を取れるよう generic 化した。

### 道具の結果の上限（`tool_output`）と自動整形（`formatter`）

v2 だけが持つ組み込み設定。v2 の既定は 2,000 行 / 51,200 バイトだが、51,200 バイトは
約 14,600 トークンで、窓 131,072 なら 1 件で 11% を使う。実測（262,142 トークンで
打ち止めた会話）では道具の結果が 48.1% を占め、内訳は bash 178k 文字・read 135k・
edit 95k だったので、既定のままでも数件で窓が埋まる。

入力窓の 1/16 を上限とし、v2 の既定を超えない形にした（3.5 バイト/トークン換算。
実測の 8,000 文字 ≒ 2,300 トークンが根拠）。ctx 131,072 → 26,880 バイト、
ctx 8,192 → 8,000 バイト（下限）、窓が不明なら既定に任せて勝手に絞らない。
削っても情報は失われない——v2 は全文を data directory の `tool-output/` へ落とし、
その directory を読む権限を既定で開けている（`debug agents` の既定 permissions で確認）。
MCP bridge の `RESULT_INLINE_LIMIT` と同じ形である。

`formatter: true` は編集後に整形を通す。v1 に対応する設定は無いので v1 の config は
従来のまま（test で `tool_output` / `formatter` が v1 側に出ないことを固定）。

生成した v2 config を実 binary の `debug config` へ食わせ、トップレベル11鍵が
1つも落ちないこと、`tool_output` が `{max_lines: 2000, max_bytes: 26880}` として
受理されることを確認。

### v2 用サードパーティ・プラグインの調査結果

**現時点では実質ゼロ**。npm で流通している OpenCode プラグインを確認した範囲
（dcp / mem / readseek / command-hooks / swarm / auto-resume / models-discovery /
openspec / conductor / kompass / magic-context / oh-my-openagent 等）は**すべて v1 API
（`@opencode-ai/plugin` 1.x）依存**で、beta 版（dcp 3.2.8-beta0、swarm 8.0.1-beta.0、
OMo 5.0.0-beta.56）も v1 のままだった。公式ドキュメントは v1 プラグインが v2 では
動かず移植が必要と明記している。先週のダウンロード数は `@opencode-ai/plugin`
10,096,185 に対し `@opencode/plugin` 33,543（v2 CLI 本体 27,635 とほぼ同数）で、
本家以外の採用がまだ無いことと整合する。

一方 **MCP は系列に依存しない**ので、既存の `addon_mcp_bridge.py` がそのまま効く
（v2 から道具 7 個の discovery を確認済み）。v1 でプラグインが埋めていた穴の多くは
v2 の組み込み（LSP と `diagnostics`、`skill`、`subagent`、`websearch`、`commands`、
`references`、`tool_output`、`compaction`、`formatter`）で埋まっており、binary には
gopls / pyright / ruff / rust-analyzer が同梱されている。

### 検証

backend test 1088 passed / 1 failed / 2 skipped（95.40秒）。1失敗は
`test_addon_agent_mcp.py::test_the_window_comes_from_the_instance_that_serves_the_model`
で、本差分の前から単体でも落ちる既存の失敗（`_model_limits` の auto 対応の話）。
frontend build 成功（18.28秒、既知の large chunk 警告）。本番 Host の
`control-deck-web.service` を再起動し、`/api/v1/meta` に `opencode-v2` が出ること、
両 path が 200 を返すこと、未認証 API が 401 であることを確認。

実機の headless 実行（`runtime=v2`、`owner_user_id` 付き、ゲートウェイ →
llama.cpp Qwen3.8-27B）で 20,000 行を吐く bash を走らせて exit 0（events 6）。
transient unit の argv が `opencode.exe run ... --format json --auto --model
controldeck/auto --standalone`（`--dir` 無し）であること、終了後に unit と
job 別 config directory が残らないことを確認。agent 側は `| tail -n 1` で自分から
絞っており、`agent-notes.md` の指示と `tool_output` の両方が効いている。

NOT TESTED: 320px/1280px の画面確認（この環境からブラウザへ繋げず未実施）。
v2 画面からの TUI セッションの実操作（起動文字列と systemd unit の argv までは確認）。
`tool_output` の上限に実際にぶつかったときの切り詰め表示（今回は agent が自分で
絞ったため上限に届かなかった）。

## OpenCode v2 をアドオンとして追加（2026-09-12）

v1（`opencode-ai` 1.18.30）はそのままに、v2（`@opencode/cli` 2.0.2）を別アドオン
`opencode-v2` として追加。導入先prefixが `data/features/opencode` と
`data/features/opencode-v2` で別なので、導入・更新・削除が互いに影響しない。
実行ファイルは `opencode2`（v2 の bin だけが持つ名前で、PATH 上の外部 v1 を
v2 と誤検出しない）。既存の feature registry / router / 設定画面をそのまま使い、
別のインストーラは作っていない。どちらを使うかは OpenCode 画面の `runtime`
（v1/v2）で切り替える。既定は v1 で、設定した系列が未導入なら導入済みのほうへ落ちる。

v2 は設定の形が変わっている。実測（2.0.2）で確かめた差は
`docs/design-opencode-feature.md` の表に置いた。要点は3つ:
(1) `OPENCODE_CONFIG` / `OPENCODE_CONFIG_DIR` / `OPENCODE_CONFIG_CONTENT` が
どれも効かないので、job ごとの config directory を `XDG_CONFIG_HOME` で向ける。
(2) 独自ヘッダーは `settings.headers` では送られず、provider 直下の `headers` が要る
（echo serverで確認: settings内は届かず、provider直下だと `x-control-deck-client:
opencode` が到達）。(3) `run` に `--dir` が無く、既定で常駐 background service へ
繋ぐため `--standalone` を付ける。runtime config は v1 の形で組んでから
`_v2_payload` で機械的に読み替え、測って書いた判断を二重に持たない。

ゲートウェイ / MCP / llama サーバーの経路は v1 と同じものを通す。生成した v2 config
を実 binary の `debug config` へ食わせ、トップレベル9鍵・permissions 152件・
agents 3件・mcp servers 1件・capabilities・limit が1つも落ちないことを確認。

実機検証（本番 Host、`control-deck-web.service` 再起動あり）:
- `registry.install('opencode-v2')` → managed/healthy/`opencode v2.0.2`、v1 は enabled のまま
- `/api/v1/meta` の enabled_features に v1 が残り、`/opencode` は 200、未認証 API は 401
- `runtime=v2` の headless 実行（`run_chat` → systemd-run transient unit → ゲートウェイ →
  llama.cpp Qwen3.8-27B）で `PONG-V2` と session ID を取得（events 2）
- `owner_user_id` 付きの v2 実行で Add-on の MCP 道具 7 個を discovery。主エージェントに
  deny した `media_scene_*` / `media_job_*` / `sonic_*` は一覧から消えており、v1 の
  `tools: false` と同じ効き（＝文脈の節約）が v2 の agent 毎 `permissions` で保たれる
- TUI 起動文字列は v1 が `OPENCODE_CONFIG=... opencode --model ...`、v2 が
  `XDG_CONFIG_HOME=... opencode2 --standalone <project>`。v2 の flag 受理も実行で確認

backend test: 1085 passed / 2 failed / 2 skipped（93.34秒）。2失敗は
`test_addon_agent_mcp.py::test_the_window_comes_from_the_instance_that_serves_the_model`
と `test_addon_execution.py::test_an_idle_slot_is_saved_even_though_it_reports_no_token_count`
で、どちらも本差分の前から単体でも落ちる既存の失敗（`_model_limits` の auto 対応と
KV slot 保存の話で、v2 とは無関係）。frontend build 成功（18.68秒、既知の large chunk 警告）。
App Studio の書き出しは `app/features/registry.py` をスタブへ差し替えるので、
`opencode_enabled` をスタブにも足した（`test_flow_app.py` で検出）。

NOT TESTED: 320px/1280px の画面確認（この環境からブラウザへ繋げず未実施。追加したのは
既存の2択セグメント（プロジェクト選択）と同じ `sm:col-span-2` + `flex-1` の作り）。
v2 の `--standalone` を使わない常駐 service 経路、v2 での `code.agent` ワークフローノード、
v2 セッションの長時間運用。

**OMo は v2 未対応**。`oh-my-openagent` は 4.19.4 も 5.0.0-beta.56 も
`@opencode-ai/plugin` / `@opencode-ai/sdk` の 1.x（1.15.13 / 1.18.22）に固定で、
v2 の `@opencode/plugin` 2.x には載っていない。アドオン一覧の summary に
「v1系列のみ対応（v2は未対応）」と出して先に伝える。OMo を使う間は v1 のままにする。

## Queue資源予約の受付IDを退避完了前に返す（2026-09-10）

Hostが発行した未受信の予約IDをAdd-onだけでは照会・取消できないため、汎用Brokerを
別branch fix/resource-queue-receipt（base6d3cd2a）で修正。Media固有コードは追加しない。
従来submitはqueueでもprovider退避taskをmax_wait_sec（既定300秒）まで待っていた。
queueでは現在状態/IDを先に返し、退避をBrokerの既存taskへ残す。fail_fastの退避判定と
acquireの待機動作は維持。timeout値の延長、公開schema追加、同期I/O追加は行わない。
任意の応答喪失に対する冪等再送・未受信IDの回収を完成させたとはしない。

新5testsはgate付きproviderで、退避前受付/照会/取消、取消済み要求を後でgrantしないこと、
正常待機のgrant、fail_fastの成功/拒否、acquireの待機を検査。修正前2 failed/3 passed、
修正後の関連5filesは58 passed/既知warning1/3.78秒。
`PYTHONPATH=backend .venv/bin/python tools/resource-queue-receipt-smoke.py` は隔離transient
systemd/Uvicornへ実HTTPを送信し、provider退避をgateで止めたまま202/IDを0.001068秒で受信。
同IDのGET/DELETE、別要求の退避後grant/release、最終lease0を確認。全0.194703秒/exit0。
証跡 `/tmp/cd-resource-receipt-chdal_xh/observations.json`。専用unitはfinallyで停止/inactive。
初回smokeはfixture内の型annotation解決がquery扱いとなり422/exit1、import位置を修正。
実GPU/provider/installed認証routeではなく、production Brokerの実network fixture受入。

frontend build成功（Vite20.47秒、既知large chunk warning）。初回backend全gateは
1064 passed/2 failed/2 skipped/96.28秒。2失敗は新worktreeの.venv不在を検査する
terminal automation試験で、既存Host venvへの参照を準備して全gateを再実行。
最終 `./deck.sh test`:1066 passed/2 skipped/既知warning1/95.08秒、exit0。
新module/scriptのcompileallとgit diff --checkも成功。
本番Host PID1141433/activeを保持し、PC/Host/MediaForge再起動なし。
NOT TESTED: installed Host→Add-onの同条件受入、実GPU退避、PC/320pxの画面操作。
backendのみの修正でUI/source bundle配布物は変更しない。導入済み修正とは案内しない。

## Embedded Add-onの動的locale通知（2026-09-06、candidate）

MediaForge側だけではHostから届かない言語変更通知を補えないため、全Add-on共通の
EmbeddedAddonViewでbrowser languagechangeを購読し、locale変更時に既存MessagePortへ
locale.changedを送る。接続時通知・ja系/それ以外enの選択・opaque sandboxは維持。
theme tokenと同じlocaleを通知し、iframe/sessionの再作成はしない。購読解除も追加。
実装先はHost別branch feat/addon-live-locale（base 72948ae）、Media固有処理なし。

隔離Vite（127.0.0.1:5179）+実Chromiumでproduction React componentをmount。
`CONTROL_DECK_E2E_URL=http://127.0.0.1:5179 CONTROL_DECK_LOCALE_FIXTURE=1
npx playwright test e2e/addon-locale.spec.ts --output /data1tb/cd-locale-fixed-20260906 --trace on`
は2 passed/1.6秒。実innerWidth1280/320、opaque origin null、ja→en→ja→en、
theme locale一致、load ID/nonce/未保存input不変、handshake1、page errors0。
言語はnavigator.languageとlanguagechangeを与えるbrowser入力fixture、認証APIとchildはmock。
本番データ/APIへは要求を送らない。実ブラウザ設定操作やinstalled MediaForge受入ではない。
同一testを旧製品codeで実行すると320pxの最初の英語切替がjaのまま失敗（5.6秒）。
証拠 /data1tb/cd-locale-baseline-20260906。修正版へ戻して上記成功を再確認。
初回fixtureはAPI routeがsrc/api importまで遮断してmount失敗、/api/v1限定へ修正。
`npm run build` 成功（Vite35.77秒、既知large chunk warning）、git diff --check成功。

backend製品/test差分0。全 `./deck.sh test` 初回は3 failed/1013 passed/2 skipped/
既知warning1/129.82秒。device pairingとopaque WSのTestClient終了時CancelledError、
registry revision試験で期待2/実3（health loop実HTTPが並行）を観測。
無変更main 72948aeでこの3件を集中実行してdevice pairingの同じCancelledErrorを再現
（1 failed/2 passed/6.17秒）。locale変更が原因とは扱わないが、全gate成功にも読み替えない。
全gate再実行も1 failed/1015 passed/2 skipped/既知warning1/124.07秒。
device pairingの再接続終了時に同じCancelledError。proxy/revisionは今回はpassだが
初回失敗を解消済みとはしない。既存失敗を別sliceで切り分け、merge/installed反映は未実施。
NOT TESTED: installed Hostから実MediaForgeのLibrary offset/選択を保持する言語切替、
実ブラウザ設定からの言語変更、live resize入力問題。3D全体の完了は未宣言。

PR #294の構造化終了処理はmerge `2fafa2d4256b14108a4beddd6b414491af53bd24`を確認し、
本branchへ統合した。backend treeは同PRと一致し、全1022 passedの対象と同一。
locale固有変更はfrontend/fixture/design/statusだけ。統合後のbuildは41.99秒成功。
同browser commandのoutputを`/data1tb/cd-locale-integrated-20260906`へ変更して
1280/320再確認: 2 passed/3.3秒、全locale/input/load/nonce/handshake assertions一致。
backend tree `de469e316484e7e050fb1da01c38452d65b384e3`の一致をgitで照合。
統合後の実relay smokeも21回完了/残存0/故障1、0.443121秒、
`/tmp/cd-relay-lifetime-q7t69ul_`。本番Host PID407562/active（21:58:31起動）、
MF PID396381/activeをread-only確認。こちらから再起動していない。
本番checkout main 72948aeには別作業のproxy.py/tokens.py/test_addon_proxy.py変更あり。
上書き・checkout切替・本番buildは行わない。installed反映は別作業との調整後。

## Add-on WebSocket relayの構造化終了処理（2026-09-06、candidate）

Host PR #293のlocale通知とは別slice。無変更main 72948aeでもdevice pairingの
TestClient context終了でCancelledErrorが再現した。旧relayはFIRST_COMPLETED後にのみ
pendingをcancel/gatherし、親ASGIがキャンセルされた経路の子task joinを保証していなかった。
汎用run_websocket_tasksでAnyIO task groupが全directionを所有し、最初の終了/失敗/親cancelで
兄弟を止めてjoinする。frame proxyとdevice relayへ適用。最初の通常例外はcleanup後に
呼出元へ返し、従来gather(return_exceptions=True)で転送失敗をsuccess扱いする経路も除く。
AnyIOは既存FastAPI環境内の依存を直接使用するためrequirements/pyprojectに宣言した。
新規同期I/Oや同期waitをasyncへ追加していない。認証・権限・relay public schemaは変更なし。

`./deck.sh test tests/test_addon_relay_tasks.py tests/test_addon_device_sessions.py
tests/test_addon_proxy.py`: 19 passed/既知warning1/6.80秒。
新4ケースは正常終了・OSError・asyncio親cancel・AnyIO scope cancelで、awaitを含む
cleanupの完了と残存asyncio task0を検査。例外と親cancelを握り潰さないこともassert。

`PYTHONPATH=backend .venv/bin/python tools/addon-relay-lifetime-smoke.py`:
専用transient systemd/loopback Uvicornで実WebSocket text/binary各20回と故障1回。
各切断後active/directions0、closed21/errors1、故障close4502。
最終typed scriptの実測0.338665秒、証拠`/tmp/cd-relay-lifetime-9ipvc0g3/observations.json`。
初回同実測0.377852秒、`/tmp/cd-relay-lifetime-wposow5o`。finallyで専用unitを停止。
production helperを使うfixture appの実network受入で、installed認証/実Add-onの証拠ではない。
本番Host/MediaForgeを再起動せず、利用者の既存接続・データ・設定は変更しない。
初回全gateは1 failed/1019 passed/2 skipped/123.34秒。Jobs streamも同じ
FIRST_COMPLETED後だけのcleanupで、context終了時CancelledErrorが発生した。
Jobs修正前の再実行は1020 passed/2 skipped/136.08秒だったが、これを解消証拠にせず、
helperをHost共通app/websocket_tasks.pyへ移しJobs streamにも適用した。
Jobsの直接ASGI fixtureで親asyncio/AnyIO cancel後に更新待ち/切断待ち両方の終了をassertする
2 testsを追加。新helper/Jobs6 testsは0.11秒passed、関連30 testsは8.25秒passed。
移動後helperの実network再確認は0.448174秒、`/tmp/cd-relay-lifetime-tsx4hl1y`、同じ21回完了。
frontend buildは35.14秒成功（既知large chunk warning）。最終全gateは
1022 passed/2 skipped/既知warning1/154.50秒。既存失敗箇所を含む追加再試験16 testsも
7.77秒passed。git diff --check/新moduleとsmokeのcompileall成功。
installed/end-to-endはNOT TESTED。backend-onlyでPC/320画面を変更していない。

## Blender SkillsのControlDeck実行版・再起動準備（2026-09-06）

方針Bを採用。上流固定commit `8f778d2405a214b508d4c7d80742be8e43acdd52`の94 SKILL.mdと
175参考資料・MIT noticeを管理下へ保持し、OpenCodeへはControlDeck対応版`blender-director`だけを
公開する。上流の全94実行指示を有効にして存在しないBlenderMCPを呼ばせる構成にはしない。
型付き7操作/画像材質/Job追跡/GLB exportの対応表、前提確認、unsupportedの明示を含めた。
Aは第二の常駐Blender/addon/MCPと資源lifecycleを増やす。Cだけではツール名と任意bpyの不一致が
解消しないため、今回の実行profileには採らない。設計はdesign-opencode-extended-stack §17。

catalog版`2026.07.10-cd1`。汎用の宣言済みAdd-on tool/public capability検査を追加し、
未導入/無効/基本環境不足/契約不一致と旧未適応installを設定画面へ表示。
その場合はenabledでもruntime skills.pathsへ入れない。GUI常駐は前提にしない。
導入は同じdata/skills/versions配下のstagingでcommit/構成を検証し、同版修復でも旧実体をrename保持。
state公開失敗で旧実体/記録/意図的無効を復元する。mutatorのprocess/thread lockとsymlink境界を追加。
OpenCodeのasync実行経路からskill検査を含むruntime config生成をasyncio.to_threadへ移した。
利用者のglobal agent設定、既存Blender、MediaForgeの実装/稼働版は変更していない。

`./deck.sh test`: 994 passed / 2 skipped / 既知warning1 / 98.15秒。
`npm ci`と`npm run build`: 成功(22.58秒)、既知large chunk warning。
最新main `512eace`を統合した再確認: `./deck.sh test`は995 passed / 2 skipped /
既知warning1 / 92.94秒。frontend buildも再成功(20.69秒)。
npm auditは依存既存の9件(5 moderate/4 high)を報告し、本sliceで無関係な依存更新はしていない。
新旧skill集中19 tests成功。失敗repairのfiles/state保持、旧版fail-closed、前提不足、symlink脱出拒否、
public capabilityのstate/schema/local_only、async内同期config呼出し禁止を検査した。

実取得はsource Python/CONTROL_DECK_CONFIG=installed config、skill dataだけを新しい
`/data1tb/ControlDeck/data/blender-skills-acceptance-i77b3sla`へ限定してregistry.installを実行。
実MediaForgeのpublic capabilityを検査しexecution=ready。上流94/参考175、runtime SKILL.md1を実測。
同directory内の専用OPENCODE_CONFIGで管理OpenCodeの`debug skill`を実行しexit0、
`blender-director`のlocationがversion配下runtimeを指すことを確認。
本番のskills/state.jsonへは導入していない。試験用取得物は上記directoryに保持する。

利用者が再起動準備を優先したため、ここではmerge/checkout/buildを整え、こちらから再起動しない。
**NOT TESTED / 再起動後の残件**: 設定画面からの実導入(PC/320px)、実エージェントが本directorを読み
3D Assetを制作する一巡、実環境の無効化/削除とglobal設定hash不変の照合。
旧MediaForge 3D制作の実績を本skill経由の実行証拠に読み替えない。Blender Skills依頼全体の完了ではない。

## Add-on終端outboxの再認証後照合（2026-09-06）

失効したchild credentialをAdd-onが再発行できないため、汎用Hostの終端専用reconcile APIを加法追加。
有効なjobs.write service identityで、同じAdd-onのruntime Jobへ終端だけを送る。別のcalling Jobからは
active caller・署名actor・caller/target ownerの一致を要求する。owner userの有効性も検証する。
対象に確定終端があれば上書きせず、status/result/errorの一致をboolean receiptとして返す。
再実行・credential・resource権限は委譲せず、既存PATCHとcontrolのJob scopeも変更しない。

`./deck.sh test`: 960 passed / 1 skipped / 既知warning1件 / 61.41秒。
`PYTHONPATH=backend .venv/bin/python tools/addon-job-history-smoke.py`の隔離systemd/実HTTPで、
再起動後interruptedへのfailed通知はalready_terminal/matches=false、fresh callerからactive targetへ
succeeded通知はapplied/matches=true、重複はalready_terminal/matches=trueを確認。
旧control200・別Job403・refresh404・PATCH404も維持。証拠`/tmp/cd-job-history-tzrzg278/observations.json`。
試験unitはfinallyで停止。installed Host適用・下流Add-on outbox・PC/mobile UIはNOT TESTED。

## Add-on Job controlの終端履歴参照（2026-09-06）

Add-on側ではHost再起動後のDB履歴に到達できず、終端通知の照合を解決できないため、汎用Host
Job controlの読み取りだけを補った。メモリにないJobは既存非同期DB読取を使い、正確なAdd-on kind、
Job scopeまたはowner、終端状態を検証する。DBだけのrunning/queuedは409、refresh/updateは従来どおり404。
履歴をメモリへ再登録せず、interruptedを結果で上書きしない。公開応答fieldは不変。

`PYTHONPATH=backend /data1tb/ControlDeck/app/.venv/bin/python tools/addon-job-history-smoke.py`を実行。
隔離DBのrunning Jobを別systemd user processの通常recoveryでinterruptedへ移し、production Job router/authへ
実HTTP照会: 200/interrupted、別Job403、refresh404、update404、DB終端不変。試験unitはfinallyで停止しinactive。
証拠`/tmp/cd-job-history-s5qzgvn8/observations.json`。秘密tokenはメモリ内のみ。
focused 25 tests passed / 2.92秒。導入済みHost、Add-on outbox再送、PC/mobile UIは **NOT TESTED**。
今回の変更は汎用APIのみであり、個別Add-onの終端同期完了とは記録しない。
最終`./deck.sh test`: 951 passed / 1 skipped / 既知warning 1件 / 66.76秒。
最新main `f2fc148`の隔離baselineは936 passed / 5 failed / 1 skipped / 68.16秒で、
既存runtime fixture間のactive Job持越しが件数上限と後続resource待機試験を汚染していた。
本sliceでfixtureのJob mapを毎回隔離し、上限を緩めず全体を通した。
最終codeの実HTTP再確認も同じ200/403/404/404、証拠`/tmp/cd-job-history-nzou5ckt/observations.json`。

## Add-on introspection の stable actor owner（2026-09-06）

OpenCodeのAgent toolはcallごとに別Host Jobを作るため、Add-onへ届く実行`subject=job:<id>`も
毎回変わる。一方、introspectionは署名tokenに含む`actor_user_id`を返しておらず、Add-onが
同じ利用者のdurable objectを次のtool callから安全に再取得できなかった。

汎用introspection応答へ、actorが署名されている場合だけ`actor_subject=user:<id>`を加法追加した。
実行Jobのauthorityである`subject`は変更せず、actorなしtokenの既存応答も変更しない。Add-onは
stable owner keyにこのopaque subjectを使えるが、Host操作は引き続き実行/child Job credentialで行う。
focused backend testはAdd-on Runtime auth/Jobs 27件成功。実Media Forge/OpenCode往復は依存Add-on
リリース後の統合受入で確認するため、このHost PR単体では **NOT TESTED**。

## 退避廃止に伴う待機経路の整理（2026-09-04）

退避(yield)を廃止したことで、到達しなくなった待機理由と、そのためだけにあった
派生値を削除した。

- `WaitReason` の `YIELD_RUNTIME_UNKNOWN` / `YIELD_LOAD_COST_UNKNOWN` /
  `YIELD_THRASH_COST` / `YIELD_MINIMUM_UPTIME` / `YIELD_THRASH_WINDOW` /
  `YIELD_DRAIN_TIMEOUT` を削除。これらを出す経路がもう無い。
- `ResourceTelemetry.reload_cost_p90()` / `LoadCostEstimate` / `YIELD_THRASH_FACTOR`、
  および load profile の `yield_basis` / `yield_threshold_sec` を削除。
  「退避する価値があるか」を判断するためだけの派生値だった。
  cold/warm の実測分布そのものは残す（診断に使える）。
- ゲートウェイの KV 空き待ちが `resource_provider.provider().await_capacity()` を
  経由していたのをやめ、`local_llm.await_capacity()` を直接呼ぶ。provider 側は
  中継しているだけで判断を持っていなかった。provider からもメソッドを削除。
- 対応するテストを整理（退避判断を対象にした2件を削除、生存テストからは
  派生値の検証だけ外す）。backend全体は891件成功。

規模: 5ファイルで +2 / −101 行。

## GPU資源の再設計: 退避をやめ、画像生成はRAMへ載せて共存する（2026-09-04）

- 方針を「誰を追い出すか」から「どこへ載せるか」へ変えた。**LLMと画像生成は共存**させ、
  VRAMが空いていなければ画像生成はシステムRAMへ載せる。
- 背景: 従来の退避(yield)機構は `supervision: observed` のまま使われ実際には動かず、
  代わりに amdgpu がバッファをGTT（ホストRAM）へ勝手に追い出していた。LLMのKVが
  RAMへ落ちるとデコードがメモリ帯域律速のため致命的に遅い（実測 75.4 → 32.6 tok/s）。
  一方、画像生成のような計算律速の処理はRAM配置の劣化が桁違いに小さい。
- **`host` デバイスを追加**（`resources/devices.py`）。psutil のRAM総量/空きをそのまま
  デバイスとして登録する。GPUが読めない環境でも host は登録する。
- **`host` は opt-in**。`preferred_devices` に挙げた要求にだけ割り当てる。
  知らない利用者へ黙って割り当てると、GPUを使うつもりのままVRAMを二重に取りにいく。
- **`preferred_devices` を順序付きにした**。`["gpu0", "host"]` で「空いていればVRAM、
  無ければRAM」を表す。順序を無視して空き容量で選ぶと、RAMの方が空きが大きいだけで
  VRAMが空いていてもRAMへ流れてしまう。ブローカー本体はこの1点だけの変更で済んだ。
- LLMは `device: "gpu0"` 固定のままで、host配置には決してならない。
- **退避機構を削除**（統廃合）。`request_yield` / `yield_wait_reason` / `YieldLevel` /
  `yield_level` / `yieldable` / `_start_yield` / `_yield_and_reschedule` /
  `_managed` / `_yield_allowed` / `_suppress` / `_legacy_cost` / thrash guard、
  `RuntimePolicy` の `supervision` / `yield_max_level` / `min_uptime_sec` /
  `warm_idle_sec`、モデル共通設定のGPU Brokerパネル。
  明示解放（`release_on_request`）と `drain_timeout_sec` は残す。
- 実装のバグを1つ潰した: `DeviceCollection.get()` / `list()` がフィールドを手で写して
  複製しており、追加した `kind` が落ちていた。`dataclasses.replace` を使う `_copy()` に
  集約して、フィールド追加時の写し漏れを構造的に防いだ。
- **Add-on 側の契約**（MediaForge の対応が必要）:
  1. CPUオフロード対応の Add-on は `preferred_devices: ["gpu0", "host"]` を送る
  2. grant の `RequestStatus.device_id` が実際の配置を返す
  3. `device_id == "host"` を受け取ったら、VRAMを確保せずCPUで実行する

  3 を守らない Add-on は host を要求してはならない。要求しなければ従来どおりVRAMのみ。
- 規模: backend は 8ファイルで +89 / −278 行。frontend もGPU Brokerパネル分を削減。
- 自動検証: `test_resource_host_placement.py` を追加（VRAMが空けばVRAM／埋まればhost／
  順序が空き容量に優先／hostはopt-in／LLMはgpu0固定／hostデバイス登録の6件）。
  退避機構を対象にした既存テスト9件は機構ごと削除。backend全体は893件成功。

## OpenCodeがLuceboxで同じツール呼び出しを繰り返して止まらない（2026-09-04）

- OpenCodeのセッションが `grep -n "vy" index.html` を延々と繰り返し、進まなくなった。
  Luceboxのログでは7ターン以上にわたり **毎ターン `out=59` トークンで完全に一致**、
  プロンプトは毎回きっかり +122 トークン（assistant 59 + tool結果 63）。
  確率的なブレが一切なく、同じ文脈から同じツール呼び出しが決定的に出続けていた。
- DFlash2 そのものは原因ではない。厳密検証なので出力は素の greedy と同一で、
  投機デコードがトークンを変えることはない。原因は **temperature=0 固定**。
  DFlash2 は temperature>0 だと投機経路を使わず自己回帰へ落ちる（142→29 tok/s）ため
  0 へ固定していたが、greedy は反復ループの古典的な原因で、エージェントループでは
  「同じ文脈 → 同じツール呼び出し → 同じ結果 → 同じ文脈」で閉じて抜けられない。
- **`gateway._greedy_sampling_for()` からクライアント別の上書きを外した**。
  以前は「OpenCode が停止中のモデルを起こす場合は個別設定より優先して投機ONで常駐」
  としていた（速度が3〜5倍効くため）が、エージェント用途では greedy の副作用の方が
  重い。サンプリング方針はモデル個別設定 `prefer_speculative` だけで決める。
  常駐ごとの方針記録（`_residency_greedy`）も不要になったので削除した。
  識別ヘッダ `x-control-deck-client` は診断用に残す。
- コーディング用途は llama.cpp 側へ寄せる。draft-mtp は temperature に依存せず投機が
  効く（実測で temperature 0.0 と 0.7 に速度差なし）。

## KVキャッシュがホストRAMへ退避して2.3倍遅くなる（2026-09-04）

- Qwen3.8-27B が同一プロンプト・同一設定で 75.4 → 32.6 tok/s に落ちていた。
- `/proc/<pid>/fdinfo` の DRM 統計で原因が確定した。

  | | 退避時 | 再起動後 |
  |---|---:|---:|
  | プロセスのVRAM要求 | 27,682 MiB | 27,594 MiB |
  | 実際にVRAMに載っている | 17,690 MiB | 27,651 MiB |
  | GTT（ホストRAM）使用 | **10,402 MiB** | **3 MiB** |
  | 生成速度（定型） | 32.6 tok/s | 73.5 tok/s |

  モデル重み15,702 + mmproj 888 = 16,590 MiB に対し要求が 27,594 MiB なので、
  差の約11GBがKVキャッシュ+計算バッファ。そのうち約10GBがGTTへ落ちていた。
- 直接の原因は、検証用に Lucebox（約14GB）を llama.cpp（約27.7GB）と同時に起動した
  こと。合計42GB > 32.6GBでVRAMが枯渇し、amdgpuがバッファをGTTへ退避させた。
  **Luceboxを止めてVRAMが空いても、amdgpuは退避したバッファを自動では戻さない。**
  モデルを再起動して解消した。
- 教訓: `ctx_size = 262144` は要求を27.7GBまで押し上げ、32.6GBのカードでは他のモデルと
  同居できない。ControlDeckは `max_loaded_models` で同時ロードを止める仕組みを持つが、
  今回は `lucebox.start_instance()` を直接呼んでブローカーを迂回していた。
  検証でも provider_adapters 経由（`load_model`）を使えば同じ事故は防げる。
- 症状の見分け方: 生成が理由なく遅いときは
  `grep drm-total /proc/<llama-serverのpid>/fdinfo/*` と
  `/sys/class/drm/card1/device/mem_info_gtt_used` を見る。GTTが数GB載っていれば退避中。

## 投機デコード各方式の再評価と --load-mode 対応（2026-09-04）

### 前回の「ngram系は効かない」は測り方が誤っていた

`ngram-*` を「素の生成と同じで効果なし」と結論したが、条件が二重に誤っていた。

1. **課題が不適切**。ngram系は文脈にあるトークン列を引いて先読みする方式で、
   文脈に無い新規テキストの生成では原理的に当たらない。前回の3プロンプト
   （数え上げ・日本語の説明文・新規コード）はすべて新規生成だった。
2. **先読み幅が悪い**。当時の instance は `draft_max=8` で、後の掃引で
   「8以上は素の生成より遅い」と判明した領域だった。

文脈を再利用する課題（プロンプトのコードを1箇所だけ変えて全文出し直す）を足し、
先読み幅を揃えて測り直した結果:

| spec_type | 再出力（文脈再利用） | 新規コード | 日本語 |
|---|---:|---:|---:|
| none | 29.4 | 29.4 | 29.4 |
| ngram-simple | 50.3 | 29.2 | 38.3 |
| ngram-mod | 34.3 | 29.4 | 36.0 |
| ngram-map-k | **53.6** | 29.4 | 38.2 |
| draft-mtp | **65.7** | **48.1** | **47.8** |

ngram系は文脈再利用で 1.7〜1.8 倍、日本語でも 1.2〜1.3 倍と、確かに効く。
新規コード生成だけは原理どおり効果が無い。それでも全課題で draft-mtp が上回るため、
このモデルでの採用値は変えない。

`--spec-draft-n-max` を上流ドキュメント推奨の 48〜64 にしても変化しなかった
（ngram-map-k: 4→53.6 / 64→53.7）。

### 方式の組み合わせは現時点では逆効果

`--spec-type` はカンマ区切りで複数取れる。実測すると
`draft-mtp,ngram-map-k` は 再出力48.7 / 新規コード22.1 / 日本語27.7 で、
**単独よりも遅い**。上流の issue #23184 に「両者が文脈を共有せず、ngram-mod が
MTP の先読みを延長できないため検証コストだけ増える」とあり、実測と一致する。

実装は組み合わせを受け付けるようにした（`parse_spec_types()`）。当初は単一値しか
通さず、上流にある機能を塞いでいた。UIの選択肢は単一のままにしてある
（現時点で組み合わせに利点が無いため）。

### 各方式の前提（上流ドキュメント）

| 方式 | 必要なもの | 効く場面 | 推奨 n-max |
|---|---|---|---:|
| draft-mtp | ターゲットGGUFにMTP層 | 汎用 | 2〜3 |
| draft-simple | 小さめの同系ドラフトGGUF | 汎用 | 3 |
| draft-eagle3 | EAGLE-3ドラフト（`--target-model-dir`で変換） | 汎用 | – |
| draft-dflash | DFlashドラフト（同上） | ブロック生成 | 15 |
| draft-dspark | DSparkドラフト（現状Qwen3のみ） | ブロック生成 | 7 |
| ngram-* | 不要 | 文脈の再利用 | 48〜64 |

`draft-simple / eagle3 / dflash / dspark` が試せていないのは「モデルが非対応」では
なく、**それぞれ専用に変換されたドラフトGGUFが手元に無い**ため。特に eagle3/dflash/dspark は
ターゲットに合わせて学習・変換されたドラフトが要る。なお llama.cpp の `draft-dflash` は
Lucebox の DFlash2 とは別実装で、`qwen38-dflash2-*.gguf` を流用できる保証は無い。

### mmap / mlock は --load-mode へ集約された

b10793 では起動時に次の警告が出ていた。

```
DEPRECATED: --mmap and --no-mmap are deprecated. use --load-mode mmap instead
DEPRECATED: --mlock is deprecated. use --load-mode mlock instead
```

ControlDeck は `--no-mmap` / `--mlock` を渡し続けていた。`--load-mode`
（`auto / none / mmap / mlock / mmap+mlock / dio`）をモデル個別設定へ追加し、
`--load-mode` を持つバイナリではそちらを使う。既存の mmap/mlock 設定は等価な
モードへ写す（mmapのみ=既定なので何も渡さない、mmap無し=`none`、mlock=`mlock`、
両方=`mmap+mlock`）。古いバイナリでは従来のフラグに落ちる。

**実測（Qwen3.8-27B / 全層GPU / Vulkan）**

| 項目 | mmap有効 | mmap無効 |
|---|---:|---:|
| 生成速度（3課題とも） | 差なし | 差なし |
| 起動（cold・page cache無し） | 11.1s | 12.9s |
| 起動（warm） | 2.9s | 4.8s |
| ホストRSS | 1035 MiB（うちfile 769） | 550 MiB（うちanon 462） |

全層をGPUへ載せているのでホストに残るのは作業バッファだけで、16GBのモデルが
ホストRAMを占めることはない。速度は変わらず、効くのは起動時間だけ。

**mlock は事実上効いていなかった**。`VmLck` は 0 のままで、unit の
`LimitMEMLOCK` は約3.8GiBしかない（16GBのモデルは固定できない）。そもそも全層GPUなら
ホスト側に固定すべき重みが無いので、この構成では意味が無い。

- `_help_text_sync()` を追加し、unit生成（同期経路）から `--load-mode` の有無を
  判定できるようにした。版ごとに1回だけ `--help` を起動して memo 化する。

## llama.cpp b10793 の投機デコード拡張をモデル個別設定へ反映（2026-09-04）

- Addonからの更新で llama.cpp が b10001 → b10793 になり、`--spec-type` の選択肢が増えた。
  ControlDeck側は `none / draft-simple / draft-mtp / ngram-simple` の4つで固定していたため、
  新しい方式を選べなかった。

  ```
  --spec-type none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,
              ngram-simple,ngram-map-k,ngram-map-k4v,ngram-mod,ngram-cache
  ```

- **選択肢をハードコードしない**ようにした。`llama.detect_spec_types()` が稼働バイナリの
  `--help` から `--spec-type` の値を読み、`/models/llama/options` 経由でUIへ渡す。版を上げれば
  UIの選択肢も自動で増える。保存値の検証だけは既知の上位集合 `llama.SPEC_TYPES` で行い、
  未知の値がunit引数へ流れないようにする。
- **ドラフトモデルを扱えるようにした**。`draft-simple / draft-eagle3 / draft-dflash /
  draft-dspark` は別途ドラフトGGUFが要る（`--spec-draft-model`）。これまで指定手段が無く、
  実質 MTP と ngram 系しか使えなかった。モデル個別設定の詳細設定へ以下を追加した。

  | 設定 | 引数 | 備考 |
  |---|---|---|
  | ドラフトGGUF | `--spec-draft-model` | 必要な方式でのみ表示。未指定なら保存時ではなく起動時に明示エラー |
  | ドラフトのGPU層数 | `--spec-draft-ngl` | -1 は auto（引数を渡さない） |
  | 先読みの下限 | `--spec-draft-n-min` | 0 は既定（渡さない） |
  | 採用する最小確率 | `--spec-draft-p-min` | 0 は既定（渡さない） |
  | ドラフト側KV K/V | `--spec-draft-type-k/v` | 空はターゲットと同じ（渡さない） |

  ドラフトGGUFは `model_path` / `mmproj_path` と同じく `files.resolve()` で許可ルート内へ
  正規化する。共通設定API（`/models/providers/llama.cpp/models/{id}/config`）ではパスを
  変更させない方針なので、ドラフトパスは専用の instance API に限定し、
  値だけの項目（下限・最小確率・ngl・KV型）は共通APIからも変更できるようにした。
- `--help` の実行結果を実体パスでmemo化した（`_help_text()`）。従来は
  `detect_options()` が呼ばれるたびにプロセスを起動しており、種別取得を足すと2回になる。
  版が変われば捨てる。
- UIは「投機デコード」セクションを作り直した。方式に応じて必要な項目だけ出し、
  ドラフト必須の方式で未指定なら警告を出す。受け入れ条件とドラフト側KVは折りたたみへ。
- 既存instanceは `get_config()` の既定マージで新キーが入るだけで、保存済みの
  `spec_type`（例: `draft-mtp`）はそのまま有効。実機で unit 引数が
  `--spec-type draft-mtp --spec-draft-n-max 8` のまま変わらないことを確認した。
- 自動検証: 新種別の受理、ドラフトモデルの引数化、auto時に渡さないこと、未指定時の
  明示エラー、MTP/ngramでドラフト不要なこと、閾値を設定時のみ渡すこと、未知種別を
  unitへ通さないこと、KV型の既定追従、バイナリからの種別読み取りとフォールバックの
  回帰9件を追加。backend全体は892件成功。
- 注意: llama.cpp の `draft-dflash` は Lucebox の DFlash とは別実装で、必要な
  ドラフトGGUFの形式も異なる。Lucebox用に用意した `qwen38-dflash2-*.gguf` を
  そのまま流用できる保証は無い（未検証）。

### 実測: 最速の投機デコード設定（Qwen3.8-27B / Vulkan / b10793）

内容の違う3プロンプト（定型の数え上げ／日本語の説明文／英語コード）で `predicted_per_second` を測った。

**方式の比較**（先読み幅は既定のまま）

| spec_type | 定型 | 日本語 | コード |
|---|---:|---:|---:|
| none | 29.2 | 29.5 | 29.4 |
| **draft-mtp** | **57〜84** | **34〜47** | **41〜46** |
| ngram-simple | 26.3 | – | – |
| ngram-mod | 29.6 | 29.5 | 29.4 |
| ngram-map-k | 29.5 | 30.3 | 29.3 |
| ngram-map-k4v | 29.5 | 30.4 | 29.4 |
| ngram-cache | 23.2 | 28.4 | 29.2 |

ngram系はどれも素の生成と同じかむしろ遅く、この構成では効果が無い。効くのは MTP だけ。
`draft-simple / eagle3 / dflash / dspark` は別途ドラフトGGUFが要り、手元に対応する
ドラフトが無いため未検証。

**先読み幅（`--spec-draft-n-max`）が支配的**

| n-max | 定型 | 日本語 | コード |
|---:|---:|---:|---:|
| 2 | 61.4 | 46.8 | 42.7 |
| 3 | 69.6 | 44.5 | 40.9 |
| **4** | **76.3** | **41.2** | **46.2** |
| 5 | 80.2 | 37.1 | 43.6 |
| 6 | 83.6 | 34.0 | 41.1 |
| 8 | 57.2 | 18.6 | 17.1 |
| 16 | 34.8 | 9.5 | 9.2 |
| 32 | 22.5 | 4.9 | 4.8 |

**8以上で素の生成より遅くなる**。外した分の検証コストが効くため、大きいほど速いわけではない。
定型文だけは大きい値が伸びるので、数え上げのような予測しやすい出力だけを見て決めると誤る。
`--spec-draft-p-min` は 0.4 で日本語 +1.8、コード +1.5 とわずかに改善した。

**採用値**: `spec_type=draft-mtp` / `draft_max=4` / `draft_p_min=0.4`。
適用後の実測は 定型 75.4・日本語 42.7・コード 47.6 tok/s。
変更前（`draft_max=8`）は日本語 18.6・コード 17.1 だったので、**日本語 2.3倍・コード 2.8倍**。

- `DEFAULT_INSTANCE["draft_max"]` を 16 → 4 にした。16 は旧 `--draft-max` 時代の値で、
  現在の `--spec-draft-n-max` では明確に遅い（上表）。バイナリ既定も 3。
  既存instanceの保存値は変えない（利用者が意図して選んでいる可能性を残す）。
  UIのプリセットも `[4,8,16,32,64]` → `[2,3,4,6,8]` へ変え、大きいほど速くはない旨を明記した。

## Luceboxにエージェント用ターンキャッシュを追加（2026-09-04）

- `dflash_server --agent-turn-cache`（生成したツール呼び出しの先までprefixキャッシュを延ばす）を
  Luceboxのモデル個別設定へ追加し、既定ONにした。
- 無効だと`prefix_len`が初回プロンプトで止まり、ターンが進むほど再prefillが増える。
  OpenCode相当のループ（tools=22、初回13,957トークン、各ターンに約1,500トークンの
  ツール出力を追加）での実測:

  | ターン | プロンプト | OFF | ON |
  |---|---:|---:|---:|
  | 1 | 13,957 | 0.9s | 0.8s |
  | 2 | 17,001 | 6.7s | 6.4s |
  | 3 | 20,081 | **12.2s** | **7.7s** |
  | 4 | 23,171〜26,193 | 19.3s | 7.9s |

  3ターン目はプロンプト長が完全に一致した比較で 12.2s → 7.7s（1.6倍）。
  4ターン目はモデルのツール選択が分岐してプロンプト長がずれたので参考値。
  ツール出力が小さい場合も、`prefix_len`はOFFで13,824に固定されるのに対し
  ONは13,983→14,094→14,256と追従し、prefillは0.3→0.8sに対し0.1→0.2sで横ばいだった。
- ツールを使わない用途では効かないだけなので既定ONにした。切ることもできる。
- 補足: 以前「OpenCodeは毎ターン11kトークンのprefillに8〜14秒かけている」と書いたが、
  これは不正確だった。既存のprefixキャッシュにより2ターン目以降のprefillは0.9秒程度で、
  遅いのは初回のみ。ただし上表のとおり、ツール出力が積み上がるとOFFでは再び伸びていく。

## LuceboxでOpenCodeが即終了する不具合（fa_window既定値の誤り・2026-09-04）

- Luceboxを起動してOpenCodeを動かすと、モデルは応答するのにセッションが即座に終わる。
- サーバーログに残っていた実際の症状:

  ```
  [server] tool_call parse failed; suppressing buffered tool text ... format=0 bytes=141
           text='<tool_call>\n<function=Bash>\n<parameter=command>\nls -la ...'
  [server] chat DONE ... out=44 ... finish=stop
  ```

  ツール呼び出しが壊れた方言（Hermes風の`<function=...>`XML）で出力され、dflash_serverが
  パースに失敗してテキストごと捨てる。OpenCodeへは中身の無いassistantメッセージが
  `finish_reason=stop`で返るため、エージェントは何もすることが無くセッションを終える。
- 原因は`fa_window`の既定値。`dflash_server --help`に明記されている:

  ```
  --fa-window <N>     Flash-attention sliding window (default: 0=full).
                       WARNING: >0 drops system prompt / tool definitions
                       from attention at long contexts. Use 0 for tools.
  ```

  ControlDeck側は既定を2048にしていた。これは上流Luceboxの`server/README.md`にある
  スループット向けの推奨値（1024〜2048）から取ったもので、**AMDLuceboxの実測起動コマンドは
  `--fa-window`を渡していない（=既定の0）**。取り違えである。
  OpenCodeのプロンプトは11,000トークンを超えるため、システムプロンプトとツール定義が
  注意窓の外へ出て、モデルがツール定義を見ずに呼び出しを組み立てていた。
- 再現と検証（tools=22 / prompt 13,958トークン / 同一プロンプト）:

  | fa_window | 結果 |
  |---|---|
  | 2048 | `Bash`を引数空`{}`で呼ぶ（必須の`command`が欠落）→ エージェントが動けない |
  | 0 | `List`を`{"path": "."}`で正しく呼ぶ |

  5,492トークンでは2048でも呼び出せるが、任意引数`description`が落ちる。
  文脈が伸びるほど欠落が増える。なお`fa_window` 2048→0 でデコード速度は実測で差が無い
  （86.2 → 85.9 ms/step）ので、2048にする利点は無かった。
- 修正:
  - `lucebox.DEFAULT_INSTANCE["fa_window"]`を0にした。
  - 既存設定の一度きりの移行を入れた（`CONFIG_REVISION = 2`）。過去に既定として
    書き込まれた`fa_window == 2048`だけを0へ直し、利用者が意図して選んだ他の値
    （1024等）はそのまま残す。移行後に意図して2048を選び直せば尊重する。
  - `runtime_status()`に`tool_warnings`を追加し、`fa_window > 0`のinstanceを名指しで警告。
    モデル個別設定の画面にも、ツール利用が壊れる旨を条件付きで表示する。
- 実機検証: 本番経路（ゲートウェイ + OpenCodeヘッダ + temperature=0固定 + tools=22 +
  13,958トークン）で、引数の揃ったtool_callが返ることを確認した。
- 未確認: OpenCode CLIでの最終確認は、こちらの環境でOpenCodeが`init`（467MBの
  `~/.local/share/opencode/opencode.db`のオープン）で止まるため完了していない。
  ゲートウェイまでリクエストが届いていないので、本件とは無関係の環境問題。
- 自動検証: 既定値、unit引数、移行（2048だけを直す/他の値は残す/書き戻す）、
  `tool_warnings`の回帰4件を追加。backend全体は882件成功。

## OpenCodeが起こしたLuceboxは投機デコード優先で常駐させる（2026-09-04）

- `prefer_speculative`（temperature を 0 に固定）は既定ONだが、日本語主体のチャットでは
  投機デコードが自己回帰より遅く、切りたい場合がある。一方 OpenCode はコード生成が主用途で
  投機デコードが 3〜5 倍効くため、個別設定をOFFにすると OpenCode まで遅くなってしまう。
- 方針: **OpenCode が停止中のモデルを起こす場合だけ、個別設定より優先して投機ONで常駐させる**。
  既に誰かが使っている常駐へ相乗りするときは、その常駐を始めたときの方針を引き継ぐ
  （同じセッションの途中で生成の性質が変わらないようにする）。方針の記録が無い場合
  （ControlDeck再起動後、ゲートウェイ外での起動）は個別設定へ戻す。
- 実装: `gateway.resolve_instance()`が転送先インスタンスを`loaded`込みで返し、
  `gateway._greedy_sampling_for()`が起動前に方針を決めて`_residency_greedy`へ記録する。
  クライアント識別は、ControlDeckが生成するOpenCodeのruntime configへ載せる
  `x-control-deck-client: opencode`ヘッダ（`@ai-sdk/openai-compatible`の`options.headers`）。
  汎用のUser-AgentではOpenCodeと利用者の自作クライアントを区別できないため。
- llama.cppには同じ制約が無いので、OpenCode相手でもサンプリングへは触らない
  （回帰テストで固定した。実装当初はランタイムを見ずに潰していた）。
- 実機検証: 個別設定をOFFにした状態で、(1)停止中→OpenCodeヘッダ付きで起動すると
  `spec-decode`、(2)その常駐へ別クライアントが投げても`spec-decode`（方針を継承）、
  (3)停止中→ヘッダ無しで起動すると`ar-decode`（個別設定に従う）、を確認した。
  実際のOpenCode 1.18.24でも`options.headers`付きruntime configが正常に動作することを確認した。
- 自動検証: 上記4分岐 + llama.cppへ影響しないこと + runtime configがヘッダを載せることの
  回帰6件を追加。backend全体は880件成功。

## Luceboxの実効速度は採択長で決まる（性能特性の実測・2026-09-04）

`temperature`固定の修正後もAIチャットで約50 tok/sだったため、性能特性を実測した。

**速度 = 採択長 ÷ 約87ms**。1ステップ（ドラフト提案 + DDTree検証）の単価は内容によらず
86〜88msで一定で、そのステップで何トークン確定できたか（`avg_commit`）だけが効く。

| 生成内容 | 採択長 | 投機ON | 投機OFF（自己回帰） | 倍率 |
|---|---:|---:|---:|---:|
| 英語コード | 10.0〜13.3 | 92〜152 tok/s | 29.0 tok/s | 3.2〜5.2倍 |
| 英語の文章 | 3.8〜4.2 | 42〜48 tok/s | 29.0 tok/s | 1.5倍 |
| 日本語の文章 | 2.1〜2.3 | 23〜25 tok/s | 29.0 tok/s | **0.8倍（遅い）** |

自己回帰は内容によらず29 tok/s。1ステップ87msは自己回帰の約2.5トークン分なので、
採択長が2.5を下回る内容では投機デコードが逆効果になる。DFlash2ドラフトは英語・コード向けで、
日本語では採択が伸びない。公開実測の180〜208 tok/sはHumanEval（英語コード）の値で、
本機のコード生成152 tok/sはその水準にある。

参考: 同一モデル・同一プロンプトでllama.cpp（Vulkan / b10001）は20.9 tok/s。
Luceboxは最悪の日本語でもllama.cpp Vulkanより速い。

**効果がなかった調整**（すべて実測で確認）
- `--max-ctx` 131072 → 8192: デコードは差なし（86.2 → 85.9 ms/step）。
  上流READMEのoversizing警告はprefillのみに効く。
- `--fa-window` 2048 → 0: 差なし（短いコンテキストでは支配的でない）。
- `--ddtree-budget` 22 → 48: 採択長は伸びる（2.17→2.56）がステップ単価が上がり（86→108ms）
  総合では遅くなる。既定の22が妥当。
- ドラフトをq8_0 → f16: 英語のみ+10%、日本語とコードは悪化。AMDLucebox指定のq8_0が妥当。

**効果があった調整**
- GPU電力cap 210W → 300W（既定値）: **+9%**（86.8 → 79.3 ms/step、日本語25.1→27.4、
  数え上げ143→157 tok/s）。現在の`amd_gpu`設定がcustom/210Wのため常時この分を失っている。
  静音・消費電力とのトレードオフなので設定は変更していない。

UI側は、モデル個別設定のトグル説明に内容別の実測値を載せ、日本語主体ならOFFの方が速い旨を
明示した。ONのまま日本語で使うと、遅いうえに出力が決定的になるという最悪の組み合わせになる。

## Luceboxが投機デコードを使わず約1/5の速度になる不具合（2026-09-04）

- AIチャットからLuceboxで生成すると約27 tok/sしか出なかった。公開実測（180+ tok/s）と大きく違う。
- 原因は`temperature`。このLuceboxビルドのDFlash2/DDTree検証は**厳密グリーディ検証のみ**で、
  `temperature > 0`のリクエストは投機経路を使わず自己回帰へフォールバックする。サーバーログの
  `[spec-decode]`と`[ar-decode]`で判別できる。同一プロンプト・同一設定でtemperatureだけを変えた実測:

  | temperature | 経路 | 速度 |
  |---|---|---|
  | 0 / 未指定 | `spec-decode`（平均12.4トークン/ステップ採択） | 142〜145 tok/s |
  | 0.2 | `ar-decode` | 28.8 tok/s |
  | 0.7 | `ar-decode` | 29.3 tok/s |

  `RuntimeChatRequest.temperature`の既定は0.4なので、ControlDeckからの生成は常に自己回帰側へ落ちていた。
- 切り分けで否定した仮説: stream有無（無関係）、max_tokens（無関係）、`--draft-block-size`の
  horizon超え（学習時の8へ戻しても`temperature 0.7`は`ar-decode`のまま。かつ16の方が速い:
  142 vs 93 tok/s なのでREADME推奨の16が正しい）、GPU電力・クロック制限（同条件で142 tok/s出る）。
  `dflash_server --help`にもサンプリング下で投機を有効にするフラグは無い。
- 対応: Luceboxのモデル個別設定へ`prefer_speculative`（「投機デコードを優先（temperature を 0 に固定）」）
  を追加し、既定ONにした。ONの間は送信直前に`temperature`を0へ固定する。判定は
  `lucebox.pins_greedy_sampling()`へ集約し、内部チャット（`LuceboxRuntimeProvider._payload`）と
  ゲートウェイ（外部クライアント。OpenCodeはこの制約を知らないため）の両方の入口で適用する。
  設定はLuceboxのinstanceにしか無いので、llama.cppのサンプリングには影響しない。
  出力が決定的になる代償があるため、UIにトレードオフを明示したうえで利用者が切れるようにしてある。
- 実機検証: 再起動後、(1)AIチャットと同じ内部経路（既定temperature 0.4、stream）と
  (2)ゲートウェイ経由でクライアントが`temperature 0.7`を送る経路（OpenCode相当）の両方で
  `[spec-decode] 145 tok/s`になることを確認した（修正前はどちらも`[ar-decode]` 29 tok/s）。
- 自動検証: 既定ON、systemdを叩かない軽量ルックアップ、payload上書き、OFF時の素通し、
  llama.cppに影響しないこと、の回帰を追加。backend全体は873件成功。

## LuceboxをLLMランタイムへ追加／llama.cppをROCm 10へ統一（2026-09-04）

- **Model画面のLLM共通設定にLuceboxを追加**した。`souten-yd/AMDLucebox`が配るRadeon AI PRO R9700
  （gfx1201）向け`dflash_server`をControlDeck管理下のランタイムとして扱う。llama.cppと同じく
  systemdユーザーユニットで常駐させ、OpenAI互換endpoint（`http://127.0.0.1:<port>/v1`）として公開する。
  「このPCで利用するランタイム」には対応GPUがある環境と導入済み環境にだけ出す。
- **Addonからの初期セットアップと更新**を`gpu-runtime`という新しいfeature種別で用意した。
  `llama-cpp`（`souten-yd/llama-builder`）と`lucebox`（`souten-yd/AMDLucebox`）の2件で、導入は
  このPCで動く構成を既定値のまま揃え、更新は導入済み構成を最新リリースへ上げる。実体の取得・展開・
  current張り替えは`models_mgmt`側が持ち、`app/features/gpu_runtime.py`はアドオンUIへつなぐ
  アダプタに徹する（並行するインストーラ基盤を作らない）。
- **llama.cppのROCmビルドをROCm 10へ統一**した。`llama-builder`のROCmターゲットは
  `amdrocm-core-dev10.0-gfx1201`でビルドされるため、`ROCM_SERIES_MAJOR = 10`として明示し、
  ホストのROCmユーザースペースがメジャー違いなら導入前・選択前に警告する（止めはしない）。
  導入・更新は固定タグではなく常に最新リリースを解決する（従来は`llama-gpu-b10001`固定で、
  更新導線そのものが無かった）。タグ比較は`b<数値>`の数値順で行う（`b9544 < b10001`）。
- **ゲートウェイをランタイム非依存**にした。`models_mgmt/local_llm.py`がllama.cppとLuceboxの
  インスタンス列挙・起動保証・residency keyを束ね、`gateway.py`／`resource_provider.py`／
  `providers.py`／`provider_adapters.py`／OpenCode連携はそれを通す。クライアントから見ると
  1アドレスに両ランタイムのモデルが並び、モデル名（alias）だけで切り替わる。GPUリースは
  1つのbroker provider（`local-llm`）が両方を代表する（同じ1枚のGPUを取り合うため）。
  共有KVを持たないLuceboxではKV空き待ちを行わない。
- **モデル一覧と登録画面でランタイムを明示**した。登録は最初にランタイムを選ばせ、llama.cppは
  GGUF 1本、Luceboxはターゲット+DFlashドラフトの組という別々のフォームにする。一覧では
  `LUCEBOX`／`DFLASH`バッジと、行ごとのランタイム名・ビルド種別・ポートを出す。優先度の
  入れ替えはランタイム内で閉じる（カタログが別のため）。
- **Luceboxの初期値はAMDLuceboxの実測プロファイル**（`--draft-block-size 16`、`--max-ctx 131072`、
  `--cache-type-k/v q8_0`、`--fa-window 2048`、`--ddtree-budget 22`、port 8216）。初期トラックは
  ROCm 10（`DEFAULT_TRACK = "rocm10"`）で、ROCm 7.2.4は比較用リファレンス兼退避先として選べる。
- 配布物の取得・検証・展開は`models_mgmt/gpu_release.py`へ集約した。SHA256SUMSがあれば必ず
  突き合わせ、展開先の外を指すリンクとパストラバーサルは拒否する。共有ライブラリのsoname
  リンク（`libggml-hip.so` → `.so.0`）は配布物に必ず含まれるので、リンク自体は拒否しない。
  `.tar.zst`はzstd CLIへ委譲する（`shell=True`は使わない）。
- 実機検証: installed ControlDeckを再起動し、アドオン導入経路で
  `lucebox-298031aa-r1 / ROCm 10`（SHA256照合あり・23MB）を導入、推奨既定のまま
  `lucebox-qwen38`（Qwen3.8-27B-UD-IQ4_XS + qwen38-dflash2-q8_0）を登録して起動した。
  ゲートウェイ`/v1/models`に`runtime: "lucebox"`付きで並ぶこと、`model: "lucebox-qwen38"`および
  `model: "auto"`（stream）でLuceboxへ到達し`spec_decode_ran: true`で応答することを確認した。
  OpenCode 1.18.24を`OPENCODE_CONFIG`付きheadless（`opencode run`）で実行し、既存設定
  （base_url=ゲートウェイ、model=`auto`）のままLuceboxが応答した（llama.cppのunitは全停止状態、
  Lucebox unitのログに該当リクエストの完了記録あり）。ROCm 10ビルドはホストROCm 7.2.1でも
  `/opt/rocm/lib`の`libamdhip64.so.7`等を解決して起動したため、警告は出しつつ導入は止めない設計にした。
- 自動検証: `test_gpu_release.py`／`test_lucebox_runtime.py`／`test_gpu_runtime_addon.py`／
  `test_llama_update.py`／`test_local_llm_gateway.py`を追加し、backend全体は869件成功／1件skip。
  同時に失敗する`test_addon_runtime_resources_jobs.py` 4件は本差分前のtree（HEADのworktree）でも
  全体runで同じく失敗する既知の順序依存で、本差分の影響ではない。frontendは`npm run build`成功。
  ブラウザ実機確認とHosted CIはNOT TESTED。
- 既知の制限: LuceboxはROCmトラックとホストROCmのメジャーが揃っている前提を検証しきれていない
  （本機はROCm 7.2.1でROCm 10ビルドが動いたが、これは保証ではない）。Luceboxは共有KVを持たない
  ため、LLM利用状況ウィジェットのKV/slot表示はllama.cppのエンドポイントのみを対象とする。

## OpenCodeがゲートウェイ経由でローカルLLMを使えない不具合（2026-09-02）

- Add-on agent toolを1つでも有効にしたOpenCodeセッションが、ゲートウェイ経由のローカルモデルで
  一切生成できず、同じ要求を約0.4秒間隔で無限に再送し続けていた。TUIには何も表示されないため
  「ゲートウェイからLLMを利用できない」としか見えない状態だった。
- 原因は2つある。
  1. 制約付きデコード（llama.cppのJSON schema → GBNF変換）が`minLength`／`maxLength`を「文字ルールの
     繰り返し回数」へ展開する。Add-on schemaの`maxLength: 100000`（`sonic.pipeline`の`input.text`）と
     `maxLength: 8000`（`media.generate`の`intent`）だけで「ルール数 × 繰り返し数」が上限を超え、
     `Failed to initialize samplers: failed to parse grammar`となる。tool定義は全リクエストへ載るため、
     該当toolを1つ公開しただけでそのモデル宛の生成が全滅する。実測では単独でも400になった。
  2. ゲートウェイのstream中継が転送先のstatusを見ずに本文だけを流していたため、この400が
     「200 + 空ストリーム」としてクライアントへ届いていた。OpenAI互換クライアントはこれを空応答と
     解釈して再送するので、原因が表に出ないまま無限ループになる（`AGENTS.md`のエラーを握り潰さない
     規則にも反する）。llama.cppのログには17,000件超の同一エラーが積もり744MBに達していた。
- 修正1: モデルへ出すtool schemaから、展開できない長さ制約を落とす`model_facing_schema()`を
  `app/addons/execution.py`へ追加し、`agent_mcp_tools()`（OpenCode等のMCPクライアント向け）と
  `agent_tool_definitions()`（ControlDeck自身のチャット／ワークフロー向け）の両方へ適用した。
  ControlDeck自身が組み立てる`control_deck.project_output_grant`にも同じ写像を通す。落とすのは
  モデルへ渡す複製だけで、実際の上限は`create_agent_tool_job()`の`validate()`が引き続き強制する。
  Add-onはサードパーティなので、文法にできる形で書かれている前提を置かずHost側で正規化する。
- 修正2: `app/models_mgmt/gateway.py`のstream経路を、本文を流し始める前に応答行を確認する形へ変えた。
  転送先が4xx／5xxならOpenAI互換のエラー本文（先頭2000文字、モデルalias付き）をそのstatusで返す。
  非streamも、転送先がJSONを返さなかった場合に「空の成功」に見えないようにした。OOM記録は両経路で行う。
- 実機検証: installed ControlDeckを再起動し、失敗していた組み合わせ（Hanabi project、Add-on MCP有効、
  `controldeck/auto`、Qwen3.8-27B）でOpenCode 1.18.24を実行した。修正前は2分で文法エラー1,272件と
  step 0件だったものが、修正後は`read`を2回callして日本語で回答し、文法エラーは0件だった。
  公開中のMCP tool 11件すべてが単独でも同時でも200になることを実endpointで確認した。
  stream経路の伝播は、意図的に文法化できないtoolを出すMCPサーバを繋いで確認し、OpenCodeが
  `statusCode 400`／`isRetryable false`の`AI_APICallError: Qwen3.8-27B: Failed to initialize samplers:
  failed to parse grammar`を1回報告して終了することを確認した（従来は無限ループ）。
- 自動検証: 回帰3件を追加し（公開schemaから長さ制約が落ちること、元schemaを壊さないこと、
  stream失敗が200の空ストリームにならないこと、エラー本文の上限）、backend全体は819件成功／1件skip。
  同時に失敗する`test_addon_runtime_resources_jobs.py` 4件は本差分前のtreeでも全体runで同じく失敗し、
  単独では成功する既知の順序依存で、本差分の影響ではない。frontend sourceは変更していないため
  frontend build／browser／Hosted CIはNOT TESTED。

## Add-on outputのcross-filesystem atomic commit（2026-08-26）

- Media Forgeのinstalled Agent placementで、Host stagingが`/data1tb`、選択したexport grantが
  `/home`にある場合、中央staging fileからgrant先への`os.replace`が`EXDEV`となる汎用不具合を実再現した。
- upload済みbytesをgrant directory内の0600 exclusive temporary fileへcopyし、fsync後に同じdirectory fd内の
  hard linkでno-replace publishしてtemporaryを除去する。これにより別filesystemでもatomic publishを維持し、
  symlink／directory inode再検証、同名上書き拒否、checksum境界は変更しない。
- Add-on runtime files／execution集中31件成功。backend全体は814件成功／1件skip、変更外の共有Job state
  4件と固定1秒resource scheduling 1件が順序実行で失敗した。前者を含むresource集中12件はclean processで
  成功し、後者も単独再実行で成功した。実Media Forge Agentによるcross-filesystem commitとcommitted
  bytes／receipt hashの一致はMedia Forge側のinstalled acceptanceで確認する。

## setup_required Add-on UI到達性修正（2026-08-26）

- 汎用Release Bundle Featureで署名済みSonicForge v0.1.0を隔離dataへfresh installしたところ、serviceは
  `setup_required`で正常稼働しnavigationも残る一方、Hostが全non-navigation contributionを除外するため、
  Add-on自身のSpeech Essentialsセットアップ画面へ到達できない不具合を実再現した。
- `setup_required`中はnavigationに加えて`embedded_views`と`settings`だけをeffectiveに残し、commands、
  quick actions、Workflow/Agent/Contextなどの実行surfaceは引き続き除外する。個別UI contributionが
  明示的に`unavailable`なら、その指定を優先する。
- Add-on registry/API/proxy集中22件成功。修正Hostと実導入済み公開bundleを使った認証済みChrome 320x720で、
  初期セットアップ前のfull workspace、JA/EN切替、`Studio / Library / Jobs / More`、event WebSocket、
  document/body横overflow 0、failed/CORS/console error 0を確認した。

## SonicForge v0.1.0 signed catalog admission（2026-08-26）

- SonicForge PR #6のmobile full-workspaceを含むmerge commit
  `62532efbabc02463bad1163af6eb0c5a76c9dc3a`から、初回公開Release v0.1.0を作成した。
  Linux x86_64 artifactは29,970,963 bytes、SHA-256は
  `da4a50ffa317fc1bda3ed87a95032a50a83e61470b5e49f1354d87ca7f0efd48`。
- canonical manifestはfeature/version/platform/architecture/artifact名/SHA-256/sizeを束縛し、repository外の
  SonicForge Ed25519 publisher keyで署名した。公開Releaseから4 assetsを別directoryへ再取得し、署名、
  adjacent checksum、GitHub asset digest、実artifactの全てを一致させた。catalogはpublisher公開鍵を信頼し、
  per-release `sha256` pinやSonicForge固有provider分岐を追加していない。
- 公開bundleはpackaged `doctor`、Add-on/feature version 0.1.0一致、`mobile: embedded`を確認した。
  隔離dataで汎用Release Bundle Feature providerからfresh installし、managed/installed v0.1.0、service healthy、
  Add-on enabledを返した。初期Speech Essentials未導入時のUI到達性不具合はprovider-neutral PR #243で修正した。
- #243 Hostと実導入済み公開bundleの認証済みChrome 320x720で、`Studio / Library / Jobs / More`、JA/EN、
  event WebSocket、document/body横overflow 0、failed/CORS/console error 0を確認した。隔離featureは汎用uninstallで
  service/unit/managed filesを撤去し、既存`/data1tb/ControlDeck/data/sonicforge`を変更せずsource serviceをhealthyへ復帰した。
- #243取り込み後、catalog/release/Add-on contract集中36件、canonical backend全件818件成功／1件skip。
  認証済み実ChromeのSettingsを320x720／1280x800で確認し、SonicForgeが`未導入`・`検証済みbundle`・有効な
  `導入`buttonとして表示された。document/body横overflow、failed response、HTTP 400以上、console errorはいずれも0件だった。
- Catalog PR #244をmerge commit `99b2e5bd4f78837da1e6774143958b4eb8eb3335`としてactive checkoutへ取り込み、
  並行中の`frontend/tsconfig.tsbuildinfo`を保持して`control-deck-web.service`を再起動した。live `127.0.0.1:8765`の
  feature APIはSonicForgeを`available=true`／`not-installed`として返し、認証済みChrome 320x720／1280x800の
  Settingsでも同じ導入候補を表示した。横overflow、failed response、HTTP 400以上、console errorはいずれも0件だった。

## Opaque Add-on frame認証修正（2026-08-26）

- Chromium 151ではsandboxed Add-on iframeのsubresource/API requestに通常の
  `SameSite=Lax` Host session cookieが付かず、SonicForge workspaceのHTMLだけが200、
  JavaScript/CSSが401となって画面が表示されない不具合を実再現した。
- 初回navigationで通常sessionを検証した後、Add-on audience・利用者・10分TTLに限定した
  `HttpOnly; Secure; SameSite=None` frame cookieを`/addon-frame/<addon-id>/`へ発行する汎用経路を追加した。
  upstreamへHost/frame cookie、Bridge nonce、Originは転送せず、従来どおりAdd-on service tokenへ交換する。
- opaque originのAPI requestは同一利用者のBridge nonceを必須とし、mutationとJSON requestのpreflightは
  `X-Control-Deck-Bridge-Session`と`Content-Type`だけを許可する。静的resourceはBridge handshake前でも
  frame cookieで取得でき、WebSocketはBridge subprotocolを継続使用する。
- 隔離Host `127.0.0.1:18766`と実SonicForge `127.0.0.1:9140`を使った認証済みChromeで、workspace表示、
  authoritative GET、voiceの一時POST/DELETEと後始末、event WebSocketを確認し、failed response、
  CORS/console errorはいずれも0件だった。
- Add-on proxy/Bridge/contract集中25件成功。canonical backend全件の初回は変更外のresource job timing testが
  1件だけ失敗（815件成功、1件skip）し、当該単独は成功。続く全件再実行は816件成功、1件skipだった。

## Media Forge v0.4.0 catalog admission／実update（2026-08-23）

- 公開Release v0.4.0のLinux x86_64 artifactは30,971,889 bytes。ローカルchecksumと
  GitHub asset digestはSHA-256
  `51ee55c1da6d491852e145d6e2fc42b1b46eccedfae50176b1a34d6ba5f98799`で一致し、
  targetはMedia Forge merge commit `5a340ac2a9729b1fd591e4287c7adfca1252ff4a`。
- PR #234は汎用trusted catalogのSHAだけを更新し、merge commitは
  `f716630b9067ca0d91ffe2722ac75ce849bb5ac6`。provider/model分岐、route、依存、capability、
  Media固有UI文言は追加していない。release-bundle／Add-on AI／contract集中28件は
  1.19秒で成功し、hosted CIは使っていない。

実Optional Feature Manager updateは12.75秒、max RSS 784,004 KiBで成功し、
`version=0.4.0`、installed／managed／enabled／healthy、`previous_version=0.3.2`を返した。
`current` symlink、`cdapp-feature-media-forge.service`のWorkingDirectory／ExecStartはすべて
`versions/0.4.0`を指し、main PIDは640027。rollback用に0.3.2と0.4.0の2版を保持する。
実HTTP `/health`はcontract 2.0／healthyで、core、ROCm runtime、R9700 gfx1201、model library、
diskをすべて`ok`とし、新しい`agent_tool:media.pack`を`available`と返した。

認証済みinstalled Chromiumのlight/dark両runでHost bridge `ready`、Host theme token、詳細モードの
reload保持、libraryへのHost route sync、390px mobile横overflow 0、60px tab、2列library、
console/page error 0を確認した。最初のrunは製品不具合ではなく、Media Forge側E2E fixtureが古い
3 preset固定を期待して停止した。現行設計の6 ratio preset + customを明示検証するよう修正後、
light/darkとも成功した。

更新後のHost `/api/v1/resources`はactive lease 0／waiting request 0／Media Forge lease 0。
Media Forgeは保存済みjob 41件中active 0、native image/video worker 0だった。H3 snapshotは更新前後
26,978,278,484 bytes、8,063,029,344-byte denoiser blobのSHA-256は
`cfe0795c00ab6e6ebf8c64fe4574f45a828e8a93e0876bca704e055662a9d7b8`で不変、asset fileは94件を
維持した。試験用`mf-e2e` password hashは各run後に元値へ戻し、作成したbrowser session 3件は
revoke済み。既存Media Forge data/model/cacheを削除していない。

## Media Forge v0.3.2 catalog admission／実update（2026-08-23）

- 公開Release v0.3.2のLinux x86_64 artifactは30,642,575 bytes、GitHub asset digestと
  ローカルSHA-256が
  `ec864154b9d5e79fdfee8f616d3b02bd74ac29f80fd10822ac676881de12e3d9`で一致した。
  targetはMedia Forge merge commit `676f8cce1e4ecdc929967a2923250440cc4de817`。
- PR #230は汎用trusted catalogのSHAだけを更新し、merge commitは
  `a47e4175e55f74d18b20bb5400b2fea96d048167`。provider code、route、依存、capability、
  Media固有UI文言は追加していない。hosted CIは使っていない。
- backend cwdからrelease-bundle／Add-on AI／contract集中28件成功（1.18秒）。repository rootから
  直接呼んだ最初のcontract実行は、test内subprocessの`app` import cwdを外して2件失敗したが、
  canonical cwdで全件成功し、code変更は不要だった。

実Optional Feature Manager updateは23.59秒で成功し、`version=0.3.2`、installed／managed／enabled／
healthy、`previous_version=0.3.1`を返した。`current` symlink、service WorkingDirectory、ExecStartは全て
`versions/0.3.2`を指し、systemd main PID `474013`でactive/running。rollback用に0.3.1と0.3.2の
2版を保持している。実HTTP `/health`はcontract 2.0／healthy、core、ROCm runtime、R9700 gfx1201、
model library、diskの全setup check `ok`だった。

認証済みChromiumでinstalled workspaceを開き、bridge `ready`、H3 card／評価button／新しいworkspace内
dialog、console/page error 0を確認した。`/api/v1/resources`のactive lease、Media Forge active lease、
waiting requestは全て0件。Media Forge DBのactive model operation／local jobも0件、H3 native workerも
0件だった。

feature-data境界は更新前後で不変。H3 snapshotは26,978,278,484 bytes、denoiser blob hashは
`cfe0795c00ab6e6ebf8c64fe4574f45a828e8a93e0876bca704e055662a9d7b8`で一致し、`.downloads`は空。
Media Forge assets／models／feature data／shared cacheは削除していない。本体にMedia固有code、route、
依存を入れない完成条件を維持した。

## Media Forge v0.3.1 catalog admission／実update（2026-08-23）

- 公開Release v0.3.1のLinux x86_64 artifactは30,641,380 bytes、GitHub asset digestと
  trusted catalog pinはSHA-256
  `1b15eaa58f477bce982c3dcc3b093518c84382d11f8fc6bb7a9e1afe47dd30c4`で一致した。
  PR #227はこのcatalog dataだけを更新し、Media固有route、provider分岐、依存、UI文言は追加していない。
- 最初の実updateは、bundle manifestが要求する既存のprovider-neutral capability
  `ai.inference`がcatalog allowlistに無く、`package requests capabilities outside the trusted catalog`
  でcurrent切替前にfail closedした。`current`はv0.3.0を維持し、停止していた旧serviceを直ちに
  healthyへ戻した。PR #228は当該標準capabilityをcatalog allowlistへ追加しただけである。
- 訂正後の`./deck.sh feature update media-forge`は13.2秒以内に成功し、version 0.3.1、installed／
  managed／enabled／healthy、`current -> versions/0.3.1`、`previous_version=0.3.0`を返した。
  systemd MainPID 421078のExecStartは`versions/0.3.1/bin/mediaforge serve`で、実HTTP `/health`は
  contract 2.0／healthy、R9700 gfx1201、ROCm Torch runtime、model library、diskをすべてokと返した。

ローカル集中gateはrelease-bundle／Add-on AI／contract 28件成功（4.17秒）。最初に別worktreeの
venvを`PYTHONPATH`なしで使った実行はsubprocess内の`app` importに2件失敗したが、
`PYTHONPATH=backend`を指定した正しいworktree実行では全件成功した。hosted CIは使用していない。
認証済みworkspaceのH3評価ボタンを押した後のHost Job／Broker／GPU実行はMedia Forge側の次の実測であり、
このcatalog更新の成功には数えていない。

canonical `./deck.sh test`の初回runは752件成功／1件skip／1件失敗（60.06秒）で、変更外の
resource job直列化テストが1秒のpoll期間内に`queued`から`running`へ移らなかった。単独再実行は
1件成功（0.05秒）、続くcanonical全件再実行は753件成功／1件skip（62.20秒）だった。

## Add-on AI Gateway実受け入れ／llama.cpp VISION登録判定（2026-08-22）

- PR #224で追加した汎用`ai.inference` bridgeを実機受け入れまで完了した。llama.cppの新規GGUF登録時は、
  許可root内で解決したモデルと同じdirectoryにある通常fileの`*mmproj*.gguf`だけを最大32件、決定的順序で検出する。
  symlinkと別directoryは候補にせず、検出してもVISIONは初期無効のまま、利用者が明示的にONにした場合だけ
  `mmproj_path`を保存する。モデルpath変更時も再び無効へ戻す。
- 共通モデル一覧へprovider identityではなく`vision_enabled`だけを投影し、llama.cppは`mmproj_path`、既存Ollamaは
  `vlm_enabled=true`の場合に一覧へ`VISION` markを表示する。Add-onのrequest／responseには従来どおりruntime、model、
  port、projector pathを公開しない。
- 35B+mmprojの初回実行で、32GiB GPUの空き34,148,831,232 bytesに対しcold-load低信頼推定を理論総VRAM
  34,208,743,424 bytesへcapしていたため、driver使用59,912,192 bytesだけで`insufficient_vram`となる問題を再現した。
  capを現在のobserved使用量とheadroomを引いた実admitted上限へ変更し、resident sizeにはmmprojも加えた。
  OOM実測recommendationは引き続き下げず、モデル本体＋mmproj＋headroom自体が収まらない要求はfail closedを維持する。

実process検証: 通常のControlDeckを利用者許可のもと停止し、PR headの隔離ControlDeckを`127.0.0.1:18776`、
llama-serverを`127.0.0.1:18096`で起動した。実modelは
`/data1tb/LLM/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-Q4_K_M.gguf`（21,166,757,728 bytes）、
同一folderの`mmproj-F16.gguf`（899,283,680 bytes）を使用し、Ollama推論は使用していない。登録判定は候補1件、
`enabled_by_default=false`、登録直後の`mmproj_path`空／`vision_enabled=false`、明示ON後のVISION available／mark trueを返した。
有効な一時Add-on service tokenからmodel fieldなしの`text.generate`を送り、応答は正確に`PR226_TEXT_OK`だった。
続く`vision.analyze`は168,170-byteの512x512 PNGを「濃紺背景のオレンジ色のロボット」と正しく記述した。

同じVISION request（canonical SHA-256
`f315c8a8e7232ca72be3a71b0d30df8eea7012f78fa7e67779edcf3ddb315997`）のまま、Host内部の稼働aliasを
`pr226-qwen36`から`pr226-qwen36-alt`へ切り替え、両方で同じ画像認識結果を得た。response keyは両方とも
`content`／`capability`だけでidentity非公開、Add-on AI audit 5件のmetadataはcapabilityだけ、llama Broker lease
5件はすべて`released`だった。先行runの`insufficient_vram`は修正後に同じ要求が即`granted/active`となった。

実Chromiumは1280x800／320x700の両方でVISION mark 2件、同folder projector検出、初期OFF、明示ONを確認した。
document／body／dialogの横overflowはすべて0、認証後console error／page errorも0件だった。
自動検証はVISION／AI／Broker集中49件、追加のadmission集中24件、frontend production build 1,542 modulesに成功した。
canonical全件の最初のrunは新testの一時llama configがsession内に残り、後続runtime-policy test 1件を汚染して
752件成功／1件skip／1件失敗となった。configをtest固有pathへ隔離後、当該2件は成功した。最終canonical rerunは
753件成功／1件skip（58.18秒）だった。

## Media Forge v0.3.0 catalog admission／実update（2026-08-22）

- 公開GitHub Release v0.3.0のLinux x86_64 artifact（30,781,945 bytes）をrelease pageから
  独立に取得し、GitHub asset digest、隣接checksum asset、取得ファイル、trusted catalogの4者を
  SHA-256 `d8055331b96befc3de2bbf99cb3823ad6a5159158a0fab6521f40f8d719aa48f`で一致させた。
  release公開だけでは導入許可にならないこと、毎releaseでcatalog pinを更新すること、独立download照合と
  実updateの記録を必須にするchecklistを`docs/design-release-bundle-features.md`へ追加した。
- backend registry／Settingsはtrusted catalog全体を正としていた一方、`deck.sh feature`だけが
  `opencode|pyinstaller`を別allowlistにしてMedia Forgeを拒否していた。shell側の固定一覧を除去し、未知IDの
  fail-closed判定はcatalogを読む`app.features.cli`へ一本化した。Media固有route、依存、provider分岐は追加していない。

実機検証: 稼働中v0.2.4から、同じ汎用Optional Feature Managerのupdateを実行した。9.90秒、最大RSS
787,656 KiBでv0.3.0／healthyとなり、`current -> versions/0.3.0`、実systemd PID 198210、実行path
`versions/0.3.0/bin/mediaforge`を観測した。旧`versions/0.2.4`はrollback用に保持され、さらに古いv0.2.3は
retention 2に従ってprovider管理領域から除かれた。serviceと同じ固定環境でpackaged doctorは
`status=ok, version=0.3.0, packaged=true`、実HTTP `/health`はcontract 2.0／healthyを返し、R9700 gfx1201、
PyTorch 2.10.0+ROCm 7.2.1、model libraryをすべてokと報告した。Add-on registryの実manifestもversion 0.3.0、
enabled、既存host capability grantを維持した。

更新前後で外部model-managementは15,975,821,813 bytesのまま、永続feature dataは4,731,433,128から
4,731,441,264 bytesとなった。差分8,136 bytesはprovision statusと既存SQLiteの更新で、asset APIは既存30件、
model APIは既存FLUX.2 Klein 4Bをavailableとして返した。providerが保持したdownload artifactも30,781,945 bytes、
上記SHA-256と一致する。`./deck.sh feature status media-forge`の実公開CLIはv0.3.0／healthyを返した。

自動検証はrelease-bundle／Add-on contract集中22件成功（0.92秒）、canonical `./deck.sh test`は
751件成功／1件skip（59.33秒）。これらは上記実process／HTTP証拠の代替にしない。既存ControlDeck sessionの
E2E資格情報がこの実行環境へ渡されていないため、更新後の実Settings画面を認証済みbrowserで開く確認は
NOT TESTED。v0.3.0 bundle単体の実browser確認はMedia Forge側で済んでいるが、Host Settings確認とは混同しない。

## Media Forge v0.1.2 catalog pin／direct GPU placement 実証（2026-08-22）

- trusted catalog の Media Forge artifact SHA-256を、公開GitHub Release v0.1.2の
  `855303fd90e25e2ff2886b255fd98365c862eb417e0e57268cb0e8a27c06916c`へ更新した。
  artifactは29,043,648 bytesで、release assetのdigest、download後の実ファイル、catalog pinが一致する。
  provider／API／route／依存へMedia固有処理は追加せず、catalog dataとinstalled-bundle browser assertionだけを
  更新した。bundle内にvenvとmodel weightsは含めず、既存の永続runtimeと共有NVMe cacheを再利用する。

実機検証: 隔離ControlDeck `127.0.0.1:18773`の標準Settings API経路で公開v0.1.2を導入した。
install job `0e2d7361ca49`は18.375702秒で終了し、`current -> versions/0.1.2`、実systemd service、
Add-on v2登録、HTTP health `healthy`を観測した。healthはR9700/gfx1201、PyTorch 2.10.0+ROCm 7.2.1、
model installed/healthyを返した。packaged provisionは永続runtime/modelを再利用し4.972467秒で完了した。

同じ標準導入serviceをControlDeck Workflow／Broker経由で実行し、Host execution 9、Media Job
`job_5f6dfb71e62c4c44909fdf56f081a78e`が13.327802秒で512x512 PNG（168,170 bytes、SHA-256
`9f644dfc60d63f51b14858fd01bd34b45f400c682019565cd001484ee48b7037`）を生成した。service logの内訳は
model load 9.265287秒、generation 1.483399秒で、pipeline／text encoder／transformer／VAEはすべて
`cuda:0`、offload hookとCPU／disk／meta device mapは0件だった。Broker lease
`c0b3ea2c-bd77-4f2e-a141-6d9dbfd73740`はactiveを経てreleasedとなり、終了後active leaseは0件だった。
50ms間隔の外部samplingで観測したVRAM増分は17,416,728,576 bytesだが、瞬間peakを捕捉する保証はないため
admission用の既存実測peakの代替にはしない。provenanceはFLUX.2 Klein 4B、実weights hash、Apache-2.0、
Diffusers 0.40.0、Media Forge 0.1.2とvalidator passを含んだ。

実ChromiumはSettingsのv0.1.2／導入済み（無効）表示から有効化し、Media iframeのModels、Create、Library、
Provenanceまで16.2秒で成功し、page errorは0件だった。Host Job `85504437fc9c`は12.888887秒で成功し、
Media Job `job_fbf824b1cb4243338d417b8bd193d7a2`、asset
`asset_75be0d4e823d49228a3f624eea56988c`（159,515 bytes、SHA-256
`75d169f5bc1529e7bd292e3cc3e158f7327ce2323f581b428b80ba86dabe6f56`）を返した。load 9.013070秒、
generation 1.458398秒で、全componentは`cuda:0`、offload hookとnon-GPU placementは0件、終了後のBrokerは
active 0／waiting 0だった。

完全に空のHugging Face cacheから約15.99GBを再downloadする試験はNOT TESTEDである。公開bundle download、
SHA検証、warm runtime/model再利用、実GPU生成は検証済みだが、既存cacheを空cacheの証拠として扱わない。
最終PR headの自動回帰はrelease-bundle／Add-on contract集中22件成功（0.92秒）、backend全738件成功／1件skip
（62.18秒）、frontend production build 1,542 modules成功（19.79秒）、installed-bundle browser E2E 1件成功
（16.2秒）だった。これらは上記実process／browser証拠の代替にしない。

## release-bundle persistent provisioning／実update rollback（2026-08-21）

- 検証済みbundleが同じentrypointの固定array argsで永続runtime/modelを準備できる、任意の
  `provision_args` lifecycleをpackage schemaへ追加した。APIからcommand／環境／pathは受け取らず、実行環境、
  cwd、最大7200秒timeoutはproviderが固定する。provision失敗は`current`を変更せず、feature data/shared cacheへの
  中間成果だけを再試行用に保持する。子processへ渡すOS環境はallowlist化し、ControlDeck service token等を継承せず、
  stdout/stderrをhost memoryへ無制限にcaptureしない。Media Forge v0.1.1のcatalog pinは
  `66dfb88425d61e533e5ca8b45e0e19169e07e66cbc9ba1846364de4177981d4a`へ更新した。

実機検証: 公開v0.1.0がenabled/runningの隔離ControlDeck `127.0.0.1:18773`からSettings update jobを開始した。
処理中は`current -> versions/0.1.0`と旧PID 1599352を維持し、37.914599秒後に29,041,267-byteの公開v0.1.1、
`versions/{0.1.0,0.1.1}`、`current -> versions/0.1.1`、新PID 1627743へ切り替わった。隔離feature dataへ
4,686,979,949-byteのROCm venvをsource checkoutなしで構築し、共有NVMeのpip/model cacheを再利用した。
healthは`healthy`、R9700/gfx1201、PyTorch 2.10.0+ROCm 7.2.1、model installed/healthy、Add-on enabledを返した。

実rollbackは、公開不健康releaseを作らず、新serviceのhealth gateだけを1回fault injectionした。providerは
v0.1.1へswitch後に失敗を受け、`current`、実systemd service、Add-on manifestをv0.1.0へ戻し、PID 1635963で
RUNNING、Add-on enabledを維持した（10.4秒）。その後の通常API update jobはv0.1.1/healthyへ復帰した。
これは実version tree／systemd／registryを通るrollback証拠であり、自然発生したhealth failureとは区別する。

標準導入bundleだけを使う実ControlDeck Workflow/Broker生成は、先行LLM残留による`insufficient_vram`待機後に
leaseを取得し、6回renew、release、512x512 PNG（128,589 bytes、SHA-256
`9a1920654a48007c4917385d05af43a82d144803c66c19cefb906a9e93be962e`）を生成した。provenanceはFLUX.2 Klein 4B、
Apache-2.0、Media Forge 0.1.1だった。実ChromiumのSettings disable→enable→Media iframe→Create→Library→
Provenanceも19.5秒で成功し、Host Job `a8c8f00183db`、別の512x512 asset 159,515 bytes、lease release、page error 0を
観測した。完全に空のHugging Face cacheからの約15.99GB model downloadだけはNOT TESTEDで、cache reuseと混同しない。
最終PR headの自動回帰はrelease-bundle／Add-on contract集中22件成功（0.91秒）、backend全738件成功／1件skip
（61.79秒）、frontend production build 1,542 modules成功。これらは上記実process／browser証拠の代替にしない。

PR #217 merge後、そのexact provider内容を隔離hostで再起動して、環境allowlist強化後の実lifecycleも確認した。
install job `de4cf085cfd3`はpersistent warm runtime/cacheから11.521913秒でv0.1.1／healthyとなり、実serviceは
R9700/gfx1201、ROCm 7.2.1、model installed/healthyを返した。続く実uninstallはservice/Add-on/current/versionsを
除去し、feature dataを4,687,592,191 bytes／8 filesのまま保持した。これにより、強化前の実E2Eをtestsだけで
最終実装へ外挿していない。完全cache-emptyの15.99GB downloadは引き続きNOT TESTEDである。

## Trusted release-bundle Optional Feature provider（2026-08-21）

- Settings の標準導入経路へ、source-controlled trusted catalog だけを入力とする汎用
  `release-bundle` provider を追加した。API はfeature ID以外のURL／repository／version／command／hash／
  destinationを受け取らない。HTTPS allowlist、bounded download、catalog固定SHA-256、safe tar、package manifest、
  Add-on v2 manifestとcapability上限を検証し、version side-by-side配置、relative `current` symlinkのatomic切替、
  user systemd service、loopback health、Add-on registry登録を順に行う。
- 同一version再導入はimmutableなversion treeを置換せず、停止serviceとAdd-on登録を修復する。live service／healthを
  Settings状態へ反映し、Feature enable/disableはAdd-on v2 registryと同期する。削除対象はprovider管理下の
  service、Add-on登録、current、versions、downloads、logsだけで、`feature-data/<id>`と共有cacheを保持する。
- Git clone/source build、caller指定source、in-place update、release checksumだけを信頼する案は採用しなかった。
  一般提供bundleの更新には、新artifact digestをcatalog reviewで明示的に承認する必要がある。
  詳細は`docs/design-release-bundle-features.md`に記録した。実装はMedia固有のroute／Python依存／処理分岐を持たず、
  Media Forgeはcatalog dataとしてのみ登録した。

実機検証: PR headの隔離ControlDeckを`127.0.0.1:18773`で起動し、Settings API jobから公開GitHub Release
`souten-yd/ControlDeckMediaForge v0.1.0`を取得した。29,035,125 bytesを4.29秒で導入し、実ファイルSHA-256
`bcbde4514c6a5ef8d1c226509a4ca6afccb888e159fca10f9cb3fa933835c87e`がcatalog pinと一致した。
`versions/0.1.0`、`current -> versions/0.1.0`、bundle内`mediaforge-core`のuser service（peak 111.5 MiB）、
HTTP 200 `setup_required`、Add-on v2 `installed_disabled`を観測した。serviceを停止して同一version update jobを
実行するとserviceが復帰し、version tree内のmarkerを保持、Add-on enabled状態も保持した。uninstall後はunit、
Add-on、versions、downloadsが消え、feature dataとshared cacheのmarker各20 bytesが残った。再導入後の実Chromium
（1280x800）はSettingsのv0.1.0／非PREVIEW／無効表示、reloadなしの有効化、Media navigation、Hostの
setup-required画面を2.1秒で確認し、page error 0だった。

自動検証: release-bundle／Add-on contract集中17件成功（0.92秒）、backend全733件成功／1件skip（57.35秒）、
frontend production build 1,542 modules成功、installed release bundle browser E2E 1件成功（2.1秒）。
異なる新versionを用いた公開release updateと、実systemd serviceの意図的health failureによる旧version rollbackは
適切な第二の署名済みrelease／安全なfault injectionがまだないためNOT TESTED。rollbackのcurrent／Add-on復元は
隔離fixtureの自動testで確認済みだが、実process証拠の代替とは扱わない。bundle導入直後は重量image runtime未構築のため
Media Forgeは正しく`setup_required`であり、ローカルGPU画像生成までを本項の完了とはしない。

## Add-on Runtime 長時間resource jobのservice token rotation（2026-08-21）

- 通常のservice tokenは10分TTLのまま維持し、`resources.acquire`を許可された有効なAdd-onが、
  同じowner／subjectに束縛されたactive Host Jobとcurrent resource leaseを両方持つ間だけ、lease IDを
  proofとして同一scopeのservice tokenへrotationできる汎用Runtime APIを追加した。actor userと
  request単位grant allowlistをそのまま保存し、別owner／別subject、終了job／lease、disable後はfail closedする。
- 8時間tokenへの一律延長、active jobだけをproofにする案、期限切れtokenの猶予復活は、漏洩時の影響時間と
  不要な権限延命を広げるため採用しなかった。credential本文、grant ID、responseはaudit metadataへ保存せず、
  lease／job IDだけを記録する。Media固有のroute、依存、capabilityは追加していない。

実process検証: 隔離config／SQLiteのControlDeckを`127.0.0.1:18771`、別processの汎用fake Add-onを
`127.0.0.1:9130`で起動した。公開HTTP APIでinstall／enable、Host Job作成、実AMD GPU Broker requestの
`granted`を確認後、credentialをrotationした。新tokenは旧tokenと異なり、subjectと`grant:e2e` allowlistを
維持し、新tokenだけでleaseのactivate／renew／releaseが各200になった。auditはrefresh 1件、metadataは
job IDだけで、旧／新tokenとgrant IDを含まないことを確認した。検証後は両processと隔離dataを停止・回収した。

自動検証: Runtime Resources／Auth集中21件成功（2.65秒）。別worktreeから共有venvを直接呼んだ先行全体runは、
worktree直下`.venv`不在によるTerminal Automation 2件と、固定待機に依存する既知の排他Job 1件が失敗した。
worktree直下からcanonical `./deck.sh test`を再実行し、最終headで725件成功／1件skip（58.08秒）。frontend sourceは
変更しておらず、frontend build／browser／Hosted CIはNOT TESTED。

## OpenCode current-project output grant（2026-08-23）

- OpenCodeのjob／TUI runtime configが、CodeDEV直下で解決済みのProject Lab project IDだけを署名済み
  Agent MCP tokenへ束縛するようにした。project外やproject内のnested cwdにはclaimを付けない。
- 汎用MCP tool `control_deck.project_output_grant`を追加した。現在のRBAC、effective agent tool、Add-on enable、
  `projects.pick`／`files.export` grantを毎回再評価し、current project内の既存subdirectoryだけを短命
  Runtime export `grant:`へ変換する。project root、絶対path、traversal、backslash、symlink脱出を拒否し、
  応答／auditへraw host pathを出さない。Media固有のroute、provider、model、配置規則は追加していない。
- Agent tool service tokenはschema検証済みinput内の`grant:`だけを最大8件まで委譲する。grant無しを
  空allowlistとして明示し、同じownerの別要求grantを暗黙利用できないようにした。
- 実受入: installed ControlDeckをbranch codeで再起動（PID 594021）し、既存のenabled Media Forgeと
  Project Lab `Hanabi`を使って公開loopback Agent MCPを実行した。778msの一巡でMedia tool 3件と汎用Host
  tool 1件を発見し、`js/scenes`に対するexport grantがopaque `grant:`、`kind=export`、`name=scenes`だけを
  返しpathを含まないことを確認した。project claim無しtokenではHost tool非掲載、project rootとtraversalは
  ともに422だった。audit 3件はaddon ID、project ID、directory depthだけで、token、grant ID、raw pathを
  含まなかった。実output commitとOpenCodeによるcode参照更新はMedia Forge側の後続G4 sliceで確認するため、
  このHost prerequisiteではNOT TESTED。
- 自動検証: Agent MCP／Add-on execution集中32件成功（最終1.52秒）。canonical `./deck.sh test`は2回とも
  755件成功／1件skip／既知の固定1秒排他Job直列化test 1件失敗（各59.52秒、59.34秒）だった。同testは
  直後の単独10回すべて成功したため、全suite中のscheduler遅延として記録し、成功へ読み替えない。
  frontend source、browser、Hosted CIはNOT TESTED。

## OpenCode Add-on MCP long-running tool timeout（2026-08-23）

- generated OpenCode MCP configのclient timeoutを10秒から135秒へ変更した。Host Agent Jobの120秒上限と
  stdio bridgeの130秒HTTP上限が先にterminal結果または明示timeoutを返すための汎用整合であり、特定Add-on、
  model、providerの判定は追加していない。
- G4実受入の先行runでは、Media Forge生成callが10.006秒と10.005秒で
  `MCP error -32001: Request timed out`になり、upstream JobはまだBroker待機中だった。
- timeout修正後、installed HostとOpenCode 1.18.18の実runで、同じMCP経路の`media.generate`が
  107.901秒で成功した。続く`media.inspect`、`control_deck.project_output_grant`、`media.pack`も同一runで
  成功し、73,238 byteのPNGをopaque grant経由でprojectへcommitした。生成configは0600、timeout 135000、
  Add-on responseとauditにtoken、raw path、model/provider identityがないことを確認した。
- focused regressionは`backend/tests/test_addon_agent_mcp.py` 7件PASS。browser、Hosted CIはNOT TESTED。
- canonical `./deck.sh test`は755 PASS / 1 SKIP / 1 FAIL（66.06秒）。失敗は本差分外の
  `test_large_output_is_offloaded_downloaded_and_deleted`が全suite中の待機後に期待outputを得られなかったもの
  で、直後の単独再実行は1 PASS（0.77秒）。full suiteのFAILは成功へ読み替えない。

## OpenCode Add-on Agent Tool MCP投影（2026-08-21）

- OpenCode featureのjob／TUI専用0600 runtime configへ、現在有効なAdd-on `agent_tools`を投影する汎用
  stdio MCPを追加した。Host cookieや内部moduleは渡さず、最大8時間の署名済みuser-bound tokenを使い、
  Hostが各discovery／callでactive user、`workflows.run`、effective contribution、schemaを再評価する。
- tool callは既存のowner付きHost Job経路へ流れ、`job_id`、opaque `asset_id`、bounded outputを返す。
  公開tool IDは一意なら維持し、衝突時だけAdd-on IDでnamespaceする。Media固有のroute、tool、依存、
  capabilityはControlDeckへ追加していない。
- ユーザー／グローバルOpenCode config変更、Host DB直結bridge、cookie転送、起動時tool snapshot固定は、
  外部環境汚染、権限境界、disable反映の理由で採用しなかった。詳細は
  `docs/design-addon-platform-v2.md` §9.4へ記録した。

実機検証: 隔離config／SQLiteのControlDeck `127.0.0.1:18770`、別processのMedia Forge
`127.0.0.1:9134`、外部OpenCode 1.18.18を使用した。`opencode mcp list`で
`controldeck_addons connected`、実stdio MCPで`media.capabilities`／`media.generate`／`media.inspect`の3件を
発見し、`media.capabilities` callがHost Job `4aa6c2f74ae9`とopaque asset
`job-result:4aa6c2f74ae9`を返した。生存中tokenのままAdd-onをdisableするとtool 3件から0件、再enableで
0件から3件へ戻った。実`opencode run`は19.5秒、exit 0で
`controldeck_addons_media_capabilities`を1回callし、Host Job `7966ff194635`の結果から`available`と応答した。
最初の`auto`実行は27B model起動後5分でtool call未到達のため中断し、成功とは記録しない。

自動検証: Agent MCP／OpenCode／Add-on execution／token集中42件成功（2.54秒）。正しい`backend/` cwdの
backend全体は最終code headで722件成功／1件skip（57.43秒）。先行runはrepository rootから起動したためCLI subprocess
2件が`app` import不可となり、既知の固定1秒GPU Job直列化testもrunner開始前にtimeoutしたが、正しいcwdの
集中10件と最終全体runでは再現しなかった。frontend sourceは変更していないため、Hosted CI、frontend
build、browser再実行はNOT TESTED。実証用Add-onは公開APIでdisable／uninstallし、Host／Media process、
今回起動したQwen3.8-27B instance、18770／9134／8097 listenerを停止した。token入りruntime configとcookieは
削除済みである。

## Add-on Context Action own-route遷移（2026-08-21）

- Context Action結果の汎用`action=open_route`を追加し、成功後にHost routerで画面を開けるようにした。
  backendはrouteを呼出し元Add-on自身の`/x/{addon_id}`名前空間へ閉じ込め、scheme／authority／fragment／
  制御文字、Host route、他Add-on route、未知actionを`invalid_context_response`で拒否する。actionを含まない
  既存結果objectはそのまま返すため後方互換である。Media固有route／action／依存は追加していない。
- 成功toastだけ、frontendでの無条件navigate、Media固有`open_workspace`はそれぞれ操作未完了、権限境界、
  Host core汚染の理由で採用しなかった。契約と判断は`docs/design-addon-platform-v2.md` §9.3へ記録した。

実機検証: 隔離config／SQLiteのControlDeckと別processのMedia Forgeをloopback起動し、実Chromiumで
FilesのPNGからContext Actionを実行した。Hostは実Runtime read grantで画像を渡し、応答後URLが
`/x/media-forge/workspace/create`へ遷移してopaque iframe bridgeがreadyになった。15.162338秒の一巡で
handshake中iframe非表示、CreateからHost Job成功、Workflow dry-run時Media Forge Job増分0、実Workflow
`SUCCEEDED`、Brokerの2 Job直列化`device_busy_exclusive`／queue 1、disable時のcontribution撤去、保存済み
Workflow保持、再enable後asset保持、320px companionを観測し、console error／page errorは0だった。
終了後は一時WorkflowとAdd-onをdriverが削除し、Brokerはactive 0／waiting 0、4 leaseすべてreleasedだった。

自動検証: addon execution／Runtime files集中22件成功、backend全717件成功／1件skip（58.11秒）、
frontend production build 1,542 modules成功（19.35秒）。
これは回帰証拠であり、上記実process／browser観測の代替には扱わない。外部hosted CIは未実施。

## Add-on Runtime 委譲actor／要求単位grant scope（2026-08-21）

- service tokenの相関subjectと利用者権限を分離し、Hostだけが署名する`actor_user_id`を追加した。
  `job:*`の既存Job束縛は維持し、Workflow／Context ActionはactorからHost Job ownerとgrant ownerを
  解決する。既存の数値subjectは後方互換として残し、browser proxy／Agentもactorを明示する。
- 要求単位の`grant_ids` allowlistを追加した。claim無しは既存token互換、空集合はgrant利用不可、
  非空集合は完全一致だけを許可する。Context Actionのraw fileはHostが実在する短命Runtime read grantへ
  変換し、pathやAdd-onが検証不能な疑似tokenをupstreamへ渡さない。Workflow execution ownerを解決
  できない場合もAdd-onを呼ばず403でfail closedする。
- 詳細な権限表、後方互換、却下した5案は`docs/design-addon-platform-v2.md` §9.2へ記録した。Media Forge
  固有のroute、依存、capabilityはControlDeckへ追加していない。

実機検証: code head `2ec4dbb`を隔離config／SQLite、Uvicorn `127.0.0.1:18767`で起動し、別processの
Media Forgeを`127.0.0.1:9131`で起動した。公開HTTP APIだけを使う17.722796秒のrunで、install／enable後に
Workflow／Context contribution各1件を発見、dry-run時のMedia Forge Job増分0、実Workflow execution
`SUCCEEDED`、委譲actorで作ったMedia Forge Job `succeeded`を確認した。Files context actionは1206 bytesの
PNGをHost UI相当のraw path入力から実Runtime `grant:`へ変換してAdd-onが内容を読み、応答へpath／grantを
反射しなかった。加えて実AMD GPU Brokerで335544320 bytes予約、2 Job直列化
`device_busy_exclusive`／queue 1、Host cancel、10.647061秒Job中のrenew 5回、active中disableによるJob cancel、
全lease release、再enable healthy、1206 bytes scoped read／output commitのSHA-256一致を観測した。
runのfinallyで一時Workflowを削除しAdd-onをuninstallした。

自動検証: Add-on Runtime／execution／proxy／contract集中53件成功。完全checkoutの先行backend全体runは
705件成功／1件skip／固定待機に依存する既知の2件が失敗し、同2件の直後単独runは2件成功した。
最終backend全体rerunは707件成功／1件skip（58.90秒）。frontend production buildは1,542 modules成功
（20.76秒）。frontend sourceは変更していない。外部hosted CIはlocal実機証跡の代替にしない。

## Add-on Runtime Host API（Authentication／Resources／Jobs／Files）実装完了（2026-08-21）

- Browser Host BridgeをGPU／backend execution APIへ拡張せず、`/api/v1/addon-runtime`にservice-to-host専用境界を追加した。通常のsession cookie、CSRF、`settings.manage`は使わず、Bearer service tokenのHMAC署名、10分TTL、`kind=service`、`aud`・path・`X-Control-Deck-Addon-ID`一致、installed／enabled、操作別Host Capability grantを毎request検証する。HMAC keyはAdd-onへ渡さず、`POST /token/introspect`が無効理由を漏らさない`active`応答、subject、期限、現在grantを返す。全mutationとintrospectionはtoken／payload／host pathを含めずauditする。
- `resources.acquire` APIはservice schemaから`owner`を削除し、Hostが常に`addon:{addon_id}`を設定する。Host Jobとtoken subjectを照合し、request／leaseのownerを全操作で再検証する。priority ceilingはinteractive 30、agent-interactive 25、workflow 15、background／batch 0、maintenance -10とした。disable／uninstallは新規requestとrenewを拒否し、waitingだけcancelする。active leaseは空き扱いにせず、serviceの明示releaseまたは30秒TTL expiryまで予約を維持する。
- `jobs.write` APIは`sub=job:{id}`ならControlDeckがすでに作ったAgent等のHost Jobへattachし、数値user subjectならAdd-on UI起点のHost Jobを新規作成する。Add-onはowner、addon ID、created_by、kindを指定・変更できない。同一Add-on/userのactive外部Jobは8件までとした。PATCHはphase必須、progress単調増加、通常update 2Hz以下、message／wait reason／terminal result 16KiB以下に限定した。disableは関連Jobをcancelし、serviceは無効化後もcontrolをpollして停止要求を認識できる。
- Host pickerはbrowser内だけのUUID/path mapを廃止し、user／addon／capability／permission束縛の短命`grant:`をHostへ登録する。Runtime APIはmetadataと最大1GiBのbounded content streamだけを返し、pathは返さない。read grantはrealpath、device／inode／size／mtimeを再検証する。outputはexport grant＋Host Jobへ束縛したprivate staging、宣言size／任意SHA-256、safe filename、directory fd／inode再検証を通してatomic commitし、`asset:` IDだけを返す。symlink escape、inode swap、同名上書きを拒否する。

検証: Runtime auth／Resources／Jobs／Filesと既存Add-on／Broker／Jobs／executionの集中49件成功。canonical `./deck.sh test`の最終rerunは699件成功（1件skip）、frontend production buildは1,542 modules成功。canonical先行runは既知の排他Job直列化testが最初のrunner開始前に固定1秒判定へ到達して697 PASS／1 FAILだったが、同test単独連続10回と最終canonical rerunはすべて成功したためhost scheduling遅延と分類した。隔離config、別Uvicorn host process、別fake Add-on process、cookieなしservice clientで、introspection active、user subject Job作成、job subject attach（重複Jobなし）、実AMD GPU Broker request→grant→activate、grant content read、output stage→commit、Job progress→succeeded、lease releaseを確認した。続くdisable caseではexclusive active lease＋waiting requestを作り、2秒grace後にJob canceled、waiting canceled、renew 409、新規request 409、active leaseは保持されたまま明示release 200、introspection inactiveとなることを確認した。一時Add-onをuninstallし、両processと隔離dataを停止・回収した。

## LLM reload cost分離・永続化 完了（2026-08-21）

- llama.cppのload profileをresidency keyごとにcold／warmへ分離した。起動後初回、stop記録なし、またはstopから900秒超過はcold、同じkeyのstopから900秒以内の次のload 1回だけをwarmとする。yield判断はwarm 3件を優先し、未達時はcold 3件、どちらも未達なら`yield_load_cost_unknown`で保守的に抑止する。推定値・catalog値へのfallbackは追加していない。
- profileは`data_dir/resource-load-profiles.json`（schema version 1）へload計測時だけatomic保存し、起動時に30日超過、破損、schema／型不一致を安全に無視する。keyごとのsample数とファイル全体をboundedにし、telemetry clearは永続fileも削除する。`GET /api/v1/resources`はcold／warmのp50・p90・count、yield basis、実際のthresholdを返す。
- managed設定画面にbasis、p90とsample数、推定実行時間のthreshold、直近の抑止理由、cold bootstrap説明と「今すぐ退避」を追加した。Brokerの抑止enumは同じyield可能blockerが続く間保持し、待機Jobの`wait_reason`へrevision追従で反映する。`estimated_runtime_sec`は要求元申告のままで、未申告requestはyieldを誘発しない。supervisionの既定は`observed`を維持した。

実機検証: code head `dee0a22` を実`control-deck-web`へ反映し、32GB AMD GPU／NVMe上Qwen3.8 27Bを`supervision=observed`でGateway cold loadした。最初のprofileはprocess 1.052秒＋model load 21.239秒＝cold 22.291秒、count 1、basis insufficientだった。Web PID `1030276`→`1031535`の再起動後も同じresidency key、cold p90 22.291秒、count 1をAPIから復元し、Broker request／active lease 0を確認した。

clean profileでControlDeck起動後初回のcoldを3件実測し、cold p90 6.206秒、basis cold、暫定threshold 12.412秒を確認した。その後managedへopt-inし、各回20GiB exclusive requestが`yield_runtime_unknown`で待機すること、手動「今すぐ退避」後にstop→grant→releaseとなること、続くGateway loadがwarmになることを3回繰り返した。warm countは1／2の間basis coldを維持し、3件目にbasis warmへ切り替わった。最終warm p90は6.128秒、yield thresholdは12.256秒で、旧cold混在値166秒ではない。最終profileはcold 3件／warm 3件として再起動後も復元した。

実Chromiumの320×700と1280×800でwarm実測、`6.1 秒（warm p90、サンプル 3 件）`、`推定実行時間が 12.3 秒を超えるジョブ`、`処理時間が未申告`、手動退避buttonをlive APIで表示した。document／body／dialogの横overflowは双方0、認証後console error／page errorも双方0。検証後は`supervision=observed`、LLM loaded 0、Broker request 0／lease 0へ戻し、warm profileの永続だけを残した。初回検証profileは`/tmp/controldeck-resource-load-profiles-first-run.json`へ退避して保全した。

自動検証はresource profile／Broker／Llama provider／無改変KV capacity／Jobs persistenceの集中52件、canonical `./deck.sh test` 683件成功（1件skip）、frontend production build（1,542 modules）に成功した。canonicalの先行2回は既存の排他Job直列化testが最初のrunner開始前に固定1秒判定へ到達して各1件失敗したが、同test単独＋連続10回、Jobs suite 11件、集中52件、最終canonical全件はいずれも成功した。

## Add-on Platform v2 PR-E Workflow／Agent／Context 統合完了（2026-08-21）

- executable contributionの共通境界を追加した。discoveryはinstalledではなく、enabled・contribution available・利用者permissionをすべて満たすものだけを返す。schema取得はredirect非追従、5秒、64KiB、Draft 2020-12 object schemaに限定し、実行はrequest 1MiB、response 4MiB、120秒、JSON objectに限定した。ControlDeck sessionのCookie／Authorizationは送らず、audience束縛service tokenだけを使う。disableとのraceは送信直前にも再検査し、失敗／成功をpayloadなしactivityへ記録する。
- Workflow node type `addon.workflow:{addon_id}:{contribution_id}`をnode catalogへ動的登録し、schemaからhost側設定UIを生成する。dry-runはcache済みschema検証とtemplate解決だけでAdd-onを呼ばない。実行はinput/output両schemaを検証し、execution/node correlationを付ける。保存済みnodeはAdd-on停止／disable後も定義と接続を保持してunavailable警告を表示し、publish／runだけをfail closedにする。
- Agent toolは実行Workflowの作成者と現在permissionを解決してLLM tool一覧へ動的追加する。各callはowner付きdurable Jobとして実行し、agentへは`job_id`、`job-result:{job_id}`の`asset_id`、bounded outputだけを返す。timeout／client cancelはJob cancelへ伝播し、raw host pathを含む引数はschemaが許可しても拒否する。
- Context ActionをFilesとProject Labへ追加した。hostがfile realpath containmentまたはproject存在を検証し、Add-onへはfile pathではなく`grant:` ID、context種別、短命scoped tokenだけを渡す。Add-onへproject全体のpath／内容／ControlDeck credentialは渡さない。直接invoke APIと全操作の監査を追加した。

検証: implementation code head `aeeb34b` でaddon execution／Jobs persistence／LLM autoload／Workflow dry-run・catalog集中36件、canonical backend全671件成功（1件skip）、frontend production build成功。最終canonical初回は既存Broker直列化testが固定1秒内に最初のJobをscheduleできず1件失敗したが、同test単独＋連続10回がすべて成功し、canonical全体再実行も671件成功したためhost scheduling遅延と分類した。実`control-deck-web`をbranchから再起動し、別processのfake Add-onをloopback起動した実Chromium E2E（3.6秒）でinstall／enable、実schema discovery、remote Workflow dry-run（remote callなし）→draft execution `SUCCEEDED`、Agent owned Job＋asset参照、raw file pathから`grant:`への変換と非反射、320×700の「拡張機能」palette／overflowなし、1280×800のProject Lab Context Action、認証後console／page error 0を確認した。fake processを停止し、一時WorkflowとAdd-onを削除した。外部hosted CIはこのrepositoryに必須checkが設定されていない場合はUNAVAILABLEとして扱い、local gateの代替PASSとは記録しない。

## Add-on Platform v2 PR-D2 Llama／Jobs resource admission 完了（2026-08-21）

- resource-aware Jobは通常runnerへ入る前にBroker requestを作り、取得までは`status=queued, phase=waiting_resource`として永続化する。待機中はrunner slotを消費せず、grant後にleaseをactivate／renewし、成功・失敗・cancelの全terminal pathでrequest／leaseを解放する。進捗はphase必須、completed単調増加、通知2Hz・DB書込5秒を上限とし、従来のresource非依存Jobの契約は維持した。
- llama.cpp GatewayをBroker admissionへ接続し、既存KV capacity probeをprovider adapterの下で維持した。停止modelは保守的なVRAM要求、起動済みmodelはprovider reservationとして扱い、client disconnect、stream／non-stream完了、load timeoutの各経路でleaseを解放する。OOMは`resource_oom`へ正規化し、runtime/model/device別profileの推奨requirement floorと60秒cooldownを次回admissionへ適用する。
- supervisionは既定`observed`のまま、`gateway_only`、local NVMe、cold-load実測p90、最低常駐時間、推定処理時間、5分あたり2回のyield上限を満たす場合だけopt-in `managed`を許可した。現行llama-serverに動的unloadがないためlevel 4のprocess stopでdrainし、新規LLM requestはdrainを取り消す。設定UIは縮退条件と閾値を表示し、待機Job UIは固定wait reasonとcancelを表示する。

検証: code head `3d1ba907eb76` でcanonical `./deck.sh test`は664件成功（1件skip）、frontend production build成功、320×700の実Chromiumでmanaged既定OFF、opt-in警告・閾値表示、個別設定保存を確認した。1280×800でもmanaged既定OFF、横overflow 0、認証後console error 0を確認した。実32GB AMD GPU／NVMe上Qwen3.8 27BではGateway cold request 83.981秒、cold-load p90 83.038秒、first-token 0.733秒、load時VRAM 29,269,983,232 bytesを観測した。managed opt-in中に20GiB exclusive Broker requestを`device_busy_exclusive`で待機させ、LLM drain／stop後59,924,480 bytesまで解放してgrant／activate／release、続くGateway requestで7.851秒で再起動した。sample 2でもp90 83.038秒を維持し、検証後はpolicyを`observed`、LLM停止、Broker予約0へ戻した。managedの既定化と動的unloadは未提供であり、sample 2はthrash閾値を一般化する十分な分布とは扱わない。

## Add-on Platform v2 Jobs resource wait単独migration 完了（2026-08-21）

- 既存`jobs.status` enumと意味を変更せず、nullable textの`phase`／`wait_reason`だけを追加するAlembic revision `c83f7a19d2e4`を作成した。既存行は両列NULLのまま保持されるため旧client／queryは従来の`queued/running/succeeded/failed/canceled/interrupted`だけを見続けられる。downgradeは追加2列だけを除去する。
- migration単独PRではORM schemaだけを宣言し、Job service／API／runner schedulingの挙動は変更しなかった。後続PR-D2がこのschemaを使って`status=queued, phase=waiting_resource`、wait reason永続化、runner slot非消費、cancel連携を実装した。

検証: migration／Jobs集中17件成功。SQLiteで旧head `b41f7d90c655`からupgradeし、既存queued行、status、NULL値を保持したままheadへ到達し、downgrade後も行を保持した。PostgreSQL offline migrationも生成成功。実`control-deck-web`の正規startup migrationではverified pre-upgrade backup（SHA-256 64桁、Jobs 133行）を作成し、upgrade後もJobs 133行、phase／wait_reason非NULL 0、revision `c83f7a19d2e4`、service health 200を確認した。この節はmigration単独PR時点の記録であり、後続admissionの最終結果は上記PR-D2節に記録する。

## Add-on Platform v2 PR-D1 AI Resource Broker core 完了（2026-08-21）

- `backend/app/resources` に device collection、provider reservation/probe、決定的scheduler、request queue、lease acquire/activate/renew/release/cancel/TTL、telemetry、管理APIを追加した。要求はVRAM confidence／有限max wait／queueかfail-fastを必須とし、固定予約を差し引いて物理的に入らない要求はqueueへ残さず拒否する。interactive優先、background starvation上限600秒、owner fairness、bounded residency bonus、FIFO tieをテストで固定した。
- 単一GPUを特別扱いせずcollectionとして扱い、exclusiveとshared-safe、固定deviceとauto、fit scheduling、provider yield levelを分離した。monitor snapshotが別metricの失敗でpublishされない場合も、monitorが既に選択したproviderを再利用してGPU factsだけを更新する。Brokerのrefresh/reaper失敗はWeb本体を停止させない。
- `/api/v1/resources` は`system.view`、mutationは`settings.manage`＋CSRF＋本文を含めないauditで保護した。このPR-D1時点ではAdd-on disable完了／uninstall時に`addon:{id}` ownerのwaiting requestとleaseを取り消していたが、後続Runtime Host APIでactive workerとの二重割当を避けるためwaitingだけcancelし、active leaseは明示release／TTL expiryまで保持する契約へ更新した。Brokerはin-memoryのためprocess再起動時にstale leaseを持ち越さず、waitingはexecution slotを占有しない。
- observed Llama起動でprocess→listen、listen→ready、最初のtokenを実測し、bounded sampleのp50/p90だけをprofile化する。推定値fallbackは持たない。OOM incidentはruntime/model/device別に回数、observed peak、下げないrecommended requirement floorを記録する。leaseを使ったmanaged supervision、thrash guard、OOM retry制御、Jobs admissionはPR-D2まで有効化しない。

検証: code head `f3935e5` でresource／Llama／Gateway集中57件、canonical cwd=`backend`のbackend全651件成功（1件skip）、frontend production build成功。実`control-deck-web`をbranchから再起動し、sysfs-amdgpuの32GB device発見、実APIでexclusive grant→2件目`device_busy_exclusive`待機→activate／renew→waiting cancel→release、最終lease予約0とtelemetryを確認した。NVMe上のQwen3.8 27BをGatewayから1 token cold-startし、request 83.376秒、cold-load p90 82.714秒、first-token 0.565秒、load時VRAM 29,269,970,944 bytes、stop後59,912,192 bytesへの解放を実測した。現行llama-serverに動的unload操作はなく、yield level 3はlevel 4（process stop）へ縮退する。このPR-D1時点ではPR-D2／Jobs待機UIはNOT TESTEDだった。単独DB migrationと後続PR-D2の結果は上記各節に記録した。

## Add-on Platform v2 PR-C Embedded View／Host Bridge 完了（2026-08-20）

- `/x/:addonId/:viewId/*`をhost管理の埋め込み画面へ接続した。iframeは`allow-same-origin`なしのopaque sandbox、`frame-src 'self'`、同一hostの`/addon-frame/{addon_id}/*`だけを使い、loopback upstreamをブラウザへ露出しない。proxyはControl DeckのCookie／Authorization／CSRF／Origin／Refererを除去し、audience束縛・10分TTLのservice tokenだけを注入する。redirectを追従せず、Set-Cookieを除去し、request 16MiB／response 32MiB／60秒read timeoutを課し、SSEとWebSocketを中継する。opaque iframeのWebSocketがCookieを送らない実挙動には、Bridge nonceを専用subprotocolで渡してuser／addon／view／effective状態を再認証し、upstreamへnonceを転送しない形で対応した。
- Bridge 1.0をMessageChannelで実装し、source／opaque origin／user・addon・view束縛nonce、schema、capability、RBAC、120 calls/minute、16KiB request、明示error code、payloadを含めないactivity／auditを強制した。context／theme／route open・sync／title、host file・export pickerのview-local opaque grant、project picker、job open・subscribe、notification dedupe・rate limit、permission、busyをhost側で実行する。theme／locale／safe area／visibility／route／session更新と`disable.pending`をevent配信し、theme変更はiframeをreloadしない。file/projectのserver pathはAdd-onへ返さない。
- handshake前skeleton、8秒timeout＋再試行／設定導線、戻る・進む・reload・共有URL、Command Palette shortcut、Tab脱出、未保存表示、desktop embedded／mobile companionを追加した。disableはregistryの`disable_pending`をeffectiveに2秒残して開いているviewへ通知した後にcontributionを撤去し、状態ページへ置換する。並行enableはpending disableを取り消せる。

検証: 最終head `50aee5b` で frontend production build、Bridge／proxy／registry／API集中26件、backend全623件成功（1件skip）。実`control-deck-web`と別processのfake Add-onを使うChromium E2Eで、opaque iframe handshake、実WebSocket、theme無reload、routeの戻る／進む／reload／共有URL、Command Palette、notification、busy、file grantでpath非公開、project picker、Tab脱出、320px companion、silent viewの8秒timeout、2秒`disable.pending`後の状態ページ置換を17.8秒で確認した。既存PR-B E2Eも8.9秒で成功し、各run後に一時Add-onをuninstallした。PR-D1以降のBroker／Jobs統合はNOT TESTED（未実装）。

## Add-on Platform v2 PR-B UI Contribution／状態表示 完了（2026-08-20）

- 認証済みeffective contributionをrevision SSEで追従し、desktop sidebar、mobileのMore、Quick Actions、Command Paletteへhost描画でnavigation／command／quick actionを追加した。Add-onがhealthyでない場合もnavigationは維持し、degradedは黄、unavailable／incompatibleは灰、setup requiredは青の状態chipと理由を表示する。状態ページから理由、setup checklist、再確認、無効化、権限・詳細へ1操作で到達できる。
- SettingsのPlugin SDK v1とAdd-on v2を「拡張機能」へ統合した。v1互換操作は維持し、v2はmanifest登録、要求capability確認、enable／disable／recheck／uninstall、付与権限、warning、settings contribution、直近activityをhost UIで管理する。従来の本体内オプトイン機能は意味の衝突を避けて「オプション機能」とした。
- `/x/:addonId/:viewId/*`はPR-Bのhost-rendered状態／contribution一覧だけを表示する。command／quick actionは選択内容を表示するが実行せず、embedded view、Host Bridge、proxy、service tokenはPR-Cまで未実装であることを画面でも明示した。

検証: frontend production build、Playwright構文読込、backend全607件成功（1件skip）。実`control-deck-web`と別processの`tools/fake-addon`を起動し、実Chromiumでmanifest登録→権限承認→enable、320px More navigation／quick actionとoverflowなし、1280px sidebar／Command Palette、fake healthをdegradedへ変更→再確認→chip／理由表示、navigation維持、disable後のnavigation即時消去と同一window sentinel維持（再読み込みなし）を1本のE2Eで確認した。一時Add-onは各run後にuninstallした。PR-C以降の実行surfaceはNOT TESTED（未実装）。

## Add-on Platform v2 PR-A Registry／Effective Contributions 完了（2026-08-20）

- `data_dir/addons`へowner/mode/symlink/64KiB境界を持つmanaged registryを追加し、installed／enabled／effectiveを分離した。install直後はdisabled、enable時に要求capabilityを明示grantし、disable/uninstallはmanifest外のserviceやdataへ触れない。壊れた／major非互換manifestは消さず`incompatible`として一覧に残し、enable/effectiveを拒否しながらdisable/uninstall可能にした。
- healthは15秒、失敗時最大120秒backoff、3回失敗でunavailable、unavailableから1回成功、degradedから2回成功でhealthyへ戻す。HTTPSまたはloopbackだけを許可し、非loopbackは`addons.allowed_origins`の管理者allowlist必須、redirect非追従、3秒timeout、64KiB上限、schema検証とした。setup、理由＋action、contribution単位availabilityを保持し、1 serviceの失敗でhost loopを止めない。
- 認証済みAdd-on一覧／詳細／install／enable／disable／recheck／uninstall／activityとeffective APIを追加した。管理操作は`settings.manage`＋CSRF＋本文なしaudit、effectiveは利用者permissionでfilterし、ユーザー別ETagと`addons.effective.changed` SSE revisionを返す。enabled navigationはhealthで消さず、unavailableな実行contributionだけを除外する。未認証の`/meta`へAdd-on存在を追加していない。

検証: Add-on contract／registry／health／API／fake service／既存Plugin SDK v1の集中33件、backend全607件成功（1件skip）、Python compile、`deck.sh`構文、diff whitespaceに成功。実`control-deck-web`を本branchから再起動し、別processのfake add-onを実loopback起動。実APIでinstall→disabled、enable→healthy、全contribution＋ETag、videoだけunavailableのdegraded再確認、navigation維持＋video executorだけ除外、disable即時effective空、uninstallを確認した。一時Add-on／user／session／auditとfake processは清掃し、service active／health 200を再確認した。backend-only PRのためviewport確認は対象外。

## Add-on Platform v2 PR-0 Contract／Harness 完了（2026-08-20）

- Plugin SDK v1を`PluginManifestV1`として互換維持し、`api_version`でv1/v2をfail-closed dispatchするAdd-on contractを追加した。Add-on `2.0`、Bridge／Theme／Health `1.0`を固定し、contract range、既知host capability、固定contribution type、HTTPS／loopback runtime、相対endpointを検証する。未知の実行fieldは拒否し、未知presentational fieldだけはwarning付きで無視する。
- `./deck.sh ext lint <manifest>`を追加した。64KiB、通常file、実行user所有、other書込み不可を確認してからJSONとcontractを検証する。既存`./deck.sh plugin`はv1管理導線として維持する。
- `tools/fake-addon`に実FastAPI serviceを追加した。全contribution、4 health状態、部分availability、setup checklist、時間／VRAM指定とcancelを持つfake GPU job、Workflow／Agent／Context endpointを提供する。
- 通知はtoastのみ（Job履歴とauditを正）、service tokenはproxy側発行更新、Add-on情報は認証前非公開とPR-0で確定した。共通UX規約を`docs/addon-ux-guidelines.md`へ分離した。registry/install/enable/effective APIはPR-Aまで未実装であり、PR-0のlint成功を利用可能状態とは扱わない。

検証: Add-on contract／CLI／fake service／既存Plugin SDK v1の集中19件、backend全593件成功（1件skip）、`deck.sh`構文検査、`ext lint`実command成功。fake add-onを実loopback processとして起動し、health 200、degraded + video executor unavailable、fake GPU job 202を確認して停止した。

## Project Labプレビューの相対リソースが401になる問題（2026-08-19）

- HTMLプレビューは`sandbox`で不透明originへ閉じ込めているため、そこから出るサブリソース要求（`<script src="three.min.js">`等）はcross-site扱いになり、`SameSite=Lax`のセッションcookieが送られない。artifactエンドポイントはcookie認証なので401になり、外部ファイルを参照するプロジェクトだけ動かなかった。Chromeは送らずWebKitは送るため、デスクトップでは最初から動かず、モバイルからは動いて見えていた（実測: 200は14件すべてTailscale経由のモバイル、401は127.0.0.1と192.168.68.69）。
- 短命tokenをパスに含む配信経路`GET /project-lab/preview/{token}/{artifact_path}`を追加した。tokenはパスにあるのでHTMLからの相対参照にそのまま引き継がれ、cookieなしで配下を配信できる。token（Fernet・TTL 15分）はプロジェクト単位で、ダウンロードは不可、配下から出るpathは従来どおり弾く。`allow-same-origin`は付けない（ControlDeckのcookie／DOM／APIへ到達させないsandboxの前提を崩さないため）。

## CodeDEVの置き場をプロジェクトルート配下へ（2026-08-19）

- OpenCode と Project Lab が別々に`Path.home() / "CodeDEV"`を組み立てていたのをやめ、`config.codedev_dir()`に一本化した。未設定なら data_dir の親（=プロジェクトルート）配下の`CodeDEV`を使うので、この環境では`/data1tb/ControlDeck/CodeDEV`になる。data_dir の中には入れない（バックアップや設定JSONと混ざらず、ファイルマネージャ・ターミナル・Gitから普通のプロジェクトとして扱えるようにするため）。`config.yaml`の`codedev_dir`で上書きできる。
- 既存の3プロジェクト（Hello / Space3DInvader / test2）を`.git`ごと新しい場所へ複製し、Files の`allowed_roots`へ新パスを追加した。
## LLM接続先をゲートウェイへ集約（2026-08-19）

- チャット・AIアシスタント・ワークフロー・RAG／GraphRAG・Deep Research・OpenCode／OMoの接続先を、llama.cppの個別ポートからControlDeckゲートウェイ（`/api/v1/llm/v1`）1本へ寄せた。`/workflows/llm-endpoints`はゲートウェイをselectedで返し、llama.cpp系の個別エンドポイントはそこへ集約する（同じモデルが接続先違いで二重に並ぶと、選び方でモデル解決も受け入れ制御も変わってしまうため）。モデル管理画面の一覧には出さない（モデルを保有するのはllama.cpp側）ので、`list_providers(include_gateway=...)`で用途を分ける。
- 内部の生成は自分のHTTPへ戻らず、`runtime_provider.resolve_target()`（実体は`gateway.resolve_internal_target()`）でゲートウェイURLを実エンドポイント＋実aliasへ解決してから叩く。ホップを増やさずに、thinking解決・キャンセル・KV受け入れ制御・Deep Researchのctx拡張といった既存処理をそのまま効かせるため。
- 転送先の既定解決を「起動中のLLMを優先し、いなければ登録順の先頭」に変更した。従来は登録順の先頭を無条件に選ぶため、8091が稼働中でもOpenCodeの指名で8090が追加起動され、VRAMが尽きて双方が不安定になっていた（実測: 22:10起動→VRAM 91%／swap 2.4GB→SIGKILL）。
- 仮想モデル`auto`を追加した。クライアント側でモデルを固定せずに済み、そのとき動いているモデルへ流れる。OpenCode導入時の自動設定の既定も`auto`にした。OMoの並列数同期も同じ解決規則で転送先instanceを引く。

- アイドル監視の`_revive_endpoint_for_opencode()`が未定義の`base_url`を参照していた（例外を握って復活しない状態）ので、解決済みportから組み立てるよう直した。あわせてゲートウェイ経由の設定では復活処理自体をスキップする。リクエスト時にオンデマンド起動されるため、使っていない間に別モデルをロードする必要がない。

- 起動に失敗して再試行待ちのループ（systemdの`sub_state=auto-restart`）を`FAILED`として扱い、ログ末尾を`last_error`に添えるようにした。従来はモデル読込中と同じ`STARTING`だったため、UIが延々と「起動中…（読み込み待ち）」を出し続けていた（実例: MTP層を持たないモデルに`spec_type=draft-mtp`を設定して21回リスタート）。`/models/llama/capacity`は失敗中のモデルを`failed`として返し、ホームのLLM利用状況が理由付きで表示する。OMo行の対象モデルも`auto`のような仮想モデルを解決してから出す。

- LLM利用状況に生成速度を追加した。全slot合算の tok/s と、それを同時実行数で割った1本あたりを並べる。並列にしても1本あたりの速度は落ちないまま合算が伸びることが読める（実測: 2本 31.5 → 4本 44.9 tok/s、1本あたりは 18.4 / 18.2）。llama.cppの`predicted_tokens_seconds`は直近に完了した1リクエストの速度で、生成中は0のまま並列の効果も見えないため使わない。`tokens_predicted_total`も完了時にしか増えないので、進行中slotの`n_decoded`を足した累計から8秒窓の差分で算出する（直前1点との差分だと、画面を複数開いてポーリング間隔が詰まったとき「間隔が短すぎる」が続いて0のままになる）。

検証: backend 579件成功（新規7件: 起動中優先の解決／内部解決／provider選択／autoモデル／ゲートウェイ既定と非表示／revive のskipと起動先／auto-restartループのFAILED判定と読込中の区別）、frontend production buildに成功。

## 旧Application Builder削除とWorkflow実行UIの刷新（2026-08-13）

- C#／ASP.NET生成系を削除した（generator群・compiler・build worker・platform advisor・design proposal・patch service・関連schema／model／config／frontend画面／テスト）。合計13,266行を除去し、`flow_app`を`app/flow_app`へ移した。Workflow削除時の「Application Projectが接続されています」制約もなくなり、代わりに書き出し済みFlow Appを片付ける。
- Workflowエディタの実行導線を作り直した。`Test`と`Run`の2ボタンを廃し、**実行ボタン1つ**に統合。押すと1枚のシートで「入力 → 下書きで試す／公開して実行 → 結果」が完結する。公開を止める問題があるときだけ赤いバナーを出し、ノードごとの詳細は畳んでおく。
- 7区画（入力／回帰テスト／実行モード／想定出力／副作用／実行前チェック／公開前検証）を並べていた`PreviewWorkspace`（487行）を削除し、回帰テストは⋯メニューの「テストケース」シートへ分離した。
- 3点リーダーメニューを英語12項目から日本語の意味ごと（編集／実行／公開・配布／データ／詳細）に区切った一覧へ整理し、区切り線を`DropdownMenu`へ追加。キャンバス右上の常設`Intelligence`ボタンは撤去してメニューへ集約した。状態表示や案内文も短くした。

検証: backend全467件成功（削除した旧builderテスト42件を除く）、frontend TypeScript／production buildに成功。実サービスで`/api/v1/application-projects`が404になり、health 200を確認。

## App Studio 単一バイナリ書き出し／プレビューのstorage shim／chunk復帰（2026-08-13）

- 書き出し形式を2つから選べるようにした。`.pyz`（配布先にpython3が必要・1MB未満・1〜2秒）と`単一バイナリ`（配布先は追加インストール不要・約10MB・5〜20秒）。ビルド側だけに開発環境が要る構成で、PyInstallerは設定→アドオン「アプリビルド環境」から導入する。
- アドオン基盤をnpm専用からnpm／pip両対応へ広げた。pipのアドオンは専用venv（`data_dir/features/<id>/venv`）へ導入し、Control Deck本体のvenvを汚さない。route_gatedでないアドオンは導入＝利用可能とし、有効化とプラットフォーム再読み込みを求めない。Settingsのアドオン説明文もfeature定義から出す。
- 単一バイナリは1〜2分かかる想定でサーバー側jobとして実行し、UIは進捗表示のまま画面を離れられる。実測では約5秒・9.8MBだった。
- Project Labのプレビューで「表示されるのに操作できない」根本原因を特定して修正した。sandbox iframeは不透明originのため`localStorage`アクセスがSecurityErrorになり、そこでscript全体が停止して以降のイベント登録とゲームループが動かなくなっていた（実例: `stick-runner.html`の冒頭で`localStorage.getItem`）。`allow-same-origin`はControl Deckのoriginを渡すことになるため使わず、preview配信時だけcharset宣言の直後へメモリ実装のlocalStorage／sessionStorage／cookie shimを差し込む。ダウンロードと実体のfileは書き換えない。
- プレビュー下部のドックがアプリ自身の操作ボタン（例: 右下のJUMP）と重なるため、ドックを畳めるようにした。畳むと左下の小さな丸ボタンだけが残る。
- 再デプロイ後に開いたままの画面が消えたchunkを読みに行き`Importing a module script failed`で落ちる問題を直した。遅延ロード失敗時に1度だけ自動再読み込みし、Service Workerは404を保存しない（`res.ok`のみキャッシュ、shell v15）。

検証: 新規2件（バイナリ実行・アドオン未導入時の拒否）を含むbackend全509件、frontend TypeScript／production buildに成功。実機でアドオン導入（PyInstaller 6.22.0、4.4秒）→ 単一バイナリ生成（9.8MB、5.0秒）→ `PATH=/nonexistent`でCLI実行（`result: BINARY WORKS`）とGUI（`/`が200、`POST /api/run`が期待値）を確認。実`stick-runner.html`へのshim挿入位置がcharset直後かつアプリscriptより前であることを確認した。

## App Studio 方針転換：Workflowを単一実行ファイル（.pyz）へ書き出す（2026-08-13）

- App Studioの成果物を「ASP.NET Coreのソース＋.NET SDKビルド」から「配布できる単一実行ファイル」へ変更した。`POST /flow-apps/{workflow_id}/exports`が標準ライブラリのzipappだけで`.pyz`を生成する。追加SDK・コンパイラ・ネットワークを使わず、生成は1〜2秒。配布先の要件はpython3だけ。
- 中身はControl Deck本体の`app/workflows/nodes.py`をそのまま同梱し、DAG実行だけをDB・承認・ライブ表示なしで移植した`flowapp/runner.py`が駆動する。ノード意味論を二重実装しないため、C#生成で起きた`string.op`の`find`/`replace`不一致のようなズレが構造的に発生しない。httpx一式（純Python）を同梱するのでHTTPS通信もそのまま動く。
- 生成物は引数なしでローカルGUI（標準ライブラリのhttp.server＋同梱HTML、トリガー入力から自動生成したフォーム・結果表示・実行ステップ表示、モバイル対応／ダークモード対応）、`--input`でCLI、`--info`で入出力一覧、`--env-file`で環境変数を読む。LLMノードは`FLOW_APP_LLM_BASE_URL`／`FLOW_APP_LLM_MODEL`で外部のOpenAI互換endpointへ直接接続する配布版に差し替える。
- 携帯可能な30ノードをallowlistで静的判定し、Knowledge・ブラウザ操作・コマンド実行などControl Deck基盤が要るノードは理由付きで書き出しを止める。無効化済みノードは判定から除外する。成果物はdata_dir配下へ保存し（DB schemaは増やさない）、SHA-256とmetadataを併記、20世代で自動整理、削除と監査に対応する。
- App Studio画面をProject Labと同じ作法で作り直した。左上基点のワークフロー選択ポップオーバー、対応可否バッジと入出力サマリ、書き出せない理由の明示、成果物カード（サイズ・日時・SHA-256・実行コマンドのコピー・ダウンロード・削除）、下部フローティングドックの「書き出す」。旧C#/ASP.NET生成の導線（Create/Target/Export/Reviewの4タブ）はUIから撤去した（コードは削除前の据え置き）。

検証: 新規6件（allowlist判定・生成物の別プロセス実行・非対応拒否・API書き出し／一覧／ダウンロード／削除／traversal 404・capability・同梱元の存在）を含むbackend全507件、frontend TypeScript／production buildに成功。実サービス（health 200）で`/api/v1/flow-apps/capability`の登録を確認。実DBの実Workflow 4件を判定し3件が書き出し可・1件が`research.deep`で拒否となることを確認。100ノードの実Workflowを566KBの`.pyz`へ書き出し、リポジトリ非依存の別プロセスとして0.16秒で`SUCCEEDED`実行。デモWorkflowではCLI実行（`HELLO WORLD / http=200`：同梱httpxでの実HTTPS通信を含む）とGUI（`/`が200、`POST /api/run`が期待値を返す）を確認した。

## Project Lab 刷新：プレビュー修正・コード表示・単体実行（2026-08-13）

- HTMLプレビューが真っ黒だった原因を修正した。artifact配信の`default-src 'none'`とiframeの`sandbox=""`がscriptを全面遮断していたため、HTMLだけCSPの`sandbox allow-scripts allow-modals`で不透明originへ隔離したうえでinline script／style／img／mediaを許可し、`connect-src 'none'`でControl Deck APIへの到達を断つ。上位tabで直接開かれてもcookie・DOM・APIへ触れない。`download=true`とHTML以外は従来どおり実行不能のCSPのまま。
- `.py`／`.js`／`.ts`／`.css`／`.sh`／`.yaml`／`.sql`など30種のsource fileを`kind: code`として検出・表示できるようにし、`language`と`runnable`をAPIへ追加した。previewは既存のredactionとサイズ上限をそのまま通す。
- manifestを持たないprojectでも成果物のPython／JavaScriptを1本だけ実行できる`POST /project-lab/projects/{id}/file-runs`を追加した。既存profile実行とunit起動処理を共通化し、隔離（NoNewPrivileges／ProtectSystem=strict／ProtectHome=read-only／ReadWritePaths=project／MemoryMax 2G）、同時実行上限、監査、ログ取得、停止をそのまま再利用する。実行対象はPython／JavaScriptのみで、CSS等は409、project外pathは404で拒否する。
- UIを全画面ビューア構成へ作り直した。ページ自体はスクロールせず、上部48pxバー＋フル画面プレビュー＋フローティングドック（ファイル名チップ／再読み込み／実行／保存／その他メニュー）で完結する。プロジェクト・ファイル・情報・実行ログはボトムシート（PCは右パネル）で開き、ファイルシートは検索と「すべて／成果物／コード」の絞り込みを持つ。PCは左レールにプロジェクトとファイルを常時表示する。
- コードは依存を増やさない自前トークナイザで行番号付きハイライト表示（複数行コメント・文字列も追従）。表はsticky header、画像は市松背景、HTMLはフル画面iframe、`ソースを表示`でHTML本文も確認できる。
- HTMLプレビューは既定を実寸（枠と同じ幅）とした。論理幅1024pxへ固定して縮小するとページ側の`@media (max-width: 768px)`や`innerWidth`判定が外れ、モバイル用ボタンが出ずタップできなくなるため。全景を見たいときだけドックの「全体」でPC幅の縮小表示へ切り替える。sandboxは`allow-scripts allow-modals allow-forms allow-popups allow-downloads`まで許可する。

検証: 更新した既存3件と新規のcode artifact／HTML CSP／file-run argv・拒否ケースを含むbackend全501件、frontend TypeScript／production buildに成功。実サービス（health 200）の実`~/CodeDEV`に対しAPI経路で確認し、`hello.html`がscript込みで配信されること、`.py`／`.js`／`.css`が`code`として検出され`runnable`が正しいこと、一時projectのPython単体実行が`SUCCEEDED`／exit 0でログに`python file run ok (3, 12)`を出すこと、CSSの実行要求が409になることを確認した（一時projectは削除済み）。

## Settings アドオン更新ボタン（2026-08-13）

- Settings→アドオンの導入済みOpenCodeへ「更新」ボタンを追加した。`POST /features/{id}/update-jobs`が`settings.manage`権限のサーバー側jobで`npm install --prefix <管理prefix> opencode-ai@latest`を実行し、既存の導入jobと同じ進捗表示・トーストで完了を伝える。
- 更新できるのはControl Deckが導入した管理prefixだけ。PATH上の外部OpenCodeを使っている場合はボタンを出さず、APIも422で拒否して外部実体・`~/.config`・`~/.local/share`に触れない。有効/無効の状態は更新前後で変えず、ルート登録も変わらないためプラットフォーム再読み込みは不要。
- job結果へ更新前後のversionを載せ、`v1.18.3 から v1.18.16 へ更新しました（起動中のセッションは再起動後に反映）`、変化がなければ`すでに最新版です（v…）`をトースト表示する。更新中は同アドオンの有効化/無効化/削除を無効化する。
- `./deck.sh feature update opencode`（`app.features.cli`）からも同じ処理を実行できる。

検証: 更新の集中3件（`@latest`引数・更新前後version・外部導入拒否・endpointの404/422/201）を含むbackend全499件、frontend TypeScript／production buildに成功。実service（PID 895605、active、health 200）の実OpenCodeを1.18.3→1.18.16へ実際に更新し、更新後も`enabled: true`／`health: healthy`を確認した。ブラウザ上の「更新」ボタン操作は利用者が実機画面で確認した（自動E2E `frontend/e2e/addon-update.spec.ts` は追加済みで、`CONTROL_DECK_E2E_USER`／`CONTROL_DECK_E2E_PASSWORD` を与えれば320px／1280pxのボタン表示・クリック・完了トーストまで再現できる）。

## AI Workflow→App Studio GUI 3件再検証（2026-07-21 18:04 JST、1件不具合検出）

- 実Ollama `qwen3.6-27b-q5_k_m:latest`の`POST /chat/generate-workflow`へ、大文字変換、前後空白除去、文字置換の3要件を渡した。最初の3案は必須config欠落を意味検証がすべて拒否し、必須configを具体化して再生成した3案は警告0で登録された。入力付きdraft実行は大文字変換`hello`→`HELLO`、空白除去`  hello  `→`hello`が期待どおり成功した。
- 3 WorkflowからApp Studioの契約ベースGUIを自動構成し、各20 fileのASP.NET Core + Blazor source preview、network deniedの隔離systemd user Build Job、warning-as-error build、生成source self-testを実行した。Project #2〜#4／Build #1〜#3はすべて`completed`となり、source／binary artifactとSHA-256を取得した。
- ビルド済みDLLを管理対象.NET 8 SDKと明示content rootで専用一時systemd user unitへ起動した。App Studioの3 completed buildを320×700／1280×800で確認し横overflow 0。生成GUI自身の両幅でフォーム送信、完了表示、typed result、横overflow 0、console／page errorなしを検証し、大文字変換と空白除去は成功した。
- 文字置換だけはAIが`string.op`へ`old`／`new`を生成したが、実executor／UI／C# generatorは`find`／`replace`を要求する。現行のcatalog promptと意味検証がこの不一致を見逃し、Control Deck実行は入力を無変換で`SUCCEEDED`、生成GUIは空の`oldValue`によりHTTP 500となった。AI node catalogのconfig key、条件付き意味検証、Python／C# runtime parityを修正して再確認する必要がある。

検証: AI生成6応答（初回3件を正しく拒否、再生成3件を登録）、入力付きWorkflow実行3件、App Studio source preview／隔離build 3件、App Studio workspace E2E 1件成功、生成GUI E2Eは2件成功／文字置換1件で期待どおり不具合を再現。Control Deck Terminal／tmuxには接続・入力・停止・削除していない。検証用Workflow／Execution／Project／Build／build directory／一時unit／user／session／auditは終了後0件に清掃済み。

## Phase 1 ユーザー／Customロール／権限管理 完了（2026-07-21）

- `users.manage`権限を要求するユーザー一覧／作成／更新APIと、Role一覧／利用可能権限一覧／Custom Role作成・更新・削除APIを追加した。usernameは英字始まりの限定文字、passwordはArgon2idでhash化し、API／auditへpassword／hashを返さない。
- Custom Roleへ付与できる権限とユーザーへ割り当てできるRoleは、操作者自身が持つ権限のsubsetだけに制限した。`users.manage`だけを持つCustom Roleから`power.manage`を付与したり、administratorを無効化する権限昇格／妨害を403で拒否する。
- administrator／operator／viewer presetは変更・削除不可。最後の有効administratorは、同等権限を持つ別Custom Role操作者からも無効化・降格できない。自分自身のrole／有効状態／passwordは管理画面経由で変更せず、本人用password変更APIを使う。
- role、is_active、管理者password reset、Custom Role権限変更時は対象の全server-side sessionを失効する。利用中Custom Roleは削除不可。user.create／user.update／role.create／role.update／role.deleteを、username、変更field名、permission件数、session失効有無だけで監査する。
- SettingsへユーザーとRoleを同一sectionで追加した。各行は編集1操作だけを表示し、詳細はmobile bottom sheet／PC modal、Custom Role権限はscroll可能なchecklist、削除だけ確認dialogとする。管理UIは8.48KBの遅延chunkへ分離し、初期index chunkを598.31KBに維持した。

検証: Custom Role／user lifecycle、password reset、role権限変更時session失効、preset不変、自分自身変更禁止、最後のadministrator保護、権限昇格拒否、viewer拒否、監査を含む集中11件、backend全497件中Terminal系29件を除く最新468件、Python compile、frontend TypeScript／production build、diff whitespace検査に成功。実service PID `691854`、active、health 200。Playwright Chromium 320×700で`apps.view`だけのCustom Role作成、1280×800でuser作成・表示名／password更新、旧credential 401／新credential 200／Apps 200、横overflow 0、console／page error 0を確認した。一時user／role／session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 7 TOTP必須ポリシー強制 完了（2026-07-21）

- `security.totp_requirement`へ`optional`／`administrators`／`all`を追加し、要求仕様§19の任意／管理者必須／全ユーザー必須を実装した。既存`require_totp_for_admin: true`は後方互換で管理者必須として扱う。
- 必須対象がTOTP未設定でloginした場合、sessionはQR enrollmentのため発行するが、`me`／`totp/setup`／`totp/verify`／`logout`以外の認証RESTを403 `totp_setup_required`、全WebSocketを認証後4403で拒否する。TOTP設定完了後だけ通常のRBAC判定へ戻る。
- 必須ポリシー下ではTOTP無効化を409で拒否して`policy_denied`監査を残す。UIはログイン直後からSettingsへ固定し、mobile／PCとも専用の設定cardだけを表示する。ほかのnavigationとmetrics WebSocketを開始せず、設定完了後に通常Settingsとnavigationを復元する。
- `/auth/me`解決直後かつZustand反映前の1 renderだけ子routeが描画される競合を修正した。直URL `/apps`でも保護queryを発行せずSettingsへredirectするため、正しい403をbrowser consoleへ不要に発生させない。

検証: all policyのREST 403、WebSocket 4403、enrollment例外、設定後の通常API復帰、必須時無効化409／監査、administrator／viewer role scope、legacy互換を含む集中14件、backend全494件中Terminal系29件を除く最新465件、Python compile、frontend TypeScript／production build、diff whitespace検査に成功。実serviceへ一時的にall policyを適用し、Playwright Chromium 320×700／1280×800で直URL固定、専用card、設定前protected request 0／metrics WS 0、QR secretから実TOTP有効化、設定後API 200／metrics WS接続、無効化buttonなし、横overflow 0、console／page error 0を確認した。検証後は元のoptional設定へ戻し、実service PID `674593`、active、health 200。一時user／session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 1 パスワード変更／TOTP個別レート制限 完了（2026-07-21）

- 認証済み本人が現在のパスワードで再認証して新しいパスワードへ変更するAPIとSettings UIを追加した。新旧同一値を拒否し、成功時は現在の端末を含む本人の全サーバー側sessionを同一処理で失効、Cookieも削除して再ログイン画面へ戻す。
- password変更失敗は直接peer+user単位5回/15分、TOTP有効化確認と無効化はendpoint別に各5回/5分で制限し、超過は429+`Retry-After`を返す。成功／失敗／rate_limitedをpassword、TOTP code、secret、recovery codeなしで監査する。
- 既存のlogin／電源TOTPを含む失敗counterを最大20,000 keyへ有界化した。usernameを大量に変える入力でもprocess memoryへkeyを無制限に残さず、古いcounterから破棄する。
- Settingsはアカウント欄の主操作を小さな「変更」入口に保ち、mobile bottom sheet／PC modalで現在値、新値、確認値をpassword inputとして入力する。全端末からlogoutすることを操作前に明記する。

検証: password再認証、新旧同一拒否、全session失効、旧password拒否／新password成功、TOTP verify／disable各5回、429+Retry-After、成功／失敗／制限監査、有界counterの集中19件、backend全492件中Terminal系29件を除く最新463件、Python compile、frontend TypeScript／production build、diff whitespace検査に成功。実service PID `657112`、active、health 200。実一時userでpassword変更成功監査1件、変更前session全失効、変更後credential再loginを確認した。Playwright Chromium 320×700／1280×800でbottom sheet／modal、password型入力、logout遷移、横overflow 0、console／page error 0。一時user／login session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 1 API／download／WebSocketレート制限 完了（2026-07-21）

- `/api/v1`全体へ接続元別sliding-window上限を追加した。既定は一般API 5,000回/分、GET download／artifact 300回/分、WebSocket handshake 300回/分で、設定から変更できる。死活監視用health／metaは除外する。
- HTTP超過は429と`Retry-After`、WebSocket超過は認証処理より前に4429で拒否する。未設定の`X-Forwarded-For`等を信用せずASGIの直接peerだけをkeyにするため、外部から送った転送headerで上限を回避できない。
- limiterはthread-safeなprocess内sliding windowとし、接続元bucketを最大20,000件へ制限する。古いbucketから破棄するため、攻撃的なkey増加でmemoryを無制限に消費しない。ログイン／電源TOTPにある既存の個別制限は維持する。

検証: sliding window、retry秒、bucket上限、直接peer、health除外、download分類、WebSocket 4429の集中テスト11件、backend全489件中Terminal系29件を除く最新460件、Python compile、frontend TypeScript／production build、diff whitespace検査に成功。Web serviceは通常停止タイムアウト後PID `641315`でactive、health 200。Playwright Chromium 320×700／1280×800のHomeで通常APIとmetrics WebSocket、横overflow 0、429誤発生 0、console／page error 0を確認した。一時user／login session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 2／11 systemd journal・指定ログファイル 完了（2026-07-21）

- Logs画面のsourceへstdout／stderrに加えて、登録済みアプリのsystemd journalと最大16件の指定ログファイルを追加した。journalは2秒ごとの有界snapshot、指定fileは既存WebSocket追従を使い、検索・一時停止・折返し・copy・downloadを共通利用する。journalはControl Deckから削除できず、指定fileのtruncateだけ`logs.delete`権限と監査を要求する。
- journalはDBに登録済みの固定unit名とuser／system scopeだけから、固定`/usr/bin/journalctl`または`/bin/journalctl`、配列argv、`shell=False`相当、5秒timeout、最大2,000件／2MiBで取得する。API入力からunit／scope／任意argumentを受けず、取得不可は本文やcommand詳細を返さない503へ分離する。
- 指定fileはApp追加／編集で1行1件を保存し、登録時と読取時の両方で`Path.resolve()`＋`files.allowed_roots`／deny root検査を通す。存在するdirectory、許可root外、後から外部へ差し替えたsymlinkを拒否し、未作成fileも検証済み親と名前だけを保持する。全sourceは共通秘密値redactorを通す。
- Alembic revision `a73d9e4c2b18`で`managed_applications.log_files_json`を追加し、SQLite upgrade→downgrade→upgradeを確認した。

検証: log／App集中8件、backend全486件中Terminal系29件を除く最新457件、frontend TypeScript／production build、migration往復、固定journalctl argv、許可root外422、journal削除409、追加fileのAPI／WebSocket／truncate／redactionを確認した。実service PID `620267`、health 200。実APIで指定file付きappと`control-deck-web.service`参照専用appを一時登録し、Playwright Chromium 320×700／1280×800でstdout／指定file切替、指定file秘密値非露出、実journal表示、横overflow 0、console／page error 0を確認した。journal対象serviceの操作は行わず、一時app／file／user／login session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 2／11 ログ秘密値マスキング 完了（2026-07-21）

- 管理アプリstdout／stderrのtail API、WebSocketリアルタイム追従、downloadの全経路へ共通redactorを追加した。`TOKEN`／`SECRET`／`PASSWORD`／`PASS`／`API_KEY`／`PRIVATE_KEY`／`AUTH`／`COOKIE`系の`key=value`、header、引用符付きJSONを`***`へ置換する。
- アプリの暗号化environmentから秘密名の値だけを復号し、ログ内にキー名なしで出た同一値もマスクする。値はAPI、WebSocket、download、audit、内部logへ追加保存せず、4文字未満の値による全体過剰置換も避ける。
- WebSocketとdownloadはbyte chunkを最大1MiBの行bufferへ入れ、改行単位でマスクしてから送る。秘密名／値が256KiB chunk境界を跨いでもraw断片を送らず、1MiBを超える改行なし行は本文を破棄して固定の省略表示だけを返す。downloadはraw `FileResponse`を廃止し、redact済みstreamだけを返す。

検証: log redaction／alert集中16件、backend全483件中Terminal系29件を除く最新454件、frontend production build、diff whitespace検査に成功。通常代入、Authorization、引用符付きJSON、既知環境秘密値、chunk分割、改行前後の巨大行、API／WebSocket／downloadを確認した。実service PID `608736`、health 200。実APIで暗号化環境値を持つ一時appの実stdoutを作り、Playwright Chromium 320×700／1280×800で安全な行と`***`だけの表示、秘密値のDOM／download非露出、横overflow 0、console／page error 0を確認した。一時app／user／login session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 3 永続ログ ERROR アラート 完了（2026-07-21）

- 管理アプリのstdout／stderrへ新しく追記された`ERROR`／`CRITICAL`／`FATAL`を15秒評価loopで検知する`app_log_error`条件を追加した。登録済み対象アプリを必須にし、イベント条件のため継続時間は0へ固定する。UIは比較・しきい値・継続時間を隠し、対象未選択では保存できない。
- `alert_log_cursors`へrule／streamごとのdevice・inodeとbyte offsetだけを保存する。新規ruleは現在のEOFから開始して過去ログを大量通知せず、Web再起動後も既読行を再通知しない。inode変更とtruncateを検出してローテーション後の新ファイル先頭へ追従し、1回256KiBの有界走査で大容量追記を順次処理する。
- ログ本文や一致行はDB、AlertEvent、Workflow payload、通知、audit、内部logのいずれにも保存しない。イベントは一致件数だけを`ログ ERROR = N`として返す。ログpathは`data_dir/logs`を`Path.resolve()`し、アプリ領域外へ解決するsymlinkを拒否する。カーソルはrule削除時に明示削除し、対象／条件変更時は現EOFへ安全に再初期化する。
- Alembic revision `f6b2c8d41a70`を追加し、SQLiteでupgrade→downgrade→upgradeを確認した。

検証: alert集中14件、backend全481件中Terminal系29件を除く最新452件、frontend TypeScript／production build、migration往復、diff whitespace検査に成功。過去ERROR非通知、新規ERROR／CRITICAL件数、process内状態消失後の重複なし、inode rotation、event解消、本文非露出、cursor削除を確認した。実service PID `594535`、health 200。実API／実stderr追記／実15秒loopで約7秒後の`app_log_error=1.0`発火を確認し、Playwright Chromium 320×700／1280×800で対象選択、不要条件の非表示、横overflow 0、console／page error 0。一時app／rule／event／user／login session／auditは0件に清掃済み。既存Terminalには接続・入力・停止・削除していない。

## Phase 3 アプリ状態アラート修正・拡張 完了（2026-07-21）

- `app_down`が0／1を返す一方、従来UIはしきい値を隠したまま既定`gt 90`を保存しており発火不能だった。アプリ停止／ヘルス失敗をboolean条件としてstored comparatorに依存せず1=trueで評価し、既存ruleをmigrationなしで動作させる。
- `app_health_failed`と`app_restart_loop`を追加した。前者はcached healthが失敗してruntimeがDEGRADEDの場合、後者はsystemd `NRestarts`の実値をしきい値と比較する。停止／FAILED／UNKNOWN、health failure、再起動回数を別条件として選択できる。
- 3条件は登録済み対象アプリを必須とし、存在しないapp IDをcreate／updateとも422で拒否する。UIはアプリ条件だけ対象selectを表示し、停止／HC失敗では比較・しきい値を隠し、再起動だけ回数比較を表示する。対象未選択では保存不可にする。

検証: alert／app health集中20件、backend全480件中Terminal系29件を除く最新451件、frontend TypeScript／production build、diff whitespace検査に成功。既存`gt 90`停止ruleの実発火、STOPPED／DEGRADED／restart_count=6、未知app／再起動しきい値1未満の拒否を確認した。最終実service PID `583994`、health 200。実APIで一時URL shortcutを対象に3 ruleを作成し、Chromium 320×700／1280×800で対象選択、boolean／numeric段階表示、横overflow 0、console／page error 0。一時app／rule／event／user／login session／auditは清掃済み。既存Terminalには接続・入力・停止していない。

## Phase 3 ディスクhealth／IO監視 完了（2026-07-21）

- `GET /system/disk`へ物理block device、ディスク別read／write Bps、全体IO wait、温度sensor／℃、SMART available／passed・failed・unknown・unavailableを追加した。partitionは`Path.resolve()`後に`/dev`配下のblock deviceであることを確認し、sysfsの親deviceへ正規化する。通常file、許可root外、壊れたsymlinkはtelemetry対象にしない。
- 温度は非特権sysfs `device/hwmon*/temp*_input`だけを読み、`/sys/devices`への解決と-50〜200℃を検証し、Compositeを優先する。SMARTは固定`/usr/sbin/smartctl`または`/usr/bin/smartctl`だけを配列argv、`shell=False`相当、8秒timeout、JSONで読み、60秒cacheする。tool／権限／sensorがない場合はAPI・UIともN/Aで、disk一覧やアプリ全体をerrorにしない。
- System UIは各mountの容量barの下へ物理device、R/W、温度、SMARTを小さくまとめ、SMART異常だけ赤、正常は緑、取得不可は通常色のN/Aとする。IO waitはdisk sectionで1回だけ表示し、大量chartを追加しない。
- 全回帰で再現したWorkflow business eventのmodule-global `asyncio.Lock`別event-loop束縛を、process全体を直列化するcancel-safeなcross-loop async lockへ修正した。TestClient／管理loopを跨いでもretryが3回でFAILEDへ収束し、同一process内の二重配送防止を維持する。

検証: disk／GPU集中8件、Workflow event retry該当testを連続2回、backend全477件中Terminal系29件を除く最新448件、frontend TypeScript／production build、compile、diff whitespace検査に成功。最終実service PID `571645`、health 200。実APIで`/dev/nvme0n1` Composite 37.85〜38.85℃、IO wait 0.2〜0.4%、`/dev/sda`の変動R/W速度を確認した。実機は`smartctl`未導入のためSMART N/A、fixtureでは固定argv JSONのpassed／39℃を確認した。Chromium 320×700／1280×800で全項目、横overflow 0、console／page error 0。一時user／login session／auditは清掃済み。既存Terminalには接続・入力・停止していない。

## Phase 3 アラートメール通知 完了（2026-07-21）

- 既存のWeb通知センター／Discord／Slack／WebhookへSMTPメールを追加した。host、port、STARTTLS／TLS／内部SMTP向け保護なし、任意username／password、送信元、最大20宛先を管理者だけが登録でき、接続設定全体を既存Fernet鍵で暗号化保存する。API／一覧はpasswordや宛先を返さず、送信元を伏せ字、宛先を件数だけで表示する。
- SMTPは標準`EmailMessage`と`SMTP`／`SMTP_SSL`を別threadで実行し、STARTTLSは証明書検証付きcontextを使う。HTTP／SMTP失敗logは例外本文、URL、SMTP応答、認証情報を出さずchannel種別と例外classだけを残す。テスト送信はsuccess／failureを監査し、設定値はmetadataへ含めない。
- 外部送信の成否をAlertEventの`notified`へ正しく反映するよう修正した。外部チャンネルが失敗してもWeb通知センターのイベントは作成し、センサー／評価loop全体を停止しない。
- Settingsの通知追加sheetはメール選択時だけSMTP項目を段階表示する。passwordは常にpassword inputで、保護なしは信頼できる内部SMTP専用と明記した。

検証: alert集中10件、backend全472件中Terminal系29件を除く最新443件、frontend TypeScript／production build、diff whitespace検査に成功。全回帰の初回だけ既存Workflow event testの別event-loop lock競合が1件発生したが、該当test単独2回と全回帰再実行は成功した。最終実service PID `542445`、health 200。127.0.0.1の隔離SMTPへAPIのテストメール1通（252 bytes）を実送信し、成功監査、秘密値非反射、チャンネル削除を確認した。Chromium 320×700／1280×800で全SMTP項目、sheet内scroll、password型、横overflow 0、console／page error 0。一時channel／user／login session／auditとSMTP受信器は清掃・終了済み。既存Terminalには接続・入力・停止していない。

## Phase 3 メトリクス長期履歴 完了（2026-07-21）

- 生メトリクス24時間、1分平均30日、1時間平均1年の3段階保持を実装した。minute保存のたびに同じUTC時間のhour行を再計算するため、途中再起動後も既存hour行を重複させず更新する。minute／hourは設定された保持期間を越えた行を同じtransactionで削除する。
- `GET /system/metrics/history`は1分〜1年を受け付け、要求期間に応じてraw／minute／hourを選ぶ。rawは最大600点、DB履歴は最大2,000点へ均等間引きし、320pxを含むブラウザへ過大なpayload／SVG点数を送らない。
- HomeのCPU／RAM／GPU／VRAM履歴へ15分、1／6／24時間、7／30／90日、1年と、15〜525,600分の任意期間を追加した。現在値のWebSocket更新は維持し、履歴線だけを選択期間へ切り替える。取得中、解像度、点数を本文を含まない状態表示で確認できる。
- Alembicへ`metrics_hour`を追加し、SQLite／PostgreSQL両方のoffline head SQL生成を確認した。設定初期値は`hour_retention_days: 365`。

検証: 長期履歴／migration集中12件、GPU／alertを含む集中12件、backend全468件中Terminal系29件を除く最新439件、frontend TypeScript／production build、SQLite／PostgreSQL Alembic offline SQL、diff whitespace検査に成功。実SQLiteはupgrade前backup後にrevision `e4f1a7b9c203`へ移行し、既存12,425分を219時間へ集約した。実service PID `520755`、health 200。認証付きAPIで24時間=`raw`、30日=`minute`、30日+1分／1年=`hour`を確認した。Chromium 320×700／1280×800で1年選択と任意44,640分を適用し、横overflow 0、console／page error 0。一時user／login session／auditは清掃済み。既存Terminalには接続・入力・停止していない。

## Phase 3／14 電源安全確認・TOTP再認証 完了（2026-07-21）

- `GET /system/power/safety`を追加し、実行中App／Workflow、接続中Terminal WebSocket／Remote Desktop tunnelの件数とTOTP要否だけを返す。Terminal／RDのSession ID、接続先、画面本文、入力内容は列挙・返却しない。確認情報を取得できない場合はUIをfail closedで実行不可にする。
- 即時電源APIは既定の`graceful`で通常の`systemctl reboot|poweroff`を使い、利用者が確認画面で危険性を読んで選んだ場合だけ固定argvの`systemctl --force reboot|poweroff`を使う。shell、任意command／argumentは受け取らず、modeを監査する。予約は常に通常停止とする。
- `security.require_totp_for_power`を追加した。有効時は即時操作と予約の直前にTOTPまたは1回限りのrecovery codeで再認証し、IP／user単位5回/分の失敗制限をかける。未設定・不正code・超過は電源command前に拒否して理由だけを監査し、codeやsecretはlog／auditへ残さない。
- PC／mobile共通の破壊的確認dialogへ4件数、実行時刻、通常停止／即時実行、TOTP入力、既存予約取消を段階表示する。320pxではdialog自体をviewport内scrollにしてSafe Areaを維持する。

検証: 電源／Remote／Auth集中25件、backend全464件中Terminal系29件を除く最新435件を3分割で成功（Terminal系は既存Session非接触条件のため直前成功記録を維持）、frontend TypeScript／production build、固定argv、TOTP成功／失敗、接続詳細を返さないsummaryを確認。実service PID `509291`でhealth 200。Chromium 1280／320pxで4件数、通常／即時・予約、viewport containment、横overflow 0、console error 0を確認し、確定buttonは押していない。実reboot／shutdown／forceは破壊的なため実行せず、mock境界までとする。

## Phase 7 PostgreSQL運用切替 実装完了（2026-07-21 13:45 JST、実server確認待ち）

- `psycopg 3`を標準依存へ追加し、Control Deck本体DB URLをSQLite／PostgreSQLだけに制限した。`./deck.sh database status|postgresql|sqlite`を追加し、PostgreSQLは非表示入力または一時環境変数から接続を先に確認する。現行SQLiteを整合backupしてから固定`config/database.env`へ0600で原子的に保存し、service migration／起動失敗時は直前設定へ戻して再起動する。SQLite復帰時も旧PostgreSQL設定を0600で退避し、既存dataを暗黙移送しない。
- systemd user unitはURLを本文へ展開せずoptional EnvironmentFileを読む。ExecStartPreとforeground起動は同じ検査で、`O_NOFOLLOW`、通常file、実行user owner、group／other permissionなし、4KiB上限、UTF-8固定1行、対応方言／psycopgを強制する。診断と例外はpasswordを表示しない。
- PostgreSQL migrationは固定advisory lockで複数instanceの同時upgradeを直列化し、version情報のない非空schemaを安全確認なしでhead stampしない。Alembic全revisionをPostgreSQL offline SQLへ生成する回帰を追加した。backup／restoreはcustom formatの`pg_dump`／`pg_restore`を固定配列argvで実行し、passwordは子process環境だけへ渡す。restore前に現DBのsafety dumpを作成し、Web serviceを停止してからclean restoreする。backup archive自体も既知rootの通常file／directoryだけを展開し、traversal、重複、symlink／hardlink／特殊file、件数／size／圧縮率超過を拒否する。

検証: database／migration集中18件、backend全453件、frontend TypeScript／production build、bash構文、diff whitespace、PostgreSQL offline head SQLに成功。実機の既定SQLiteで`database status`、整合backup作成／manifestの`database_backend: sqlite`、安全な再展開、一時archive清掃を確認した。更新systemd unitは最終PID `448633`でExecStartPre成功、active／health 200。不足client時の`database postgresql`は設定fileを作らず失敗し、SQLite／service継続を確認した。実機にPostgreSQL server／clientがないため、実接続・online migration・pg_dump／pg_restoreは未実施であり、これを実server確認待ちとして残す。既存Terminalには接続・入力・停止していない。

## Phase 7 provider共通pull／モデル設定管理 完了（2026-07-21 13:30 JST）

- capability付きprovider adapterへ`pull`、モデル設定の取得／保存、操作開始前のcapability検査を追加した。Ollamaのpullは既存のブラウザ非依存durable Jobへ接続し、進捗と同じevent IDを維持する。llama.cppは登録済みaliasの生成／runtime設定だけを共通routeから変更でき、alias、GGUF path、mmproj、role、portは専用登録APIに限定する。
- `POST /models/providers/{provider}/pull-jobs`と`GET/PUT /models/providers/{provider}/models/{model}/config`を追加した。未知provider／modelは404、capabilityなし・外部OpenAI互換への変更は409、不明field・型／範囲違反は422とし、黙って成功扱いしない。設定監査は値を残さずprovider、field名、reload有無だけを記録する。従来Ollama固有routeは互換のため維持し、Models UIのpull／個別設定は共通routeへ移行した。

検証: provider集中8件、backend全440件、frontend TypeScript／production build、diff whitespace検査に成功。実Control Deckを最終PID `427143`へ再起動しhealth 200、実Ollama／`qwen3.6-27b-q5_k_m:latest`で共通config GET 200→同一値PUT 200を確認した。認証付きChromium 1件で320／1280pxの共通config GET／PUT、durable pull job導線、横overflowなしを確認した。実pullは不要な再downloadを避けfixtureとし、既存モデル削除／reloadは実行していない。一時user／login session／auditは0件に清掃済みで、既存Terminalには接続・入力・停止していない。

## Phase 2 system scope systemd service 最小特権境界 実装完了（2026-07-21 13:15 JST、root導入確認待ち）

- Appsの既存systemd serviceへ`user`／`system` scopeを追加した。system scopeはAPIからunit名を自由入力できず、ローカル設定の固定ID、表示名、unit、許可操作を`./deck.sh service`がroot所有`/etc/control-deck/system-services.json`へ導入した項目だけを登録できる。DBにはcatalog IDとscopeを保存し、unit名はcatalogから再解決するため、作成・編集requestによる別unitへの差し替えを拒否する。
- Web processはrootで動かさず、状態取得だけを非特権`systemctl show`で行う。start／stop／restartはroot所有・非group/world writable・非symlinkの固定helperを`/usr/bin/sudo -n`で呼ぶ。helperは`O_NOFOLLOW`でroot所有catalogを再読込し、固定`/usr/bin/systemctl`、固定action、固定unitだけを配列引数で実行する。killはsystem scopeで禁止し、catalogが許可しない操作はhelper呼出前に409＋失敗監査とする。sudoersはこのhelperの2引数commandだけをNOPASSWD許可し、任意systemctl／shell／unit名を許可しない。
- Apps追加UIはユーザー／システムを段階選択し、system scopeでは導入済みcatalogのselectだけを表示する。App APIは実行可能actionを返し、カード／詳細の主操作・menuから許可外操作を隠す。320px／PCのいずれも主操作1個＋その他menuを維持する。Alembicと軽量migrationへscope／catalog IDを追加した。

検証: backend全439件、system service集中4件、frontend TypeScript／production build、diff whitespace検査に成功。symlink catalog、未知ID、unit差替え、許可外action、固定helper argv、system→user切替を確認した。実Control DeckをPID `394387`へ再起動しhealth 200、認証付きChromiumで320／1280pxのcatalog限定select、自由unit入力なし、root allowlist説明、横overflowなしを確認した。一時user／login session／auditは清掃済み。実機にはxrdp.serviceがactive／enabledで存在するが、この環境のsudoは対話password必須でhelper／catalogをまだroot導入できないため、実helperによるstart／stop／restart確認だけは`./deck.sh service`の対話sudo実行後に継続する。

## AI Workflow→AppBuilder GUI 3件実動作確認（2026-07-21 13:00 JST）

- ローカルQwen3.6 27Bへ自然言語でtext、number、selectの3 Workflowを生成させ、生成定義を登録、公開版として実入力で実行した。初回text出力は`output.render.config.name`が欠落したため、検証結果を同じAIへ返す再生成が必要だった。AI生成後の構造・意味・入出力契約検証を省略しない運用境界を実データで確認した。
- 各WorkflowからApp Studioのresponsive GUIを自動構成し、保存済みSourceを隔離systemd user Build JobでASP.NET Coreへ実ビルド／self-testした。ビルド済み主DLLを専用一時systemd user unitで起動し、生成アプリ自身のAPIとGUIへ入力して同じ値が結果表示されることを確認した。

検証: 3件すべてでStudio previewと生成実アプリを320×700／1280×800で確認し、横overflow、console／page errorなし。作成したWorkflow／Execution／Application Project／Build、build directory、一時systemd unit、一時user／login session／auditは終了後0件に清掃し、既存Terminalには接続・入力・停止していない。

## Phase 4 ファイル圧縮／安全展開・高度preview 完了（2026-07-21 12:40 JST）

- 許可root内の通常file／directoryをZIPまたはtar.gzへ圧縮し、ZIP／tar.gz／tgzを新規directoryへ展開するAPIとFilesメニューを追加した。source／destinationは既存のrealpath＋許可root／deny-root検証を必ず通し、圧縮先と展開先は既存pathを上書きしない。作成・展開はいずれも同じ親directoryの予測不能な一時pathへ完了させ、Linux `renameat2(RENAME_NOREPLACE)`で既存pathへ上書きせず原子的公開するため、失敗時に部分archive／部分展開を公開しない。成功時は形式、項目数、非圧縮byte数だけを監査する。
- 作成時はsymlinkと特殊fileを拒否し、各通常fileを`O_NOFOLLOW`で開いてinode／device／sizeを再照合するため、検査後の差し替えで許可root外を読み込まない。展開前に全memberを検査し、絶対path、`..`、backslash traversal、NUL、4KiB超path、重複／file-directory競合、symlink／hardlink／device／FIFOを拒否する。最大10万項目、`files.max_upload_size_gb`と同じ非圧縮size上限、16MiBを超える最大200倍の圧縮率、64MiB reserveを残せない空き容量を拒否し、宣言sizeと実読込sizeの不一致でも全体をrollbackする。shellや外部archive commandは使わない。
- 認証・`files.view`付き`/files/preview`を画像／PDF／音声／動画に限定し、`Content-Disposition: inline`、`nosniff`、private/no-store、SVG sandbox CSPを付与した。Starletteのsingle／multi Range配信を維持し、mediaのseekと大容量PDFを全量memory化しない。HTML等のunsafe inline型は415で拒否する。
- Files UIはPDF、audio、videoを拡張子別のnative viewerへ接続し、PCは右側preview panel、320pxを含むmobileはSafe Area対応bottom sheetで表示する。entry主操作は従来どおりtapで開き、圧縮／展開、download、edit等は3点menuへ置く。圧縮はZIP／tar.gzを選択でき、展開はarchive名から新規folderを提案し、危険memberと既存項目を上書きしない境界を画面に明示する。

検証: backend全435件、archive／media集中3件、frontend TypeScript／production build成功。ZIPとtar.gzの往復、Zip Slip、backslash／symlink／hardlink・特殊file境界、高圧縮率、原子的な既存path上書き拒否、部分出力なし、preview 206／Content-Range／inline／nosniff、HTML 415を自動確認した。実service PID `361454`、active、health 200で、一時許可rootに作成した3-entry ZIPを実APIで圧縮→展開し、同名再作成409・既存archive不変、内容一致、`bytes 3-6/10` Rangeを確認した。認証付きChromium 1件で320／1280pxのPDF／audio／video surface、ZIP展開提案、tar.gz圧縮request、横overflowなしを確認した。一時user／login session／audit／file fixtureは0件に清掃済み。

## Phase 2／3 アプリ別GPU／VRAM・許可コマンドHC 完了（2026-07-21 12:21 JST）

- 管理アプリのsystemd MainPIDと子processだけを対象に、Linux DRMの`/proc/<pid>/fdinfo`標準統計からGPU engine累積時間とresident VRAMを有界収集する。PIDは数値から組み立てた固定`/proc` pathだけを使い、1 process最大512 FD、1 fdinfo最大32KiB、`O_NOFOLLOW`で読む。外部GPU CLIをApp一覧取得ごとに起動せず、同じDRM clientの継承FDをprocess tree内で重複計上しない。App ID＋MainPIDでsample世代を分離し、0.25秒未満の近接再取得や再起動前counterとの誤差分を避ける。GPU clientがないアプリは0、driverが項目を公開しない場合はN/Aへ縮退し、アプリ全体をエラーにしない。
- `AppRuntime`へ`gpu_percent`／`vram_bytes`を追加し、PCカードではCPU／RAMと同じcompact metrics、詳細bottom sheetではモバイルを含めGPU／VRAMまたはN/Aを示す。App APIからはprocess名やfdinfo本文を返さない。
- `applications.health_commands`を固定ID、表示名、固定argvのローカル設定catalogとして追加した。実行ファイルは絶対pathを実行時に`resolve(strict=True)`し、NUL／空引数／長大argv／認証情報を示す文字列を設定検証で拒否する。APIはIDと表示名だけを返し、App設定はcatalog IDだけを保存するため、requestから実行path・argv・環境変数を注入できない。
- 許可コマンドHCは最大4並列、0.2〜30秒で、Web processの実command子processにせず`systemd-run --user --wait --collect`の一時serviceとして実行する。`NoNewPrivileges`、read-only system/home、PrivateTmp、CPU／memory／task／runtime上限、固定PATH、`StandardOutput/Error=null`を強制し、shellを使わない。結果は成功／終了コード／timeoutだけとし、stdout／stderrをAPI・logへ出さない。

検証: backend全432件、アプリHC／DRM集中11件、frontend TypeScript／production build成功。実systemd user transient unitで固定`/usr/bin/true`の成功を確認した。`./deck.sh`最終反映後はservice PID `328130`、active、health 200。実DBのApp 2件中running 1件でGPU／VRAM双方が取得可能だった。認証付きChromium 2件で、320／1280pxの固定catalog選択、自由argv入力なし、決定的fixtureのGPU 38%／VRAM 4.0GB表示、横overflowなしを確認した。一時user／login session／auditは清掃済み。

## Terminal V2 Phase 4c-5 物理端末証拠取得導線 準備完了（2026-07-21 11:56 JST、端末確認は継続中）

- 管理者Settingsへ追加した物理iPhone Safari／Standalone PWA専用入口から、既存Sessionへ接続せずV2 Labへ遷移できるようにした。明示的に作成したV2 Lab Sessionだけを対象とし、通常Sessionと別tabのLab SessionをV2で開かない境界を維持する。
- V2 Lab headerへ本文非収集の検証レポートを追加した。Browser／Standalone判別、secure context、layout／Visual Viewport寸法とoffset、横overflow、root containment、IME textarea数、rows／cols、replay／echo／scroll／resize／reconnectだけをその場でsnapshot化する。画面本文、入力、Clipboard、cwd、command、token、Session IDは収集・永続化せず、利用者が明示した場合だけJSONをClipboardへコピーする。
- replay未計測、echo 20 sample未満、scroll未操作を合格扱いせず、設計閾値を満たした項目だけを表示上で合格にする。E2Eへ320pxのレポート表示、合否項目、本文marker／Paste結果の非混入回帰を追加した。

検証: frontend TypeScript／production build、Playwright test discovery、diff whitespace検査に成功。実ControlDeckのChromium E2E 1件で、POST応答から直ちに所有登録した専用V2 Lab Sessionだけを使い、320pxのレポート表示、閾値判定、Terminal本文marker／Paste結果の非混入、既存の4 viewport／latency／resume／reload／session switch回帰を確認した。所有Sessionと一時user／login session／auditは清掃済みで、既存Sessionには接続・入力・終了していない。物理iPhone Safari／Standalone PWAそれぞれの操作確認とJSON証拠は未取得であり、Phase 4c-5は未完了、canary／V2既定化は未着手のまま維持する。

## Terminal V2 Phase 4c-4 自動viewport／latency回帰 完了（2026-07-21 11:36 JST）

- V2 rootへ本文・入力内容を含まないmount内telemetryを追加した。initial／resumeごとのreplay総時間、write drain、最終2 paint、履歴byte／chunk、通常入力送信から次のPTY outputまでのp95／max／sample数、local scrollのpaint時間、resize／reconnect回数、最終rows／colsだけを保持する。最大128 sampleの数値だけを有界保持し、文字、Clipboard、cwd、command、tokenは記録しない。
- 320×700、390×844、768×1024、1280×800を同じ専用Lab Sessionで再mountし、各幅でLIVE、入力echo、Visual Viewport containment、横overflow 0、単一IME textarea、最小rows／cols、mobile helper表示／desktop非表示を検証する自動回帰を追加した。390pxでは高さ430pxへ縮めるkeyboard相当のgeometry変化と復帰を行い、root／helper下端、PTY resize、textarea個数を確認する。
- 設計の44px touch targetに対して、V1／V2双方のsession selectorとmobile Paste／Enter／helper keyが32〜40pxだった不一致を修正した。全viewportで可視button／selectを実寸検査し、V1のPaste／Copy／helper focus、geometry event集約、keyboard開閉10往復も同時回帰した。

検証: Terminal backend集中29件、frontend TypeScript／production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、Playwright ChromiumのV2 Lab 1件で4 viewport、20回の1-byte echo p95 50ms未満／max 250ms未満、scroll p95／max 100ms未満、replay 4秒未満、履歴scroll→WebSocket resume、page reload、keyboard相当resize、全可視操作44px以上、横overflowなし、console／page errorなしを確認した。V1代表回帰3件も成功した。検証用Session／user／session／audit／一時credentialは清掃済み。物理iPhone Safari／PWAはChromium emulationと区別して未完了であり、証拠が得られるまでcanary／既定化へ進めない。

追補: Linux Playwright WebKitでも同じ4 viewport／latency／resume／reload／session switch回帰を実行し、作成直後のquery refetch前に旧`select` optionsが残り得るbrowser間raceを検出した。Terminal Session作成は一覧再取得完了後だけ全画面viewをmountするよう修正した。E2E cleanupは一覧や選択値からIDを推測せず、自身が送ったPOST responseのIDだけを直ちに所有集合へ登録し、その集合以外への接続・入力・削除を行わない。WebKitが未対応の`interactive-widget` viewport hintを仕様どおり無視した既知診断だけを除き、console／page errorなしを維持する。これはSafari系engineの自動証拠であり、物理iPhone／standalone PWAの代替にはしない。

## Terminal V2 Phase 4c-3 UI／操作同等化 完了（2026-07-21 11:16 JST）

- V2専用Lab SessionへV1と同じTerminal workspace契約を接続した。PC Copy、モバイルPasteタップ／上swipe Copy、Enter、Esc、Tab、Ctrl、矢印、`^C`／`^D`／`^Z`／`^L`、Clipboard paste、32KiB以上のchunk／ACK／cancel／retry進捗、本文swipe、右端history bar、Snippet／Automation panel、session switchを同じaccessible nameと操作位置で提供する。
- 通常keyとhelperは履歴表示中だけlocal xterm buffer末尾へ戻して直接送信する。Pasteは既存の有界controllerを再利用し、UTF-8 byte境界、bracketed paste、大容量送信中の再接続を扱う。Copyは選択範囲を優先し、未選択時だけ最大100,000行のlocal bufferを結合する。本文swipeと右端barはlocal bufferだけを動かし、tmuxへの二重scroll命令を送らない。
- Lab tab内の専用Session一覧と切替を維持し、通常Sessionや別tabのLab SessionをV2へ接続しないfail-closed境界は変更していない。既定は引き続きV1で、物理iPhone Safari／PWAを含む全viewport計測、canary、V2既定化、V1ロールバック確認は後続項目として残す。

検証: Terminal backend集中29件、frontend TypeScript／production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、Playwright Chromium 1件で320×700の専用Lab作成、全helper key byte列、大容量UTF-8 Pasteのbyte数／SHA-256一致、Copy、Automation、120行local history、WebSocket再接続、page reload、同一tabの2 Session切替、4秒以内のLIVE、横overflowなし、IME textarea 1個、console／page errorなしを確認した。検証用Session／user／session／audit／一時credentialは清掃済み。

## Terminal V2 Phase 4c-2 core／専用Lab Session 完了（2026-07-21 11:02 JST）

- 既定V1を変更せず、`?terminalLab=v2`でそのbrowser tabが新規作成した専用Sessionだけを`XtermViewV2`へ接続する並行Lab経路を完成させた。V2は独立WebSocket generation、initial／resume、journal sequence、reset fallback、受信順write scheduler、最大32KiBの描画slice、最終2 paint境界、単一xterm instanceを持ち、PTY output全量をReact stateへ入れない。
- V2 inputは通常keyをlocal history末尾へ戻して直接送信し、大容量Pasteは既存のchunk／ACK／cancel／retry controllerを再利用する。Visual Viewportのwidth／height／offset、composition中のresize保留、xterm local resizeとPTY resize generation、単一IME textareaを維持する。本文swipeと右端barはtmux copy-modeへ命令せず、同じlocal xterm bufferだけを操作する。
- Lab属性をfrontendの記憶だけにせず、tmuxのsession-local `@control-deck-engine=v2-lab`へ永続化した。API一覧とfallback PTYも同じengine契約を返し、V2 WebSocketはLab属性のないSessionをattach前に4403、V1 WebSocketはV2 Lab Sessionを4403で拒否する。別tabが作ったLab Sessionは通常一覧／切替候補へ出さず、同一tmux SessionへのV1／V2混在をfail-closedにした。不明engineの作成は422で拒否する。

検証: backend全425件、terminal集中23件、frontend TypeScript／production build成功。実ControlDeckのPlaywright Chromium 1件で320×700の専用Lab作成、4秒以内のLIVE、通常入力echo、WebSocket切断後の差分resume、同一local履歴、ページreload後の同じSession ID／履歴、IME textarea 1個、横overflowなし、console／page errorなしを確認した。さらに実tmux Lab Sessionへ履歴markerを出力して`control-deck-web`を再起動し、永続Lab属性、同じSession ID、capture replay内markerを確認した。検証用Session／user／session／audit／一時credentialは清掃済み。既定は引き続きV1で、V2のPaste／Copy／helper／swipe／Automation／session switch全操作同等化、全viewport計測、物理iPhone Safari／PWA、canary／既定化は後続項目として未完了のまま維持する。

## Workflow Phase 5b sample／全node docs／指定回帰flow 完了（2026-07-21 10:52 JST）

- SampleBookを14件から19件へ拡張し、`Execution Time Travel`、`Local LLM Runtime Route`、`PC State Recovery`、`AI Diagnose & Patch`、`Regression Batch`を追加した。全sampleへ目的、難易度、所要時間、capability／副作用／Secret・model・App要件、型付き入出力、sample入力、期待assert、mock、node walkthrough、failure injection、recovery／retry、install前previewを同じ契約で付与した。全件をコピー直後にpreview／publishでき、外部依存のないsampleは最終typed outputまで実行できる。
- backend node catalogを正として、全62 nodeへ目的、使う／使わない場面、全設定項目、型付き入出力、変数例、副作用、権限、Secret方針、retry／timeout／error route、代表Error、性能／cost、2 recipes、migration noteを構造化して返すようにした。SampleBookのNode Referenceはこのcanonical metadataを表示し、frontend側の説明欠落に依存しない。
- SampleBook UIへ動的category、install前preview、型／入力例／期待結果、failure／recoveryを追加した。320pxは一覧と詳細を切り替えるbottom-sheet相当、PCは一覧と詳細の2 paneを維持し、既存のコピー→Editor導線へ接続した。

検証: backend全423件、SampleBook／node docs集中16件、frontend TypeScript／production build成功。全19 sampleの構造・安全preview・公開、全62 nodeの15 sectionと2 recipes、Regression Batchの5件→3 batch、Time Travelの同一入力によるv1 historical／v2 current差、意図的timeoutの決定的診断→operation patch選択適用→成功、永続state進捗を確認した。実serviceを`./deck.sh`で再起動してactive／health 200を確認し、Playwright Chromium 1件で320pxのinstall前preview→コピー→公開→実行、1280pxの全node doc section、両幅の横overflowなし、console／page errorなしを確認した。さらに実行を`WAITING`にしたまま`control-deck-web`を実再起動し、同じexecution IDが`SUCCEEDED`／progress 1へ復帰した。実Ollamaのloaded `qwen3.6-27b-q5_k_m:latest`を`ai.route`が検出・選択し、動的endpoint／modelで回答まで成功した。検証用Workflow／execution／state／user／session／audit／一時credentialは清掃済み。

## Workflow Phase 5 AI diagnose／patch／runtime route／Project Intelligence 完了（2026-07-21 10:32 JST）

- Editorへ`Project Intelligence`を追加した。保存済み定義、直近20 execution、node別実行数／失敗率／平均時間、実行順、side effect、semantic issue、不明なLLM設定、test case、接続済みApp Studio Projectを統合し、稼働中provider／modelとGPU／空きVRAMも同じ読み取り専用reportへ示す。センサーやruntime取得失敗は空候補／N/Aへ縮退し、Editor全体を停止しない。
- 失敗実行のdiagnose APIは、Workflow definitionをSecret-safe snapshotへ変換し、選択実行から失敗node、typed error、redacted runtimeだけを最小payloadとしてLLMへ渡す。応答は原因、確信度、最大3案のversion 1 operation patchに限定し、各案を構造／semantic／Secret境界で再検証してから差分を表示する。model停止、不正JSON、無効operation、案なしではtimeout／retryを扱う決定的診断へfallbackする。ローカルruntimeの単一案平坦応答と`update_node.changes.config`はcanonical `set_config`へ限定正規化し、その後に同じ厳格validatorを通す。
- patchはnode ID基準の`set_config`／`update_node`／node・edge追加削除だけを許可し、最大100操作／256KiB、literal Secret拒否、trigger削除拒否、構造検証、semantic error停止、RFC 6902形式exportを実装した。previewは保存せずbefore／after quality・件数・警告を返し、利用者が選択した案だけをoptimistic timestamp付きで適用する。適用前version snapshotとchecksum／件数だけの監査を残し、値やpromptは監査へ保存しない。trigger schemaから決定的な`AI baseline` test caseも重複なしで生成する。
- `ai.route` nodeを追加した。最大20候補または検出済みprovider／modelをavailability、selected runtime、loaded状態、context、空きVRAM、priorityとBalanced／Availability／Loaded／Context／VRAM戦略で評価し、選択した`base_url`／`model`／理由／候補評価／runtime snapshotを型付きで返す。条件を満たさない場合はtyped `AI_RUNTIME_UNAVAILABLE`、不正設定は非retry errorとする。`llm.chat`のendpoint／modelをtemplate対応に変更し、route出力をそのまま後続生成へ接続できる。
- EditorのPC command surfaceとmobile More menuから2 step以内でProject Intelligenceを開ける。品質／issue、baseline test、AIへの再検討指示、検出endpoint／model、決定的診断、案ごとのquality・警告・操作JSON、選択適用を1つのPC side surface／mobile bottom sheetへまとめた。AI案の自動適用は行わず、既存のCanvas手動編集、Undo／Redo、autosave、競合再読込も維持した。

検証: backend全418件、Intelligence／runtime route／patch／Secret安全性集中20件、frontend TypeScript／production build成功。operation atomicity、literal Secret拒否、optimistic conflict、version snapshot、監査payload非保存、baseline test重複防止、VRAM N/A、route→LLM endpoint／model template接続を確認した。実serviceを再起動してactive／health 200を確認し、Playwright Chromium 1件で320pxの実timeout失敗→ローカル診断→操作差分→選択適用→baseline test、1280pxの`ai.route`設定とlive endpoint候補を確認した。両幅で横overflow、console／page errorなし。実runtime routeはOllamaのloaded modelを1候補から選択し、context 262,144、VRAM N/Aでも停止しないことを確認した。実Ollama `qwen3.6-27b-q5_k_m:latest`へtemperature 0.1のstructured診断を行い、12.31秒、AI source、valid 1案、fallbackなしを確認した。検証用Workflow／execution／test case／version／user／session／audit／一時credentialは清掃済み。次はsample／全node詳細docs／指定E2E flow。

## Workflow Phase 3 batch／rate limit／circuit breaker 完了（2026-07-21 10:00 JST）

- `data.batch`を追加し、最大10,000件のJSON arrayを順序を保った1〜1,000件単位のbatch配列へ決定的に分割するようにした。項目数、batch数、設定件数を型付きで返し、`batches`を`flow.map`へ渡すことで一定件数単位のsubflow処理を構成できる。
- `control.rate_limit`を追加した。同じWorkflow・scopeの複数executionが固定時間窓の使用数をDB-backed stateで共有し、service再起動後も上限を維持する。最大実行数1〜10,000、時間窓0.1秒〜24時間、最大待機0〜3,600秒を検証し、上限時は空きまで非同期waitするか、typed `RATE_LIMITED`で即時error routeへ送る。取得結果は使用数、残数、reset日時、待機秒数を返す。
- `control.circuit_breaker`を追加した。同じWorkflow・scopeで`check`／`record_success`／`record_failure`／`status`／`reset`を共有し、連続失敗でCLOSEDからOPENへ遮断、回復時刻後はCAS leaseに勝った1 executionだけをHALF_OPEN probeとして許可する。probe成功でCLOSEDへ戻り、失敗で再OPENする。OPEN前から遅れて到着した成功は回路を閉じない。Editorはcheckの許可／遮断handle、状態・連続失敗・再試行日時を表示する。
- レートと回路の内部状態は既存の型付き`WorkflowStateEntry`を専用namespaceで再利用し、version CASによりSQLite同時実行時の更新欠落を防いだ。scopeは英字始まり最大64文字、Secret値の使用を拒否し、監査にはpayloadを含めずscope、操作、状態、execution／node IDだけを記録する。Workflow削除時は既存state清掃経路で同時に削除される。

検証: backend全413件、resilience／metadata／semantic validation集中19件、frontend TypeScript／production build成功。8 workerで16同時rate取得を競合させ上限4件だけが成功し、12同時HALF_OPEN checkでprobeが1件だけ許可されること、時間窓更新、OPEN遮断、probe成功／失敗、遅延成功無視、Secret非永続化、Workflow削除清掃、実executionの許可／遮断routeを確認した。実serviceを`./deck.sh`で再起動してactive／health 200を確認し、Playwright Chromium 1件で1280px Editorのbatch・rate・circuit設定と許可／遮断handle、320px公開Runnerの5項目→3 batch、連続実行の実待機、CLOSED許可、失敗記録後OPEN遮断、2秒後HALF_OPEN probe→成功復帰、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／execution／state／user／session／一時credentialは清掃済み。次はAI diagnose／patch／runtime route／Project Intelligence。

## Workflow Phase 3 version-pinned Subflow Map 完了（2026-07-21 09:41 JST）

- `flow.map`を追加し、JSON arrayの各項目を明示的に公開された同一サブWorkflowへ型付きで渡すようにした。開始時に最新published version IDとdefinition snapshotを1回だけ固定し、途中で公開版が変わっても全項目は同じversionを使う。子triggerへ`item`／`index`／`total`をネイティブ型で渡し、message／追加JSON入力も項目ごとにtemplate展開できる。結果は完了順に依存せず入力順で、子の型付きoutputs／error、execution ID、成功・失敗数、対象Workflow／versionを返す。
- itemは最大100件、並列数は1〜5、各子timeoutは10〜3,600秒、親nodeは最大2時間へ制限した。失敗方針は、実行中batchを完了して親をtyped `SUBFLOW_MAP_FAILED`で止める`stop`と、失敗を結果へ収集して残項目を続ける`collect`を明示選択する。親cancelは待機中の子taskへ伝播する。既存3段depth上限にWorkflow lineageを追加し、`flow.call`／`control.try`／`flow.map`の直接・間接cycleを子execution作成前に`SUBFLOW_CYCLE`で拒否する。
- Secretは実値のまま子入力へ渡せる一方、親Map結果のitem／子output／親子contextでは伏字化し、`workflow.subflow_map`監査にはpayloadを含めず件数、並列数、失敗方針、固定version、子execution IDだけを記録する。Editorへ公開Workflow、items、並列数、失敗方針、message、追加入力、timeoutと全出力を段階表示し、object／array code fieldを整形・型保持して編集できる。公開RunnerとEditor実行入力へ型付き`json_array`を追加した。Map内の動的待機を可能にするため、`util.wait`の秒数もtemplate展開とtyped errorへ統一した。

検証: backend全408件、Subflow Map／既存loop集中16件、Runner contract集中7件、frontend TypeScript／production build成功。実backend integrationで並列上限、入力順、stop時の後続未起動、collect成功2／失敗1、同一version固定、cycleのDB検索前拒否、Secretの親子context／監査非露出を確認した。実Control DeckのPlaywright Chromium 1件で1280px Editorの全Map設定と横overflowなし、320px公開Runnerの`json_array`から完了時間の異なる3子を並列実行し、入力順0／1／2、成功3、異なる子execution ID、同一公開version、型付き結果、横overflowなし、console／page errorなしを確認した。serviceは`./deck.sh`で反映後active、health 200。検証用Workflow／execution／user／session／一時credential／test artifactは清掃済み。次はbatch／rate limit／circuit breaker。

## Workflow Phase 3 durable business event 完了（2026-07-21 09:24 JST）

- `event.emit`から同名のWorkflow custom event triggerへ配送するDB-backed outboxを追加した。発行時にevent本体とsubscriber別deliveryを先行保存し、最新published definitionかつenabledの受信Workflowだけを起動する。event envelopeはevent ID／名前／payload／発行元Workflow・execution・node／時刻を持ち、受信実行を`event:workflow`として履歴から判別できる。Editorはevent種別に応じてalert filterとcustom event名を段階表示し、発行nodeのobject payloadを整形・型保持して編集できる。
- payloadはJSON object・64KiB以内、event名は英字始まり最大128文字、subscriberは100件、連鎖は8 hop、outboxは10,000件へ制限した。lineage内Workflowを除外して直接・間接cycleを止める。Secretをpayloadへ含めても永続化前に伏字化し、発行／配送監査へ本文を含めない。配送例外は型名だけを保存し、例外本文に含まれ得る秘密値をDBへ残さない。
- 未完了deliveryはservice内loopが最大3回まで再送し、`DELIVERING`のまま停止した状態も同じevent IDで回収する。成功、部分失敗、失敗をsubscriber集計し、event ID／状態／購読・配送・失敗数／受信execution ID／失敗Workflow IDをnode出力へ返す。完了eventは7日後にdeliveryとともに清掃し、Workflow削除時も外部キー順に清掃する。Alembic `c2f8a6d53b91`にoutbox／delivery table、一意event ID、一意subscriber delivery、外部キーと検索indexを追加した。

検証: backend全405件、business event／migration集中10件、frontend TypeScript／production build成功。実ブラウザのPlaywright Chromium 1件で1280px Editorの受信種別段階表示／発行payload、320px公開Runnerから動的nested payloadを発行し、別の公開・有効化済みWorkflowが受信して型付き結果を返すところまで確認した。両幅で横overflow、console／page errorなし。実DBへ`DELIVERING`・attempt 1の配送を残してserviceを停止し、`./deck.sh`再起動直後にattempt 2で`DISPATCHED`、受信実行`event:workflow`、結果`recovered=after-restart`を確認した。Secret本文の非永続化、3回上限、保持清掃、migration upgrade／downgradeも自動試験済み。検証用Workflow／execution／event／delivery／user／sessionは清掃済み。次はsubworkflow map。

## App Studio Workflow契約ベース自動アプリ化 完了（2026-07-21 09:07 JST）

- WorkflowからProjectを作る2経路を、空の`pages: []`ではなく動作可能な推奨Application Specの自動生成へ変更した。trigger入力とoutput契約を同じJSON Schemaから取得し、型に応じたtext／number／boolean／select／JSON入力、同期Workflow endpoint、API-key認証、結果renderer、Home navigation、responsive Stack／Cardを初期適用する。生成runtimeが未対応のWorkflow固有schema拡張は安全に除外し、Editorの提案と生成ASP.NETフォームが同じrequest／response schemaを正とする。
- `xAppAdvisor`へ提案根拠となる入出力・control型・ready状態を保持し、App Studioは「自動構成済み」、入力／出力一覧、契約フォームをCanvas上で表示する。主要導線を「生成・動作確認へ」とし、ユーザーがゼロからPageを設計せず、そのままSource Preview／Buildへ進めるようにした。
- 気に入らない場合は「AIに再検討」からWorkflow契約と実行endpointを明示したpromptでSimple／Balanced／Denseの3案を生成し、既存のvisual diff／静的検証／選択適用を経由する。初期指示をWorkflow動作維持の再検討へ設定した。自由codeや自動適用は行わず、invalid案はApply不可のまま示す。Canvas Inspector、component追加、Undo／Redo、単一Saveによる任意修正も維持した。
- Workflowからの作成画面を「ほぼ自動でアプリを作成」として整理し、Draft／公開版のどちらも作成直後に動作可能なGUIへ進む。公開版bindingとimmutable version IDの既存境界、手動で与えたApplication Spec、WorkflowなしProjectの空Canvas互換は維持した。

検証: backend全401件、Application Builder集中33件、frontend TypeScript／production build成功。実serviceを再起動しAlembic `c2f8a6d53b91` headとservice activeを確認した。実Control DeckのPlaywright Chromiumで320pxの自動構成、text／number／boolean入力、出力、AI再検討の初期指示、mobile Inspectorによる手動修正導線、1280px再読込、生成workspace遷移、両幅の横overflowなし、console／page errorなしを確認した。実Ollama `qwen3.6-27b-q5_k_m:latest`へWorkflow契約付き再検討を依頼し、Simple／Balanced／Dense 3案（Patch 2／3／3件、静的検証valid 2案／Apply停止1案）をschemaどおり取得した。公式.NET SDK 8.0.423を一時配置し、自動Specの15-file ASP.NET sourceをwarning-as-errorでbuild（warning／error 0）、generated self-test成功、実Kestrelでhealth 200、未認証401、認証API 200を確認した。さらに実ブラウザで320pxからMessage／Count／Enabledを送信し`answer=Automatic app works`を結果regionへ表示、320／1280pxのSafe Areaと横overflowなし、console／page errorなしを確認した。一時SDK／生成物／server／テストuser／session／Workflow／Projectは削除またはTrashへ移動した。

## Workflow Phase 3 typed/versioned durable state 完了（2026-07-21 08:24 JST）

- `data.state`をWorkflow単位の期限なしDB-backed stateとして追加した。get／set／delete／incrementを提供し、同じnamespace／keyでもWorkflow間を分離する。初回setでstring／number／integer／boolean／object／arrayの型を固定し、型変更はdelete後だけ許可する。実行内`var.set`、browser reloadで消えるApplication Builder client state、TTL付きcacheとは別のserver state境界とした。
- 各stateは1から始まる単調versionを持つ。expected version 0はcreate-only、1以上は一致時だけset／increment／deleteするcompare-and-setとして扱い、不一致は`STATE_VERSION_CONFLICT`へ明示する。expected version未指定でもDB更新をversion条件付きにし、内部競合だけを上限付きretryする。integer／numberのincrementはread-modify-writeを同じCASで保護する。
- namespaceは英字始まりの最大64文字、keyは最大128文字、valueは256KiB、1 Workflow 10,000 keyへ制限した。valueは永続化前にSecretをredactし、Secret値をkeyへ含める操作は拒否する。set／increment／delete監査にはpayloadを含めずnamespace、key、entry ID、version、type、execution／node IDだけを記録する。Workflow削除時はstate entryをexecutionより先に清掃する。
- Editorへ操作、namespace、key、型、JSON value、increment delta、expected versionを設定する型付きnodeと詳細helpを追加した。Alembic revision `b7e1d94c2f60`でstate table、Workflow・execution外部キー、一意keyと検索indexを追加した。

検証: backend全400件、state／migration／dry-run集中13件、frontend TypeScript／production build成功。型固定、create-only、stale version拒否、delete後の型変更、Secret安全性、256KiB境界、8 worker・32同時incrementの欠落なし（value 32／version 33）、同一versionへの8同時CASが成功1／競合7となることを確認した。実DBはupgrade前backupとmanifestを0600で保存しSHA-256一致、revision headを確認した。実serviceへinteger stateをset後に`control-deck-web`を再起動し、expected version付きincrementで40→42／version 1→2を確認した。Playwright Chromium 1件で320px公開Runnerのset／increment、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／user／session／audit／state entry／一時credentialは清掃済み。次はevent。

## Workflow Phase 3 durable TTL cache 完了（2026-07-21 08:14 JST）

- `data.cache`をWorkflow単位のDB-backed TTL cacheとして追加した。set／get／delete／sizeを提供し、同じnamespace／keyでもWorkflow間を分離する。TTLは必須の1秒〜30日（既定1時間）とし、期限切れをget／sizeで返さずlazyに物理削除することで、期限なしの次項目`state`と責務を分けた。
- namespaceは英字始まりの最大64文字、keyは最大128文字、valueは256KiB、1 Workflow 10,000 keyへ制限した。valueは永続化前にSecretをredactし、Secret値をkeyへ含める操作は拒否する。set／delete監査にはpayloadを含めずnamespace、key、entry ID、execution／node ID、件数だけを記録する。Workflow削除時はcache entryをexecutionより先に清掃する。
- Editorへ操作、namespace、key、JSON value、TTLを設定する型付きnodeと詳細helpを追加した。Alembic revision `a4d9c73b1e52`でcache table、Workflow・execution外部キー、一意keyと期限・検索indexを追加した。

検証: backend全397件、cache／migration／dry-run集中13件、frontend TypeScript／production build成功。TTL失効、上書き、Workflow分離、削除、Secret安全性、256KiB／30日境界、8 worker・32同時setの単一key収束を確認した。実DBはupgrade前backupとmanifestを0600で保存しSHA-256一致、revision headを確認した。実serviceへset後に`control-deck-web`を再起動し、同じ値をgetできることを確認した。Playwright Chromium 1件で320px公開Runnerのset／get、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／user／session／audit／cache entry／一時credentialは清掃済み。次はstate／event。

## Workflow Phase 3 durable queue 完了（2026-07-21 08:04 JST）

- `data.queue`をWorkflow単位のDB-backed durable FIFOとして追加した。enqueue／dequeue／peek／sizeを提供し、同名queueでもWorkflow間を分離する。名前は英字始まりの最大64文字、itemは最大256KiB、1 queue 10,000件へ制限した。payloadは永続化前にSecretをredactし、enqueue／dequeueの監査にはpayloadを含めずitem ID等のmetadataだけを記録する。
- dequeueはSQLiteの単一`DELETE ... RETURNING`で先頭itemの選択と削除をatomicにし、lock／採番競合だけを上限付きでretryする。8 workerから32件を同時取得して欠落・重複がないことを確認した。Workflow削除時はqueue itemをexecutionより先に清掃する。
- Editorへ操作、queue名、enqueue値を設定する型付きnodeを追加し、公開Runnerと同じ実行contractへ接続した。Alembic revision `f310a4c29d7b`でqueue table、Workflow・execution外部キー、FIFO順序と容量検査用indexを追加した。

検証: backend全394件、queue／migration／dry-run集中12件、frontend TypeScript／production build成功。実DBはupgrade前backupとmanifestを0600で保存し、SHA-256一致、revision headを確認した。実serviceへenqueue後に`control-deck-web`を再起動し、同じ値をdequeueできることを確認した。Playwright Chromium 1件で320px公開Runnerのenqueue／dequeue、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflowは削除済みで、検証user／session／audit／一時credentialも清掃した。次はcache／state／event。

## Workflow Phase 3 durable human.form 完了（2026-07-20 23:05 JST）

- `human.form`を既存`WorkflowPause` checkpoint基盤へ追加した。承認と区別する`form`種別で入力待ちをDBへ保存し、ControlDeck再起動・in-memory task消失後も同じexecution IDから継続する。送信は通常経路、キャンセルはerror、期限切れはtimeoutへ進み、待機中の実行cancel、担当ユーザー限定、human interaction別の監査actionへ対応した。
- Editorは既存の型付き入力定義を再利用し、最大20項目のtext／paragraph／number／boolean／select／multi-select／date／datetime／JSON／key-value、必須、説明、初期値、最大長を設定できる。重複・不正key、未対応型、選択肢不足は公開前にblockingとする。Secret入力とfile uploadは永続フォームへ保存せず明示的に未対応とした。
- 実行時は入力定義をJSON Schemaへ変換し、Editor Debuggerと公開Runnerで同じresponsive formを描画する。必須値が揃うまで送信を無効化し、serverでも型・enum・追加field・64KiB上限を再検証する。入力は`{{form.response.field}}`から型付きで参照でき、既存`human.approval`のAPI response形と画面文言は後方互換を維持した。

検証: backend全392件、human pause／flow集中9件、frontend TypeScript／production build成功。submit、未完成入力のcancel、期限切れのerror／timeout経路を回帰した。実serviceでフォーム待機中に`control-deck-web`を再起動し、同一executionのpending form復元→schema検証済み送信→Return完了を確認した。Playwright Chromium 2件で既存承認回帰と、320px公開Runnerの必須／text／select／boolean送信、1280px結果表示、Editorのフォーム設定、両幅の横overflowなしを確認した。検証用Workflow／user／login session／audit／一時credentialは削除済み。

## Terminal mobile復元／入力／keyboard回帰 修正（2026-07-20 22:26 JST）

- 通信に問題がない状態でも、通常の1文字入力とBackspaceのたびに`scrollToBottom()`を実行して100,000行対応scrollbackを再描画していた。入力hot pathから除外し、実際に履歴中の場合だけ一度末尾へ戻すようにした。320pxで文字echo／削除を各5回実測し、10回すべて約13〜18msで反映した。
- tmuxの100,000行履歴は削除せず、Web初期復元だけを最新10,000行かつ512KiBの小さい方へ限定した。従来の最大4MiB一括転送・parser解析によるmobile main thread占有を回避し、実serviceの3,000行履歴再接続は412msでLIVEに戻った。
- software keyboardでVisual Viewportが縮小した際、backend resize ACK後までxtermの行数更新を待っていたため、待機中の入力行がkeyboard背面にclipされていた。先行outputとの順序をwrite queueで保ったxtermを先に可視領域へ合わせ、PTY側ACKまではinput FIFOだけを保持するようにした。表示位置と端末サイズの両方を崩さず、キー入力を不要に待たせない。

検証: Terminal backend 21件、backend全390件、frontend TypeScript／production build成功。実serviceのChromium 320pxで履歴再接続412ms、文字／Backspace応答13〜18ms、keyboard相当の430px高さでroot／helper／input行が可視範囲内、本文swipe、右端bar、IME composition、10回開閉、再接続／reload、100／300KB Paste、session切替、1280px Copy／remountを確認。Terminal E2Eは24成功、任意の10分soak等2件skip。検証用user／login session／audit／Terminalは清掃し、既存`490b7fd0`／`502a6ad9`とFrameDeck PID 183796を維持した。

## Workflow Phase 3 durable Delay／Try境界／System Trigger 完了（2026-07-20 21:50 JST）

- `control.delay`を承認と同じDB checkpoint基盤へ実装した。0.1秒〜7日を指定でき、実行を`WAITING`として永続化し、ControlDeck再起動・task消失後も同じexecution IDから期限到来時に再開する。待機中cancelに対応し、完了済み上流nodeを再実行しない。pause recoveryはexecutionごとの最新pauseだけを消費するため、Delay後に承認が続く場合も古いDelayを再消費しない。
- `control.try`は公開済みサブフローを実行する明示的な失敗境界として追加した。子実行の成功は`success`、typed error／失敗は`error`へ分岐し、execution ID、status、outputs、Error Contextを返す。擬似的な同時finallyは設けず、共通後処理は両branchを`control.merge`へ接続する順序が明確な構成とした。
- 既存`trigger`へ`system` modeを追加し、GPU／VRAM／diskのアラート発火、管理アプリのsystemd状態変化、llama-server状態変化、許可root内のfile作成・変更・削除を実イベントとして接続した。初回観測では発火せず変化時だけ起動し、file pathは`Path.resolve()`相当＋allowed root／deny root検証を通す。イベントpayloadは32項目・各2,000文字へ制限し、token／secret等のキーを除外する。
- schedule／Webhook／alert／systemの自動起動対象判定を編集中draftから最新published definitionへ統一した。Webhook tokenは公開履歴へ平文保存せずSHA-256だけを保持・照合する。EditorにはSystem監視対象、file path、Delay、Tryの設定とsuccess／error handleを追加し、全selectへID／accessible nameを付与した。「ディスク監視イベントを記録」sampleも追加した。

検証: backend全387件、Phase 3集中42件、frontend TypeScript／production build成功。実serviceのChromium 320pxでfile変更 → `system:file` → durable Delay → published Try subflow → success Returnを完走し、System／Delay／Try inspector、両branch、横overflowなし、console／page errorなしを確認した。App長時間稼働表示と推奨設定UIも同時回帰成功。検証用workflow／file／user／session／auditは清掃し、既存Terminal session 2件とFrameDeck PID 183796は維持した。

## Terminal入力遅延／App稼働時間フレーム／Workflow明示フロー制御（2026-07-20 21:17 JST）

- iPhone接続先への実測はRTT通常5〜12ms、packet loss 0%で、文字入力とBackspace遅延の主因が回線ではないことを確認した。software keyboard表示に伴うPTY resize中は入力順序保護barrierがSIGWINCH後の再描画を最大125ms待っていたため、resize ACKをxtermへ受信順にcommitした直後にFIFO入力を解放するよう変更した。ACK前は引き続き入力を保持し、local cell size確定前のechoは許可しない。
- Apps cardの稼働時間／開始日時からborder・背景の独立frameを外し、44px操作buttonと同じ行高を持つ等幅数字の補助情報へ変更した。Web／停止等の操作とは色と形で競合せず、長時間稼働でもcard高と左右位置を維持する。
- Workflow Phase 3へ終端専用`flow.return`、非retryのtyped `flow.error`、副作用なし`flow.note`、決定的`test.assert`を追加した。error code／retryable／有限detailsを標準Error Contextとnode runへ保存し、error branchから`{{node.error.code}}`等で参照できる。Returnの後続edgeはengine／semantic checkで拒否し、Editorでもsource handleを表示しない。外部サービス不要の「入力ガードと明示 Return」sampleを追加した。

検証: backend全384件、flow／terminal集中43件、frontend TypeScript／production build成功。実serviceへ反映し、Chromium 320pxでresize ACK→local commit直後のFIFO入力解放と、Dark／OLEDのApp cardを320／1280pxで確認した。既存Terminal session 2件とFrameDeck unitは維持。一時E2E user／login session／audit、E2E所有Terminalは限定清掃した。

## Terminal復元／mobile keyboard／scroll性能回帰 修正（2026-07-20 21:03 JST）

- tmux本体の100,000行履歴は維持しつつ、Web接続時にxtermへ一括復元するsnapshotを最新4MiBへ限定した。古い履歴をterminal sessionから削除せず、モバイルbrowserのparserを最大16MiBで長時間占有していた経路だけを除いた。
- Visual Viewportの連続resizeでgeometry timerを毎回後ろ倒しにする処理を、先頭eventから有界時間で処理するleading-edge coalescingへ変更した。外枠はIME変換中でも可視viewportのheight／offsetへ即時追従し、xterm cell／PTY resizeは変換終了まで固定するため、入力行と補助barがsoftware keyboardの背面へ残らない。
- 本文swipe時に毎frame実行していた履歴barの同期rect計測を廃止し、ResizeObserverで寸法をcache、onScroll表示更新をanimation frame単位へ集約した。本文gestureはlocal xterm scrollのまま1.35倍の軽い加速を加え、右端bar、入力focus、tmuxへの履歴移動をhot pathへ混ぜていない。

検証: Terminal backend 21件、backend全381件、frontend TypeScript／production build成功。実serviceのChromiumで320pxの初期描画、本文swipe 100ms未満、3000行再接続4秒未満、縮小viewport内の入力行、IME中のcell固定、keyboard相当の開閉10回で再接続／履歴再送なし、大容量Paste、session切替、1280px Copy/remountを確認した。Terminal E2Eは22成功＋修正後focused 1成功、service reloadと10分soakの2件は安全条件によりskip。既存`490b7fd0`／`502a6ad9`とFrameDeck PID 183796・起動時刻は前後で不変。

## Workflow durable pause／Artifact offload 完了（2026-07-20 21:03 JST）

- `WorkflowPause`をDB-backed checkpointとして実装し、承認文、担当者、JSON Schema入力、期限、応答、状態を永続化した。tokenは平文を保存せず生成直後にSHA-256化する。WAITING executionはservice再起動後も同じexecution IDで承認／却下／期限切れ／cancelでき、完了済み上流nodeを副作用込みで再実行しない。
- Editor／公開Runnerへ同じschema-driven承認フォームを追加し、string／number／integer／boolean／enum、required、64KiB応答上限、server側JSON Schema検証を共通化した。pause／resume eventをdurable sequenceへ記録する。
- `WorkflowArtifact`を追加し、redact後の即値が256KiBを超える場合はapplication-owned 0700 rootへ0600・atomic・no-followでJSON保存し、DB／execution contextにはfilename、MIME、size、SHA-256とIDだけを残す。1値32MiB／1node 20件を上限とし、containment、symlink、size、checksumをdownload前に再検証する。認可付き一覧／download、no-store／nosniff、監査、workflow削除時の実file清掃、UI download導線を実装した。
- Alembic revision `e2689f3f0c28`でpause／artifact tableを追加した。versioned DBのupgrade前にも検証付きSQLite backupを必須化し、実DBはbackup checksum一致後にrevisionを適用した。

検証: durable pauseの実service再起動越し承認、同一execution完了、上流trigger 1回を確認。pause／artifact／event集中7件、migration 4件、backend全381件、frontend build、320px公開Runner承認E2E成功。実DBは`e2689f3f0c28`、一時workflow／artifact／userは清掃済み。

## App長時間稼働表示／Dark・OLED視認性、主要UI整理（2026-07-20 20:19 JST）

- Apps cardを名称headerと、状態・稼働情報＋操作のcompactな2段へ再構成した。`実行中`等の状態badgeは折返し禁止とし、長時間稼働は`稼働 114日 7時間`、起動時刻は現在年を省略した`開始 3/28 10:23`として別々に読める。CPU／RAMはPCだけの補助値、モバイル操作はaccessibleなicon中心にし、44px hit areaを保ちながら長時間稼働cardを150px以下に収めた。
- Dark／OLEDではcard、稼働情報面、名称、主要値、補助値、操作buttonの背景色と文字色を明示し、黒背景で文字色が暗く継承される経路をなくした。FrameDeckの実process／systemd unitへ操作せず、API応答だけを114日稼働相当に置換するE2Eで再現した。
- Terminal一覧は3点menuを廃止して`🔧` Automationと`🗑️` Deleteを44pxの直接操作にした。workload／Web接続状態と、最終活動／作成日時・接続操作を別段に分け、複数badge時にも日付とbuttonが重ならない。Workflow nodeの推奨設定面は白へ寄らないsolid accent面＋白文字＋白buttonに変更し、Terminal Snippet editorもlight／dark双方で背景、border、本文、focusを明示した。

検証: frontend TypeScript／production build成功。Playwright ChromiumでFrameDeck相当の長時間稼働cardをDark＋OLEDの1280／320pxで確認し、状態1行、稼働面と操作段の非重複、横overflowなし、非黒文字色を検証した。Terminal／Snippet／Workflow視認性E2E 3件も320／PCで成功。検証用user／login session／auditは削除し、実FrameDeckと既存Terminal session 2件は変更していない。

## Alembic schema migration／既存SQLite保全 完了（2026-07-20 20:08 JST）

- Alembicを導入し、現行34 tableを明示するbaseline revision `dd6115224a90`を作成した。起動時は排他lock下でrevisionを検証・upgradeし、既存unversioned DBはSQLite backup APIでsnapshotを取得してからbaselineをadoptする。管理CLIは稼働中serviceと並行してschemaを変更せず、起動lifespanだけをmigration境界とする。
- backup前に空き容量を検査し、backup DBとmanifestを0600、directoryを0700で保存する。source／backup双方の`integrity_check`、`foreign_key_check`、全table件数、SHA-256を照合する。head revisionでもmodel table／columnとDB readを検証し、schema driftを`create_all`で隠さず起動失敗にする。旧light migrationは移行期間の互換no-opとして残した。
- 実DBをrevision管理へ移行し、`control-deck-pre-alembic-20260720T105852Z.db`とchecksum manifestを検証した。2回目以降の起動でbackupは増えずrevision一致を確認した。Alembicのlogging設定がapplication loggerを抑制しないよう統合した。

検証: backend全375件、migration subprocess 3件、migration＋auth 11件、`alembic check`成功。実serviceの再起動、health 200、revision／35 table、backup checksum／integrity／row count、再起動冪等性を確認した。

## Workflow Execution durable event／SSE 完了（2026-07-20 19:49 JST）

- `WorkflowExecutionEvent`と実行ごとの`last_event_sequence`を追加し、開始、状態遷移、node開始／終了、実行終了をDB commit後の単調sequenceとして保存する。payloadは再帰redact後64KiB、1実行2,000件、7日保持に制限し、並列DAGのsequence採番を直列化した。観測系の保存失敗でworkflow本体は停止しない。
- 認証・`workflows.edit`権限付きの`GET /workflow-executions/{id}/events?after_sequence=N`とSSE `.../stream`を追加した。`Last-Event-ID`／query cursorから最大200件ずつ欠落分を再送し、保持範囲外は`stream.reset`、15秒heartbeat、終了時は`stream.closed`を返す。同一Origin、no-cache、proxy buffering無効を強制した。
- Execution Debuggerは実行中の1.2秒ポーリングを廃止し、EventSource到着時だけlive detailと履歴cacheをまとめて更新する。切断中だけ3秒fallback、外部scheduleの新規実行発見は15秒、接続状態は小さなLive／Connecting indicatorで示し、通常操作を増やしていない。
- workflow削除時はeventをexecutionより先に削除するFK順序へ更新した。既存SQLiteは移行時の互換処理でsequence列を補い、新規event tableを作成した。schema管理は上記Alembic baselineへ移行済み。

検証: backend全372件、event focused 2件、frontend TypeScript／production build成功。実ControlDeck serviceを再起動しhealth正常、Playwright Chromiumで認証付きSSE接続、実workflow完了追従、320／1280px横overflowなし、console／page errorなしを確認した。再試験後journalにevent／workflow errorなし。検証用workflow／execution／event／user／sessionは削除済み。

## Application Builder Phase B3 Isolated Build／Self-test 完了（2026-07-20 19:31 JST）

- 保存済みSpecとWorkflowからserver側で再生成した決定的Source ZIPだけを入力に、C# Console／ASP.NET Coreを一時systemd user unitでoffline restore、warning-as-error build、生成self-testするdurable Build Jobを実装した。SDK allowlist、venv launcher、64MiB ZIP／500 file／128MiB展開、regular relative path、重複・暗号化・symlink・escape拒否を強制する。
- unitはbuild ID固有名とapplication-owned 0700 rootを持ち、read-only system／home、root限定write、NoNewPrivileges、IPv4／IPv6 network fail-closed、2GiB memory、128 tasks、CPU 200%、60〜3,600秒timeoutを固定argvで適用する。SDK子processの環境は専用HOME／TMP／NuGet、固定PATH／localeだけにし、Control Deck config／Secret／PYTHONPATHを継承しない。
- DBとatomic state fileでphase、exit、resultを追跡し、同時2件／Projectごと1件、実systemctl cancel、service中断検出、1MiB redacted journal、source／binary artifact、checksum、認証download、active削除拒否、監査を実装した。unit名と期待rootはID取得直後の最初のcommitへ含め、root作成前に停止した失敗記録も他unitへ触れず削除できる。
- App StudioのExport workspaceへ`Build & test`を主操作として置き、SDK／systemd／network／並列数、phase progress、Cancel、artifact、3点menu内log／Deleteを段階表示する。削除だけ確認し、320pxでは縦積み、PCでは既存workspace内cardに収めた。Linux／Windows向け生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。

検証: backend全370件、Application Builder focused 41件、frontend TypeScript／production build成功。実Control Deck serviceのPlaywright Chromiumで320pxからConsole buildを開始し、network probeを通過したoffline restore／build／self-test、phase、source／binary、checksum付きdownload、log、Deleteを確認した。2件目を実行中にCancelしてsystemctl stop／Cancelledを確認し、ASP.NET Coreも同じ隔離unitでbuild／self-test／binaryまで完了した。1280px横overflow、console／page errorなし。journalでvenv worker、self-test pass、resource使用量、cancel stopを確認した。検証用build root／unit／Project／Workflow／user／login session／auditを削除した。

## Terminal presentation boundary／Snippet Automation 完了（2026-07-20 19:18 JST）

- 18:20 checkpointの未解消項目は解消した。frontendは履歴復元後にxtermの端末自動応答だけをoverlay下で送信し、backendの`presentation_input_start`／`presentation_sync`が観測したPTY output sequenceをxtermが描画し終えるまでLIVE表示へ移らない。固定delayは使わず、初回接続と3,000行履歴の再接続で未完成frameを公開しない。補助key／Paste／本文swipe／右端scrollbarの回帰も維持した。実sessionが存在する環境でのservice reload E2Eは安全guardにより実行せず、通常の`./deck.sh`再起動前後で既存2 sessionが不変であることを確認した。
- Terminalへ共通Snippet libraryを追加した。名前／説明／tag／`{{parameter}}`と`{{cwd}}`等のbuilt-inを登録し、複数Snippetを順序付きでcomposeできる。実行前Reviewで展開後command、byte数、許可root内の正規化済みworking directory、session conditionを確認する。Review後だけRun／Scheduleを有効にし、通常画面は共通`Snippets`入口とsession cardの3点menuだけに抑えた。
- 既定は対話Terminalへ入力しないDetached runとし、Web processの子processではなく`systemd-run --user` transient serviceで実行する。venv Python、固定環境、timeout／resource上限、2MiB bounded log、暗号化command snapshot、checksum、durable statusと監査を実装した。command／parameter本文はunit名、unit file、監査metadataへ含めない。
- 上級の`Send to session`は正確なsession IDへtmux bracketed pasteする。即時実行は明示Review後のAlwaysも選べるが、予約実行では無条件投入を拒否し、`Shell ready`または`Program matches`を必須にした。condition不一致は入力せず`SKIPPED`として残す。
- 1回／毎日／毎週／隔週、timezone、次回時刻、PC停止中のcatch-up、timeout、pause／resume／edit／delete／Run nowを持つscheduleを追加した。schedule別systemd user timerをatomicな0600 unitとして管理し、登録後はtimerのactive状態まで確認する。PCは右side panel、320pxはSafe Area対応bottom sheet、Library／Run／Schedulesの段階開示と状態indicatorにした。

検証: backend全368件、Terminal focused 27件、frontend TypeScript／production build成功。実Control Deck service上のPlaywright Chromiumで320pxのSnippet追加、parameter検出、Review前Schedule無効、Detached transient service実行と出力、隔週timer登録、session別入口、1280px右panel、両幅の横overflowなしを確認した。systemd journalでもvenv workerの成功とtimerのactive化、その後のtest cleanup停止を確認した。検証用Snippet／Schedule／run log／Terminal session／user／login session／auditを削除し、既存`490b7fd0`（PEDS／node）と`502a6ad9`（ControlDeck／node）は再起動・E2E後も不変。

## Terminal replay／mobile input — 再起動前checkpoint（2026-07-20 18:20 JST）

履歴記録。未解消項目と再開指示は上の19:18完了記録で置き換え済み。

- 解消・実機確認済み:
  - 画面swipeはgesture中のxterm local scrollだけに統一した。旧実装は`touchend`で同じ移動量をtmux `history_scroll`へ再送しており、指を離した瞬間の後着全画面描画で文字がずれていた。二重送信を削除後、320px実tmux E2Eで応答100ms未満、touchend後のbuffer／DOM行一致を確認した。
  - 右端overlay barも描画完了後のbuffer／DOM行一致を確認した。
  - Paste／Enter／矢印等のmobile補助buttonは`pointerdown.preventDefault()`でxterm textareaのfocusを奪わない。keyboardが開いている時／閉じている時の双方でfocus状態、terminal root／host／screen、Visual Viewport、page scroll位置が不変のE2Eに成功した。
- 未解消:
  - 初回接続と大量tmux履歴への再接続は、LIVE公開後に約16msだけ未完成画面を1frame表示し、その次に完成画面となる。最新320px E2Eは両方とも可視signature 2種類で失敗している。サーバー再読み込みE2Eは安全上、隔離環境以外では実行禁止に変更したため未再検証。
  - 確定原因はxtermの端末自動応答。最新診断では`history_end`受信611.7ms直後に、復元中queueへ溜まった7／11／25／25 byteの制御応答を611.8〜611.9msでLIVE flushし、tmux全画面PTY frame 78／279／357 byteが613.2〜613.7msに到着している。この公開後handshake redrawが高速スクロールに見える。通常の履歴frameや単純なDOM renderer遅延ではない。
- 次の実装:
  1. `TerminalConnectionController.markLive()`が現在同時に行う「state=LIVE」「queuedInput flush」「renderer公開」を分割する。
  2. 復元中はユーザー操作不能なので、queued inputをxterm自動応答としてoverlay下で先にflushする。
  3. backendと明示的なpresentation handshake／PTY出力sequence境界を設け、その応答によるPTY writeとDOM／buffer行一致完了後だけLIVE化・overlay解除する。90〜600msの固定idle待ちへ戻さない。
  4. 初回接続、大量履歴再接続、隔離serviceでのserver reloadの3timelineを可視signature 1種類で通す。本文swipe、右端bar、補助keyの合格試験も同時回帰する。
- E2E安全境界:
  - 旧テストは閉じたtest sessionではなく一覧の`.last()`へ再接続し、afterEachが「現在選択中session」を削除していた。このためE2E userがsouten所有の`cdc502ad`と`a2302ae9`を誤削除したことを監査logで確認済み（tmux session自体は復元不能）。
  - 修正後は作成前後のsession ID差分を確認して`ownedSessions`へ記録し、接続・削除をそのIDだけにfail-closedで限定した。server reload試験は`CONTROL_DECK_E2E_ALLOW_SERVICE_RELOAD=1`かつ非E2E session 0件でなければskipする。
  - 停止時のユーザーsessionは`490b7fd0`（`/home/souten/PEDS`, node）と`502a6ad9`（`/home/souten/ControlDeck`, node）の2件。最新の安全化後E2E前後で同じ2IDが保持され、test session残留なしを確認した。これらを検証cleanupで削除しない。`502a6ad9`の履歴には旧テスト識別文字列が混入している。
- 現在の検証証拠: frontend production build成功、Playwright test list 24件読込成功、Terminal backend unit 19件成功。実機focused E2Eは補助key／本文swipe／右端barが成功、初回接続／大量履歴再接続が失敗。serviceは2026-07-20 18:10 JST反映版でactive。

## Terminal replay／mobile clipboard UX（旧記録・上のcheckpointで更新）

- frontendは初期表示と再接続の切断検知時点から、initial frame・reset・replay frameのwrite queue完了までxterm描画面を同期的に隠す。backendは隠れた状態でtmux attachのalternate-screen初期化を先に消費し、`history_reset`後の通常bufferへcapture履歴を復元する。xtermのwrite callbackはDOM rendererの描画完了より先に返るため、最終frame後はPTYの静止を待ち、bottomへ確定してrefreshした表示内容が3 animation frame連続で一致してから一度だけ復元面を表示する。これによりサーバー再読み込み時の空／部分描画による高速スクロールを見せず、同時に本文のscrollbackも失わない。
- モバイル補助barは頻用する`Enter`を常設し、PasteはClipboard APIからterminalへ直接送信するため中間paste欄を廃止した。Clipboard APIを利用できない非secure接続／権限拒否時だけOS keyboardの貼り付けへ案内する。Enter／矢印／Esc／Tab／Ctrl等の補助操作はxterm textareaへ再focusせず、software keyboardを開かない。
- Copyは削除せずgestureへ割り当てた。Pasteを28px以上上へswipeすると中間menuなしでCopy確認sheetを直接開き、選択範囲またはactive bufferをコピーできる。gesture後のsynthetic clickを抑止して誤Pasteを防ぎ、context menuと`ArrowUp`でも同じdialogを開ける代替経路、desktop headerの常設Copyを維持する。
- tmuxは固定画面を再描画するためxterm側scrollbackだけでは実出力を移動できない。本文の縦swipeを認証済みWebSocketの範囲付き`history_scroll` controlへ接続し、backendが固定argvのtmux copy-modeを最大100行ずつ動かす。次の本文tap／補助key／Paste時はcopy-modeを先に抜けて通常入力へ戻す。tmuxなしfallbackは従来のxterm local scrollを使う。

検証: Terminal backend unit 19件、frontend TypeScript／production build成功。実ControlDeck systemd user serviceをAPIから再読み込みし、Playwright Chromiumで320pxの直接Paste、Paste上swipeから直接Copy、swipe時の誤Pasteなし、Enter／矢印でtextarea非focus、実tmuxへ180行を出力した本文swipe、履歴表示後のEnterによるlive復帰、full page reload、server reload中のxterm非表示と履歴保持、1280pxのdesktop Copy／remountを確認した。server reloadは`requestAnimationFrame`ごとの表示timelineを記録し、約16ms周期で表示signature変化時のスクリーンショットを保存する回帰テストを追加した。修正前は復元解除後に空／部分描画と完全描画の2種類を再現し、修正後は完成済みの可視frame 1種類、可視状態でのDOM mutation 0件を確認した。対象E2E 5件成功。検証用terminal session、user、session、auditは削除済み。

## Terminal session identity／App Studio project deletion UX（2026-07-20）

- Terminal一覧をopaqueなsession ID中心から、foreground program、homeを`~`へ短縮した現在ディレクトリ、最終activity、tmux永続性を主情報とするcardへ再構成した。青はforeground programあり、緑はshell待機、赤は終了を表し、青のpulseは直近10秒にactivityがある場合だけに限定した。tmux clientの接続有無は別badgeにして「前景program」「最近の活動」「画面を開いている」を混同しない。PIDはAPIの診断情報には維持するが通常一覧へは表示しない。
- 接続中Terminalのheaderにもprogram／directoryと、Live／Reconnecting／ExitedのWebSocket状態、Running／Shell readyのworkload状態を併記した。接続中も3秒間隔でmetadataだけを更新し、terminal output本文を一覧判定へ流用しない。終了は常時buttonから3点メニューへ移し、主操作をConnect 1個へ整理した。
- App StudioのProject cardへ3点メニューを追加し、Deleteだけを破壊的な副操作として配置した。確認dialogはApplication Spec、生成Source、build履歴／成果物も削除対象であり取消不能と明示する。成功後は一覧を更新してtoastを出し、backendはactive buildを409で保護したうえでterminal build記録／成果物をcontainment確認して削除し、project削除を監査する。

検証: backend全351件、Terminal／Application Builder対象49件、frontend TypeScript／production build成功。実ControlDeck serviceを再起動しhealth 200、systemd user service activeを確認。Playwright Chromiumで、320pxのshell待機cardから`~/ControlDeck`の`sleep`実行へ変化するprogram／directory／pulse状態、接続中header、一覧復帰後の状態、1280pxを含む横overflowなしを確認した。App Studioは320pxでProjectの3点メニュー→Delete確認→一覧消去→API 404、toast、および1280px横overflowなしを確認した。対象E2E 2件成功。検証用Project、terminal session、user、session、auditは削除済み。

## Application Builder Phase E7／B2.5 Secret Injection／Bounded Side-effect Source（2026-07-20）

- Workflow compilerでSecret参照を実名から決定的な`SECRET_001…`へ変換し、生成source、manifest、README、監査へ実名・値を含めない境界を追加した。生成runtimeは`CONTROLDECK_SECRET_001…`からだけ値を読み、欠落、未宣言alias、64KiB超を停止し、最終JSONとfile write内容を値で再redactする。Secret参照はHTTP header／bodyだけへ許可し、literal credentialとURL／path／制御／出力での利用をblocking diagnosticにした。
- C# Console `controldeck.csharp-console/1.4.0`とASP.NET `controldeck.aspnet-api/1.0.0`の共通runtimeへ`http.request`と`file.read/write/exists/glob`を追加した。HTTPは固定HTTPSまたはloopback HTTP、method／header／body／response上限、redirect／cookie無効、credential query／header制約を持つ。fileはapplication-owned root相対、realpath containment、途中symlink拒否、2MiB write、4MiB append、atomic overwrite、bounded globを持つ。HTTPとwriteの監査は内容を含めず、origin／相対path、byte数、結果だけをrotation付きJSONLへ保存する。
- ASP.NETではSecretまたは副作用を含むWorkflowにAPI-key認証を必須とし、anonymous endpointへの公開を生成前に拒否する。manifestはopaque環境変数、side-effect区分、work／audit rootだけを記録する。App StudioのExportは`B2.5/E7`、project badgeは`E7`として表示し、生成処理自体はexecutor、network、subprocess、filesystem write、Secret解決を行わない。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統に維持した。

検証: backend全350件、Application Builder unit 32件、frontend production build成功。公式.NET SDK 8.0.423を既知サイズ216,832,058 byteとgzipで確認し、10-file Console／15-file ASP.NET／動的path containment fixtureをwarning-as-error buildしてwarning／error 0件、双方self-test成功。実ConsoleでSecret欠落exit 134、local HTTP responseと書込fileの値redaction、内容を含めないHTTP attempt／200とfile success監査、`..` escape／symlink traversalのexit 134拒否を確認した。実Kestrelでhealth 200、API keyなし／誤り401、正しいkey 200、response／file／auditへの値非露出を確認した。実ControlDeck serviceへ反映し、Playwright Chromiumの320／1280pxでunauthenticated source block、15-file E7 Preview、Secret実名非返却、opaque alias／side-effect／root manifest、横overflowなし、console／page errorなしを確認した。対象E2E 2件成功。検証用project／workflow／user／sessionを削除済み。次は隔離build。

## Workflow Test primary action UX（2026-07-20）

- Workflow Editorの頻用操作として`Test`を3点メニューだけに置かず、主操作`Run`の直前へ常時表示した。`Test`は公開版を変更しない下書き実行へ直接入り、`Run`は必要な保存・公開を経て実行する。両者の副作用差をtooltipとaccessible nameにも明記した。
- ツールバーの過密化を避けるため、`Test`はbeaker icon付きのoutline secondary action、`Run`は塗りのprimary actionとして優先度を分けた。320pxでも両操作を隠さず、戻る・Saveを含む操作領域を44px以上に統一し、モバイルの保存状態は省スペースなaccessible status dotへ変更した。
- 3点メニュー側は重複する`Preview & Test`を`Preflight Check`へ整理し、安全な実行前チェックを初期選択する低頻度の補助導線に限定した。頻用Testは1 stepへ短縮しつつ、実行しない検証も失っていない。

検証: frontend TypeScript build／production build成功。実ControlDeck serviceとPlaywright Chromiumで、320pxにおけるTest→RunのDOM／視覚順、両ボタンの常時表示、公開有無の説明、Testから下書きテストmodeが選択済みで開くこと、明示確認までexecutorを開始しないこと、320／390／768／1280px横overflowなし、既存3点メニュー経由の安全確認、Runの入力・公開・実行回帰を確認した。対象E2E 2件成功。検証用workflow／user／sessionを削除済み。

## Application Builder Phase E6 Typed API Query／Filter／Sort／Pagination（2026-07-20）

- Application SpecのQueryをEntity／同期APIの2 sourceへ拡張した。Entity Queryは最大20 filter、最大3 sort、offset paginationをtyped contractにし、field型ごとに許可operatorと値型をcompilerで検査する。API Queryはroute parameterなしの同期endpoint、固定JSON object input、dotted result pathを持ち、request JSON Schemaとresponse内のobject collectionを生成前に照合する。API側のfilter／sort／paginationはendpoint inputへ明示する設計とし、二重の取得条件やAPI collectionへの未対応mutationはblocking diagnosticで停止する。
- App StudioのQuery Editorをsourceごとの段階開示へ再構成した。Entityではfield／operator／typed value、sort direction、Previous／Nextの有無を編集でき、APIではendpoint、collection result path、固定request inputだけを示す。Query ID、共通limit／cache／auto-loadは一か所に保ち、無効な保存済み参照も`unavailable`としてround-tripする。Data TableはQuery側page sizeを正とし、Inspector側の重複設定を表示しない。
- `controldeck.aspnet-api/0.9.0`はEntity listへURL-encoded JSON filter／sortとlimit／offsetを送り、API Queryへ固定inputをPOSTしてresult pathを安全に辿る。生成SQLite runtimeはmetadataのfield whitelistだけからSQL columnを選び、値をすべてparameter bindingする。LIKE wildcardをescapeし、型／operator／件数／JSON sizeをserver側でも再検査する。sortはIDを末尾へ加えて決定的にし、不正条件は固定400とする。browser側はQuery ID＋offset単位のcache／pending、Previous／Next、固定loading／empty／errorを扱い、row値は`textContent`だけへ描画する。

検証: backend全348件、Application Builder unit 30件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423で21-file生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright ChromiumでEntityのboolean filter、rank降順、2件offset pagination、同期API固定input／nested collection、未知field filterの400拒否、意図的503の固定error、XSS文字列非HTML化、320／1280px横overflowなし、鍵file非生成を確認した。実ControlDeck serviceのPhase A／focused workspace E2Eでfilter／sort／paginationとAPI Queryの編集、workspace移動後の未保存値保持、単一Save、21-file E6 source preview、catalog v11、generator 0.9.0、query runtime manifestを確認し、検証用project／workflow／user／session／auditを削除した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はSecret injectionとside-effect nodeの安全な生成境界、その後の隔離buildへ進む。

## Application Builder Phase E5 Typed Entity Query（2026-07-20）

- Application Specへ`queries`を追加し、Query ID、取得元Entity、1〜100件のlimit、自動取得、`network-only`／page memory cache、0〜3,600秒の鮮度をtyped contractにした。取得元Entityの存在とCRUD `list`公開、Query ID重複、binding参照、Data Table以外のconsumer、存在しないcolumnをcompilerで拒否する。既存`entity:` bindingは後方互換として維持する。
- App StudioのData画面へQuery Editorを追加した。CRUD listを公開済みのEntityだけを選択肢にし、通常はQuery ID、取得元、件数、自動取得、cacheだけを編集する。Query IDはbindingを壊さないstable IDとして作成後read-onlyにし、Data Table Inspectorでは自由入力ではなく保存済みQueryを選択する。Query binding時は無効になるTable側page sizeを隠し、Query側のlimitを単一の設定元にした。
- `controldeck.aspnet-api/0.8.0`は`query:`へ結び付いたData TableをEntity list routeへ生成する。初期状態をloadingまたは明示的な未取得として示し、empty、内部response本文を出さない固定error、44px Refreshでの再試行を同じlive statusに表示する。memory cacheはQuery ID単位で鮮度を検査し、同時取得を共有する。手動Refreshとcreate／update／delete後はcacheを破棄して再取得する。row値は引き続き`textContent`だけを使い、localStorageや任意scriptへ保存しない。

検証: backend全347件、Application Builder unit 29件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423で生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright Chromiumで意図的な503から固定error表示、Refreshによるempty回復、認証付きEntity create後のrow再取得、HTMLとして解釈しないXSS文字列、320／1280px横overflowなし、鍵file非生成を確認した。実ControlDeck serviceのfocused workspace E2EでQuery編集、workspace移動後の未保存値保持、単一Save、21-file E5 source preview、catalog v10、generator 0.8.0、query runtime manifestを確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はAPI query sourceとfilter／sort／paginationのtyped contractを定義する。

## Application Builder Phase E4 Typed Client State／Focused Workspace UX（2026-07-20）

- AppBuilderを長い設定一覧から、目的別の`Create`／`Target`／`Export`／`Review` workspaceへ再構成した。作成画面は`Canvas`／`Data`へ分け、PCではAdd／Canvas／Inspectorの3ペイン、320pxではCanvasを主面としてAdd／Layers／Inspectを下部ボタンとボトムシートへ移す。下書きを保持したままworkspaceを移動でき、保存は上部の単一`Save changes`へ統一した。詳細なbinding／interaction／JSON／AI lockは段階開示し、通常編集時の判断量を減らした。
- Application Specへtyped client stateを追加した。stateは安定ID、string／number／integer／boolean／object／array型、nullable、初期値を持ち、個別64KiB／合計256KiB、有限数、ID重複、初期値型をcompilerで検査する。App StudioのData画面で同じSpec／Undo／Redo／dirty stateへ統合して編集し、component bindingと`state-set` eventは存在するstateだけを選択できる。
- `controldeck.aspnet-api/0.7.0`はText／Markdown／Metric／Text Inputの対応型だけを`state:` consumerとして生成する。初期値はscriptへ直挿入せずescaped JSON data attributeからmemory mapへ読み込み、表示更新は`textContent`またはinput valueだけに限定した。Text Inputのchange、Workflow成功response、固定化したHTTP／network errorをtyped stateへ設定し、reloadでSpec初期値へ戻す。型不一致、consumerなしのstate-set、response schema不一致は生成前のblocking diagnosticとする。localStorage／永続DB／任意scriptは使わない。

検証: backend全346件、Application Builder unit 28件、frontend production build、生成JavaScriptのNode構文検査に成功。公式.NET SDK 8.0.423で生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright Chromiumで320pxのtyped初期表示、inputから複数consumerへの更新、HTMLとして解釈しないXSS文字列、Workflow成功object state、安全な500 error state、localStorage非使用、reload reset、横overflowなしを確認した。実ControlDeck serviceへ反映し、AppBuilder全体回帰とfocused workspace専用E2Eを320／1280pxで実行し、単一Save、モバイルbottom sheet、下書き保持、20-file E4 source preview、catalog v9、generator 0.7.0、console／page errorなしを確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped query bindingを定義し、取得状態／再読込／失敗表示の境界を実装する。

## Application Builder Phase E3 Workflow Form／Typed Result／Navigation（2026-07-20）

- Design System catalog v8で`action.workflow-run`へ保存済みWorkflow binding、同期API endpoint ID、result labelをtyped propertyとして追加した。App Studio InspectorではWorkflow bindingと、そのWorkflowへ結び付いた同期endpointだけを候補表示し、Navigate eventのtargetも実在Pageから選択する。endpoint ID省略時は同じWorkflowのendpointがちょうど1件の場合だけ自動解決し、binding／endpoint欠落、不一致、複数候補、async、route parameter、50 field超、未対応schema typeは生成前のblocking diagnosticにする。
- `controldeck.aspnet-api/0.6.0`は同期endpointのJSON Schema objectからstring／enum／integer／number／boolean／object／array formを生成し、required、文字数、数値範囲、説明をHTML controlへ写す。JSON Schemaのrequired booleanはfalseも正しく送れるようcheckboxのHTML requiredとは分離し、object／arrayは型を確認してJSON parseする。送信は既存HttpOnly session＋same-origin custom headerを使い、server側C2 JSON Schema検証を最終境界として維持する。
- 成功responseはprimitive、object、object-arrayを`textContent`だけでDOMへ構築し、object 1,000 field、array 1,000 row／20 columnで描画上限を固定した。`innerHTML`やSpec由来JavaScriptは生成しない。`success`／`error`のNavigate eventはcompilerで存在確認済みPageを固定routeへ変換し、HTTP／network failureをerror側へ送る。typed stateの宣言・consumerがない`state-set`や再帰Workflow eventは動作したふりをせずblocking diagnosticのまま残す。

検証: backend全344件、Application Builder unit 26件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423を完全サイズ216,832,058 byte／公式Content-MD5／gzipで確認し、生成Web／self-testをwarning-as-error buildしてwarning／error 0件、self-test成功。実Kestrelで未認証401、session、CSRF headerなし401、request schema 400、required boolean=falseを含むtyped request／result、CRUD、CSP／nosniff／no-referrer、鍵file非生成を確認した。Playwright Chromiumで320pxのstring／enum／integer／number／boolean／object／array入力、typed result、320／1280px CRUD、success→Done／response schema 500→Errorsの固定navigation、横overflowなしを確認した。実ControlDeckへ反映し、認証付きE2EでWorkflow／同期endpoint選択、result label、catalog v8、21-file E3 source preview／ZIP、既存Entity／ASP.NET API／Console生成回帰、320／390／768／1280px overflowなしを確認した。検証用project、workflow、user、session、auditを削除済み。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped client state／query bindingを先に定義し、そのconsumerがあるeventだけを安全に生成する。

## Application Builder Phase E2 Entity Mutation／Browser Session（2026-07-20）

- Design System catalog v7でData Tableへ`enableCreate`／`enableUpdate`／`enableDelete`をtyped boolean propertyとして追加し、既存Inspector、共通Application Spec state、50段Undo／Redo、単一Saveへ統合した。各mutationはbinding先Entityが同じCRUD operationを明示公開する場合だけ生成し、未公開operation／Entity collectionなしはblocking diagnostic、認証なしmutationはwarningにする。
- `controldeck.aspnet-api/0.5.0`はEntity schemaからcreate／update formとrowのMore menuを生成する。string／integer／number／boolean／offset datetime／JSON、nullable／default／maxLengthを型別controlとserializerへ写し、POST／PATCH／DELETE後にlistを再読込する。Deleteだけ確認し、赤色はDeleteだけに限定する。Entity値は引き続き`textContent`だけで描画し、任意HTML／JavaScript／`innerHTML`は生成しない。
- Page＋`authentication: api-key`では、環境変数のAPI keyを初回sign-in時だけ固定時間比較し、32-byte乱数tokenのSHA-256だけをprocess内に最大1,000件／12時間保持するHttpOnly／SameSite=Strict cookie sessionへ交換する。API keyやtokenをlocalStorage、成果物、log、SQLiteへ保存しない。unsafe methodはsession cookieに加えて`X-Requested-With: GeneratedApp`を必須にし、loginはIP単位5回／5分、source数10,000、body 16KiBへ制限する。平文HTTP loginはloopback clientだけを許可し、network公開時はHTTPSを要求する。
- GUI hostへCSP、`frame-ancestors 'none'`、nosniff、no-referrerを追加した。Antiforgery／data-protection keyはprocess内repositoryだけに置き、user profileへ鍵fileを残さない。sessionは意図的に再起動で失効し、永続認証や利用者管理を装わない。

検証: backend全343件、Application Builder unit 25件、frontend production build成功。公式.NET SDK 8.0.423の完全サイズ／公式Content-MD5／gzipを確認し、生成Web／self-testを`--warnaserror` buildしてwarning／error 0件、self-test成功。実Kestrelで未認証401、cookie属性、CSRF headerなしmutation 401、create／list／update／delete、logout、6回目login 429、再起動後session 401、CSP header、user profile鍵file非生成を確認した。Playwright ChromiumでAPI-key sign-in、HttpOnly非露出、320／1280px CRUD、Delete確認、横overflowなし、console／page errorなしを確認した。実ControlDeckへ反映し、認証付き320px E2Eでcatalog v7の3 mutation設定、Preview反映、API-key GUIの21-file E2 source preview／ZIP、既存D2／ASP.NET Entity／Console生成回帰、320／390／768／1280px overflowなしを確認した。検証用project、workflow、user、session、auditを削除済み。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はE3でWorkflow trigger form／result renderingとnavigation eventの安全な生成境界へ進む。

## Application Builder Phase E1 Blazor GUI Source（2026-07-20）

- `controldeck.aspnet-api/0.4.0`へSemantic Component treeの決定的Blazor static SSR生成を追加した。Page route／navigation、Stack／Row／Grid／Card、Text／Markdown／Metric／Text Input、Entity collectionへbindingしたData TableをRazor／CSS／JavaScriptへ変換し、GUI＋Entity時はmanifestを含む21-file ZIPを生成する。HTML／Razor attributeをescapeし、Entity rowは`textContent`だけで描画して`innerHTML`や任意codeを生成しない。
- Data TableはD1 CRUDのlist operationと同じbase pathへ接続し、Entity field binding、未公開list、未対応component、event／Workflow actionは生成前diagnosticで停止する。browser session認証adapterは未実装のため、Pageを持つ生成GUIは明示的な`authentication: none`だけを許可し、API key GUIを公開済みと装わない。
- 320px mobile、768px tablet、1024px以上desktopのresponsive Grid、horizontal table scroll、44px control、Safe Area、dark／lightを生成CSSへ固定した。Razor antiforgeryがuser profileへ鍵fileを暗黙生成しないよう、process内だけの`IXmlRepository`を生成してdata-protection keyを再起動時に破棄する。現段階のGUIはstatic SSR＋read-only Entity listであり、form actionや長期sessionを保証しない。

検証: backend全342件、Application Builder unit 24件、frontend production build成功。公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、self-test成功。実KestrelでPage／CSS／JavaScript、anonymous Entity create／list、SQLite永続化を確認し、Playwright Chromiumで320／1280pxのEntity row表示、横overflowなし、Safe Area rule、console／page errorなしを確認した。生成時に検出した一時data-protection鍵は削除し、最終生成物ではuser profileへの鍵file非生成も確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はE2でform／CRUD mutationと認証adapterの境界を設計する。

## Application Builder Phase D2 Entity Editor／CRUD Table Binding（2026-07-20）

- App StudioへresponsiveなEntity editorを追加し、Entity／table、field ID／type／nullable／default／maxLength／unique／index、selfを含むEntity ID relation／delete policy、CRUD有効化／operation／base pathをtyped controlで編集可能にした。最初のEntity追加時はdatabaseをSQLiteへ明示し、予約ID、重複ID／table／route、空field／operation、default／relation不整合は保存ボタンを無効にしてbackend diagnostic前にも示す。Entity定義削除だけは確認を要求する。
- Entity editorを独立snapshot保存にはせず、既存Design editorと同じApplication Spec state、50段Undo／Redo、dirty判定、単一Saveへ統合した。E2Eで検出した「Entity保存が未保存Designを上書きし得る」競合をこの統合で解消し、Design／EntityのどちらのSaveからでも同一snapshotだけを保存する。
- Component inspectorのEntity bindingは自由入力から、現在のEntityとgenerator管理列を含むfield候補のselectへ昇格した。`entity:Project`はcollection、`entity:Project.name`はfield bindingとして保存し、backendも存在しないEntity／fieldを`BINDING_ENTITY_MISSING`／`BINDING_ENTITY_FIELD_MISSING`で拒否する。これにより既存CRUD Table／Data TableをD1 CRUD schemaへ安全に結び付けられる。

検証: backend全341件、Application Builder unit 23件、frontend production build成功。binding Entity／field参照のvalid／invalidをunit回帰した。実ControlDeck serviceへ反映し、認証付き320px E2EでDesignと同じsnapshotへのEntity追加、Data Tableの`Project.name` binding、保存／再読込、field追加、SQLite Entity入りASP.NET 16-file ZIP、Console 10-file ZIP、Linux／Windows向け2生成選択肢、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、audit、Playwright artifactは削除済み。次はPhase E1でSemantic Component／Entity bindingをASP.NET Blazor GUI sourceへ変換する境界を実装する。

## Application Builder Phase D1 Typed Entity／SQLite Migration／CRUD Source（2026-07-20）

- Application Specの`entities`を任意dictからtyped contractへ昇格した。Entityは安全なID／table名、最大100 field、string／integer／number／boolean／offset付きdatetime／JSON、nullable、明示default、string長、unique／index、Entity ID外部キーとrestrict／cascade／set-null、明示的に有効化するCRUD operation／base pathを持つ。generator管理の`id/createdAt/updatedAt`、重複field／table／route、default型・長さ、参照先・型・nullability、SQLite以外のdatabaseを保存前diagnosticで拒否する。
- `controldeck.aspnet-api/0.3.0`はEntityを含む時だけ固定`Microsoft.Data.Sqlite/8.0.29`を追加し、`Entities.generated.cs`を含む決定的16-file ZIPを生成する。EntityなしのC2機能は追加packageなしの15-fileを維持する。Linux／Windowsでsource生成可能な選択肢はC# ConsoleとASP.NET Coreの2系統に限定し、advisor-only targetを生成可能と表示しない。
- SQLiteは`CONTROLDECK_APP_DATA_DIR`またはアプリ専用LocalApplicationData rootを`GetFullPath`で正規化し、固定`application.sqlite3`だけをWAL／foreign key有効で開く。startup migrationはtransaction内でtable、追加列、unique／通常index、外部キーを決定的に適用し、checksum＋Entity IDを記録する。既存列の型／nullability変更、後付けrelation、defaultなしrequired列追加はsilent変換せずstartupを停止する。
- 認証方針を継承するparameterized CRUDはlist（limit 1〜100／offset）、read、create、partial update、deleteをoperation単位で公開する。bodyは2MiB、100診断、unknown／管理列、型、maxLength、offsetなしdatetimeを拒否し、UUID path、unique／FK競合を400／409へ分離する。deleteは同じSQLite transactionの`_controldeck_audit`へaction／Entity／resource ID／時刻だけを永続化し、入力値やSecretをlogへ出さない。OpenAPI 3.1も実際に公開したoperationとschema／認証だけを出力する。

検証: backend全341件、Application Builder unit 23件、frontend production build成功。Entity有無の15／16-file決定性、typed validation、package／DDL／parameter、OpenAPI CRUDを回帰した。公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、self-test成功。実Kestrelで未認証401、入力400、create／list／read／patch／delete、default、unique 409、FK 409、cascade delete、UUID 400、WAL、追加列default migration、永続delete auditを確認し、既存boolean列をstringへ変える非互換migrationがstartupを明示停止することも確認した。実ControlDeck serviceへ反映し、認証付き320px E2EでSQLite Entityを含むASP.NET 16-file ZIP、Console 10-file回帰、Linux向け2生成選択肢、320／390／768／1280 overflowなし、console／page errorなしを確認した。次はPhase D2でEntity／relation／CRUD公開範囲を編集するGUIとCRUD Table bindingを実装する。

## Application Builder Phase C2 Schema／Durable Background Job Runtime（2026-07-20）

- `controldeck.aspnet-api/0.2.0`へ更新し、固定metadataの15-file ZIPへdependency-freeな`JsonSchema.generated.cs`と`BackgroundJobs.generated.cs`を追加した。manifest phase、capability、previewのblocking phaseもC2へ同期し、C# ConsoleはB2のまま分離する。
- API requestはroute値を注入する前のJSON body、responseはsync結果とasync完了結果を実際にschema検査する。OpenAPI 3.1にも同じschemaを出力し、request違反は400、response違反は値を露出せず500／failedへする。型、enum／const、object properties／required／additionalProperties、array items／unique／件数、string長、数値範囲／multipleOf、allOf／anyOf／oneOf／notを最大64深度・100診断・10,000 array itemで扱う。regex方言、format、`$ref`等の未対応keywordは保存／生成前のstructured diagnosticで停止する。
- chunked bodyを含めて最大2MiBをbufferし、空body、JSON object、Content-Type、不正JSONを区別する。API keyは引き続き環境変数だけから固定時間比較し、asyncのcancel理由をmanual、timeout、Application shutdownで分離した。例外値を公開／記録せず、内部logはjob IDと例外型だけに限定する。
- background job contractへIANA time zone、固定input、`skip/queue-one` concurrency、`skip/run-once` catch-upを追加した。manual jobは認証付き`/api/background-jobs/{definitionId}/run`、定義一覧、共通status／SSE／cancelを持つ。interval、daily、数値／月・曜日名／list／range／stepを扱う5-field cronはASP.NET `BackgroundService`で実行し、ブラウザやControlDeck Web processの子processにしない。
- schedule stateは`CONTROLDECK_APP_DATA_DIR`またはアプリ専用LocalApplicationData rootを`GetFullPath`で正規化し、その直下の固定ファイルだけへ1MiB上限でatomic move保存する。last start／evaluation、実行中pending、queue-oneを永続化し、再起動重複を抑止する。実行中crashはpendingを1回再実行し、DST invalid daily時刻は誤ったoffsetで実行せず、cronはUTC minuteを各time zoneへ変換して評価する。

検証: backend全340件、Application Builder unit 22件、frontend production build成功。同一入力ZIP、schema keyword、daily／cron／time zone、Workflow参照、予約route、15-file preview／downloadを回帰した。一時配置した公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、schema／wrapped cron self-test成功。実Kestrelで未認証401、request schema 400、正常response、response schema 500、manual／interval完了、manual overlap 409、cancel理由、OpenAPI、state保存、60秒interval再起動重複なし、実行中強制終了後のpending 1回復旧を確認した。実ControlDeck serviceへ反映し、認証付き320px E2Eでsupported schema＋manual jobを含むASP.NET 15-file ZIP、Console 10-file回帰、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、audit、一時SDK／生成物は削除済み。次はPhase D1でtyped Entity／SQLite migration／CRUD contractを定義し、API runtimeとGUI生成の共通永続化境界を実装する。

## Application Builder Phase C1 ASP.NET API Source Generator（2026-07-19）

- ASP.NET source generatorへ任意handler codeや曖昧なdictを渡さないため、Application Specへframework非依存のtyped `apiEndpoints`／`backgroundJobs` schemaを追加した。APIはstable ID、POST path（固定segment／`{parameter}`）、Workflow binding、sync／async、inherit／anonymous認証、request／response schema、0.1〜7200秒timeoutを持つ。JobはWorkflow binding、manual／interval／daily／cron、schedule、enabled、timeoutを持つ。
- validatorへ正規化したmethod＋route重複、Workflow binding参照、path parameter重複、明示anonymous公開warning、job Workflow参照、非manual schedule必須、interval秒数検査を追加した。生成時も予約route、100 endpoint上限、単一Workflow snapshotを検査する。local認証adapter、GUI Page、Entity、scheduled job、runtime JSON Schema validationは対応したふりをせずblocking diagnosticにする。
- `controldeck.aspnet-api/0.1.0`を追加し、`aspnet-blazor` targetのsource capabilityをC1 API範囲でavailableにした。同じ入力から固定metadataの13-file ZIP、net8.0 Web／self-test project、Dockerfile、README、checksum manifestを決定的に生成する。Blazor GUI生成はD/E〜G2、`aspnet-react`は未対応のまま分離する。
- 生成Web hostは2MiB request上限、`/healthz`、OpenAPI 3.1、route parameterを入力へ統合するsync／async POST endpointを持つ。API keyは成果物へ埋め込まず`CONTROLDECK_APP_API_KEY`から受け、`X-API-Key`を固定時間比較する。endpoint単位のanonymous指定をOpenAPIと実行時の双方へ反映する。
- asyncは最大1,000件のin-memory job、status、SSE、DELETE cancel、endpoint timeout、Application shutdown cancelを生成する。job作成時の認証方針をstatus／SSE／cancelへ引き継ぎ、終了済みcancelとの競合も409へ閉じる。scheduled background jobと永続queueは次のC2境界とする。

検証: backend全340件、Application Builder unit 22件、frontend production build成功。同一入力ZIP一致、認証／予約route／GUI・schema・schedule block、source preview／download／監査を回帰した。一時配置した公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、双方warning／error 0件、self-test成功。実Kestrelでhealth、OpenAPI、未認証401、認証付きasync作成、DELETE cancel、`cancelled` status／SSEを確認し、一時SDK・生成物・serverを削除した。実ControlDeck serviceへ反映し、認証付き320px E2EでASP.NET 13-file preview／ZIP、Console 10-file回帰、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。次はPhase C2のscheduled background job／永続実行境界とruntime JSON Schema validationを設計・実装する。

## Application Builder Phase B2.4 Nested Loop Runtime（2026-07-19）

- C# Console generatorを1.3.0へ更新し、`control.loop`の`count`／`foreach`、`body`／`done` edgeをnative source対象へ追加した。loop自体は既存engine同様に通常nodeのretry／timeout permitを占有せず、各反復が独立したnested DAGを実行する。
- countは1〜100へclampし、foreachはJSON array／単一JSON値／非空行listを受け付けて最大100件とする。`parallel`は1〜5のbatch実行とし、body nodeは既存generated runtimeの全体4並列Semaphoreを共有するため、loop同士や通常nodeとの過剰並列を防ぐ。
- 各反復は親node outcomeとnamed variableのsnapshotを持ち、`{{loop.index}}`、`{{loop.item}}`、`{{loop.total}}`を公開する。反復結果は`index/item/outputs`としてdefinition順と独立した入力順で集約し、完了後は既存engine互換で最終反復のnode output／named variableだけをdone側contextへ引き継ぐ。
- loop完了時はbody edgeをouter schedulerへ再送せず、done／無指定edgeだけをliveにする。nested loop、body内condition／merge／retry／timeout、stop時の全体cancelは同じrecursive graph runnerを利用する。未知mode、非整数count／parallel、loop以外をsourceとするbody／done branchは生成前diagnosticで停止する。
- generated workflow sourceへ`#nullable enable`を追加し、C# `net8.0`のnullable annotationをwarningなく有効にした。

検証: backend全337件、Application Builder unit 19件、frontend production build成功。一時配置した公式.NET SDK 8.0.423でgenerated loop projectを`--warnaserror` buildし、warning／error 0件、foreach 2並列の実CLI結果`{"answer":"1:b/2"}`、generated self-test成功を確認後、一時SDKと展開物を削除した。実ControlDeck serviceへ反映し、認証付き320px E2Eでbranch／merge／data.template／count loop body・doneを含むWorkflowのPreflight ready、10 file Preview、1.3 source ZIP download、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。次はPhase C1のASP.NET API／health／OpenAPI generator基盤へ進む。

## Application Builder Phase B2.3 Named Variable／Pure Data Runtime（2026-07-19）

- C# Console generatorを1.2.0へ更新し、成功nodeの`config.output_var`をnamed output variableとして保存するgenerated runtimeを追加した。`{{vars.name.path}}`はnode ID参照と同じdot path／JSON array indexで解決し、依存nodeの完了後、後続nodeをscheduleする前に公開する。
- `data.transform`の`json_parse/json_get/json_set`、`data.template`のtext／JSON出力、`data.filter`のexists／truthy／equals／not_equals／contains／数値比較・unique・stable sort・limit、`data.aggregate`のcount／sum／avg／min／max・groupをnative source対象へ追加した。既存executorと同じUTF-8 2MiB、array 10,000件、number型、dot path上限契約を生成sourceへ保持する。
- templateは通常node、named variable、`data`疑似contextを共通の非再帰reference resolverで扱う。filterのJSON同値比較、object key／array contains、canonical unique key、型順sortと、aggregateの入力順groupを追加依存なしの`System.Text.Json`で決定的に生成する。
- JSON Schema validation、CSV相互変換、未知filter／aggregate操作、未知template format／sort orderは、互換性のないstubへ置換せずconfig単位のblocking diagnosticにする。Secret、human approval、loop、side-effect node、build／packageは引き続き未対応として明示停止する。

検証: backend全336件、Application Builder unit 18件、frontend production build成功。同一入力ZIP byte一致を維持し、named variable、全4 pure data node、2MiB／10,000件上限、unique／sort／group、非対応operation停止を生成sourceとdiagnosticで回帰した。実ControlDeck serviceへ反映し、認証付き320px E2Eでbranch／mergeのnamed variableから`data.template`を経由するWorkflowを作成し、Preflight ready、10 file Preview、1.2 source ZIP download、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。ホストに.NET SDKがないためgenerated sourceの実buildは未実施で、capabilityもbuild unavailableのまま。次はB2.4でloop／残りpure nodeのruntime境界を設計し、その後ASP.NET generatorへ進む。

## Application Builder Phase B2.2 Branch／Merge／Execution Policy Runtime（2026-07-19）

- C# Console generatorを1.1.0へ更新し、`condition.if`と`control.merge`を正式native source対象へ追加した。conditionは`eq/ne/contains/gt/gte/lt/lte`、mergeは`wait_all/collect/first_success/first_complete/quorum`を既存engineと同じcontractで生成する。
- generated runtimeへDAG schedulerを追加した。triggerだけを開始点にし、最大4並列、最初のlive入力、`join=all`、dead-edge伝播、未選択branchのskip、definition順tie-breakを実装。conditionの無指定edgeはtrue、error／timeout route、timeout専用edgeがない場合のerror fallbackを既存engine semanticsに合わせた。
- nodeごとのretry count（最大5）、retry wait（最大300秒）、timeout（0.1〜7200秒）、`on_error=stop/continue/branch`を生成する。各attemptへlinked cancellationを作成し、timeout時にunderlying Taskもcancelしてからretryする。metered nodeはSemaphoreSlim、trigger／waitはunmeteredとする。
- Workflow IRのretry有効時の既定waitをengineと同じ5秒へ補正した。生成runtimeの既定timeoutは通常120秒、`util.wait` 3700秒。stop時は残りgenerated taskをcancelして待機し、browser／ControlDeck processから独立して終了する。
- generator preflightは対応済みbranch／execution policyのblockを解除し、不正branch、未知`on_error`、human approval、named output variableは引き続きblocking diagnosticにする。generator versionはcapability、manifest、generated metadataのすべてで1.1.0へ同期した。

検証: backend全335件、Application Builder unit 17件、frontend production build成功。true／false edge、wait-all merge、retry既定5秒、timeout、continue、4並列、error/timeout routeを生成source上で回帰し、同一入力ZIP byte一致とplaceholder collision防止も維持。実ControlDeck serviceへ反映し、認証付き320px E2Eでcondition→true/false→merge Workflowを作成し、Platform Advisor、Preflight ready、10 file Preview、1.1 source ZIP downloadまで確認した。F3.1〜F3.7、320／390／768／1280 overflow、console／page errorも同時回帰し、検証用project、workflow、user、session、auditは削除済み。次はB2.3のnamed variable／pure data node拡張。

## Application Builder Phase B2.1 Deterministic C# Console Source Generator（2026-07-19）

- `csharp-console`を最初の正式source targetとし、保存済みApplication SpecとWorkflow snapshotだけからC# `net8.0` projectを決定的に生成するbackend generatorを追加した。対応nodeは`trigger`、`util.wait`、制限付き`util.now`、`var.set`、制限付き`string.op`、`output.render`、`signal.display`。node実行やLLMによる自由code生成は行わない。
- Managed (`Generated/`、project file)、Extension (`Extensions/`)、Config (`appsettings.json`)を分離し、generator version、Spec／Workflow checksum、全file SHA-256、managed／extension／config一覧を`.controldeck/generation-manifest.json`へ記録する。ZIP entryはpath順、固定timestamp、固定permission、無圧縮として、同じ入力から同じbyte列とarchive checksumを生成する。
- source projectへCLI entrypointとNuGet追加依存のないgenerated self-test projectを含めた。app identifierはC# identifierへ再sanitizeし、ZIP pathを生成器管理下に固定する。source生成はメモリ内だけで、filesystem write、subprocess、network、executor、Secret解決を行わない。
- `GET /application-projects/{id}/source-preview`でfile一覧、checksum、manifest、diagnosticを副作用なし確認し、`POST /application-projects/{id}/source-archive`で認証・edit権限・CSRFを通してZIPを取得する。ZIP生成はtarget、generator、checksum、file数、byte数だけを監査し、source本文やSecretを監査logへ保存しない。
- Secret参照、複数Workflow、未対応node、branch edge、retry／timeout／continue policy、未対応`string.op`／日時formatはblocking diagnosticにする。`csharp-console`のsource matrixだけをavailableへ変更し、build、package、signingは未実装のまま成功扱いしない。
- App Studioへ保存済みConsole target用のSource Preview、Managed／Extension／Config file一覧、4種checksum、Source ZIP生成を追加した。Platform Advisorでlinux targetへ切替、Preflight ready、target保存、Preview、Downloadの順に進み、未対応Specでは生成を無効化してbackend diagnosticを表示する。

検証: backend全334件、Application Builder unit 16件、frontend production build成功。同一入力のZIP byte完全一致、固定entry metadata、manifest checksum、未対応node／Secret停止、認証API、2回の監査記録を確認。実ControlDeck serviceへ反映し、認証付き320px E2Eでweb向けB1 blockからlinux C# Console推薦へ切替、Preflight ready、target保存、10 file Preview、実ZIP downloadを確認した。F3.1〜F3.7、320／390／768／1280 overflow、console／page errorも同時回帰し、検証用project、workflow、user、session、auditは終了時に削除した。次はB2.2 branch／execution policy／native node拡張、その後ASP.NET generatorとbuild境界。

## Application Builder Phase B1 Platform Advisor／Preflight（2026-07-19）

- backendのframework registryを10候補のSDK、feature、対応platformと`spec/source/localBuild/remoteBuild/package/signing/store/stability` matrixへ拡張した。host SDKはread-onlyに検出し、Apple targetのmacOS host制約と未提供generatorを成功扱いしない。
- 対象OS、offline、local file、tray、background、GPU、embedded server、store、優先言語、native feel、web reuse、package sizeを固定registryだけで決定的に採点するPlatform Advisor APIを追加した。全候補の順位、理由、制約、代替を返し、同一入力は同一結果になる。
- App Studioへschema/capability駆動のPlatform Advisorを追加した。推薦を初期選択しつつ、複数frameworkのoverride、要求platformのcoverage確認、target保存を行える。未実装の生成／build操作は表示しない。
- 保存前Preflightは既存Spec validationとtarget matrixを統合し、framework、platform、SDK、host、source generatorのblocking diagnosticを返す。executor、network、subprocess、filesystem write、Secret解決はすべて行わず、その副作用なし状態もresponseで明示する。
- target validatorへplatform必須・framework対応platform検査を追加した。B1では全frameworkの`source`を`unavailable`に保ち、`SOURCE_GENERATOR_UNAVAILABLE`で明示停止する。次はB2のdeterministic source generator基盤。

検証: backend全332件、Application Builder unit 14件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2Eで決定的な`aspnet-blazor`推薦、ASP.NET Core + Blazor／React-PWAの2 target override、Preflightの生成block、保存後APIのtarget 2件を確認。既存F3.1〜F3.7を同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake generate/build/publish UIなし。検証用project、workflow、user、session、auditは終了時に削除した。

## App Studio F3.7 Accessibility Audit／Keyboard Reorder（2026-07-19）

- backend Design System catalogをv6へ更新し、通常文字contrast 4.5:1、大きな文字3.0:1、touch target 44px、focus indicator 2pxを監査閾値として配信。frontendへ閾値を直書きしない。
- EditorのAuditはDefault Previewの実DOMを走査し、annotateされた実表示文字のcomputed foreground／opaque background／継承opacityからWCAG contrastを計算する。semantic interactive controlは実bounding box、`tabIndex`／disabled状態、focus後のcomputed outline／box-shadowを検査し、contrast／focus／keyboard／touch別に件数とdiagnosticを表示する。
- Text Inputを副作用のないread-only focusable controlとしてPreviewし、Workflow action、Event付きTable／Chartへ44px以上、keyboard到達性、2px focus outlineを付与。AuditはDefault stateへ戻して実行し、Workflow、Event、navigation、network、DB、LLM、Secretを実行しない。
- accent preview色をwhite小文字との4.5:1以上へ調整し、blue／violet／emerald／amberの全presetで共通rendererが監査可能な配色にした。任意CSSや色値は引き続きSpecへ保存できない。
- Component Treeの各非root部品へ`Alt+ArrowUp`／`Alt+ArrowDown`と`aria-keyshortcuts`を追加。dragやtouch向けInspector Moveと同じ単一操作、Undo／Redo、dirty、Saveへ接続し、keyboardだけでも兄弟順を変更できる。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでTreeのcomputed 2px outline、Alt+↑／↓によるPreview DOM順の往復、Preview inputのcomputed 2px outlineと実寸44px以上、contrast／focus／keyboard／touch audit全passを確認。F3.1〜F3.6、保存／reload、Binding/Event、Parameterized Template、Visual Diff、3案比較、Patch部分適用、lockも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.6 Parameterized Composite／Pattern（2026-07-19）

- backend Design System catalogをv5へ更新し、5 Composite／4 Patternすべてへ型付きparameter schemaを追加。key、label、string／number／boolean／enum、default、required、文字数／数値範囲、選択肢、固定Component ID＋property targetをbackendだけで定義する。
- KPI、Job Status、Log Viewer、CRUD Table、Timeline、Dashboard、Settings、Wizard、Launcherで、title、accessible label、action label、help／empty text、初期数値等を挿入前に設定可能にした。既定値だけでも従来と同等の有効なtemplateを生成する。
- frontendはcatalogからparameter dialogを生成し、必須、型、範囲を挿入前に検査する。任意JSON Pointer、Binding、Event、式、HTML、code targetは受け付けず、catalogが宣言した既存Semantic Component propertyだけへ値を適用してからIDを衝突なく再採番する。
- parameter展開を既存の単一template挿入操作へ含め、Undo／Redo、dirty判定、明示Save、backend Spec validation、reload復元を維持する。parameter値やtemplate metadataをruntime実行せず、最終的な通常Component propertyだけをSpecへ保存する。
- focus／keyboard／contrastの自動auditはF3.7で実装した。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでKPI必須値のInsert停止、`CPU Load`／`42`、Dashboardのtitle／metric／chart／table labelを設定し、Preview反映、保存、reload後の表示・chart accessible name復元を確認。F3.1〜F3.5、Binding/Event、Visual Diff、3案比較、Patch部分適用、lockも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.5 Visual Preview Diff／3案比較（2026-07-19）

- EditorのSpec描画を共通`AppSpecPreview`へ分離し、通常Editor、Patch Review、AI Design Proposalが同じSemantic Component、Design Token、Preview state、responsive Grid rendererを使用する。比較画面だけの別実装や静的mock画像を持たない。
- Patch ReviewへBefore／Afterの実画面比較を追加。選択中Patchをbackendで適用・再検証した`patchedSpec`だけをAfterへ渡し、Mobile／Tablet／Desktopを同じviewport条件で切り替える。選択変更時は従来どおり旧Previewと視覚比較を破棄し、再PreviewまでApplyを無効にする。
- AI DesignのSimple／Balanced／Dense各案へ実画面Previewを追加し、3案を同一viewportで並べて比較できる。各案のvalidity、diagnostic、理由、Patch件数、Review導線を維持し、自動適用はしない。
- 共通Previewは読み取り専用で、Workflow、Event、navigation、network、DB、LLM、Secret解決を実行しない。source generator／buildの未実装表示も維持する。
- template parameterはF3.6で実装し、focus／keyboard／contrastの自動auditは後続とする。

検証: backend全331件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでPatch Beforeに`Hello App Studio`、Afterに`Patched App Studio`を同時表示し、Mobile→Desktop切替後に選択PatchだけをApplyできることを確認。schema準拠の決定的proposal応答でSimple／Balanced／Dense 3案のMobile PreviewとDesktop切替も確認した。F3.1〜F3.4、保存／reload、Binding/Event、lock拒否も同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.4 Structured Binding／Event Editor（2026-07-19）

- backend Semantic Component catalogをv4へ更新し、11種のBinding sourceをlabel／reference label付き定義として配信。input／workflow action／table／chartごとのeventと、Run workflow／Navigate／Set state actionの許可組合せ・target種別も同じcatalogを正とする。旧`bindingSources`は互換読取用に維持する。
- validatorはBindingのsource／reference、空値、長さ、Secret参照と、component固有event、許可action、target形式、Workflow／Page参照、State keyを保存・Patch・AI proposalの全経路で検証する。任意handler code、式、Secret、runtime実行は受け付けない。
- Inspectorへsource／referenceの構造化Binding editorと、event有効化／action／targetの構造化Event editorを追加。catalogからcontrolを生成し、既存`source:reference`文字列とobject形式を読み取り、保存時は後方互換な文字列へ正規化する。
- 全controlを44px touch targetとし、既存Undo／Redo、dirty判定、明示Saveへ接続。Eventの初期targetは決定的な安全値を使い、PreviewではWorkflow、navigation、network、DBを実行しない。
- 視覚diffはF3.5で実装し、template parameter、focus／keyboard／contrastの自動auditは後続とする。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでWorkflow output BindingとChange→Set state Eventを編集し、保存、reload復元を確認。F3.1〜F3.3、Patch部分適用、lock、AI Designも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.3 Structured Grid／Table／Chart Editor（2026-07-19）

- Responsive Gridのmobile／tablet／desktop列数、Data Tableのcolumn、Line Chartのseriesを専用property schemaへ昇格。frontendが独自の列型・tone・上限を持たずbackend catalogを正とする。
- backendでGrid 1〜12列、Table最大50列、Chart最大20 series、identifier、重複key、表示label、column type、semantic toneを検証。保存、Patch Review、AI proposal Previewの全経路に適用する。
- Inspectorへ3 breakpoint数値control、Table column／Chart seriesの追加・削除、key／label、type／tone editorを追加。全controlをmobileで44px以上とし、既存Undo／Redo、dirty判定、明示Saveへ接続した。
- Previewは選択中viewportの列数を実際のGridへ反映し、設定済みTable headerとChart series名を表示する。静的sampleだけを使い、Workflow、network、DB、Secretは実行しない。
- 自由formatter code／CSS色／式は受理しない。binding/event専用editorはF3.4で実装し、template parameter、視覚diff、focus／contrast auditは後続とする。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでGrid mobile 2列、Table column、Chart seriesを編集し、Preview反映、保存、reload復元を確認。F3.1/F3.2、Patch部分適用、lock、AI Designも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.2 Schema-driven Properties／全状態Preview（2026-07-19）

- backend Design System catalogをv3へ更新し、全11 Semantic Componentへstring／multiline／boolean／number／enum／JSONのproperty schema、required、選択肢、数値範囲を追加。保存・Patch・AI proposalの全経路で同じschemaを再検証する。
- Inspectorへschema駆動fieldを追加。単純項目はラベル付き44px controlで編集し、columns／series等と将来fieldは既存Properties JSONから編集できる。frontendにcomponent別formを二重定義しない。
- Previewへbackend catalog由来のDefault／Loading／Empty／Error／Disabled selectorを追加。同じSpecとMobile／Tablet／Desktop frameでskeleton、空データ、user-facing error、操作不可状態を副作用なしで確認できる。
- input／actionに加えてtable／chartのaccessible labelを検証し、chart role/label、loadingの`aria-busy`、status／alertをPreviewへ追加。Page title欠落は既存Specを拒否せずwarningにする。
- 状態Previewは設計時だけの表示でSpecへ保存せず、executor、network、DB、Secretを呼ばない。source generator、build、視覚diff、template parameterは未実装として維持する。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2Eでschema駆動Text編集、5 Preview state、Terminal preset、KPI Composite、Dashboard Pattern、保存／reload、Patch部分適用、lock拒否、AI Design入口を確認。320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.1 Design System foundation（2026-07-19）

- backend schema catalogをv2へ更新し、14 Design Token群、Modern／Compact／Touch／Dashboard／Data Dense／Minimal／Terminal／Mediaの8 presetを追加。Application Specのpreset、token名、token値を同じregistryで検証し、未知値と任意CSSをblocking diagnosticにした。
- KPI Card／Job Status／Log Viewer／CRUD Table／Timelineの5 Composite、Dashboard／Settings／Wizard／Launcherの4 Patternを、既存Semantic Componentだけで構成した決定的templateとして配信する。
- EditorへDesign System selectorを追加。frontendへpreset／template treeを二重定義せず、catalogから適用・挿入し、既存Component Treeと衝突しないIDへ再採番する。操作は既存Undo／Redo、dirty判定、backend validation、明示Saveを通す。
- Previewへaccent、spacing、radius、surface、text、typography、shadow tokenを反映。input／actionの明示的な空labelはa11y errorとし、省略時はcatalog defaultを使って既存Spec互換を維持する。
- source generator、build、template parameter、全状態preview、視覚diffは未実装のまま成功扱いせず、F3.2以降の残件とした。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでTerminal presetのmonospace反映、KPI CompositeとDashboard Patternの挿入、保存／reload復元、既存Patch部分適用・lock拒否・AI Design入口を確認。320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## Workflow navigation／Run統合（2026-07-19）

- 独立した`Play` navigationを廃止し、sidebar、mobile navigation、Quick Actions、Command Paletteを`Workflows`へ統合。`workflows.edit`または`workflows.run`のどちらかを持つ利用者へ表示する共通navigation metadataにした。
- `Workflows`一覧はeditor利用者へdraftを含む編集対象、run-only利用者へ専用`/workflow-runner` API由来の公開済み項目だけを表示する。公開版は各項目の`Open App`から開き、内部定義を返さない既存の公開API境界を維持する。
- `/runner?workflow={id}`は公開アプリの互換deep linkとして残し、workflow未指定時は`/workflows`へ戻す。旧mobile navigation保存値の`/runner`は`/workflows`へ重複なく変換し、localStorageへ永続化する。
- Workflow Editorの主操作を`Run`へ変更。入力dialog、差分時だけの保存・公開、公開version実行、同じ画面のdebug panel表示を一操作にまとめた。公開だけの操作は`More > Publish Only`へ維持する。

検証: backend全330件、frontend production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、health 200、systemd user serviceのenabled／activeを確認。認証付きPlaywright 8件で320pxのRun入力・実行・debug panel、公開アプリ実行／承認、旧navigation保存値移行、mobile設定、390px Safe Area、1280px公開実行、横overflowなしを確認した。検証用user、session、auditは終了時に削除した。

## App Studio F2.3 Structured AI Design Proposals（2026-07-19）

- App Studioへ`AI Design`を追加。要求、application／mobile／選択component scope、Preserve／Balanced／Redesign mode、検出済みmodelを指定し、Simple／Balanced／Denseの3案を生成する。
- 既存共通runtime providerを再利用し、Ollama／llama.cpp／LM Studio等の登録済みendpoint/modelだけを許可。llama.cppは停止時の自動起動・model load待機を継承する。
- LLMへはredact・文字数制限済みApplication Specとbackend catalogのSemantic Component／Design Token／Binding Sourceだけを送る。自由code、Secret、任意file、DB実データは送信しない。
- LLM応答を3案の説明と`add/remove/replace/move`へ限定。Patch値を`valueJson`としてschema拘束後、backendで再parseし、各案をF2.1 Previewへ通す。案の選択後もF2.2 Patch Reviewで部分選択・差分・lock・checksumを再確認する。
- Application Spec v1へ`llmRuntime`を追加。`None`と`External provider · not bundled`を編集でき、Ollama／LM Studio／OpenAI互換を選択可能。Externalでruntime同梱を指定するとblocking diagnosticとし、接続先/modelは環境変数で注入する。
- source generator、build、Embedded Runtime、Remote ControlDeck、視覚diff、案の合成は未実装として選択不可／非表示を維持する。

検証: backend全330件、Application Builder unit 12件成功、frontend production build成功。実Ollama `qwen3.6-27b-q5_k_m`で3案生成を実施し、3方向のschema parse、正式Patch変換、案ごとのPreview診断を確認（1案valid、2案は未知component／不正pathとして安全にApply不可）。認証付き320px E2EでExternal LM Studio非同梱設定、AI Design dialog、未入力時Generate停止、既存Patch／lock／overflow回帰を確認。

## App Studio F2.2 Patch Review／部分適用（2026-07-19）

- Application Editorへ`Review Patch`を追加。1〜200件の`add/remove/replace/move` JSON Patchを読み込み、operation単位で選択して正確なsubsetだけをbackend Previewへ送る。
- Before／AfterのPage・Component数、structured diagnostic、base/result checksumを表示。選択変更後は旧Previewを破棄し、再PreviewまでApplyを無効化する。
- 有効な選択差分だけをchecksum付きApply APIで原子的に保存し、Project queryを再取得してPreview／Tree／Inspectorへ反映する。dirtyなlocal Designがある間はReviewを停止し、先に明示Saveを要求する。
- Inspectorへstructure／binding／style／position／content lock編集を追加。保存したcontent lockによる表示値変更拒否をPreview上で確認でき、`PATCH_LOCK_VIOLATION`時はApplyできない。
- AI提案生成、視覚diff、3案比較は未実装。高度なstructured Patch importだけを提供し、fake AIやdummy build操作は追加していない。

検証: backend全328件、frontend production build成功。認証付きE2Eで320pxから2件のPatchを読み込み、1件だけを選択・Preview・Apply、非選択Component非追加、保存後のcontent lock設定、ロック対象Patch拒否、320／390／768／1280の横overflowなし、fake build UIなしを確認。実ControlDeck serviceへ反映してhealthを確認した。

## App Studio F2.1 Structured Patch foundation（2026-07-19）

- AI設計提案を自由codeではなくApplication Spec JSON Patchへ限定するbackend基盤を追加。`add/remove/replace/move`、最大200 operation、JSON Pointer深度64をschema化した。
- 副作用なしPreview APIでdeep copyへPatchを適用し、base/result checksum、patched Spec、適用済みoperation、structured diagnosticを返す。完成Specは既存のcomponent、binding、secret、target検証を再通過する。
- structure／binding／style／position／content lockをcomponentとancestorの双方で強制。lock変更、prototype pollution token、不正escape、範囲外index、Spec外scopeを拒否する。
- ProjectへのApply APIはbase checksumによる楽観排他を必須にし、stale差分を409で停止。全Patchが有効な場合だけatomic commitし、patch件数とchecksumを監査する。
- F2.2の比較・部分適用UIとF2.3のLLM 3案生成はこのAPIを利用する。現時点でfake AI提案UIは追加しない。

検証: backend全328件、Application Builder unit 10件成功。replace＋move、入力Spec不変、content lock、scope/prototype拒否、secret literal拒否、Preview checksum、atomic apply、reload済みSpec、stale checksum 409を確認。

## App Studio F1.2 Component Editor（2026-07-19）

- backend Semantic Component catalogから生成するPalette、再帰Component Tree、Preview選択、Binding／Properties InspectorをApplication Editorへ統合。frontendへ部品一覧を二重定義しない。
- Page＋responsive Stackの初期作成、選択containerへの部品追加、primitive選択時のroot追加、上下移動、削除、Desktop dragによるcontainer変更を実装。touch／keyboardでは44px以上の明示buttonを使える。
- Desktop／Tablet／320px Mobile Previewを同じApplication Specから描画。Stack、Row、Grid、Card、Text、Markdown、Metric、Text Input、Workflow Run、Table、Line Chartをstatic sampleで確認できる。
- 最大50操作のUndo／Redo、dirty判定、backend validationを通す明示Save、保存後reload復元、接続Workflowへの導線を追加。static previewはexecutor、network、DB、LLM、secretを実行しない。
- source生成／build／packageのdummy操作は追加していない。

検証: backend全325件、frontend production build成功。認証付きE2Eで320pxからWorkflow→App Studio Project作成、Page／Text追加、Properties編集、Undo／Redo、Mobile Preview、Save、reload復元、320／390／768／1280の横overflowなし、fake build UIなしを確認。

## App Studio F1.1 Semantic Component foundation（2026-07-19）

- framework固有classを保存しないSemantic Component catalogをbackendへ追加。layout、display、input、action、table、chartの初期11部品と決定的default、container可否、Design Token enum、binding sourceをschema APIから配信する。
- Application Spec v1へ再帰component tree、Page root、structure／binding／style／position／content lockを後方互換で追加。root未定義の既存Pageは引き続きround-tripできる。
- 全Page横断のcomponent ID重複、unknown type、primitiveへのchildren、不正children型をstructured Diagnosticとして検出。frontend editorが独自の部品対応表を持たない境界を確立した。
- 本PRはF1 frontend editorの基盤に限定し、生成／build／dummy成功、LLM自由code生成は追加しない。

検証: backend全325件、Application Builder unit 7件成功。既存Spec互換、正常component tree、重複ID、unknown type、children制約、schema API catalogを確認。

## Mobile bottom navigation customization（2026-07-19）

- Settingsへ`Bottom Navigation`設定を追加。現在の権限と導入済みfeatureから利用可能な画面だけを候補にし、端末ごとに0〜6件を有効化できる。
- 選択項目は44px以上の上下buttonで安全に並べ替え、即時保存する。ResetでHome／Apps／Play／Terminal／AI Assistantの推奨構成へ戻せる。
- Moreは選択数に含めず常に右端へ固定。選択項目が少ない場合は各項目を最大96pxに抑え、6項目＋Moreでも320pxを横overflowしないflex配置とした。
- navigation metadataを共通catalogへ抽出し、desktop sidebar、mobile navigation、Settings候補のlabel／permission／feature判定を二重管理しない。

検証: backend全324件、frontend production build成功。認証付きPlaywright 3件で320×700から6項目選択、7列相当表示、上端への並べ替え、reload後の復元、7件目候補の無効化、More固定、横overflowなしを確認。

## iPhone standalone Play header境界修正（2026-07-19）

- 共通headerが`height: 48px`へ固定されたまま上部Safe Areaを内側へ加算していたため、ホーム画面から全画面起動したiPhoneではLogoがheader外へ押し出され、直後のPlay背景と重なっていた。
- headerを固定高からSafe Area込みで自然に伸びる`min-height`へ変更。Playだけへ例外余白を加えず、全通常ページでLogo、本文、下部navigationの領域境界を維持する。
- Safe AreaをCSS変数境界にまとめ、実機相当47pxを自動テストで再現可能にした。
- 下部navigationだけでなくMoreのQuick ActionsからもAI Assistantを開ける導線を復元し、主要機能の入口を一貫させた。

検証: frontend production build成功。認証付きPlaywright 2件で通常15ページの320×700／1280×800統一レイアウトと、390×844・上部Safe Area 47pxでLogo下端よりPlay背景開始位置が下になることを確認。実ControlDeck serviceへ反映済み。

## Navigation naming／page layout統一（2026-07-19）

- グローバルnavigation、mobile navigation、Quick Actions、Command Palette、各機能menuを英語名へ統一。公開Workflowの実行面は短く幅を取らない`Play`、Application BuilderのUI製品名は将来の設計・編集機能を含む`App Studio`とした。
- 通常ページへ共通`PageHeader`を導入し、titleを20px／line-height 28px、同じ上端・説明・action配置へ統一。AI Chat、Workflow Editor、接続中Terminal、Remote Viewerは独立表示として維持する。
- mobile navigation itemへ`min-width:0`とtruncateを追加し、英語名が320px gridの列幅を押し広げないようにした。

検証: frontend production build成功。認証付きPlaywright 4件でApp Studio導線、Playの実行／承認、320×700と1280×800の通常15ページを確認し、全page titleが20px／28px、document/body横overflow 0となることを確認した。

## Remote Desktop service health（2026-07-19）

- Remote画面の状態確認をguacd単体から、ServerPC接続先のTCP待受確認まで拡張した。guacd／WebSocketが正常でもxrdpが停止している状態を区別し、接続を繰り返さずSSHで実行する復旧コマンドを表示する。
- 状態は10秒ごとに再確認し、xrdp復旧後は警告を自動解除する。秘密値は送信せず、保存済みself connectionのhost／portへの有限TCP接続だけを使う。
- 実機調査ではControlDeck→WebSocket→guacdは成功し、OSのxrdp／xrdp-sesmanがinactive、localhost:3389が閉じていたことを確認した。起動にはroot権限が必要なため、Webプロセスからsystem serviceを直接操作しない。

検証: remote backend test 9件、frontend production build成功。実機の停止状態でlocalhost:3389 unavailableと復旧案内を確認。

## Project Lab core（2026-07-19）

- `/project-lab`へCodeDEV成果物評価画面を追加。`~/CodeDEV`直下のproject、Python/Node/Vite/React/CMake/Rust/.NET/static-web、Git branch/dirty、manifest profileを設定なしで検出する。
- `.controldeck/project.json` v1をstrict Pydantic schema化。command文字列、project外cwd/glob、秘密environment直書きを拒否し、argv配列とSecret名参照だけを受け付ける。
- HTML、画像、CSV/TSV、JSON、Markdown、PDF、audio/video、log/textをread-only artifactとしてcatalog化。HTMLは認証付き配信、CSP、script無効sandbox iframe、その他は型別preview/downloadを使用する。
- CodeDEV外/symlink escape、秘密file名、`.env`、source、`.git`、`.venv`、node_modules、build cacheを除外。inline JSON/CSV/textの秘密らしい値をredactする。
- `project_lab.view`をadmin/operatorへ追加。現Phaseはdiscoveryとpreviewだけで、programの自動起動、run成功dummy UI、任意port proxy、LLM送信を実装しない。

検証: backend全313件、frontend production build成功。実ControlDeck serviceへ反映し、隔離した`~/CodeDEV/codex-project-lab-e2e`でHTML sandbox、redact済みJSON、CSV tableを操作確認。320×700、390×844、1280×800で横overflow 0、sandboxによる意図的script block以外のconsole/page error 0。検証project、user、session、auditは確認後に削除した。

## 公開アプリ（Workflow Runner）（2026-07-19）

- 公開済みWorkflowをキャンバスなしで操作する「公開アプリ」を`/runner`へ追加。公開版の入力form、
  想定output contract、副作用区分、実行、停止、human approval、typed output、最近の実行、過去入力再利用を同じ画面へ統合した。
- 専用`/workflow-runner` APIは公開名・説明・version・input/output schema・結果だけを返し、definition、node/edge/config、
  runtime snapshot、source node IDを返さない。draft/test executionも公開アプリから参照できない。従来のdefinition／node debug APIは
  `workflows.edit`へ制限し、`workflows.run`だけのoperatorは公開アプリAPIを利用する境界へ修正した。
- 公開時にtrigger inputsと`output.render`からJSON Schemaを生成してimmutable `WorkflowVersion`へ保存。versioned description列を
  SQLite light migrationへ追加し、既存公開版の空contractは起動時に安全なsnapshotからbackfillする。draftの定義・説明変更は再公開まで
  公開アプリへ反映されない。
- editor PreviewとRunnerで13入力型とtyped rendererを共有する`RuntimeComponents`を追加。iPhone下部navigationのWorkflowをRunnerへ置換し、
  editorはedit権限の利用者だけに表示する。AI assistantの公開workflow実行も公開アプリAPIへ移行した。
- エディタの主操作を「更新して開く／アプリを開く」、日常実行面を「公開アプリ」へ整理。差分がある場合だけ保存・公開検証・version更新してdeep linkへ移り、公開済みならversionを増やさず開く。ワークフロー一覧は公開履歴があれば既存公開版を開け、編集中draftを暗黙更新しない。URL queryで選択を再読込後も復元し、存在しない／非公開IDは無限loadingにせず復帰案内を表示する。
- `llm.chat`は管理中のOllamaを既存provider adapter経由で自動ロードし、llama.cppはsystemd user unit起動とhealth完了を待つ。既定有効、startup timeout 240秒（10〜600秒）、同一endpoint/modelの多重起動抑止、進捗表示、外部endpoint素通しを実装した。
- 承認待ちAPIをeditor／公開アプリ共通の型付きcontractへ統一。公開アプリで発生していた承認待ち取得時の500を解消し、redact済み承認文、担当者、ISO 8601期限、承認／却下を320pxでも同じ実行カード内に表示する。
- Project LabとApplication Builderの新規要件を監査し、`docs/design-workflow-runner-project-lab.md`と
  `docs/design-application-builder.md`へ共通IR、決定的generator、Phase A限定初回PR、反復GUI editor、structured AI patch、
  design system、platform advisor、Web/Avalonia/Tauri優先、build/artifact境界を記録した。

検証: backend全323件、frontend production build成功。実ControlDeck serviceを再起動し、healthを確認。
認証付きPlaywrightで320×700から公開workflowを作成・公開し、公開アプリでparagraph入力→Markdown output、過去入力再利用、一覧の公開版button、deep link、reload復元、
キャンバス／内部node名非表示を確認。320×700、390×844、768×1024、1280×800でdocument/body横overflow 0、console error 0。
さらに公開版のhuman approvalで承認文・担当者・期限を確認し、320×700の同じ画面から承認して成功outputへ到達するtargeted E2E 2件を実サービスで確認した。
backend testではrun-only operatorがRunnerを利用でき、definition/list/debug execution APIは403になることを確認した。
実機では未ロードの`qwen3.6-27b-q5_k_m`を`llm.chat`から起動し、provider load→loaded確認→本文生成に成功。確認後は元のunloaded状態へ復元した。

## Workflow Phase 3 human approval／control merge（2026-07-19）

- 隠し共通設定だった承認gateを正式な`human.approval` nodeへ昇格。上流変数を使う承認文、ユーザー名による
  承認者限定、0.1秒〜24時間の期限、承認／却下の監査、承認後のtyped outputをcatalog・metadata・UIへ統合した。
- 承認待ち情報を実行パネルに表示。resolved secretは表示・node output前にredactし、却下は
  `APPROVAL_REJECTED`、期限切れは`APPROVAL_TIMEOUT` Error Contextとしてerror／timeout routeへ渡す。
- `control.merge`を追加し、wait_all、first_success、first_complete、quorum、collectをengineの到着順・成功状態と統合。
  直接上流だけを`items[{node_id,status,output}]`、`values`、`value`へまとめ、成功0件／quorum未達は明示errorにする。
- semantic checkに承認期限、merge方式、入力本数、quorum範囲を追加。node referenceとREADMEを標準45 nodeへ更新した。
- 当時process memory上だった承認待ちは、現在は`WorkflowPause` migration、token hash、schema修正入力、
  service再起動継続まで実装済み（本書冒頭の完了記録を参照）。

検証: backend全295件、frontend production build、実ControlDeck service再起動に成功。390×844 E2Eで
公開版のtrigger→2並列node→wait_all merge→指定ユーザーのhuman approval→typed status outputを実行し、
承認文と承認者表示、Web UIからの再開、merge count=2、横overflow 0、console error 0を確認した。

## Workflow Phase 3 型付きError Context／視覚的error route（2026-07-19）

- node失敗時の共通出力を`error` objectへ統一し、node ID/type、message、code、retryable、attempt、
  redact済みinput summary、timestampを後段へ渡す。node runの`error_json`にも同じ有限snapshotを保存する。
- `on_error=branch`のnodeへ赤い「失敗」と橙の「時間切れ」handleを分離して表示し、edgeも赤破線／橙点線で識別する。
  timeout専用edgeがない既存definitionは従来のerror edgeへ流す後方互換fallbackを維持する。
- inspectorに共通node timeout、retry、失敗動作と直近Error Contextを集約。変数pickerから
  `error.message/code/retryable/attempt/timestamp/input_summary`を挿入できるようにした。
- semantic checkへ無効な`on_error`、0.1秒未満／非数値timeout、未接続error route、重複error routeの検査を追加。
  backend metadataにも共通実行制御schemaを公開し、frontend固有の暗黙設定にしない。

検証: backend全291件、frontend production build、実ControlDeck service再起動に成功。320px起点のPlaywright E2Eで
preview／通常実行／回帰test／公開／履歴／node単体実行／部分再実行／pinned dataに加え、390px inspectorから
node timeoutとerror branchを設定し、失敗／時間切れhandleの表示、横overflow 0、console error 0を確認した。

## Workflow Phase 3 確定的data node（2026-07-19）

- `data.template`を追加。既存の上流変数と任意JSON `data`をMustache/Jinja風`{{...}}`で展開し、textまたは
  構文検証済みJSONを返す。式、関数、attribute access、任意codeは実行せず、入力／出力を2MiBに制限。
- `data.filter`を追加。最大10000件のarrayへnested fieldのtruthy/exists/equality/contains/数値比較、
  unique、stable sort、limitを順に適用し、結果／入力件数を返す。異種値sortも決定的な順序に正規化。
- `data.aggregate`を追加。最大10000件のarrayを任意fieldでgroup化し、count/sum/avg/min/maxを返す。
  count以外はnumberを要求し、文字列の暗黙変換による誤集計を拒否。
- executor、LLM catalog、required config、metadata、output schema、frontend node definition／詳細説明を同時更新し、
  consistency testの集合一致を維持。標準nodeは43種類となった。

検証: backend全289件成功、frontend production build成功。実ControlDeck serviceを再起動し、390×844 E2Eで
trigger入力→filter→aggregate→template→typed outputを実行して`kept=2, sum=30.0`を確認。mobile node libraryで
3 nodeの検索・表示、横overflow 0も確認し、test workflowと一時userは削除済み。

## README機能ガイド拡充（2026-07-19）

- READMEの直近追加を現行実装へ更新し、標準43ノード、実行snapshot、node run、部分再実行、固定データ、
  回帰テスト、draft／公開版、`output.render`、共有Deep Researchエンジンを反映。
- ダッシュボード／監視、アプリ／health check、Web terminal、remote desktop、AI assistant、Deep Research、
  model、Knowledge/RAG、file、GitHub、power、security、PC／iPhone navigationについて、特徴だけでなく
  操作の開始点、使い分け、安全境界、mobile gesture、運用上の注意を機能別ガイドとして追加。
- workflowは入力定義→安全preview→通常test→node単体／部分実行→回帰test→公開の8stepへ整理し、
  typed final output、draft／published／pinned dataの役割と公開preflightをREADMEだけで追えるようにした。
- README内の相対linkの存在とMarkdown差分を確認。実装の詳細・検証証跡は本ファイルと各design documentへ誘導する。

## AIアシスタント standalone PWA下端余白修正（2026-07-19）

- ホーム画面追加から全画面起動するiPhoneでは`env(safe-area-inset-bottom)`が有効になり、AI入力composerの外側へ
  約34pxのpaddingを加えて入力カード全体を持ち上げていた。通常browserのviewport検証ではSafe Areaが0のため再現しない条件差を特定。
- `/assistant`をアプリshellの全画面routeへ追加してモバイル下部navigationの予約領域を除去し、composerの追加下paddingを0へ変更。
  入力カード背景をdialog最下端まで連続させ、空白帯を作らない。
- 追加確認で、standalone PWAでは外側のfixed shellと内側`100dvh` dialogが異なる高さになり、shellの黒背景が
  下端へ露出する条件を確認。dialogをshell基準の`height: 100%`へ統一し、shell／dialog／composerの下端を一致させた。
- token生成／音声状態行を入力カード内の固定24px footerとして入力欄の下側へ統合。待機時も同じ領域へ
  keyboard hintを表示し、状態の出現／消失でcomposer高や入力欄の座標を変えない。footerは入力カードと同じ背景を使い、
  dialog最下端まで連続させるため、独立した黒い空欄を作らない。
- 入力カードを少し囲っていたcomposer外面の背景色と上borderを撤去して透明化。入力カードと固定status footerだけを
  操作surfaceとして残し、周囲に別の薄い帯や箱が見えない構成へ整理した。
- Playwrightを`navigator.standalone=true`で起動し、320×700とiPhone相当390×844のscreenshotを目視確認。
  dark themeの390px条件で`shellBottom = dialogBottom = composerBottom = inputCardBottom = 844px`、composer padding 0px、
  document幅390pxを実測。音声状態の表示前後も入力欄top座標が不変で、状態footerが入力欄の下にあることを確認。
  モバイル下部navigation非表示、frontend production build、実ControlDeck service再起動も成功。

## モバイル横overflow・ターミナル右端タッチ修正（2026-07-19）

- iPhone Safariで16px未満のinput/select/textareaへfocusするとVisual Viewportが自動拡大し、keyboard表示後に
  右へpanした状態が残ることを横はみ出しの主因として特定。767px以下ではフォームの実効font-sizeを16px以上へ統一した
- `w-screen`（100vw）がscrollbar幅やVisual Viewportとの差分を含んでsheet/drawerをdocument幅より広げるため、
  BottomSheet、Drawer、workflow SampleBookを`width: 100%`かつ`100dvw`上限へ変更。html/body/rootもdocument幅でclipする
- terminal rootのVisual Viewport追従幅をlayout viewport以下へclamp。xtermの右scrollbarはcoarse pointerのモバイルで
  1px予約へ縮小し、文字領域を削らない20px幅のoverlay履歴barへ置換。barのtapは対応位置へjumpし、dragは
  指位置へ連続追従するがIME textareaへfocusさせない。端以外のtap入力とterminal面全体の上下swipeも維持する
- 320x700 / 390x844でreload後と文字入力focus後のdocument横overflow、実効font-sizeを確認するE2Eと、
  terminal右端touchではkeyboard入力へfocusせず中央touchではfocusするE2Eを追加した

検証: backend全278件成功、frontend本番build成功。実サービスを`./deck.sh`で再起動し、Playwright Chromiumの
terminal回帰18件成功・任意10分soak 1件skip。320px/390pxとも横overflow 0、overlay barのtap/drag、
IME、100/300KB・UTF-8 paste、keyboard 10回開閉、再接続、履歴、desktop wheelを確認。テスト用ユーザーは削除済み。

## ワークフローキャンバスのiPhone操作統一（2026-07-19）

- node inspectorを88dvhの固定surfaceへ変更し、node種別や設定/input/output/error tabの内容量が変わっても
  sheetのtop/heightを維持。削除を設定末尾からheaderの44px actionへ移し、どのtabからも同じ位置で操作できる
- node handleは12pxの見た目を保ったままmobileの透明hit areaを周囲16pxへ拡大し、node外へ出た領域をclipしない
- edgeの透明選択幅をmobileで32pxへ拡大。選択時にaccent強調と固定toolbarを表示し、44px削除action、
  source/target端点の36px reconnect radiusによる付け替えを追加。変更は既存definition形式のままdirty管理する
- 操作契約を`docs/design-workflow-integrated-ide.md`へ記録し、常時buttonをnodeへ載せず選択時だけ段階表示する

検証: frontend本番build成功、実サービス再起動成功。320px E2Eでedge選択、source/target reconnect端点、edge削除、
handle hit area、inspectorのtab切替前後のtop/height一致、headerからのnode削除、横overflow 0、console error 0を確認。

## Model画面のOllamaロード状態追従修正（2026-07-17）

- チャット等によるOllamaの暗黙ロード後も、15秒間隔のモデル一覧cacheにより左インジケータと右操作ボタンが
  未ロード表示のまま残る問題を修正。軽量な`/models/running`（Ollama `/api/ps`）を表示中のみ2秒間隔で取得し、
  インジケータ、VRAM表示、「ロード/アンロード」ボタンを同一のlive stateから描画する
- 画面上のロード/アンロード操作完了時はlive stateを即時更新し、再取得完了を待つ間の連打を防ぐ処理中表示を追加
- backendも`/api/tags`と`/api/ps`間の`name`/`model`、`:latest`省略、大小文字、digestの表記差を正規化し、
  Ollama更新やローカル登録モデルでもロード判定が欠落しないようにした

検証: backend全267件成功、frontend本番build成功。実機Qwen3.6 27Bを32K CTXでロードしてbackend判定を確認し、
320px幅Playwrightで外部ロード後2秒以内に「未ロード/ロード」から「ロード中/アンロード」へ変わることを確認した。
検証後はQwenをアンロード済み。

## モデル個別出力tokenへの統一（2026-07-17）

- ⚙共通設定の「チャット・ワークフロー生成の出力token上限」を撤去。通常チャットとワークフローJSON生成は、
  Ollamaのモデル個別`num_predict`、llama.cpp instance個別`n_predict`を同じresolverから使用する
- `-1/-2`等の無制限指定はplatform安全上限262,144 tokenへ正規化。モデル個別値を持たない外部OpenAI互換
  endpointだけ8,192 tokenへフォールバックし、管理中モデルの設定を共通値で上書きしない
- モデル個別の通常/Deep Research CTX、Ollama `num_predict`、llama.cpp `n_predict`、Deep Research総出力に
  262,144 token presetを追加。Deep Research policyの保存上限も256Kへ拡張した

検証: backend全266件成功、frontend本番build成功。Ollama/llama.cpp個別値、無制限値の256K正規化、
外部endpointの8K fallbackを自動テストし、共通設定から重複項目が消え個別設定に256K presetがあることをUI確認した。

## Deep Research共有エンジン・ノード・ローカル資料統合（2026-07-19）

- `research.deep`に残っていた短い検索結果の単発要約器を廃止し、AIアシスタントの反復型Deep Researchエンジンへ統合。
  計画、最低2回の探索、coverage再評価、SearXNG、公開本文/PDF、学術横断、GitHub構造解析、特許/市場、6章継続生成、
  引用検証、Deep Research専用CTX、進捗をノードとアシスタントで共有する
- quick（2 round/8検索）、standard（3/16）、deep（4/24）、exhaustive（最大6/36）とcustom budgetをノード設定へ追加。
  source portfolio、SearXNG category、RAG collection、ローカルproject、根拠context、report token上限を設定可能にした
- AIアシスタント設定にも検索深度とWeb/PDF・学術・GitHub・直接URL・添付/RAG・ローカルコード・特許・市場の選択を追加。
  添付PDF/文書は会話RAGからDeep Researchへ再利用する
- ローカルコードadapterを追加。`files.allowed_roots`を通したrealpath検証、symlink/秘密ファイル/依存物除外、最大5,000 entry・
  最大12主要ファイルの有限読取、Python/TypeScript静的symbol索引により、コードを実行せず構造・テスト・CIを根拠化する
- 旧`arxiv/crossref/local` source設定は`academic/local_code`へ実行時aliasし、既存workflowを壊さない。出力は旧`findings/count`を
  維持しつつ、共有契約の`sources/research/sub_questions`を追加した

検証: backend全282件成功、frontend本番build成功。実サービスを再起動し、AssistantのDeep設定を含む
320×700/1280×800 E2Eで横overflowなしを確認。実機llama.cpp Qwen3.6-27Bで`research.deep`ノードから
ControlDeck自身を`local_code`限定・quick budgetで評価し、295.9秒、2 round/4 search、4,044文字、引用31件、
不正引用0、引用段落率100%で完走した。この評価で空URLのローカル/RAG資料が`/`へ正規化され1件に誤dedupeされる
問題を発見し、空URLはtitle/pathをkeyにする修正と回帰テストを追加。主要12ファイル/14根拠候補、先頭5件のunique key、
秘密・symlink・依存/cache除外も決定論的に再確認した。
ローカルSearXNGもオンデマンド起動し、`general,it`カテゴリ指定で3件のJSON検索結果を実取得した。

## ワークフロー実行スナップショット・当時版再実行基盤（2026-07-19）

- `WorkflowVersion`へ連番、input/output schema、checksum、published_atを追加し、同一定義checksumは実行間で再利用。
  `WorkflowExecution`へversion ID、redact済みdefinition snapshot、runtime snapshotを追加した
- runtime snapshotにはnode version、LLM endpoint/model/sampling、Python version、利用可能なsecret名だけを保存。
  定義へ直書きされたpassword/token/API keyは`***`にし、`{{secrets.NAME}}`は値を持たない参照名として残す
- `WorkflowNodeRun`を追加し、node ID/type/version、status、redact済み上流入力、出力、error、token usage、開始/終了、
  elapsed、attempt/retry、cache source、schema versionをノードごとに独立保存。巨大化防止の有限JSON上限も設けた
- `GET /workflows/{id}/versions/{version_id}`、`GET /workflows/{id}/executions/{execution_id}/nodes`、
  `POST /workflows/{id}/executions/{execution_id}/retry`を追加。retryは`current/historical`を明示選択し、入力を再利用する
- 実行履歴sheetへ「現在のフローで再実行」「当時のフローで再実行」を追加し、node runの時間・retry・実出力を表示。
  Workflow削除時はnode run → execution → versionの順に削除してFK整合を維持する
- SQLite軽量migrationへ既存version/executionの追加columnを登録。`workflow_node_runs`は`create_all`で冪等作成する

検証: backend全283件成功。current/historicalで異なる出力になるAPI回帰、秘密値非保存、node run、version detail、
削除順序を確認。frontend本番build成功。実サービス再起動で既存SQLiteへ追加5 version column、3 execution column、
`workflow_node_runs`テーブルが作成されたことをinspection。Playwrightでpreview/test/過去入力に加え、履歴sheetのnode run、
現在版/当時版再実行ボタン、320×700で横overflowなしを確認した。

## ワークフローノード単体実行・固定データ・部分再実行（2026-07-19）

- `WorkflowPinnedData`をdraft補助データとして追加し、workflow/nodeごとにredact済み出力と元execution IDを保存。
  定義・`WorkflowVersion`・published版には含めず、本番実行は固定データを参照しない。1MB上限、pin/unpin監査、workflow削除時の
  FK順序を実装した
- inspectorの実行tabから、最新成功実行、指定した直近実行、手動JSON、固定データを選び、保存済み上流contextで単一executorを
  実行可能にした。固定データ選択時はexecutorを呼ばず`CACHED`を返し、キャンバスにも`📌 固定`を表示する
- `POST /workflows/{id}/nodes/{node_id}/run-to`を追加。対象ノードの祖先だけをDAGから抽出して実行し、下流の外部送信・書込みを
  起動しない。`POST .../resume-from/{node_id}`は過去contextの祖先出力を保持し、現在版/当時版を選んで対象以降だけを再計算する
- 部分実行前に未保存draftを保存し、runtime snapshotへ`run_to_node_id` / `resume_from_node_id`を記録。
  output variableも保存済み祖先contextから再構築する

検証: backend全284件成功。API回帰で単体実行、秘密keyの固定時redact、executor不使用cache、対象まで実行時の下流除外、
現在版での途中再開と旧上流値の再利用、node run列を確認。frontend本番buildと実サービス再起動に成功し、SQLiteの
`workflow_pinned_data`作成もinspection。320px Playwrightで単体実行、pin表示/解除、対象まで実行、途中再実行導線、
横overflowなし、console errorなしを確認した。検証用workflow/user/pinは削除済み。

## ワークフロー回帰テスト（2026-07-19）

- `WorkflowTestCase`へ名前、redact済み入力、mock境界、期待出力、追加assertion、直近execution/resultを保存。
  literal secretは入力・期待値・assertion間のコピーも含めて永続化前に除去し、各JSONを1MB以内へ制限する
- test case CRUD、単独run、全case batch run APIを追加。実行ごとに現在のdraftをversion snapshot化し、期待outputの完全一致と
  `exists/not_exists/equals/contains/gt/gte/lt/lte` assertionを決定論的に評価する。結果にはpath、期待値、実値、個別合否を残す
- Preview Workspaceの入力・通常テスト結果と同じ画面へ回帰テストを統合。現在入力、または成功時の最終出力からcaseを作成し、
  入力再読込、単独/一括実行、成功/失敗、assertion件数、失敗差分、削除をモバイルでも操作できる
- workflow削除時はtest caseをexecutionより先に削除し、`last_execution_id`のFK整合を維持する

検証: backend全285件成功。API回帰で2 case一括実行、成功/失敗差分、3 assertion成功、秘密入力の`***`化、
case/workflow削除を確認。frontend本番buildと実サービス再起動に成功し、`workflow_test_cases`テーブル作成をinspection。
320px Playwrightで通常テスト結果からcase作成→一括実行→成功判定→入力再読込、横overflowなし、console errorなしを確認した。
検証用workflow/case/userは削除済み。

## ワークフローdraft／公開版分離（2026-07-19）

- workflow本体の`definition_json`を自動保存draft、`WorkflowVersion.published_at`が付いたimmutable snapshotを公開版として分離。
  checksum比較から`編集中`／`公開 vN`を返し、保存後に公開版との差が生じても公開snapshotを変更しない
- `POST /workflows/{id}/publish`を追加。構造・意味検証、最終output有無と名前重複、secret存在、pinned data残存、
  回帰テスト状態、quality scoreをpreflightし、blocking issueが1件でもあれば409で公開しない。公開操作は監査する
- 通常の「実行」、schedule、Webhook、system event、`flow.call`は公開版だけを選択し、未公開workflowは明示エラーにする。
  Preview Workspaceの通常テスト、test case、node run-to/resumeはdraft開発経路として分離を維持する
- 後方互換migrationとして、導入時点ですでに`enabled`だった自動実行workflowだけは起動時に現在定義をlegacy baseline公開版へ
  1回移行する。新規workflowは公開前にenableできず、再起動を利用した検証回避はできない
- desktop command barに状態badgeと公開button、mobileの三点menuに公開actionを追加。未保存変更は先に保存し、保存失敗時は公開を中断する

検証: backend全286件成功。未公開本番実行の拒否、公開後の本番出力、draft変更後も旧公開版を実行すること、draft testは新値を使うこと、
pin残存時の公開拒否、解除後の再公開、Webhook/subflow/approvalの公開版回帰を確認。frontend本番buildと実サービス再起動に成功。
320px Playwrightで回帰case合格後のmobile公開、公開toast、横overflowなし、console errorなしを確認した。
検証用workflow/version/case/userは削除済み。

## 型付き最終出力 output.render（2026-07-19）

- `output.render` executor/metadata/catalog/validation/frontend定義を追加。Auto、text、Markdown、JSON tree/raw、Table、
  Key-value、Code、Image/Gallery、Audio、Video、File、Link、Status、Metric、Progress、Citation listを選択できる
- name/title/description/value/renderer/schema、download/copy/collapse、sensitive、filename/MIMEを設定し、全実行経路で
  `name/type/value/source_node_id`と表示metadataを同じ最終output contractとして返す。JSON系rendererは文字列を型付き値へ復元する
- Preview Workspaceは画像、リンク、表、音声、動画、JSON/code、その他を型別表示。`sensitive`出力値はlive後段では利用可能だが、
  DB・履歴・API保存時に`***`化する。旧`signal.display`は後方互換aliasとして維持し、新規作成ではtyped outputを推奨する
- READMEへ入力→preview→単体/部分実行→回帰→公開の操作手順、typed output、draft/公開/pinの違いと安全境界を追記した

検証: backend全287件成功。typed table contract、title、JSON配列復元、sensitive保存redact、公開preflightを回帰で確認。
frontend本番buildと実サービス再起動、health確認に成功。320pxの共通Preview Workspaceは既存E2Eで横overflowなしを維持し、
型別rendererのブラウザ個別操作は次のE2E拡充対象とする。

## AIアシスタント Deep Research超強化（2026-07-17）

- 数件の資料提示で停止していた原因を、固定3クエリ・本文8件・単発要約・最終生成HTTP timeout 300秒と特定。
  `docs/design-deep-research-engine.md`へ調査状態機械、source portfolio、有限資源、引用品質、CTX切替を詳細設計した
- LLMによる調査計画から、最低2/最大4ラウンドでcoverage、未解決点、矛盾を評価し、検索語をpivotするagentic loopへ変更。
  最大24検索、120候補、本文32件、最終根拠36件、根拠context 90,000文字、レポート8,192 tokenとし、
  進捗と品質指標をserver job/message metaへcheckpointする
- Webに加えてOpenAlex/Crossref/arXiv/Europe PMC/DBLP/DOAJ、PatentsView特許、SEC EDGAR、直接URL、
  HTML/text/PDFをsource portfolio化。失敗sourceは調査全体を落とさずcoverage limitへ明示する。
  PatentsView keyは暗号化Workflow Secret `PATENTSVIEW_API_KEY`を再利用し、ログへ出さない
- GitHub URLを検出するとrepository metadata、recursive tree、README/manifest、主要source、test、CIを取得。
  Python ASTとTypeScript/JavaScriptの保守的静的抽出で関数、クラス、変数、import/export、API route、
  観測可能な呼び出しを索引化し、構造・データフロー・既存機能の統合可能性をpath付きで評価する
- 引用番号の実在、引用資料数、根拠付き段落率、本文長を決定論的に評価し、coverage 55%未満等は根拠を増やさず
  1回だけ引用修正する。最終資料は会話内文献ID `R1…`へ変換し、後続会話で必要分だけ再展開する
- 一律256Kへ変える共通CTX設定を撤去し、Ollama/llama.cppの各モデル個別設定へDeep Research専用CTXを追加。
  未指定なら同じモデルの通常CTXを使用して何も変更しない。異なる場合、Ollamaはrequest単位で適用後に通常optionsへ、
  llama.cppは開始前に専用CTXで再ロードし、成功・失敗・キャンセル後に通常CTXと元の稼働状態へ必ず復元する
- AI画面の詳細へround、検索回数、候補/採用資料、GitHub解析数、coverage、引用段落率、CTX適用を表示する
- 最終レポートが単発8,192 token上限で途中終了しても検出していなかった不具合を修正。6章を独立生成し、
  完結markerが無い章は続きから最大8回生成して重複除去・結合する。総出力は既定32K/最大128K token、
  各章へ均等配分し、完結章数と未完結候補をUIへ表示する。短い改稿で長い草稿を置換しない長さ検証も追加

検証: backend全264件成功、frontend本番build成功。Model設定E2Eと、認証付きAssistant E2Eの320x700 / 1280x800で
256K CTX表示、探索指標、文献ID、横overflowなしを確認。実機Ollama Qwen3.6-27Bで`num_ctx=262144`、
Web・専門検索・GitHub構造取得を4ラウンド/検索24回実行し、81件の証拠候補から23件を最終選定。
20分6秒で5,860文字、引用101箇所/12資料、不正引用0、引用段落率100%のレポート生成を完了した。
従来の300秒timeoutを実機再現して1,800秒へ修正した。公開GitHub branchがローカル最新実装より古く、モデル評価が
現行実装と食い違うsource freshness限界も検出したため、公開時点・取得限界をcoverageへ残す運用とした。
途切れ不具合は実機Qwen3.6-27Bへ128 tokenで長文を要求し、`done_reason=length`、完結markerなし、253文字で終了する形で再現。
章の初回出力が同様に途切れるfixtureで全6章が継続・完結する回帰テストを追加した。

## Model設定分離・ファン表示・プラットフォーム再読み込み（2026-07-17）

- Model画面の⚙を全runtime共通設定だけに限定し、共通CTX項目とprovider/モデル個別設定を撤去。
  Ollama/llama.cppのモデル行から開く画面には、そのモデル固有の生成・ハードウェア・通常/Deep Research CTXだけを表示する
- GPUはAMD sysfsの`fan1_input`とamd-smiのRPMを取得し、ホームのGPU使用率カードへ温度と併記。
  CPUはpsutil hwmonでCPUと明示されたfanだけを採用し、筐体/PSU/GPUの誤表示を避け、取得不能時は`N/A`とする
- 操作シートの電源付近へ「Control Deckを再読み込み」を追加。固定引数のsystemd user transient unitで
  Webサービスを応答後に再起動し、ブラウザはhealth復帰を監視して自動reloadする。実行は認可し監査ログへ記録する

検証: backend全264件成功、frontend本番build成功。認証付きPlaywrightでModel個別/共通分離とDashboard fan表示を確認し、
320x700 / 1280x800とも横overflowなし。実機GPU fan 889 RPM、CPU fanセンサー非公開のためN/Aを確認。
platform reload APIは202応答後にservice PID `1607245→1609074`、health復帰を確認した。

## AIアシスタント 会話内文献レジストリ（2026-07-17）

- 詳細設計を`docs/design-ai-chat-reference-registry.md`へ記録。Webページ、論文、資料等を会話単位の
  `chat_references`へ永続化し、`R1…R9, RA…RZ, R10…`の短い36進IDを割り当てる
- URL正規化+SHA-256キー（URLなしはタイトル+provider）で会話内重複を排除。同じ出典を複数回の調査で
  取得してもIDを維持し、会話削除時は文献も削除する。同時登録はDB unique制約を正本に最大3回再評価する
- Web・学術・Deep・複合調査のLLM根拠、回答引用、message meta、WebSocket sourcesを`[R英数字]`へ統一。
  Deep Search内部の一時連番も永続IDへ変換してから保存する
- 後続入力の`R1` / `@RA` / `[RA]`を検出し、同じ会話に存在する指定文献だけをLLMへ展開する。
  保存抜粋6,000文字/件、最大12件、合計18,000文字で制限し、全出典本文の常時注入によるCTX圧迫を避ける。
  存在しないIDは推測で補わないsystem指示を追加した
- provider非依存の文献ツール境界として、軽量一覧、1件取得、最大12件の一括解決APIを追加。
  Ollama、llama.cpp Vulkan/ROCm、その他OpenAI互換runtimeで共通利用し、将来のfunction callingも同じserviceへ接続できる
- 出典カードを「会話内文献」へ変更し、短いIDバッジと36pxの「参照」操作を追加。押すと入力欄へ
  `[R1] `を挿入し、そのまま後続質問を書ける

検証: backend全254件成功、frontend本番build成功。実サービス再起動後に`chat_references`作成とhealthを確認。
認証付きPlaywright Chromiumの320x700 / 1280x800で文献ID・参照操作を表示し、入力への`[R1]`挿入、
document横overflowなしを確認。採番境界`R9→RA` / `RZ→R10`、URL重複、一覧/個別/一括解決、
選択文献だけのコンテキスト注入、会話削除を自動テストした。

## AIチャット UI・自動モード・長文ストリーム・音声入力（2026-07-17）

- 詳細設計を`docs/design-ai-chat-auto-mode-asr.md`へ記録。利用者の追補指定に従い、長時間処理を含む
  実行前確認は挟まず、自動判定後に開始する。モードは通常「自動」で、入力からchat/Web/学術/Deep/
  ワークフロー生成・実行を判定し、理由を表示する。必要な場合は単一メニューで明示上書きできる
- AI画面を他タブと同じzinc/accent、中央コンテンツ幅、段階開示、Safe Areaへ統一。右上の閉じる操作は
  44pxタッチ領域、枠・影・強いコントラスト、PCの「閉じる」ラベル、focus ringを持つデザインへ変更
- 常設の6モードpill列を廃止し、自動判定status + mode menuへ集約。ワークフロー生成意図は確認なしで
  server jobによる生成→検証→登録→動作確認→最大4回の自動修正へ進む
- 自動判定を決定論ルール + LLMプランナーの二段構成へ拡張。明確な依頼は即時判定し、曖昧・複合的な依頼は
  temperature 0/thinking off/JSON Schemaで`chat/Web/学術/複合調査`と検索手順を生成する。不正JSONやprovider失敗時は
  通常対話へフォールバックする。Ollamaはnative `format`へJSON Schemaを渡し、thinking modelが推論だけで出力上限を
  使い切ってJSONを途中切断する問題も修正
- 構造化出力dialectをruntime provider共通層へ集約。OpenAI標準JSON Schema → JSON Object → prompt制約のみの
  段階fallbackをOllama、llama.cpp Vulkan/ROCm、その他OpenAI互換で共有し、LLMノードとGraphRAG抽出にも適用。
  Ollama native `format`はprovider固有の最適化として残し、契約自体は依存させない
- 複合調査はWeb・学術検索を併用し、URLで出典を重複排除。LLMが情報不足を再評価して標準3回/上限5回、
  検索呼び出し合計8回まで追加調査し、引用付きで要約する。判定計画・検索・評価・要約の進捗は永続jobと
  chat message metaへ保存し、画面再接続後も復元・表示する
- ヘッダー左上へ現在機能を常時表示。自動時は`自動判定: Web検索`、明示選択時は`選択: 学術検索`の形式とし、
  右側のmode menuと役割を分離した。320pxでは会話切替をヘッダー2段目へ配置して44px操作領域を維持する
- 機能選択menuを会話履歴の左へ移し、機能選択・狭幅履歴・履歴削除を同じ行へ統合。左上概略と重複していた
  判定理由のContext barは行全体を削除し、会話本文の表示領域を広げた。幅は従来の機能選択112px・
  履歴可変幅（320px時132px）を維持し、高さだけ両方36pxへ抑えて同一角丸・shadow/focus表現へ統一
- 会話切替の右端へ44pxのゴミ箱ボタンを追加。選択中の履歴を確認なしで即時削除し、新しい空の会話へ切り替える。
  設定内の削除操作も同じ確認なしの挙動へ統一
- 削除後に空会話を即DB作成して「新しい会話」が履歴へ残る不具合を修正。初期表示・新規・削除後は未保存下書きとし、
  最初の送信時だけ会話をDB登録する
- 長文出力が約300 deltaで止まる原因を、bounded `Job.events`の配列長をcursorに使っていた不整合と特定。
  単調増加event sequence/offsetへ変更し、購読遅延時と完了時はDB全文snapshotへ収束する。
  frontendも40ms単位のdelta反映、最大5回の指数backoff再接続、利用者が末尾付近にいる場合だけの追従へ変更
- 入力欄左に44pxのマイク/停止ボタンを追加。1.2秒無音または30秒上限で確定し、ローカル認識結果を
  直接送信する。LLM回答中はミュートし、停止/unmount/失敗時にMediaStream、AudioContext、timerを解放する
- 初回マイク操作でwhisper.cpp v1.9.1と日本語精度を優先した多言語`large-v3-turbo`モデルをbackground job導入する。
  保存先はGit管理外の`~/.local/share/control-deck/runtimes/whisper.cpp/v1.9.1`。モデルは1,624,555,275 bytesと固定SHA-256を検証し、
  静的linkしたruntimeのinstall revisionも一致する場合だけ再利用する。音声は25MiB上限で一時領域へ保存し、
  ffmpegで16kHz mono PCM化、認識後は成功・失敗とも削除する
- 通常回答とワークフロー生成の出力上限は当時の共通既定を8,192 tokenへ変更（後にモデル個別設定へ統一）

検証: backend全251件成功、frontend本番build成功。実機でwhisper.cppをsource buildし、`large-v3-turbo`モデル取得・hash検証に成功。
2回目は0.33秒で既存runtime/modelを再利用した。Wikimedia Commonsの公開日本語音声`Ja-happyou.ogg`を
同じ変換・認識関数へ通し、6.92秒で`発表`と認識。実サービス再起動とhealthを確認。認証付きPlaywright Chromiumで
320x700/1280x800の横overflow 0、textarea 16px、マイク/閉じる/履歴削除44px、自動Web/フロー生成判定、
履歴の確認なし削除→新規会話切替、無音MediaStreamで録音開始→停止→idle復帰、console errorなしを確認した。
実機Qwen3.6-27B + Ollamaでは曖昧な依頼を12.67秒で`research`、Web/学術4手順、最大4反復として有効JSON判定。
Web+学術各1手順の実ジョブも46.22秒で完了し、出典18件、本文1,421文字、計画・進捗4件をDBへ保存した。

## モバイルターミナル閉じるボタン（2026-07-17）

- 全画面ターミナルの閉じる操作をヘッダー右端へ固定し、44px以上のタッチ領域、明確なborder/background/shadow、
  accent focus ringへ統一。PCでは「閉じる」ラベル、320pxでは視認性の高いXアイコンを表示する

## Claude修復コンソールの撤去（2026-07-17）

- Web起動のたびに`seed_repair_app()`が「Claude 修復コンソール」を再登録していた処理を廃止
- 専用`scripts/claude-repair.sh`を削除。既存環境では旧seed由来と判定できる登録だけを起動時に削除し、
  `cdapp-*` systemd user unitの停止・撤去と監査ログ記録を行う
- 同名でも専用scriptを参照しないユーザー登録アプリは削除しない

検証: backend 236件成功。実サービス再起動で旧app ID 5、`cdapp-5.service`、専用scriptを撤去し、
`app.retired_remove`監査ログ1件を確認。2回目の再起動後も登録0件・監査ログ1件のままで再作成されないことを確認。

## サマリー

| Phase | 状態 |
|---|---|
| 文書整備 | ✅ 完了 |
| Phase 1 — 認証 + レイアウト | ✅ 完了 |
| Phase 2 — アプリ管理 | ✅ コア完了（アイコン・TCP/HTTP/ファイル/許可コマンドHC対応済み） |
| Phase 3 — 監視 | ✅ コア完了（アラート通知、アプリ別CPU/RAM/GPU/VRAMを含む） |
| Phase 4 — ファイル + ターミナル | ✅ コア完了（ごみ箱・再開可能upload・ZIP/tar.gz・PDF/audio/video preview対応済み） |
| Phase 5 — ワークフロー | ✅ コア完了（下記参照） |
| Phase 6 — リモートデスクトップ | ✅ コア完了（guacd トンネル + 接続管理 + ビューア） |
| Phase 7 — TOTP ほか | ✅ コア完了（TOTP/PWA/バックアップ。WoL はワークフローノードで対応） |
| Phase 5b — ワークフロー統合開発環境 | 🚧 Phase 1完了、Phase 2（snapshot/retry/node run/pin/部分再実行/test/event/SSE/Alembic/durable pause/artifact）完了、Phase 3 core（typed output/error、approval/merge/data、明示flow control、durable Delay、Try、System Trigger）完了 |

### ワークフロー統合開発環境 監査・詳細仕様（2026-07-19）

- 実コード、API、UI 導線、自動テストを照合し、React Flow、safe dry-run、metadata、実行履歴、
  WorkflowVersion、approval/error handle、parallel loop を再利用できる基盤として確認
- editor 内 chat、実行入力、dry-run、live/history、node 設定が別 surface に分断され、node run、execution snapshot、
  typed output contract、draft/published、retry/resume、sequence 付き event が不足していることを確認
- 現状・実装場所・問題・再利用判断の監査表、target UI、definition v2、data model、API、execution semantics、
  security、quality、migration、test、Phase/PR 計画を `docs/design-workflow-integrated-ide.md` に記録
- mock 回帰と実ローカル LLM/runtime 評価の二層検証、および全 Phase 後に最低 15 sample と全 node の
  詳細説明を提供する Phase 6 を追加

検証: 文書変更のみ。コード実装・service 動作確認は各 Phase PR で実施する。

### ワークフロー統合開発環境 Phase 1 UX 基盤（2026-07-19）

- editor 内の「チャット」を廃止し、trigger input、実行mode、想定最終output、side effect、safe preview結果、
  通常test結果、node別結果、過去実行input loadを同じ `PreviewWorkspace` に統合
- `POST /workflows/preview-definition`、`POST /workflows/{id}/test`、
  `POST /workflows/{id}/executions/{execution_id}/load-inputs` を追加し、legacy `signal.display` から共通output形式を返す
- trigger inputを boolean/multi-select/date/datetime/file-list/JSON/key-value/secret-reference と説明、初期値、
  placeholder、最大長へ拡張。node inspectorを設定/入力/出力/実行/error/詳細の6 tabへ統一
- 実行情報panelをcanvas下部のdebug panelへ移し、live node status、history、versionを維持
- workflow contextをDB保存/API応答する前に再帰redactし、sensitive keyの値が別outputへコピーされた場合も置換。
  live executor contextは変更せず、secret値をresponse/log/DBへ出さない境界を強化
- Phase 2対象のcache/pinを使うnode単体実行、途中再開、historical/current retryはinspector内に導線を先行表示し、
  誤実行を避けるため未実装buttonはdisabledで明示

検証: backend全278件成功、frontend本番build成功。実serviceを再起動しhealth正常。
Playwright Chromiumで入力 → safe preview → test → final output → 過去input load、inspector 6 tabを確認し、
320×700 / 390×844 / 768×1024 / 1280×800で横overflow 0、console/page error 0。一時user/workflowは検証後0件。

## README の現行機能反映（2026-07-17）

- 2026-07-13以降に追加された独立AIアシスタント、LLM provider / llama.cpp複数GGUF管理、Knowledge/RAG、
  ワークフロー安全プレビュー、ジョブ基盤、ファイル・アプリ管理強化、モバイル改善を主機能と直近追加へ反映
- OpenCodeについて、通常起動では導入・有効化しないオプトイン境界、管理prefixへの導入、PATH上の既存導入、
  有効化後のendpoint/model/project/operation設定、disable/uninstallの違いをREADMEへ追加
- `deck.sh` の現行サブコマンドとOpenCode実装・詳細設計を照合し、READMEから設計文書への導線を追加
- ワークフローは標準39ノードと条件登録の`code.agent`を区別し、生成時の意味検証・品質スコア、catalog、
  安全preview、並列map、scrape viewer、RAG/Deep Researchなど2026-07-13以降の追加内容をREADMEへ反映

### AIワークフロー生成の空JSON応答・出力上限修正（2026-07-17）

- Qwen3.6-27B + OllamaでAIアシスタントの最小ワークフロー生成を再現。従来の簡略`response_format`では
  HTTP 200でも本文0文字となり、「有効なJSONを返しませんでした」になることを確認
- OpenAI互換の標準`json_schema` payloadを初回から送り、非対応providerだけschemaなしへfallbackするよう修正。
  JSON抽出もgreedyな正規表現から完全なobjectを順にdecodeする方式へ変更
- ワークフロー生成の固定800 tokenを廃止し、当時のModel共通出力上限を使用（後にモデル個別設定へ統一）。
  UIへ8K〜131K出力と256K CTX presetを追加。CTXと最大出力は独立設定のまま維持

検証: 修正前の同一最小フローはHTTP 200・本文0文字で再現。標準schema化後は実機Qwen3.6-27B + Ollamaで
本文711文字、3ノード・2エッジを生成し、JSON抽出・構造/意味検証とも問題0。backend 235件、frontend本番build、
再起動後のhealth APIを確認。Playwright Chromiumの320px/1280pxで131072 token presetの表示、横overflow 0を確認。
診断でロードしたOllamaモデルは検証後にアンロードした。

追加検証: 静音profile 210Wで20ノード一括生成を試し、厳密JSON SchemaとJSON objectの両方式が300秒でtimeout。
LLMに18段の処理設計を短いJSONで生成させ、サーバー側でtrigger/resultを含む正規定義へ合成する分割方式では約22秒で成功した。
20ノード・19エッジ、構造/意味error 0、品質78の「LLM 20ノード・テキスト処理デモ 0717-0753」をworkflow ID 2へ登録し、未実行のまま内容確認用に保持。

## Web通信・監視処理の軽量化（2026-07-15）

- 高周波の外向きpingはなく、常時通信は認証済みmetrics WebSocketの2秒更新だった。変更前の実ブラウザでは
  12秒に6 frame、`GET /apps`は5秒周期で3回。`/apps`はsystemd状態・プロセスツリー・待受ポートを走査し、
  平均28.7ms（最大39.1ms）だった
- 主負荷は2秒ごとに起動する`amd-smi metric --json`。実機で1回40〜60ms CPU、最大RSS約25MB、
  約23KB JSONを生成していた。複数AMD GPUからVRAM総量最大のdGPUを選び、同じ主要値
  （使用率・VRAM・温度・hotspot・電力・power cap）をamdgpu sysfsから直読するfast pathへ変更。
  sysfsが不完全な環境だけCLIへfallbackする
- アプリ状態の共有queryを15秒周期へ変更。操作時の楽観更新と完了後invalidate、非表示タブ停止は維持

検証: backend 181件成功、frontend本番ビルド成功。実サービスは`sysfs-amdgpu`で32GB dGPUを選択し、
10秒のservice cgroup CPUは1.67%相当、`amd-smi`周期プロセス0。旧CLI実測分を加えた変更前推計4.2%から
約60%削減。1280pxで31秒確認しmetrics 16 frame（初回含む）、`/apps` 3回、console error・横スクロールなし。
320pxのシステム画面も横スクロール・console errorなし、GPU値とmetrics WS継続を確認。

### 汎用ジョブ制御・Model進捗通信

- 互換用`jobs`表へ`job_controls`表を追加し、owner、冪等キー、priority、heartbeat、revisionを永続化。
  最大4同時実行の安定priority queue、queued/running cancel、再起動時interrupted化を実装
- REST、cancel、全体`WS /jobs/stream`でowner本人とownerなしsystem jobだけを返す。cancelは監査対象
- 個別ジョブstreamの0.4秒pollとModel画面の1〜2秒pollを通知Eventへ置換。全体WS更新を100msで束ね、
  高頻度token/eventでも中間通知を増幅させず最新revisionと最終状態を保持
- Playwright Chromiumの1280px/320pxで12秒撮影・通信計測し、jobs RESTは初回1回、jobs WSは1接続、
  横overflow 0、console error 0を確認。backend 198件、本番build成功

### ターミナルの緑色入力欄・画面欠落の追加再現

- Playwrightの320px touch viewportをキーボード相当の高さ390pxへ縮小して撮影。緑色部分は入力textareaではなく、
  永続化用tmuxの既定status barが最下段で入力欄のように見えていたものと特定
- Control Deckは上部にセッション切替UIを持つため、Control Deckのtmux sessionだけstatus barを非表示化。
  既存の永続sessionにも次回接続時に適用し、表示を1行増やす。他のユーザーtmux sessionへは影響しない

## チャット生成遅延・runtime選択基盤（2026-07-15）

- 実機Qwen3.6-27B + llama.cppでワークフロー生成を再現し、従来は内部推論がctx 2048まで1161 token続いて
  47秒後に本文JSONなしで422となることを確認。ワークフロー生成をthinking off、最大800 token、JSON Schemaへ変更し、
  11.55秒で有効JSON（quality 78）を返すようにした
- 永続チャットを既定thinking offかつ有限出力に変更し、OpenAI互換の`reasoning_content`を本文と分離。
  短文「1+1」の実機応答は初回出力・完了とも0.66秒、本文`2`、thinking 0文字を確認
- GPU/導入済みruntimeから Ollama、llama.cpp/ROCm、llama.cpp/Vulkan の利用可能な構成だけを返す
  RuntimePolicy APIを追加。選択状態、排他/共存、共通idle、チャット出力上限・思考、アシスタント名を保存し、
  llama設定UIのハードコード初期値も保存済み値へ修正
- AMD GPU電力上限を含む後続の詳細設計を`design-model-runtime-assistant.md`へ統合。電力制限機能自体は実装中

検証: backend 183件成功、frontend本番ビルド成功。runtime policyの保存・範囲検証・排他切替を単体テスト済み。

### AMD GPU 静音プロファイル

- 最大VRAMを持つAMD dGPUを選び、実機の電力cap、MCLK/SCLK DPM levelを読んで設定範囲を生成。
  AMD以外および変更非対応GPUではUIを表示しない
- 静音（最小210W・MCLK最大から1段低下）、バランス（255W・clock自動）、フルパワー（既定300W・clock自動）、
  カスタム（実機範囲の電力・MCLK/SCLK上限）をRuntimePolicyとしてサーバー保存。balanced/fullはMCLKを必ずautoへ戻す
- チャット、ワークフロー生成、永続チャット、LLM node、RAG、Ollama手動load、llama.cpp手動startおよび
  systemd `ExecStartPre`の全経路で、モデル起動・生成前に同じpreflightを適用
- `deck.sh service`の初回sudo認証でroot所有の専用helperと限定NOPASSWD sudoersを登録。
  Webプロセスはroot化せず、任意パス/コマンドや範囲外値を受け付けない

実機では静音profileを適用し、power cap 210W、MCLK設定上限1124MHz、負荷中最大875MHzを確認。
81 completion tokenは4.48秒。カスタムSCLK 500MHz制限時は実測最大583MHz、同等生成8.98秒となり、
性能低下を確認後に静音profile（SCLK自動）へ復帰してサーバー保存。1280px/320pxとも全profile・210W・1124MHzを表示し、
横スクロール・console errorなし。backend 191件成功、frontend本番ビルド成功。

### Model画面・llama.cppモデル個別設定の再監査

- ページ名称・説明をOllama固定からLLM Model管理へ変更。選択中runtimeのモデルを共通provider APIから表示し、
  llama.cpp選択時は「GGUF登録」、Ollama選択時は従来の取得/削除を提示
- runtime/backendの選択はシート最上位cardだけに統一。下部の重複backend cardを廃止し、未導入backendの追加と
  現在のGGUFモデル個別設定へ役割を限定
- llama.cppの型付き設定へ、CTX、最大出力、GPU層、K/V別cache量子化、Flash Attention、MTP/draft/ngram、
  MoE CPU配置、batch/ubatch、thread、sampling、mmap/mlockを追加。実バイナリ`--help`に存在する能力だけUI表示
- 自由入力`extra_args`を廃止し、未知キーを422で拒否。model pathはrealpath正規化、許可ルート、GGUF拡張子を検証。
  旧設定は新しい型付き既定値を補いながら移行する
- 保存後、稼働中ユニットの内容が変わった場合だけ再起動して設定を反映。同一設定のloadでは無駄な再ロードを避ける

実機Qwen3.6-27B Q5_K_Mを、新しい`n-predict/batch/ubatch/cache K/V/thread/sampling`引数入りsystemd unitで再起動し、
health 200と短文応答`2`（completion 2 token）を確認。Playwright Chromiumの1280px/320px双方で使用中runtime badgeが1個、
MTP/K/V/MoEが各1箇所、横overflow・console errorなし。複数GGUF catalog/router化は次段の残件。

### llama.cppモデル個別設定の保存422修正（2026-07-16）

- 実サービスで設定保存を再現し、`PUT /models/llama/instances/llama`が422になることを確認。
  GET応答のinstanceをfrontendがそのままPUTし、`selected/loaded/unit/runtime_status/base_url/last_used_at`という
  読取り専用statusフィールドまで含めていたため、backendの`extra="forbid"`に拒否されていた
- frontendは書込み可能な型付き28フィールドだけを明示的に選んで送信。backendの未知フィールド拒否は維持し、
  将来status情報が増えても保存payloadへ混入しない境界にした
- FastAPIの配列形式validation detailをAPI clientで`field: message`へ整形し、数値だけのエラー表示も解消

検証: 修正前は同一instance保存が422で6種の`extra_forbidden`。修正後は実サービスで`n_predict`を
2048→2049へ変更して200・永続化を確認し、200で2048へ復元。専用Playwrightで読取り専用field非送信と
validation message表示を確認。frontend本番build、backend全テスト成功。

### 独立AIアシスタント・ワークフロー生成の再評価

- `/assistant`を独立routeとして追加し、PCサイドバー、モバイル操作シート、command paletteから2step以内で起動。
  ワークフロー画面の既存入口も同じcomponentとして維持
- RuntimePolicyで保存したアシスタント表示名を画面へ反映。server DBの会話一覧を選択でき、新規・改名・削除を追加。
  改名/削除は所有者検証し、削除は利用者指定により確認なしで実行して監査ログへ記録
- 独立routeから実機Qwen3.6-27B + llama.cppで副作用のない最小フローを生成し、10.87秒、品質78/100、
  schema/意味検証済みの開始→結果表示フローとして登録・エディタ遷移を確認。検証用会話/フローは終了後に削除

Playwright Chromiumの1280pxで直接route、会話名server保存、生成・登録を確認。320pxでは会話selectorと全モード、
入力欄が可視範囲内で、横overflow・console errorなし。チャット本文生成の既存実測は0.66秒。

### AIアシスタント全画面表示（2026-07-16）

- 従来のmobile 94dvh bottom sheet / PC中央760px modalを廃止し、他の没入機能と同じ
  `100dvh × 100%`の全画面表示へ統一。背景overlay・画面外click終了をなくし、明示的な閉じる操作へ一本化
- headerへ上部Safe Areaを適用し、既存の下部入力Safe Area、会話・設定・モード・生成機能は維持

検証: frontend本番build成功。Playwright Chromiumの1280x800 / 320x700でdialogがそれぞれ
viewport全域（1280x800 / 320x700）に一致し、横overflow・console errorなし。閉じる操作でhomeへ復帰。

### AIアシスタントのモバイル入力横overflow修正（2026-07-16）

- 320px表示で長い非改行文字列を入力して調査。入力textareaがflex itemの既定値`min-width: auto`のままで、
  mobileでも14pxだったため、iOS Safariでは内容のintrinsic widthによるflex拡張とfocus時auto zoomが重なり、
  右へはみ出して見える条件になっていた
- 入力行を`min-width: 0`で縮小可能にし、textareaへ`width: 0; min-width: 0`を設定。mobile form文字を16px、
  `sm`以上を従来の14pxとして、iOSのfocus zoomを防止した
- dialog、設定、本文、footerへ横方向のcontainmentを追加し、会話名編集はmobileで折り返すよう変更。
  ユーザー入力とLLM応答には`overflow-wrap: anywhere`を設定し、長いURLや非改行文字列もbubble内で折り返す

修正前の再現計測はtextarea `font-size: 14px` / `min-width: auto`。修正後のPlaywright Chromium実測は、
320x700でdocument/body/dialog幅がすべて320px、textarea `font-size: 16px` / `min-width: 0px`、
1280x800でもdocument/body/dialog幅がすべて1280pxで横overflowなし。専用回帰テストを追加した。
Playwright WebKitはホスト側共有ライブラリ不足のため未実行で、実iPhone Safariの最終確認は残る。

### ワークフロー副作用なしdry-run・node metadata

- 従来の「ノード単体テスト」は実executorを呼び、app停止/file書込/Webhook等の副作用を起こし得たため、
  UI既定をexecutorを呼ばない「安全プレビュー」へ変更。既存APIの明示的実テスト互換は維持
- 編集中/保存済みworkflowを永続化や実行なしで静的走査し、構造/意味error、warning、到達wave、
  条件分岐/loop、予定副作用と必要capabilityを返すdry-run APIと結果sheetを追加。secret名/値もredact
- backend executor 35種とcontrol.loopの計36種にversion、side effect、capability、主要config/output型、
  retry/cancel/progress/dry-run対応metadataを追加。LLM catalogで欠落していた5種も統合し、集合差をテスト
- Playwright Chromium 1280px/320pxでfile.write→Webhookを撮影し、書込1/外部通信1の予定表示、
  executor未実行の明記、横overflow 0、console error 0を確認。詳細設計は`design-workflow-dry-run-metadata.md`

## Phase 2 / Phase 4 残件対応（2026-07-15）

- **アプリアイコン**: PNG / JPEG / WebP / SVG（2MB以下）を登録・更新画面からアップロード。実パスをAPIへ露出せず、
  認証・`apps.view` 権限付きエンドポイントから配信。SVGは script / foreignObject / イベント属性 / 外部参照を除去し、
  ラスター画像はマジックバイトを検証。置換・削除・アプリ削除時の後始末を監査対象化
- **ごみ箱**: 通常削除を `data_dir/trash` への移動に変更。ユーザー単位の一覧 / 復元 / 完全削除 / 空にする、
  保持日数・容量上限による古い項目の自動purgeを自己メンテナンスへ統合。元パス復元時も許可ルート検証を再実施
- **再開可能アップロード**: 4MBチャンク、厳密なoffset検証、進捗、中止、同じファイル再選択時の再開、
  完了時のatomic replace。途中ファイルは非公開の `data_dir/uploads` にユーザー所有者付きで保持

検証: `./deck.sh test` 165件成功、フロントエンド本番ビルド成功。悪意あるSVG、偽装画像、実パス非露出、
ごみ箱復元・完全削除、チャンク順序違反・再開・取消を自動テストで確認。実サービスを再起動して health API を確認し、
一時E2Eユーザーでファイル画面・ごみ箱を1280px / 320pxの実ブラウザで確認（横スクロール・console errorなし）。

## 永続電源予約（2026-07-15）

- Webプロセス内 `asyncio.sleep` を廃止し、予約確定時だけ `control-deck-power-schedule.timer/service` を
  systemdユーザーユニットとして生成・`enable --now`。取消時は無効化してユニットと状態を削除
- systemdユーザーtimerによりWebサービス再起動・SSH切断後も継続。`Persistent=false` として、
  PC停止中に期限を過ぎた予約が次回起動直後に誤実行されないようにした。実行ワーカーは一般ユーザーで動き、
  固定引数・配列subprocessでlogindへ要求し、予約実行と成否を監査ログへ記録。実行後はunitを自動回収
- UIは即時 / 15分 / 30分 / 1時間 / 3時間 / 8時間と現在予約の取消に対応

検証: `./deck.sh test` 168件成功、フロントエンド本番ビルド成功。実機で24時間後の検証用timerを作成し、
`Persistent=false` / `active` / `enabled` / 次回実行時刻を確認後、即時取消して `inactive` を確認。
実サービス上の予約ダイアログを1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。
破壊的な電源実行は未実施。

## アプリ別ヘルスチェック（2026-07-15）

- アプリ登録・編集でプロセス存在 / TCPポート / HTTP GET（期待status・本文文字列）/
  ファイル存在を設定可能。ファイルはrealpath正規化と許可ルート・拒否パス検証を強制
- バックグラウンドで15秒間隔に並列確認し、実行中プロセスのチェック失敗を `DEGRADED` として一覧・詳細へ反映
- `POST /apps/{id}/health-check` で手動確認でき、詳細画面に結果と確認ボタンを追加
- HTTP本文は先頭64KBまで、タイムアウトは0.2〜30秒。任意コマンド型は許可コマンド基盤がないため未開放

検証: TCP / HTTP status・本文 / 許可・拒否ファイル / API保存・手動実行 / `DEGRADED` 遷移を自動テストで確認。
実サービスの詳細・編集画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

## LLM runtime provider一般化（2026-07-15）

- Claude作業中の `Models.tsx` を破棄せず、Ollama / llama.cppを同じ「LLMランタイム設定」のタブへ統合
- providerカタログを追加し、Ollama設定URL、llama.cpp設定ポート、LM Studio等の代表ポート、管理アプリの待受ポートを
  OpenAI互換 `/v1/models` で並列検出。provider名・管理対象・導入/稼働状態・モデル一覧を共通形式で返す
- `GET /models/providers` を追加し、従来の `GET /workflows/llm-endpoints` も同じ検出サービスへ移行。
  既存の `base_url` / `models` 形式は維持し、チャット・ワークフローとの互換性を保持
- 設定画面に検出済みproviderとモデル数を表示。Ollama固有のモデル取得・削除・詳細設定は既存APIに分離したまま維持

検証: providerの稼働/停止判定、モデル列挙、一意ID、API、従来ワークフロー検出形式の互換テストと本番ビルドを確認。

### llama.cpp 複数GGUF catalog / instance（再監査補完）

- 従来の単一GGUF設定を互換mirror付きcatalogへ移行。alias・port・実体pathの一意性と最大8件を検証し、
  GGUFごとにhash付きsystemd user unit、起動/停止/health、自動起動、idle unload除外、最終利用時刻を管理
- Model画面からcatalogの選択・追加・改名・設定削除（GGUF本体は保持）を行い、各モデルにCTX、出力token、
  GPU offload、K/V量子化、Flash Attention、MTP/speculative、MoE、thread/batch/sampling/RAM設定を保存
- provider共通health APIと、Ollama/llama.cppを合算する同時ロード上限を追加。チャット、ワークフロー、RAGの
  endpoint利用時に対象instanceを活動中として記録し、誤ったidle unloadを防止
- 詳細設計を`docs/design-llama-multi-instance.md`へ記録し、旧単一設定/APIも互換維持

検証: `./deck.sh test` 206件成功、frontend本番ビルド成功。Playwright Chromiumで1280px/320pxの設定シートを
上端・下端まで撮影し、横overflow 0、console/page error 0。利用可能runtime、AMD GPU、共通load上限、
CTX、K/V cache、MTP、MoEの表示を確認。

### LLM runtime生成・stream・cancel共通契約（2026-07-16）

- provider lifecycleと分離した`LlmRuntimeProvider`生成契約を追加し、Ollama native JSONL、llama.cpp/外部
  OpenAI互換SSEをcontent/thinking/usage eventへ正規化
- workflow生成の非stream処理、永続chat worker、旧chat WebSocketを同じproviderへ移行し、GPU preflight、
  thinking、keep-alive、structured response fallback、秘密値を含めないエラーを統一
- request IDのactive registry、明示cancel、task cancel、WebSocket切断時のHTTP接続cleanupを実装。
  `chat.completion` job取消はprovider cancelを通知してからtaskを停止
- 詳細設計を`docs/design-llm-runtime-chat-contract.md`へ記録

検証: backend 211件成功、frontend本番ビルド成功。実機Qwen3.6-27B + llama.cppで新providerから短文`2`を0.71秒でstream完了。
長文生成を最初のchunkで明示cancelし、0.54秒、active request 0、完了後cancel=falseを確認。
実サービスの統合設定画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

### Provider共通モデルライフサイクル

- providerごとに `list/load/unload/delete/pull/configure` capabilityを公開し、共通adapterでモデル情報を
  `id/name/size_bytes/modified_at/loaded/details` に正規化
- `GET /models/providers/{provider}/models` とモデル単位の `load` / `unload` / `DELETE`、provider単位のdurable pull job、モデル設定GET／PUTを追加。
  Ollamaは全操作、llama.cppは設定中GGUFの一覧・起動・停止、外部OpenAI互換は一覧のみ対応
- 未対応の変更操作は `409`、未知provider/modelは `404`、不正設定は`422`。変更操作は値を含めずprovider付きで監査
- 既存Ollamaモデル画面とllama.cpp起動・停止UIを共通APIへ移行。pull／個別設定も共通routeを使用し、既存の固有APIは互換のため維持

検証: `./deck.sh test` 178件成功、フロントエンド本番ビルド成功。実サービスの共通APIからOllamaモデルを取得し、
1280px / 320pxの画面を確認（横スクロール・ログイン後のconsole errorなし）。破壊的なモデル操作は未実施。

### OpenCode オプトインfeature（2026-07-16）

- `./deck.sh feature status/install/enable/disable/uninstall opencode`とfeature registryを追加。通常起動では
  導入・有効化せず、管理prefixと既存外部OpenCodeを区別し、uninstallで外部binary/config/dataを削除しない
- process起動時の有効状態に応じてOpenCode API、lazy frontend route/chunk、PC/モバイルメニュー、command palette、
  workflow `code.agent` executor/catalog/metadataを条件登録。無効時はCSS非表示ではなくAPI/直routeとも404
- projectは既存allowed-root/realpath/symlink検証を通し、promptとOpenAI互換provider設定を600権限のjob別一時ファイルへ
  分離。OpenCodeはsystemd user transient unitで起動し、job cancelでunit停止、出力上限と一時ファイル後始末を強制
- 独立画面からanalyze/implement/fix/test/review、endpoint/model/projectを設定でき、実行は既存job streamで追跡する

実機では外部OpenCode 1.17.15を明示enableし、llama.cpp（Qwen3.6-27B、CTX 16384）経由のrepository分析jobが
6 eventsで成功。完了後に既定無効へ戻し、`enabled_features=[]`、API/直route 404、node catalog非掲載を確認した。
Playwright Chromiumの1280px/320pxで横overflow 0、console/page error 0、healthy表示と全5操作を撮影確認。
外部実体保持、PATH差、配列argv、symlink拒否、cancel unit停止、一時ファイル消去を回帰テスト化した。

### ワークフローノードcatalog・並列map完成（2026-07-16）

- 完了表記を再評価し、`control.loop.parallel`が共有contextを上書きしてitem/indexを競合させる問題を再現。
  iterationごとの分離context、入力順`results`、最後のbody出力の互換mirrorへ直し、1〜5並列mapを実装
- `data.transform`へJSON parse/get/set、Draft 2020-12 Schema検証、CSV相互変換、`file.glob`へallowed-root・
  symlink脱出防止付き検索、`ai.utility`へOpenAI互換embedding、rerank、LLM judgeをoperation統合
- health checkは既存`http.request.expect_status`と重複するため再実装せず、既存UI/説明を維持。file.op/C++ buildと
  command出力で見つけたcatalog/required key/output metadataの実装不一致も実キーへ修正
- node進捗ContextVarをtask単位に分離し、loop/data/glob/AI補助のprogressをlive contextへ公開。
  backend catalogを表示可否の正として、検索、カテゴリ、localStorageお気に入り、利用可能のみ既定ON、未導入確認を追加

検証: backend 224件成功、frontend本番build成功。実サービスAPIでSchema検証を実行し、catalog 39種と新3種の
progress対応を確認。Playwright Chromium 1280px/320pxで検索・お気に入り・絞込・未導入表示を撮影し、
横overflow 0、console/page error 0。詳細設計は`docs/design-workflow-node-catalog.md`。

### 全機能後のWeb軽量化再測定（2026-07-16）

- ダッシュボード30秒でmetrics WS 1接続/15 frame、`GET /apps` 2回、overview初回1回。外向き高周波pingなし
- 1280px実ブラウザで横overflow 0、console/page error 0。ブラウザ切断後のservice cgroup 10秒CPUは0.46%
- GPU collectorは引き続き`sysfs-amdgpu`で、周期的な`amd-smi metric` process起動を行わない

## リモートデスクトップの環境互換性メモ（2026-07-12、重要）

- **Control Deck 側は完全動作**: WS トンネル・認証・guacd ハンドシェイク・ビューアは実機で確認済み
  （guacd が接続を受理し ready/size/image/cursor を配信）
- **ブロッカー**: Ubuntu 24.04 同梱の guacd 1.3.0（FreeRDP 2.11.5）は GNOME Remote Desktop 46
  （FreeRDP 3 系）と RDP ネゴシエーション非互換（全 security タイプで "wrong security type"）
- **対処**: ヘッドレスは **xrdp**（FreeRDP2 互換）を使う方式へ変更。`enable-desktop`（既定ヘッドレス）は
  xrdp を導入し、システムアカウントで PAM 認証、接続時に新規セッションを作成。GNOME RD の RDP は解放
- **接続フォームに security 選択を追加**（any/nla/tls/rdp）。Windows は nla、xrdp は any
- 既知の注意: xrdp + GNOME は「同一ユーザーが同時に 1 セッションのみ」の制約あり。画面を閉じた
  ヘッドレス運用（コンソール未ログイン）を想定

## この PC のヘッドレスデスクトップ操作（2026-07-12、ユーザー要望）

- **`./deck.sh enable-desktop`**（既定ヘッドレス）: GNOME Remote Desktop を `grdctl --system` で設定し、
  この Ubuntu を Web から操作可能にする。TLS 証明書を openssl で自動生成、RDP 認証情報を対話入力、
  guacd を導入、Control Deck に `127.0.0.1:3389` への接続「この PC（headless）」を自動登録
- **ヘッドレス（既定）**: 接続時に仮想セッションを作成（物理画面不要、画面を閉じた運用向け）。
  **リモート接続を有効化するまで仮想デスクトップは作られない**（enable-desktop を実行し、かつ
  クライアントが接続したときのみ）
- **`--active`**: 現在のログインセッションを共有（画面ミラー）。`grdctl`（ユーザー daemon）
- **`./deck.sh disable-desktop`**: 無効化
- 接続登録は `app.cli register-local-desktop`（パスワードは環境変数経由で argv に載せない、暗号化保存）
- セキュリティ: RDP:3389 は Control Deck 経由での利用を前提。外部はファイアウォール/VPN で遮断を案内

注: enable-desktop はシステム状態変更（サービス有効化・ポート開放・パスワード設定）を伴うため、
ユーザーが明示実行する。アプリ側が勝手に仮想セッションを作ることはない。

## Phase 6 リモートデスクトップ（2026-07-12）

- **guacd トンネル**: WebSocket（guacamole-common-js）↔ guacd(TCP:4822) を橋渡し。接続開始時の
  ハンドシェイク（select → args → size/audio/video/image → connect）をサーバー側で実施し、
  以降は raw ストリームを双方向パイプ（guacamole-lite 相当を Python で実装、外部依存なし）
- **接続管理**: RDP / VNC / SSH の接続 CRUD。パスワード等の機微パラメータは Fernet 暗号化保存、
  API 応答には含めない（has_password フラグのみ）。RDP は ignore-cert / display-update を既定化
- **ビューア**: guacamole-common-js（遅延ロード）。マウス + タッチパッド（タップ=クリック・長押し=右クリック）+
  キーボード、Ctrl+Alt+Del、画面リサイズ追従、モバイルはソフトキーボード呼び出し
- **導入**: `remote_desktop.enabled: true` のとき deck.sh が guacd の apt 導入を試みる。
  未導入時は UI に案内を表示し接続ボタンを無効化
- **バックアップ修正**: sqlite3 CLI 非依存に変更（venv Python の sqlite3 backup API で整合スナップショット）

検証: pytest 79 件成功（命令エンコード/パーサ、モック guacd での select→args→connect ハンドシェイク、
接続 CRUD、パスワード暗号化非漏洩）。Playwright で接続一覧・追加フォームを PC/モバイル確認。
ライブ接続は guacd + 実ホストが必要なためこの環境では未実施。

## バックアップ / リストア（2026-07-12、Phase 7）

- `./deck.sh backup [出力先]`: DB / 設定 / 暗号鍵 / RAG / アプリの systemd ユニットを tar.gz に。
  sqlite3 があれば WAL checkpoint 後にコピー（ログは容量のため既定除外）
- `./deck.sh restore <ファイル>`: 復元前に自動退避コピー、確認プロンプトつき、daemon-reload
- `GET /system/backup`（settings.manage）: 設定ページの「バックアップ」からブラウザで DL 可能
- 検証: backup→DB 改変→restore で復旧＋退避コピー生成を確認。DL API も 200/gzip 確認

## PWA 対応（2026-07-12、Phase 7）

- manifest.webmanifest（standalone、テーマ色、192/512/maskable アイコン）+ apple-touch-icon /
  apple-mobile-web-app メタ。ホーム画面追加・フルスクリーン起動に対応
- Service Worker（sw.js）: **アプリシェル（HTML/JS/CSS/アイコン）のみキャッシュ**。
  `/api/`・WebSocket・認証は一切キャッシュしない（機密を Service Worker に保存しない方針）。
  アセットは cache-first（ハッシュ付き名）、ナビゲーションは network-first + オフライン時シェルフォールバック
- 本番ビルドのみ SW 登録（開発時は登録しない）。アイコンは Chromium で SVG ロゴから生成
- TOTP リセット: `./deck.sh reset-totp <ユーザー名>`（`--all` で全員）でロックアウト復旧可能

検証: SW 登録・アクティブ化・manifest 読込を Playwright で確認。オフライン再読み込みでアプリシェルが
起動することを確認。

## TOTP 二要素認証（2026-07-12、Phase 7）

- 有効化: setup（QR=SVG data URI、Pillow 不要）→ 6 桁 verify → リカバリーコード 10 個を 1 回表示
- ログイン 2 段階: TOTP 有効時は `two_factor_required` → コード入力（6 桁 or 使い捨てリカバリー）
- 無効化はコード確認つき。シークレット/リカバリーコードは Fernet 暗号化保存、使用時に消費
- `require_totp_for_admin` は後方互換設定として管理者必須ポリシーへ統合。bootstrap に SQLite 軽量マイグレーション追加
- 検証: pytest 71 件、Playwright + pyotp で全フロー E2E

## アラート通知（2026-07-12、Phase 3 残り完了）

- **ルール**: メトリクス（CPU/RAM/GPU/VRAM/CPU温度/GPU温度/ディスク使用率/アプリ停止）× 演算子（>/≥/</≤）
  × しきい値 × 継続時間（sustained）× クールダウン。アプリ停止は対象アプリ指定
- **通知チャンネル**: Discord / Slack / 汎用 Webhook。URL は Fernet 暗号化保存・表示時マスク・テスト送信可
- **評価ループ**: 15 秒間隔で評価。継続時間を満たすと AlertEvent 発火＋通知、条件解消で resolved。
  ウォッチドッグの心拍対象にも追加
- **UI**: 設定に通知チャンネル / アラートルール管理、ダッシュボードにアクティブアラートバナー
- **必要ソフトの自動導入**: deck.sh に tesseract / tmux の apt 導入（passwordless sudo 時）と
  Playwright ブラウザ（Chromium）自動導入を追加

検証: pytest 62 件成功。E2E で webhook 受信サーバーへの発火通知＋テスト送信を確認。

## ワークフロー拡張 v2（2026-07-12、ユーザー要望）

- **ノード追加**（全 25 種）: ループ（回数 / foreach、body/done 2 出力、`{{ID.item}}`/`{{ID.index}}` 参照）、
  変数セット、文字列操作（大小変換 / 置換 / 正規表現抽出 / 分割 / JSON 抽出 / テンプレート）、
  Markdown→HTML、ファイル読込 / 出力（追記可）/ 操作（copy/move/delete/mkdir）、
  LLM 生成（OpenAI 互換 = Ollama/vLLM/llama.cpp/OpenAI）、Web スクレイピング（CSS セレクター）、
  ブラウザ操作（Playwright）、OCR（tesseract）、Wake-on-LAN、
  SSH 実行（鍵認証 BatchMode、host 検証）、Git 操作（サブコマンド許可制）、C++ ビルド（CMake/Make）、
  Python 実行（**初期無効**、`security.allow_arbitrary_commands` で許可、venv python の -I -c 実行）
- **安全性**: すべて shell=False の配列実行。ファイル系は許可ルート検証を通す。SSH host / Git サブコマンドは
  ホワイトリスト。任意シェル文字列ノードは非提供
- **エディター刷新**: アイコン付きノード + カテゴリカラーバー、実行状態リング、ドットグリッド背景、
  ミニマップ、矢印マーカーエッジ、ループの反復/完了ハンドル
- **カスタムノード / スニペット**: 選択ノード群をスニペットとして localStorage 保存 → パレットから再挿入
- **ワークフロー入出力**: 定義を JSON でエクスポート / インポート（他環境への持ち運び）

検証: pytest 56 件成功（v2 ノード 13 件: 文字列 / 変数チェーン / Markdown / ファイル IO / WOL /
Git 許可制 / SSH host 検証 / Python 無効 / ループ foreach・count / スクレイピング）。
E2E で「foreach ループ → 大文字化 → ファイル追記」を実行し APPLE/BANANA/CHERRY 出力を確認。
Playwright でダーク/ライト・PC/モバイルのエディターとパレットを確認、横スクロール 0・エラーなし。

### RAG 構築 / DB 操作ノード（2026-07-12 実装）

- **db.query**: SQLite（許可ルート配下のファイル）または任意 SQLAlchemy URL（PostgreSQL 等）へ SQL 実行。
  名前付きパラメータ（`:id`）でバインド。先頭が SELECT/INSERT/UPDATE/DELETE/CREATE 等の SQL のみ許可、
  SQLite パスは許可ルート検証。SELECT は最大 500 行を dict で返す
- **rag.build**: テキスト（またはファイル）をチャンク分割 → OpenAI 互換 `/v1/embeddings`（Ollama の
  nomic-embed-text 等）で埋め込み → コレクション別 SQLite（data_dir/rag/{name}.db）へ保存
- **rag.query**: 質問を埋め込み、numpy コサイン類似度で top-k チャンクを取得。`{{ID.context}}` を
  LLM ノードへ渡すことで RAG パイプラインを構成（依存はベクトル DB 不要、numpy のみ）

検証: pytest 69 件成功。E2E で DB クエリ（テーブル作成→挿入→カウント）、RAG（フェイク埋め込みで
build→query マッチ）を実機ワークフローで確認。

## 自己メンテナンス / ウォッチドッグ（2026-07-12、ユーザー要望で追加）

- **systemd ウォッチドッグ**: `Type=notify` + `WatchdogSec=30` + `NotifyAccess=main`。
  起動完了時に READY=1、内部ヘルスチェック（DB 接続 / メトリクス収集の鮮度 / スケジューラー心拍）が
  正常な間のみ 15 秒間隔で WATCHDOG=1 を送信。ハング・内部異常時は systemd が SIGABRT → 自動再起動
- **自己メンテナンスループ**（起動 5 分後 + 1 時間間隔）:
  ログローテーション（copytruncate + gzip、`rotate_size_mb`/`rotate_generations`/`retention_days`、
  仕様 §11.3 対応）/ 期限切れセッション purge / 監査ログ保持（`audit_retention_days` 既定 180 日）/
  SQLite WAL checkpoint + optimize / ディスク残量自己点検（10% 未満で警告）
- **自己状態 API/UI**: `GET /system/self-status` + システムページ「Control Deck 自己診断」セクション

検証: pytest 43 件成功（ローテーション世代管理 / purge / ヘルスチェック / sd_notify フォールバック）。
実機で SIGSTOP によるハング模擬 → 30 秒で `Watchdog timeout` → SIGABRT → 自動再起動 → 復旧を確認。

## Phase 5 実装内容（2026-07-12）

- **エンジン**: ノードグラフ実行（トリガー → 逐次 + 条件分岐）。ノード別タイムアウト、
  ステップ上限 / ループ防止、実行キャンセル、ノードごとの入出力・エラー・時刻を保存
- **ノード**: トリガー（手動 / 間隔 / 毎日 / cron）、アプリ起動・停止・再起動・状態取得、
  HTTP リクエスト（期待ステータス検証）、条件分岐（eq/ne/gt/lt/contains、真偽 2 分岐）、待機、
  Webhook 通知（汎用 / Discord / Slack）、ファイル存在確認（許可ルート検証を通す）。
  テンプレート `{{ノードID.フィールド}}` で前段出力を参照可能。**任意シェル実行ノードは提供しない**（§20.6 安全モード）
- **スケジューラー**: 30 秒間隔で有効ワークフローの間隔 / 毎日 / cron（croniter）トリガーを評価
- **API**: workflows CRUD / run / enable / disable、workflow-executions 一覧・詳細・cancel（すべて RBAC + 監査）
- **UI**: 一覧（実行ボタン + 前回結果 + スケジュールトグル）、React Flow エディター（遅延ロード、
  カスタムノード、条件ノードは真/偽 2 ハンドル、ノードパレット + 設定ボトムシート、モバイル対応）、
  実行履歴シート（ノードごとの出力 JSON 表示、実行中は自動更新）

### UI テーマ / ロゴ（同日、PR #4）

- モード（システム / ライト / ダーク）+ アクセント 6 色 + OLED 完全黒。localStorage 永続化
- スライダーモチーフの SVG ロゴ（アクセント色連動、favicon 含む）

検証: pytest 37 件成功（定義検証 / テンプレート / 条件分岐グラフ / API CRUD+実行 / viewer 権限 /
スケジュール判定）。E2E で HTTP ヘルスチェック → 条件分岐 → true 側のみ実行を確認。
Playwright（1280 / 390px）でエディター・パレット・設定シート・実行履歴を確認、横スクロール 0・エラーなし。

## Phase 4 実装内容（2026-07-12）

- **ファイル**: 許可ルート限定（realpath + commonpath + 拒否リスト ~/.ssh 等）。一覧 / アップロード
  （複数・D&D・上書き確認・サイズ上限）/ ダウンロード / プレビュー（画像）/ テキスト編集
  （Monaco 遅延ロード、CDN 不使用、Ctrl+S 保存）/ mkdir / rename / copy / move / 削除（確認 + 監査）
- **ターミナル**: PTY + WebSocket。tmux があれば永続セッション（cdterm-*）、なければプロセス内 PTY
  フォールバック（切断→再接続でバッファリプレイ）。xterm.js 遅延ロード、モバイル全画面 +
  補助キーバー（Esc/Tab/Ctrl/矢印/^C/^D）、visualViewport 対応、リサイズ同期、監査記録
- **運用**: 単一エントリースクリプト `./deck.sh` へ統合（venv / Node 依存 / ビルド / 設定 / linger /
  管理者の不足を自動判定して整えてから起動。旧 scripts/* は互換ラッパー化）
- **修正**: ログインレート制限を「失敗のみカウント」方式へ（正規ユーザーの連続ログインで誤制限しない）

検証: pytest 31 件成功（ファイル API roundtrip / トラバーサル / symlink 脱出 / viewer 権限 /
ターミナルライフサイクル / ブルートフォース制限）。実機 E2E（アップロード→ダウンロード、
symlink→/etc が 403、WS ターミナルで echo 実行→再接続リプレイ）。Playwright で 1280/390/320px
確認（横スクロール 0、コンソールエラーなし、モバイル全画面ターミナル + キーバー表示）。

### ターミナルのモバイルキーボード・長文履歴再監査（2026-07-15）

- mobile software keyboardによるvisual viewportの縮小・移動へ、ターミナルroot自体を追従させた。
  bodyを固定せず背景scrollだけを止め、browserの自動panとの二重移動、入力位置や画面の欠落を解消
- xtermとtmuxの履歴を100,000行へ統一。接続時にtmuxの全履歴を最大16MiBでsnapshot再生し、
  再接続中の出力も復元する。上限超過時は無言で消さず切り詰め通知を表示
- attach直後の端末resetがsnapshotを消していた順序不具合を修正し、初期化→browser reset→snapshotの順へ統一。
  session IDも8桁hexへ限定し、PCヘッダーに全文コピーを追加
- `deck.sh`のservice登録判定が`pipefail`と`grep -q`でSIGPIPE終了し、登録済みでもforeground起動を試みる場合が
  あったため、`systemctl --user cat`による判定へ変更

検証: 実tmuxへ10,000行を出力し、Playwright Chromiumの1280px/320px双方で先頭・末尾を確認、末尾重複1回。
320pxでvisual viewportを`top=180 / height=300`へ移動してroot・補助キーバーが同範囲内、入力textareaが透明、
bodyが`position: static`のままであることを座標・computed style・撮影で確認。詳細は
`docs/design-terminal-mobile-history.md`。

### ターミナルのモバイル1行操作・touch履歴追従（2026-07-16）

- xterm.js 6の独自scrollbarがmobile touch dragを履歴位置へ反映しないことを実ブラウザで再現。
  terminal面の単指縦dragをcell単位の`scrollLines`へ変換し、指移動中に過去出力を追従表示するよう修正
- モバイル補助操作列は高さ40px・`flex-nowrap`の1行へ固定し、横scrollbarを非表示化。
  空の2段目を作っていたSafe Area paddingを撤去し、履歴専用ボタンを置かず画面scrollへ統一
- `visualViewport.scroll`時は座標同期だけにし、hostの行列数が変化した場合だけPTY resize。
  keyboard/IME入力中の無駄なreflowを止め、長文入力の表示変動を抑制
- tmux初期描画の非同期`Terminal.write()`を同期`reset()`が追い越し、履歴境界へ現在画面が混在する不具合を特定。
  初期描画→reset→snapshot→streamをwrite callbackのPromise chainで直列化し、完了扱いだった履歴重複を再修正
- tmux captureとbrowser全文コピーでsoft wrapを論理行へ復元し、画面幅由来の改行がコピー内容へ混ざらないよう修正

検証: 実tmux sessionへ300行を出力し、Playwright Chromium `320x700 / hasTouch`で指dragにより
viewportYが263→250へ移動し、251〜291行を欠落・重複なく連続表示。全13補助ボタンのtop/bottomが
667/694pxで一致し、toolbarは40pxの1行、横scrollbar表示なし。1280x800ではmouse wheelで
253→250、251〜300行を連続表示。双方console error 0。backend 224件成功、frontend本番build成功。

### ターミナル処理中入力の描画競合（2026-07-16）

- `\r\x1b[K`で80ms更新するWorking表示中に文字入力し、320pxでterminal面の黒化と表示行分離を再現。
  PTY入力・処理結果は保持されており、空になった旧native viewportのmomentum-scroll合成layerが主因と特定
- 旧viewportの合成layerを無効化し、全scrollイベントの強制refreshを廃止してxtermの差分描画へ戻した。
  viewport下地もtheme背景へ統一し、renderer更新間の黒い下地露出を防止

検証: 実tmuxでWorkingを80ms更新しながら途中入力。Playwright Chromium 320x700では入力結果を保持し、
touch履歴位置44→31、1280x800ではwheel履歴位置3→0へ移動。黒化・行分離・横overflow・console errorなし。

### ターミナルIME確定時の行位置ずれ（2026-07-16）

- 320pxでkeyboard相当のviewport縮小中、文字確定ごとに全行が3px上へずれ、再表示で直る現象をidle/Working中に再現
- xtermの端数cellがhost content高を3px超える状態で、`overflow: hidden`のhostがIME用textareaを表示するため
  `scrollTop=3`へ自動scrollされていた。hostをscroll containerにしない`overflow: clip`へ変更
- keyboard開閉を4回反復し、各入力確定前後でhost scrollTop=0、先頭行top=43px、末尾行top=358pxを維持。
  閉じた後は末尾行top=643pxへ正常復帰し、Working中に入力した`ZZZZ`の実行結果も保持。
  mobile touch履歴48→35、PC wheel履歴78→75、双方console errorなし。backend 224件・frontend本番build成功

### iOS Visual Viewport・xterm fit・PTY resize安定化（2026-07-16）

- `FitAddon`が直接の親paddingを寸法から引かないため、hostの左右8px・上4px分だけ行列を過大算出していたことを特定。
  装飾paddingを外側wrapperへ分離し、xtermの直接の親を無padding・非scroll containerへ変更
- keyboard animation中にvisual viewportとResizeObserverの中間寸法を逐次反映していた処理を、最新世代だけを
  2 RAF + 50ms後に適用する単一schedulerへ統合。確定geometryのfit/refreshはPTY write queueと直列化
- 0/極小・非表示geometryを除外し、PTY通知をrows>=3 / cols>=10、同一接続内の重複なしに制限。
  再接続時は最終有効寸法を再送し、backendもrows 3〜500 / cols 10〜1000へ正規化して重複ioctlを抑止
- visibility復帰、pageshow、window/visual viewport resize、ResizeObserverを統合し、全listener・RAF・timer・WS handlerをcleanup。
  opt-in診断は`localStorage['control-deck:terminal-geometry-debug']='1'`でのみ有効

検証: Playwright Chromium 320x700で80ms未満のWorking出力中にkeyboard相当の410/700px切替を10回実施。
開時21行・閉時41行、全行15px等間隔、host padding/scrollTop=0、xterm instance=1、入力10文字保持、
無効PTY resize 0、同一接続内重複0、touch履歴89→76、再mount後41x38再同期、console errorなし。
PC 1280x800↔900x600を5回反復し、50/37行、cols 160/112、入力保持、wheel履歴137→134、console errorなし。
backend 225件成功、frontend本番build成功。

### iOS IME composition・geometry・TUI描画同期（2026-07-16）

- PR #73の通常fitに残っていた全行`refresh()`と、IME状態を知らないroot座標/寸法更新が、iOS未確定文字のtextarea座標と
  fullscreen TUI再描画を別時点へ動かす競合を根本原因として再監査
- `TerminalWriteQueue`、`TerminalImeController`、`TerminalGeometryController`へ責務を分離。composition開始から終了後2 RAFまで
  resize/refresh/root geometry/PTY resizeを禁止し、保留変更を単一schedulerで最終geometryへ1回だけ反映
- 通常fitから全行refreshを削除。renderer復旧はpageshow/visibility/再接続時にも実測不一致がある場合だけ、同一世代1回・1秒cooldownで実行
- size/position/renderer/connection invalidation、2 RAF + 50ms、write queue投入を集約。DOM read/writeをframe分離し、position-only・同一寸法をno-op、
  queue滞留最大1件、touchmoveのcell計測をgesture開始1回へ削減。scrollback 100,000と既存操作は維持
- opt-in診断へIME event、textarea/cursor/各layout rect、fit世代、処理回数、queue滞留、Long Taskを追加。通常時は詳細object/DOM診断/consoleを生成しない

自動検証（Playwright Chromium mobile 320x700、実tmux）: size request集中時はfit request 28→実fit 1、resize 1、PTY 1、refresh 0、
queue最大1、Long Task 0。composition中100件はfit/resize/refresh/PTY/DOM readすべて0、終了後fit/resize/PTY各1、textarea 1。
50ms Working 200回 + keyboard開閉10回で出力/入力欠落なし、refresh 0、queue最大1、textarea 1、controller listener 13で固定。
helper 40px、layout合計誤差1.5px以内、screen/helper非重複を確認。PC 1280x800でwheel履歴、全文copy、再mount 0→1 textarea、
console error 0を確認。10分soakは730周期（IME/開閉/pageshow/20周期ごと再mount）成功、終了時heap 11.9MB→11.9MB、
geometry task滞留0・最大1、refresh 0、Long Task 0、textarea 1、controller listener 13。backend 225件、frontend build、
Playwright通常5件成功（soak 1件は通常skip）。物理iPhone Safari/PWAの日本語候補UI・開閉10回・background/回線再接続録画は
環境外のため**実機確認待ち**。
- マージ後の座標実測で、最終resize後もxterm 6.0.0のtextareaだけ旧top=643pxに残ることを追加検出。xterm最新版はcursor move時だけ
  textareaを同期するため、最終geometry完了後に内部と同じセル座標式を1回適用する追補修正を実施。composition中はtop=643px、
  host bottom=660px、helper top=660pxを固定。終了後はtextarea top/bottom=373/388px、host bottom=390px、helper top=390pxへ同期し、
  textarea 1、rows/cols=23/38、terminal runtime console error 0を実測。composition/PC回帰2件も成功
- その後の物理iPhone報告で、keyboard表示中の通常PTY文字まで`Working`が文字単位・複数座標へ分散する重大回帰を確認。
  Chromiumでは再現せず、PR #75で追加したscreen外寸近似によるhelper textareaの`left/top/width/height/lineHeight`直接変更と、
  composition flush後の無条件focusがiOS Safariのviewport/合成layerを再駆動する最有力要因と判断した。原因を混ぜない緊急対応として
  PR #75の独自同期、専用completion RAF、無条件focusだけを撤去し、PR #74のcomposition lock、単一geometry scheduler、write queue、
  通常fit refresh 0、touch/copy/reconnect/100,000行scrollbackは維持した。
- ロールバック後の自動検証: frontend build成功、backend 225件成功、Playwright Chromiumはmobile 320pxのcomposition/geometry、
  xterm DOM row高さ・間隔一定かつtransformなし、50ms Working 200回 + keyboard相当10往復、desktop wheel/copy/remountの5件成功
  （10分soakのみ通常skip）。ControlDeckによるcomposition後textarea inline style変更0、full refresh 0。物理iPhoneでの
  英字/日本語/削除/Working/keyboard開閉10回は再確認待ちとし、この時点ではresize ACKやroot top/left変更を追加しなかった。
- PR #76後の物理iPhoneでも通常PTY文字の分散、placeholder二重化、空白画面化が再現したため、保留していた世代付きPTY resize
  transactionを実装。frontendはconnection/resize generation付き要求を送り、backendは`TIOCSWINSZ`成功後だけACKする。
  backendのbinary/ACK送信を接続単位lockで直列化し、frontendはACK後に受信した最初のPTY frameを単一write queueで描画完了してから、
  保留inputを`term.onData()`受信単位のFIFOで解放する。出力しないshellだけ125ms fail-safe、上限256 chunk/256KiB、再接続/disposeで
  旧queue破棄。同一geometry、position-only、force syncはbarrierを作らない。
- fixed rootへVisual Viewport offsetTop/Leftを再適用する二重panを撤去。size変更だけroot寸法とfitへ反映し、transaction中の新geometryは
  最新1件へ集約。resize完了後にbuffer/DOM cursor周辺だけを比較し、不一致時のみ同一resize世代1回・cursor前後1行をrefreshする。
  通常input/Backspace/WorkingではDOM比較・refreshなし。PTY制御要約、世代時系列、tmux/PTY size、明示buffer/DOM snapshotを
  opt-in最大300件診断へ追加し、通常時は本文decode/DOM計測/subprocess診断を行わない。
- 検証: backend 226件成功、frontend build成功。Playwright Chromium 320pxは9件成功（10分soak 1件skip）。古いACK破棄、ACK前
  3 input chunk（絵文字含む）保持、ACKだけでは未解放、次PTY write callback後FIFO解放、再接続世代で旧input破棄、position-only/
  同一geometry barrier 0、placeholder buffer/DOM各1・mismatch 0、xterm/textarea各1を確認。Working 50ms×200回 + keyboard相当10往復は
  resize/PTY/ACK各18、timeout 0、full refresh 0、geometry queue最大1、Long Task 0、出力/入力欠落0。PC wheel/copy/remountも成功。
  Playwright WebKit 26.5は取得済みだが、ホストに`libevent-2.1-7t64`、`libavif16`、`libwoff1`がなく、sudoersも対話認証を
  要求するため、この環境では起動不可。物理iPhoneの縦横回転・background復帰を含む10セットは新transaction反映後の確認対象で、
  Chromium成功と区別して未完了扱いとする。
- 実サービスの世代診断で根本原因を追加確定。keyboard相当resize要求`38x23`に対し、従来はACK/125ms後もPTY=`38x23`、
  tmux client/window=`38x41`で、独立process groupの`tmux attach-session`へSIGWINCHが伝播していなかった。ioctl成功後に
  ControlDeck所有attach process groupへ明示SIGWINCHを送るよう修正し、ACK時点・transaction後probeともPTY/client/window=`38x23`、
  `window-size=latest`一致を実測。さらにlocal xterm resizeをbackend ACK前からACK handlerのwrite queue commitへ移し、ACK前の旧size
  PTY frame→local resize→ACK後SIGWINCH frameの順を保証した。これにより旧41行前提のANSI cursor/Working出力を23行xtermへ
  解釈させる世代跨ぎを防止する。
- PR #77後の「keyboard開閉で全履歴再読込に見える」現象を接続診断で分類。実ControlDeck Webの320px keyboard相当開閉10回では
  WebSocket created/close増加0、history_reset増加0、replay増加0であり、接続維持中の実size変更18回に対するtmux/TUIの
  SIGWINCH全画面再描画だった。一方、意図的なWebSocket切断では従来、新attach作成と`history_reset + capture-pane全量`が必ず発生した。
- 再接続を`clientInstanceId + connectionGeneration + lastSequence`による差分resumeへ変更。同じbrowser instanceのtmux attachを
  切断後30秒保持し、4MiB/4096 chunkのsequence journalへ切断中も1回だけ記録する。journal範囲内は既存xterm bufferを維持して差分だけ、
  範囲外/backend再起動/完全reloadだけ`resume_reset_required`後のbounded snapshotへfallbackする。接続状態を
  DISCONNECTED/CONNECTING/INITIAL_REPLAY/RESUMING/LIVE/CLOSEDで管理し、LIVE前inputをFIFO保持。新世代接続後に旧socketのfinallyが
  cleanupを予約する競合も防止した。
- 検証: terminal backend 12件・backend全231件成功、frontend本番build成功。実ControlDeck Web Chromiumは14件成功
  （10分soak 1件skip）。keyboard開閉10回は接続1、
  history_reset 1→1、replay 1016B→1016B、full refresh 0、geometry queue最大1、Long Task 0。Working 50ms×200回中も接続/replay増加0。
  意図的切断1回はresume_ready 1、history_reset増加0、切断中2 chunkを順序どおり差分描画し、入力欠落・重複0。journal範囲外は
  reset/fallback各1、完全reloadはinitial replay 1、session切替は履歴混在0。
- PR #78で追加した`crypto.randomUUID()`がiOS Safariの非secure HTTP contextで未定義となり、XtermViewのeffect初期化が例外終了する
  回帰を修正。共通`createUuid()`を`src/lib/clientId.ts`へ分離し、randomUUID→getRandomValuesによるUUID v4→時刻・
  Math.randomの一時IDの順にfallbackする。全経路でbackend契約を満たし、crypto自体が未定義でも例外にしない。IDはXtermViewのeffectごとに1回生成し、
  同一mount内のWebSocket再接続では維持する。Playwright runnerの生成試験4件（cryptoなし1000件一意性を含む）、frontend本番build、
  backend全235件が成功。サービス再起動後の`/api/v1/health`は127.0.0.1とTailscale HTTP `100.82.8.44:8765`の双方で成功した。
  認証付きChromium 16件成功（soak 1件skip）。randomUUIDを無効化したHTTP上のmount・再接続維持・画面例外なしに加え、
  320px keyboard 10往復、差分resume、desktop wheel/copy/remountも確認。物理iPhone Safari/PWA確認は未完了扱いとする。
  Service Worker cacheをv14へ更新し、旧shell/assets cacheをactivate時に削除する。

- Webターミナルの長文paste欠落を修正。接続前FIFOとresize FIFOの256 chunk/256KiB上限による無言破棄を廃止し、pasteを通常キー入力から
  分離した`TerminalInputController`へ移行。全文をUTF-8化して8KiBずつ、未ACK 1 chunkで送信し、`bufferedAmount`、LIVE状態、resize barrierに
  基づきpause/resumeする。backendはinput control+binaryを検証し、PTY全量書込み後だけsequence ACKを返す。ACKはclient streamへ5分/8192件
  保持し、再接続時の同一sequence再送は二重書込みせず再ACKする。stale世代ACKは無視し、session切替/disposeは残りをcancelする。
- `TerminalConnection.write()`は単発`os.write()`から、部分書込み・InterruptedError・non-blocking書込み可能待ち・0 byte異常を扱う全量書込みへ変更。
  長文表示は32KiB以上だけ100ms throttleの進捗、キャンセル、失敗時再試行を表示。xterm標準と同じCR変換とbracketed pasteをpaste全体へ1回だけ適用。
  opt-in診断はpaste/chunk/sequence/文字・byte数/累積量/世代/bufferedAmount/hash/マスク値だけを記録し、本文は保存しない。
- 検証: backend全235件、frontend本番build、controller/ID Playwright 7件成功。実サービス再起動後health成功。認証付きChromium実サービスで
  100KB ASCII（102422B）、300KB ASCII（307232B）、日本語+絵文字（104540B）をraw PTY受信機の長さ+SHA-256で完全一致確認し、欠落・重複・
  replacement character 0。100KB送信中の320px keyboard geometry 10往復+resize barrier、300KB送信中のWebSocket切断+差分resumeも同じhashで完了。
  PC幅のwheel/copy/remount、session切替、従来resize FIFOも成功。物理iPhone Safari/PWAでのbrowser pasteイベント分割数だけ未計測。

## 実装済み機能

### バックエンド（FastAPI + SQLite WAL）
- 認証: Argon2id / サーバー側セッション（HttpOnly + SameSite Cookie、DB はトークンハッシュのみ）/
  CSRF（X-Requested-With 必須）/ ログインレート制限 / セッション一覧・失効
- RBAC: administrator / operator / viewer。REST・WebSocket 双方で権限依存性を強制
- 監査ログ: ログイン成功・失敗 / アプリ登録・編集・削除・起動・停止・強制終了 / ログ削除 / 電源操作
- アプリ管理: python_script / shell_script / executable / systemd_service（ユーザーユニット＋固定root allowlistのsystem service）
  - systemd ユーザーユニット生成（`cdapp-{id}.service`、引数エスケープ + インジェクション対策 + StartLimit 再起動ループ検出）
  - start / stop / restart / kill、8 状態マッピング、PID / 稼働時間 / CPU / RAM 取得
  - 環境変数は Fernet 暗号化保存、表示時は秘密キーをマスク
  - Python インタープリター自動検出 / プロジェクト検出（提示のみ）
- ログ: stdout / stderr の append 保存、tail / ダウンロード / 削除 / WS ストリーム
- 監視: psutil + GPU プロバイダー（amd-smi → rocm-smi → sysfs → nvidia-smi、失敗時 N/A）
  - 単一メトリクス WS ストリーム、1 分平均を SQLite へ保存（保持期間つき）、RAPL による CPU 電力推定
- 電源: reboot / shutdown / systemdユーザーtimerによる予約・取消（Web再起動後も継続、期限切れは再実行しない）

### フロントエンド（React + TS + Vite + Tailwind v4、gzip 約 99KB）
- ログイン / 認証ガード / 401 自動リダイレクト
- デスクトップ: 折りたたみサイドバー、Ctrl+K コマンドパレット（アプリ検索・起動停止・ページ移動・電源）
- モバイル: 下部ナビ 5 項目（中央「操作」→ボトムシート、電源は視覚分離）、Safe Area 対応、FAB
- ダッシュボード: CPU / RAM / GPU / VRAM タイル + スパークライン + 実行中 / 失敗アプリ
- アプリ: カード（主操作 1 個 + ⋯メニュー）、詳細ボトムシート、3 ステップ追加フロー（venv・エントリーポイント自動提案）、削除確認ダイアログ
- ログ: WS リアルタイム追従、仮想スクロール（2 万行保持）、正規表現検索、一時停止、stdout/stderr 切替、DL / 削除
- システム: ホスト / CPU コア別バー / GPU / ディスク / ネットワーク / 上位プロセス
- 設定: テーマ（システム / ライト / ダーク）、セッション管理、監査ログ閲覧（admin）
- 楽観的 UI（起動→即 STARTING）、WS 自動再接続 + 再接続中バッジ、タブ非表示時は購読停止

### スクリプト / 運用
- `scripts/setup.sh`（venv + npm + build + linger）、`run-dev.sh`（起動時 venv 自動構築）、
  `create-admin.sh`、`install-service.sh`（systemd ユーザーサービス、root 不要）

## 検証結果（2026-07-12、Ubuntu 24.04 実機）

- pytest 19 件成功（認証 / CSRF / 権限 / ユニット生成エスケープ / パストラバーサル / symlink 脱出 / マスキング）
- API E2E: 登録→起動→ログ→再起動→停止→ログ削除→監査を curl で確認
- **プロセス継続性**: Web バックエンド kill 後もアプリ継続、再起動後に同一 PID・稼働時間を取得
- WS: 未認証 403 / 偽 Origin 403 / 認証済みメトリクス・ログストリーム受信 OK
- GPU: AMD GPU を amd-smi で取得（使用率 / VRAM 23.3/32GB / 温度 / Hotspot / 電力 / ファン）
- UI: Playwright で 1280 / 390 / 320px を検証。横スクロール 0px、コンソールエラーなし。
  ダッシュボード / アプリ / ログ / システム / 設定 / 操作シート / 追加ドロワー / パレットのスクリーンショット確認
- systemd サービス: `control-deck-web` をユーザーサービスとして登録、非 root（一般ユーザー）で稼働、
  linger 有効化により SSH / ログアウト後も継続

## 既知の制約 / 次の作業

1. system service helper／root catalogの初回導入は`./deck.sh service`で対話sudo認証が必要。コード・境界・自動／ブラウザ回帰は完了したが、この実機でのhelper操作確認は導入後に行う
2. PostgreSQL運用切替コードは完了したが、この実機にserver／clientがなくonline migration／backup／restoreは確認待ち
3. 電源 reboot/shutdown は API 実装済みだが、破壊的な実機実行は未検証

## 履歴

- 2026-07-21: 宣言型plugin SDK v1を追加。厳格manifest（API version／ID／SemVer／capability／既知permission／安全なnavigation URL）、0600原子保存、symlink・サイズ・所有者検査、管理API／CLI／設定UI、PC sidebar／mobile操作sheet／command paletteへの権限付き導線、CSRF／RBAC／監査を実装。外部plugin codeは本体へloadせず、削除もmanifestと状態だけに限定。CLI実往復、backend全460件、frontend本番build、実service health、Chromium 1280／320px（横overflow・console errorなし）を確認し、一時manifest／user／session／auditを除去

- 2026-07-19: WorkflowとRunnerを「作成・デバッグ」と「公開アプリ」に役割整理。editor主操作を更新して開く／アプリを開く、一覧へ公開版button、URL deep link/reload復元、無効ID復帰表示を追加。`llm.chat`は管理Ollamaをprovider adapterで自動load、llama.cppをsystemd user起動してhealth待機し、外部endpointは非操作。backend全322件、frontend本番build、実サービス、Playwright 3件、未load実機27B modelのload→生成→unload復元を確認
- 2026-07-19: `human.approval`の承認待ちcontractを型付きobjectへ統一し、公開アプリの500を修正。承認文・担当者・期限・承認／却下をeditorと公開アプリへ統合し、API回帰testと320px公開操作E2Eを追加
- 2026-07-19: 実行履歴のNodeRun観測を強化。並列性を読めるGantt風timeline、node/total token、bottleneck、入出力size、実入力・実出力・log・error・artifactの選択式詳細を追加。LLMの`tokens`を`total_tokens`へ正規化し、path/log/artifactを有限長保存。NodeRun出力もSecret値で再redactし、認証tokenと`total_tokens`/`max_tokens`等の数値metadataを区別する共通規則へ修正。backend全319件、frontend本番build、実サービス再起動、320/390/768/1280px Playwright 2件でtimeline・実入力・実出力・横overflowを確認
- 2026-07-19: ワークフロー実行UXを「実行前チェック」「下書きをテスト」「検証して実行」へ整理。前2者は同じ静的preview・公開可否を必ず表示し、draftテストだけがexecutorと副作用を実行する。editor主操作は保存→公開blocking検証→差分時のみversion公開→その固定version実行を1操作化し、変更なしではversionを増やさない。明示的な配備のみは「実行せず公開」へ移動し、Runner等の本番入口は公開版限定を維持。実行入力へaccessibility名も追加。backend全319件、frontend本番build、実サービス再起動、320/390/768/1280pxと公開・draft実行を含むPlaywright 2件成功を確認
- 2026-07-19: Project Lab Web live previewを実装。Web profileの`{host}`/`{port}`自動割当、systemd process treeのLISTEN所有確認、run ID限定同一origin proxy、Cookie/Authorization/CSRF/Set-Cookie遮断、16MiB request上限、sandbox iframe、redirect/絶対path resource、起動待ち・停止を追加。`project_runs.web_port`軽量migrationを追加。backend全318件、frontend本番build、実サービスでpython http.server起動→proxy HTML/CSS→停止後409、320/390/1280px Playwright E2E成功を確認
- 2026-07-19: Project Lab durable runを実装。`ProjectRun`/`ProjectRunArtifact`、CLI/test profileの許可SDK・path検証、配列argvの`systemd-run --user`隔離実行、timeout/resource/concurrency制限、DB状態復元、cancel、1MiB上限・秘密伏せ字log、生成/変更artifact差分とchecksum、監査、operator権限、実行履歴UIを追加。Secret参照は安全なcredential分離まで明示拒否。ランナーをグローバル「操作」シートへ追加し、run権限だけの利用者を編集画面へ送らないよう権限も分離。backend全315件、frontend本番build、実サービスsystemd run→SUCCEEDED/exit 0/artifact/log、Runner 320/390/768/1280px E2E成功
- 2026-07-19: Workflow guided configurationを実装。node metadata v3へ安全な`initial_config`、全設定fieldの推奨値/理由、主要入出力、型付き出力、最短手順、構成例を追加。新規ノードの初期設定、空欄だけへの推奨値適用、接続時の主要入力補完、検索・直前/その他上流・型・直近サンプル付き変数picker、カーソル位置挿入、設定内helpを追加。外部URL/path/model/Secretは自動推測しない。backend全308件、frontend本番build、実サービス再起動、390/320px E2E、横overflow/console errorなしを確認
- 2026-07-19: Application Builder Phase A完了。ApplicationProject、Application Spec v1、Workflow/Application IR、portable type system、structured diagnostics、target/framework/node capability registry、静的validate/CRUD API、ワークフローの「アプリ化」入口と基本Project画面を追加。source生成・build・artifact・自由code LLMは未実装として明示し、dummy成功UIを置かない。node metadata v3へ推奨初期値・理由・help・変数picker hintの互換fieldを追加。backend全307件、frontend本番build、実サービスmigration/health、320/390/768/1280px E2E、横overflowなしを確認
- 2026-07-19: ワークフローの安全プレビューと公開判定を共通preflightへ統一。409の構造化blocking理由を画面表示し、最終出力不足には`output.render`追加を案内する。全サンプルをコピー直後に安全プレビュー・公開前検証・公開できる回帰テストを追加し、既存の監視／復旧／Gitサンプルへ型付き出力を補完。外部サービス不要でfilter・sort・aggregate・並列Table/JSON/Metric出力を扱う「受注データ分析」複合サンプルを追加
- 2026-07-19: AIアシスタントの空の生成状態行を条件描画化。さらにstandalone PWAでだけ有効になるSafe Area paddingとアプリshell下部navigation予約を除去し、入力カードをdialog下端へ密着。実サービスを再起動し、standalone条件の320×700／390×844 screenshot、1280×800、横overflowなしを確認
- 2026-07-17: モバイル下部ナビのリモートデスクトップと操作シートのAIアシスタントを交換
- 2026-07-16: LLM runtimeのcomplete/stream/cancel契約を統合し、永続chatとworkflow生成の重複処理を置換
- 2026-07-16: OpenCodeを既定無効featureとして条件登録し、実機llama.cpp分析、cancel、PC/320pxを検証
- 2026-07-16: ワークフロー並列mapを分離context化し、検索/お気に入りとdata/glob/AI統合nodeを完成
- 2026-07-15: llama.cppを複数GGUF catalog/個別systemd unit化し、共通health/load上限とモデル別idle/自動起動を追加
- 2026-07-15: AIアシスタントを独立route化し、表示名・会話切替/改名/削除と実機ワークフロー生成登録を確認
- 2026-07-15: Model画面をruntime横断化し、llama.cppのK/V・MTP・MoE等の型付きモデル個別設定とAMD custom MCLKを追加
- 2026-07-15: ターミナルをmobile keyboardのvisual viewportへ追従。tmux/xterm 100,000行履歴、再接続snapshot、PC全文コピーを追加
- 2026-07-15: capability付きprovider adapterと共通モデル一覧・ロード・アンロード・削除APIを追加
- 2026-07-15: ClaudeのLLM設定タブ統合を流用し、providerカタログと共通エンドポイント検出APIを追加
- 2026-07-15: アプリ別ヘルスチェック（プロセス/TCP/HTTP status・本文/許可ルート内ファイル）、DEGRADED表示、手動確認UIを追加
- 2026-07-15: 電源予約をWeb内タイマーから永続systemdユーザーtimerへ移行し、予約・取消UIと実行監査を追加
- 2026-07-15: 完了表記を受け入れ条件で再監査。アプリアイコン、ごみ箱、再開可能チャンクアップロードを実装し、古い残件一覧を現状へ更新
- 2026-07-12: リポジトリ初期化。要求仕様原本と初期文書 8 点を記録
- 2026-07-12: PR #1 バックエンド（認証 / RBAC / 監査 / アプリ管理 / systemd / 監視 / 電源 / スクリプト）
- 2026-07-12: PR #2 フロントエンド（レイアウト / ダッシュボード / アプリ / ログ / システム / 設定）+ amd-smi パーサー修正
- 2026-07-13: リモートデスクトップ描画の根本修正（WS トンネルの Guacamole 命令境界保存）+ タッチ操作をタッチパッド方式に刷新（長押しドラッグ / 2本指右クリック / 3本指キーボード）+ タッチ端末は2倍解像度で接続し縮小表示
- 2026-07-13: ターミナル永続化の根本修正（tmux を systemd-run --user --scope で独立 cgroup 起動。サービス再起動で tmux ごと kill されていた）+ WS 自動再接続 + モバイル向けコピー/貼り付けシート
- 2026-07-14: 最新RAG/Deep Search強化: 外部検索ノードを4ソース統合(arXiv/Crossref/PatentsView特許[要無料キー]/SEC EDGAR市場調査)、Web検索ノード新設(DuckDuckGo/SearXNG・URL復元)、RAG検索にHyDE+マルチクエリ(RAG-Fusion)追加、Deep Researchノード(サブ質問分解→多ソース反復探索→引用付きレポート)。Deep Searchはノード組合せ(Web検索→スクレイピング→RAG構築→rag.query(HyDE)→LLM統合)でも構築可能
- 2026-07-14: Model(Ollama)管理タブ追加（一覧/取得[Ollamaレジストリ+HuggingFace GGUF検索]/削除/ロード/アンロード/詳細/keep_alive・アイドル自動アンロード[expires_at変化で活動検知]・呼び出し時オートロード・既定モデル設定・pull進捗WS）+ GraphRAG（LLMでトリプル抽出しグラフ化、graph検索モード、Knowledgeにグラフタブ）
- 2026-07-14: Knowledge(RAG) 超強化: RAG エンジン v2（コレクション設定/文書管理/6チャンク戦略[recursive/fixed/sentence/paragraph/markdown/parent_child]/SQLite FTS5 trigramで日本語全文/ベクトル・全文・ハイブリッド(RRF)検索/親子チャンク）+ Knowledge タブと管理ページ（コレクションCRUD・文書取り込み[テキスト/URL/ファイル]・検索テスト・設定）+ ノード統合強化（rag.build/rag.query に戦略・検索方式を選択追加、学術検索ノード[arXiv/Crossref]追加）。ノードは乱立させず統合方針
- 2026-07-13: Web スクレイピング強化: 抽出ビューワ（サニタイズ HTML をサンドボックス iframe に描画→要素クリックで CSS セレクタ自動生成、候補セレクタ一覧、抽出ワード↔結果の対比プレビュー）、複数抽出項目（各項目が出力変数、属性 text/html/href/src 等・複数取得選択可）、単一 selector との後方互換 + 下部ナビを fixed からフロー内配置に変更（iOS Safari 下部ツールバーによる浮き上がりバグ修正）
- 2026-07-13: ワークフロー v3（Dify/n8n 流）: トリガーに型付き入力フィールド定義（テキスト/長文/数値/選択/ファイル、実行時入力ダイアログ）、全後段から参照できる変数ピッカー（ノード出力メタデータ + 名前付き変数 {{vars.*}}）、LLM の稼働サーバー検出 + 構造化出力（json_object / json_schema + プリセット、非対応サーバーへはプロンプト埋め込みフォールバック）、全ノードに出力変数名設定、新ノード util.now / http.download
- 2026-07-13: GitHub 管理（リポジトリ登録でクローン/更新/保存/リバート/削除をボタン操作、~/ControlDeckApps へ格納、gh auth login のターミナル連携）+ 下部ナビ再編 + Overlay フォーカス奪取バグ修正
- 2026-07-13: アプリに Web ボタン（プロセスツリーの LISTEN ポートを検出しブラウザで開く。複数ポートは初回選択→ web_port として保存、設定編集で検出ポートから変更可）
- 2026-07-13: アプリ機能の使い勝手改善: テスト実行のストリーミング化（WS /apps/test-run/stream、常駐アプリ対応・停止ボタン）、実行 cwd を既定ホームに（test-run とユニットの WorkingDirectory）、パス入力にサーバー側ファイル選択ダイアログ（FilePicker）、リモートビューアのタッチ判定を pointer:coarse に精緻化

## 推奨スキル（設定画面から導入・更新・無効化・削除）

OpenCode に読ませる制作手順を、ControlDeck のデータ配下だけで管理する。読ませ方は
OpenCode の `skills.paths`（runtime config へ有効なものだけ並べる）。利用者の
`~/.claude/skills` や `~/.config/opencode` へは書かない。

同梱: `controldeck-image` / `controldeck-sound-effects` / `controldeck-music` /
`controldeck-3d-scene`。実行先は既存の MCP ツール（media.* / sonic.*）で、
引数は実物のスキーマと突き合わせてある。

外部: `blender-skills`（commit 固定）。BlenderMCP 前提なので実行部分は噛み合わない
ことを `requires` に書いて画面へ出す。

### 2026-11-10 PatentBuddy option registration (in progress)

User requested PatentBuddy in the same optional-feature install/update/enable/disable lifecycle as the other Add-ons. Added a release-bundle trusted catalog entry, scoped to `souten-yd/PatentBuddy` and `control-deck-patent-buddy-*` assets. Uses the existing souten-yd publisher key already trusted for SonicForge; no new private key is generated or copied. The entry is preview until the signed package and installed UI acceptance pass. ResearchDataHub is a separate data server, not the Add-on's local database. Host runtime implementation and existing user changes are untouched. Release-bundle tests and live install/update acceptance are pending.

PatentBuddy registration verification: `test_release_bundle_feature.py` 20 passed. The v0.5.5 bundle's canonical signature verifies with this catalog's existing publisher public key, and `_load_package` accepts its exact feature/version/platform/entrypoint/Add-on capabilities. PatentBuddy PR #14 merged as `9a26769`; release run42819243693 is publishing v0.5.5. Installed acceptance follows publication; this entry remains preview and does not claim AI/Jobs/GraphRAG complete.

### 2026-11-10 Add-on lease recovery inspection (in progress)

PatentBuddy PDF imports need to distinguish an already-released or expired lease from an active lease after a lost release response or worker restart. Add a read-only scoped GET for a lease using the existing cleanup authority and owner/Job checks. No authority expansion, GPU execution, or frontend changes. Authorization regression tests and live acceptance are pending.

Lease inspection implementation: GET `/api/v1/addon-runtime/{addon_id}/resources/leases/{lease_id}` uses existing CleanupResourceAuth and verifies the Add-on and Job owner. It preserves expiry and state and performs no mutation/audit write. Shared lease ownership DB lookup now runs through `asyncio.to_thread`. Related 35 tests pass (4.69s), including wrong user/Job/header, missing lease, expired lease and disabled cleanup authority. Full suite and live rollout are in progress.

Lease inspection final validation: initial full run had 1225 passes and one unrelated Project Lab 404/405 assertion failure because this fresh worktree lacked frontend/dist (SPA route not registered). Built frontend successfully (21.32s); the exact failed test then passed unchanged. Full rerun: 1226 passed, 2 skipped, existing Starlette warning, 112.41s. Logs `/tmp/pb-host-lease-{full,build,environment-test,full2}.log`. Tests were not relaxed. Live rollout and PatentBuddy recovery acceptance follow merge.


## Add-on local retrieval AI（2026-11-11・実機受入前）

- PR351: ai.inference認可の /ai/retrieval/capabilities と /execute を追加。Host登録済みembedding/reranker GGUFだけを選択し、loopback/役割/原本fileを確認して既存gateway Broker lease→ensure_ready→推論→releaseを使う。cloud fallbackなし。
- 入力32件/各8192文字/計64KiB、応答4MiB/8192次元。finite値・行数/indexの完全被覆を検証。proxy/redirect拒否、設定差替えを前後で検出、切断時に推論taskをcancelしleaseを解放。
- model_refはモデルfile path/size/mtime_ns/inodeと実行設定のopaque SHA256指紋。alias/path/URL/資格情報は出さない。内容hashの証明ではないためrevision_kind=host_file_metadata_v1と明示する。
- 関連46 tests成功3.04秒、frontend build19.08秒。初回全体1failed/1231passedはfrontend未ビルド時に起動したSPA routerの既存404/405検証。build完了後、全1232 tests成功105.70秒/2skip/既存warning。
- 初回関連試験はdisable拒否409を401/403と誤期待し、async test plugin不存在で4件失敗。既存409契約とasyncio.runへ修正（依存追加なし）。pytest既定tmp cleanup中のwatchdog stackは試験プロセスのcleanup。以降専用basetempで検証。
- 実Hostにはまだ未反映。実登録モデルはLLM Qwen3.8-27Bのみだったため、通常role-presets APIの導入jobでBGE-M3とQwen3-Reranker-4Bを準備中。導入中のHost再起動はしない。実API/PatentBuddy結合・モデル品質/計時は未検証。

## Add-on generation provenance（2026-11-11・実装中）

PatentBuddyの生成チャートに使用モデル版を残すため、local-only生成の任意provenance契約を追加する。通常の既存AI呼出は互換維持。Hostが選択したGGUF/実行バイナリのfile metadata指紋とsystemd管理processの同一性を照合し、推論前後で変化した場合は成功の証明を返さない。alias/path/URL/資格情報は返さない。内容hashではなくfile metadata由来であることを明示する。呼出側の任意モデル/URL指定やcloud fallbackを追加しない。

まず既存target選択/Host lease/ensure_readyを調査し、設定だけでなく実processのargv/起動時刻も照合するhelperを実装する。次にopt-in complete/stream契約・テスト、main統合/限定実機反映、PatentBuddyで検証済みprovenance保存へ接続する。現段階は計画であり未実装・未配布。ライブHostのユーザー未コミット変更に触れない。

Generation provenance実装（PR352候補）: `require_provenance: true`をlocal_only生成の任意オプションとして追加。completeはprovenance object、streamは正常終了直前のprovenance eventを返す。既定falseの既存応答は維持。モデル選択時のfile metadataと、admission/ensure_ready後のsystemd MainPID・同一UID・実exe/argv・LISTEN port・file更新時刻を照合し、生成後も同じmodel_ref/runtime_refの場合だけ成功を返す。process開始後にモデル/binaryを書換えた場合は既にGPUへ載った重みを確定できないため拒否。補助mmproj/draftも識別対象。alias/path/PID/argv/資格情報は返さず、`revision_kind=host_process_file_metadata_v1`とする。内容hashや完全な数値再現性の証明ではない。

関連61 tests成功3.29秒（strict opt-in、前/後差替え、取消時のlease解放・provenance非発行、既存API互換を含む）。frontend build17.98秒成功。実Host既存local-only streamで合成59文字を生成し、候補helperを前後に呼び同一model/processを確認。一時addon/user/sessionを除去、Host Job/auditを保持（/tmp/pb-generation-provenance-live.log）。これはhelperの実機検証であり、新APIオプションの実導入試験ではない。候補codeはライブHostへ未反映。

PR352最終ローカル検証: 全1253 tests成功・2 skip・既存Starlette warning、107.00秒（/tmp/pb-provenance-host-full.log）。その後、補助draft GGUFの識別をprojector試験と別parameterへ追加し、helper12 testsを再検証。コード変更はなくテスト被覆追加のみ。ライブAPI反映とPatentBuddy保存接続は次工程。

### Generation provenance実Host受入完了（2026-11-11）

PR352をmain `2c951d7`へ統合。実行中Host jobs=0、ライブai.pyが変更前mainとbyte一致、新helper不存在を確認して、マージ済みのai.py/generation_provenance.pyだけ反映。既存dirty/untracked 21ファイルをhash保存し、通常の`./deck.sh`再起動後も全件一致。ユーザーのpasskey等の作業は保持。health200。

実Hostの正規ai.inference/Jobs/CPU leaseを持つ一時Add-onから`require_provenance:true`を要求し、complete（61文字）とstream（provenance event→done）の双方で、実Qwen3.8ローカル生成と候補helperのmodel_ref/runtime_ref一致を確認。`/tmp/pb-generation-provenance-api-live.log`、err空。一時addon/user/session除去済み、Host Job/audit保持。新APIの実導入は成功し、PatentBuddyの生成版記録は呼出側実装が次工程。

PR351の「実機受入前」は当時の履歴。現在embedding/reranker APIも実導入済みで、BGE-M3/Qwen3-RerankerとPatentBuddy候補の索引/チャート生成を検証済み。詳細はPatentBuddy docs/HANDOFF.mdのPR23検証記録を参照。生成provenanceはmetadata/process連続性の識別であり、重み内容hashや数値再現性の保証へ拡張解釈しない。


### 2026-11-11 Add-on opaque iframeのCSP接続先

PatentBuddyのHTTP LAN起動を実WebKitで調べ、connect-src 'self'がopaque sandboxで拒否されることを再現。proxyは上流CSPを維持し、connect-src（またはdefault-src fallback）にselfがある場合だけブラウザ側の正確なHost originを追加する。noneやhashは維持、外部origin/wildcard/unsafe-inlineを追加しない。CSPヘッダーの大小文字差による意図しない二重生成も整理。関連proxy17 tests成功、実画面と全体検証は進行中。ユーザーのパスキー作業を含むlive checkoutの他ファイルは変更しない。

- 最終frontend build成功、backend全1255 tests成功/2skip105.69秒（/tmp/pb-host-csp-full-cwd.log）。初回repo rootからpytestを直接実行したためCLIのapp import2件だけ失敗、規定backend cwdで全再実行成功。live proxyのみ反映/deck.sh再起動後、再現条件のWebKit HTTPでstatus/原文成功。HTTPS WebKit/Chromeと実Host local LLM要約も成功。既存23files hash不変（/tmp/pb-csp-preserved.json）。



## 2026-11-12 — Strata導入 / Lucebox撤去 / 本体更新機能（開発ブランチ）

- GPU推論のオプションにStrata Terminalを追加。明示的な導入・更新・削除をControlDeck管理領域へ限定する。デフォルトランタイムは変更せず、模型ライブラリも削除しない。

- 利用不可のLuceboxをモデル登録・共通設定・Provider・Gateway・UI/APIから撤去。旧設定が選択していた場合はOllamaへ移す。

- 本体のGitHub mainへのfast-forward更新確認と管理者の明示操作による独立systemd更新ジョブを追加。変更済み作業ツリーと未対応配置では更新不可。

- 受け入れ条件：backend pytest、frontend build、旧Lucebox参照grep、Strata導入/削除テスト、本体更新失敗時のロールバック、モバイル画面確認。マージとリリースは検証結果が得られるまで未完了。


## 2026-11-12 — Project Maya-S option (PR #359, Draft)

- `feat/maya-s24-optional-addon-20261112` (historical branch name) adds an independent `maya-s` opt-in feature stacked on PR #355. Official Project Maya GitHub Release code only on install; explicit separate GLM-5.3-Flash Maya-S model download (~96.5GB) with confirmation, explicit start/stop and loopback-only API.
- Uninstall keeps model weights, defaults and other runtimes unchanged. `backend/tests/test_maya_s_addon.py` adds mocked offline checks. See `docs/design-maya-s-optional-addon.md`.
- **Not merged/released**. Full tests, R9700/ROCm10 and mobile verification remain pending; CI stays manual-only as in the main development rules. Existing PR #355 is an upstream dependency and must merge first.

- Draft PR #359 now also wires the explicitly prepared Maya-S model into the existing ControlDeck provider/model list and authenticated shared gateway. Dormant Maya is not an AUTO candidate and cannot start by chat request. Added offline tests for this contract; actual runtime tests remain pending.

- PR #359 model choice updated to **GLM-5.3-Flash Maya-S** (`glm-maya-s`, ~96.5GB) for the supported R9700. No S24 fallback or migration. Source branch name remains historical; actual runtime, APIs, frontend, docs and tests use `maya-s`. `maya_s.py` rejects old S24 config/marker and the health check requires the Maya-S label. Hardware checks and full CI remain pending.
## 2026-11-12 PR #355 ローカルStrata受け入れ（進行中）

- 管理Terminal 0.2.13.1 / HIP engine 0.1.59をR9700へ導入。既存Flash-Next IQ2_XSの2分割GGUFを`/data1tb/LLM`から使用し、元ファイルを移動・削除せず推論用packを管理領域へ作成。
- CLI exit 0とAPI Readyを区別し、status・health loaded・modelsで初回activationを確認。エンジンはsystemd user unit内で実行する。
- 既存GGUFの許可パス・分割検証、停止時のモデル/コンテキスト/KV/画像/並列数/投機デコード設定変更を追加。OpenCodeへのcontext上限伝達、Add-on AIのStrata解決・共通admission・local_only/provenance経路を補完。
- 8K/16K/32K/64Kを実起動し、入力約1K/5.8K、出力256 tokensを各2回測定。32Kの5.8K入力でprefill 2209.8–2317.5 tok/s、decode 129.7–130.7 tok/s、TTFT 2.53–2.65秒。64Kで50,911入力tokensの応答も成功（prefill 2600.1、decode 108.0 tok/s、TTFT 19.65秒）。測定は単一リクエスト、thinking off、MTPであり一般品質・並列性能の保証ではない。
- ローカル稼働Hostは最新main bf53da1と既存terminal修正を統合した別checkoutへ更新済み。更新候補backend 1255 passed/2 skipped、frontend build成功。localhostでPC/320pxのpassword/passkey 2件成功。旧checkout・DB・設定・unitのバックアップ保持。PR本体は未マージ、Strata統合UI・OpenCode・Add-on実経路の最終検証を継続。
- 本体更新の配布元をmainから正式GitHub Releaseのタグ・検証済みSHAへ修正し、適用前backupと再起動後health確認を追加。
- 証跡は`/data1tb/ControlDeck/strata-pr355-evidence/`。ベンチマークraw JSONと各段階のログを保持。

## 2026-11-12 PR #359 build-only acceptance (in progress)

- User scope: install/build Maya-S, integrate add-in lifecycle/settings/runtime selection; **do not download model weights or load/run the Maya model**. Existing separately downloaded GGUF can be selected later.
- Official Maya v1.1.61 code installed. HIP build-only action is independent of model setup; one compiler job limits RAM use. Settings save does not run setup or inference. Existing-model preparation never passes `--download-model` and disables speed calibration.
- Added context/GPU/parallel/RAM headroom/drafting settings, selected-runtime policy and Add-on text capability wiring. Unready Maya is rejected, not started by a generation request. OpenCode receives the configured context through local_llm.
- Local backend 1281 passed / 2 skipped before final resource-accounting test; frontend build passed. Final build/UI/CI acceptance pending. Maya inference/performance/quality explicitly NOT TESTED by user instruction.
- PR #355 merged to main as d01dddca after final manual CI 43236566272 passed.
- 2026-11-12 (UI split): Add-on panel now holds only engine state and the HIP build action. Model download (~96.5GB, GPU index + confirmation), "load an existing GGUF", per-model settings (GGUF path / context / GPU / parallel / RAM headroom / MTP drafting) and load/unload moved to **Models**. The download/existing buttons appear only while Maya-S is installed but not prepared; once prepared, the Maya-S row opens a per-model settings sheet. Added read-only `GET /features/maya-s/model`; prepare/start/existing jobs use `model.maya-s.*` kinds so they appear in Models' active job list. Focused + full backend (1286 passed / 2 skipped) and frontend build pass. Real model load/inference still NOT TESTED (waiting for the user's RAM upgrade).

## AMD GPU Smart Fan Control（2026-11-12着手、2026-11-14 PR #360マージ・`ControlDeck_v0.12`）

Settings > ファンコントロールにamdgpuのファンカーブを使う多温度センサー制御を実装中。GUIには温度・最低ファン・減速保持/間隔/幅・Zero RPM猶予・FF/PIゲイン・学習切替のスライダー、GPU OD_RANGEベースのカーブプレビュー、推奨/静音/冷却優先プリセットを追加。設定の保存/読み取りは既存FanSettingsに統合し、安全上限をバックエンド側で検証。当初の未検証項目は下記の2026-11-13〜14の記録で解消した。設計と検証手順は docs/design-amd-smart-fan.md を参照。

### 2026-11-13 実機検証・PID制御モード

- R9700（SMU 14）実機で、OverDrive 有効化・hw helper 導入・SMU 14 のリセット禁止に対応した。マップ制御を調整し、プリセット `llm_r9700` / `llm_quiet` を追加した。
- PID制御モード（`control_mode: "pid"`）を追加した。
  - 熱モデルのフィードフォワード（D）、アンチワインドアップ付き PI、アイドル温度の毎回測定、高回転から下げていく自動調整（SIMC で Kp・Ti を設定）、安定状態からの熱モデル更新。
  - 温度・ファン履歴を常に記録し、画面が表示されているときだけ描画する。
- 実機の連続LLM負荷で、hotspot を平均90.0°C（目標90）・約2330rpm一定に保つことを確認した。短い負荷では回転が変わらない。降速時の段差（PMFW の跳ね返り）も解消した。
- テスト: バックエンド全体 1319件成功・2件スキップ、frontend build 成功。
- ~~NOT TESTED: ブラウザでの画面確認~~ → 2026-11-14 に利用者が自分のブラウザで確認（下記）。

### 2026-11-14 グラフ／PIDの2モードとリリース

- インテリジェント制御を2モードにした。グラフ（マップ）制御 `control_mode: "curve"`（熱モデルからカーブを自動設計し、上げはファームウェアのカーブで即応、下げはソフトで保持→ゆっくり解放）と、PID制御（バックカリキュレーション型アンチワインドアップ Tt=Ti/2、冷え側偏差の上限−4°C）。
- R9700実測のプリセット `curve_r9700` / `pid_r9700` を追加（hotspot 90 / VRAM 90 / edge 85°C）。実機の短い負荷3回＋3分連続で、グラフ制御は最高 88/78/63°C・連続時約2470rpm、PID は 90/80/65°C・約2360rpm。どちらも目標超過なし。
- ブラウザでの画面（制御方式3種、プリセット、状態欄、温度グラフ）は利用者が確認。モバイル幅・物理スマートフォンはNOT TESTED。
- main（#362まで）を取り込んだ候補で backend **1327 passed / 2 skipped**、`tsc --noEmit` と frontend production build 成功（Ubuntu・R9700 の自前PC）。
- 配布は従来どおり `ControlDeck_v0.12` のソースアーカイブ。タグは PR #360 のマージコミット。

### 2026-11-14 PR #359 R9700 実機受け入れ（Maya-S 実推論）

- 利用者がダウンロード済みの `Maya-S-v2-IQ2_XXS`（`/data1tb/LLM/GLM-5.3-Flash-Maya/`、約90GB・3分割）を Models 画面の「既存GGUFで準備」から指定。上流 setup が `build-hip/maya` をビルドし、pack（約0.9GB）を GGUF フォルダ内に作成。モデルの再ダウンロードなし。
- 起動から ready まで 51〜54 秒。VRAM 約29GB / RAM 約70GB 使用、エキスパート 86GB をRAM/VRAMの2段で保持。
- ループバック直接・認証付き共通ゲートウェイ（`maya-s`）とも応答成功。ゲートウェイは認証なしで 401、ストリームも成功。
- 単一リクエスト・thinking low・context 8192 で decode 約24 tok/s（MTP off）、MTP on（`MAYA_GLM_MTP_DECODE=1`、採択率85〜95%）で 24.6〜26.9 tok/s。上流は離散GPUで one-GPU drafting を既定にしないため、ControlDeck の「drafting: on」は停止中の設定保存で初めて反映される。
- 稼働中の uninstall / 設定保存 / 更新 / 再準備 / ビルドはすべて拒否。停止→設定変更→再起動を確認。不正 alias の起動は拒否。
- 修正: 上流 setup がビルドした `build-hip/maya` を「HIP未ビルド」と表示していた。`runtime_status().built` を `build-hip` で判定し、ビルドのみ操作も同じディレクトリへ出力するよう変更。
- main を取り込んだ PR head で backend **1358 passed / 2 skipped**、frontend production build 成功。
- NOT TESTED: ブラウザでの画面確認（PC・モバイル）、上流の新Releaseへの更新（現在 v1.1.61 が最新）、応答品質の評価。

### 2026-11-14 Maya-S / Strata の起動・停止設定（llama.cpp と同等）

- 共通のアイドル自動アンロード（Models のランタイム設定「アイドル時に自動アンロード」・分数）を Maya-S と Strata にも適用（`large_model_lifecycle.idle_unload_loop`）。モデル個別に「共通アイドル停止から除外」「ControlDeck起動時に自動起動」。Maya-S は「要求時に自動起動」（既定オン。準備済みモデルのみ起動し、ダウンロードはしない）。Strata は従来どおり一度ロードしたモデルを要求時に起動。
- 2つは同時に載らないため、要求時起動の前にもう一方を停止（Maya-S は使っていない llama.cpp チャットモデルも解放）。ゲートウェイは GPU 予約の前に解放する（broker は llama.cpp しか解放できず、Strata 要求が Maya-S の後ろで 300 秒待って失敗していた）。推論中は止めず、常駐指定のモデルも止めない。
- 利用時刻は ready 確認ごとに記録し、ポートへ直接つないでいる外部クライアントがいれば停止しない。起動・停止の設定は稼働中でも変更できる。
- コンテキスト: Maya-S はプリセット（8K〜1M）またはカスタム。Strata は 4k〜1024k の任意の `<N>k`（256K 超は Strata が yarn を付ける）。
- 修正: llama.cpp の空エンドポイント（ep-8096）が Strata のポートと重なり、停止中の Strata へのチャットが llama 経由になって起動されず失敗していた。登録済み Strata/Maya-S のポートを優先。Strata API が `unsloth` 系列を 422 で拒否していたのも修正。
- R9700 実機: アイドル停止（常駐指定は維持、30分超で停止）、ゲートウェイとチャット経路の両方で Maya-S → Strata → Maya-S の要求時切替（45〜66秒）を確認。backend 1384 passed / 2 skipped、frontend build 成功。
- NOT TESTED: ブラウザでの設定画面、ControlDeck 再起動時の自動起動（設定がオフのため）。

### 2026-11-14 Settings / Models の UI 見直し

- **Settings**: 1枚の縦長ページ（PC 9,880px）を7カテゴリ（アカウント・表示・機能と拡張・ハードウェア・通知・ユーザーと権限・システム）に分け、`/settings/:section` で直接開けるようにした。PC は左にカテゴリ＋検索、モバイルはカテゴリ一覧→各ページ。検索は見出しと項目名（例: Discord・パスキー）で絞り込み、Enter で最初の候補へ。旧 `?extension=` は「機能と拡張」の拡張機能タブへ。
  - ログイン中の端末: この端末を先頭に最新順、5件以降は折りたたみ、ブラウザ名と OS の短い表記、「この端末以外をすべて失効」。
  - 監査ログ: アドオンの自動処理（`addon:*`）を既定で隠し、件数を表示。古い記録は段階的に読み込み（最大500）。
  - オプション機能: 利用中 / 導入済み（無効）/ 導入できる機能 に分け、各行はその時に必要な操作1つ（導入・有効化・更新あり時の更新）だけを出す。無効化・更新・アンインストール・パスや詳細は「詳細と操作」に畳む。
- **Models**: 一覧の行名をゲートウェイで使うモデル名（alias）に統一し、ランタイム・ファイル/量子化・サイズ・ポートを補足行へ。サイズ不明の「0 MB」と「選択中: Maya-S / .」を解消。
  - モデル個別設定を llama.cpp・Strata・Maya-S で同じ構成にした: 状態の要約 → モデル → 基本（CTX・出力）→ 起動と停止 → 思考 → 性能（畳む）→ 詳細（畳む）、保存ボタンはシート下端に固定。Strata は「要約＋設定を変更」の2段をやめ、開いてすぐ編集できる（稼働中は起動と停止だけ）。llama.cpp の「PC起動時に自動起動」「共通アイドル停止から除外」を上級設定から「起動と停止」へ移した。
  - LLM 共通設定: 使うランタイム → 起動と停止（アイドル自動アンロード・方式・同時ロード数）→ AIアシスタント → Deep Research・GPU電力・モデルの保存場所（畳む）。
- 検証: tsc・production build 成功。API ログインした Playwright で 320px / 1280px の全カテゴリ・検索・旧リンク・Models の3シートと共通設定を確認（76項目、横スクロール・ページエラーなし）。既存 e2e は login ヘルパーが「ログイン」ボタン2つで strict mode 違反になり、変更前から実行できない。
- NOT TESTED: 利用者のブラウザでの見た目と操作感、パスワード変更・ユーザー作成など状態を変える e2e の再実行。

### 2026-11-14 Maya-S のコーディング向け設定（R9700・RAM 92GB 実測）

- 採用: context 1M、RAM の空き確保量 8GB、`MAYA_GLM_PCIE_SHARE=0.3`、MTP 有効、出力上限 65,536（OpenCode へ通知）、思考の上限 32,768。
- 実測: decode 25.1 tok/s（短い入力）、23.2 tok/s @29K、21.6 tok/s @103K。prefill 約620〜650 tok/s。8K→1M で速度差はほぼ無い（KV は RAM）。
- 効かない: `--calibrate`（HIP 非対応）、PCIe 0.2/0.7、CPU レーン 6/12、prefetch。PREFILL_MB 8192 は headroom 8 と組み合わせると起動しない。
- 注意: `MAYA_GLM_PCIE_SHARE` は Maya の config に直接書いており、再セットアップで消える。

### 2026-11-14 ファン・OpenCode 出力上限・開発ワークフロー・Strata UD-IQ4_XS・UI 統一
- ファン（PR #363 に追加）: hotspot が目標の 3°C 以内では回転を下げず、8°C 以上離れたら通常の速さで下げる。Maya-S（212 W）12分連続負荷で rpm 平均 2,533・標準偏差 131。
- OpenCode: 全モデル一律の出力上限 8,192 を撤廃し、出力はコンテキストの半分（Maya 専用設定は削除）。
- 開発ワークフロー: Superpowers v7.0.2 / Adversarial Review 2.0.2 / Auto Advisor 2.1.1 を個別に切替、レビュー・相談のモデルを指定可。ゲートウェイ `POST /api/v1/llm/v1/models/{model}/load`。実機で3つとも読み込み、`/load` で Maya-S 78.7 秒。
- Strata: `qwen-flash-next` を Unsloth UD-IQ4_XS（SHA-256 3ファイル一致）・256k に切り替え。実測 prefill 2,172 tok/s @25K・2,990 tok/s @96K、decode 74 / 68 tok/s。KV は 256k で 3.1 GiB（RAM）。IQ2_XS（GGUF 70G・pack 35G）は削除。
- UI（読み取り専用の UX レビューを反映）: シートの保存ボタンを1本化、起動と停止の文言を3ランタイムで統一、共通設定でランタイム選択が編集を消す不具合と Strata フォームが読み込み完了で消える不具合を修正、OpenCode 設定を1枚のシートに、オプション機能の進捗をジョブのカードだけに表示、監査ログのスマホ表示、ファン設定の並び替え。UI スモーク 106/106（320px / 1280px）。
- NOT TESTED: 実際のコーディング作業での Adversarial Review / Auto Advisor の挙動。

### 2026-11-14 OpenCode v1 と OMo の撤去、v2 プラグインをオプション機能で管理
- 撤去: オプション機能 `opencode`（v1・opencode-ai）と v1 専用の `omo`（oh-my-openagent）。OMo の並列数同期（llama のスロット変更時・容量 API の `omo`）とダッシュボードの OMo 表示も削除。OpenCode の系列は常に v2（古い設定の `runtime: v1` は v2 に揃える）。`/opencode` は `/opencode-v2` へ転送（`?session=` を保持）。ナビの表示名は「OpenCode」。
- 追加: オプション機能に `opencode-superpowers` / `opencode-adversarial-review` / `opencode-auto-advisor`（kind `opencode-plugin`、`opencode-v2` 必須）。導入は検証済みの版（v7.0.2 / 2.0.2 / 2.1.1）、更新は最新版。管理領域へ npm で入れ、セッション設定には `file://` で渡す（OpenCode が実行時に取得しない）。OpenCode v2.3.0 が `file://` のプラグインを読み込むことを `debug agents` で確認。
- OpenCode の設定は「使う／使わない」とモデル選択だけ。未導入の拡張は選べず、オプション機能への導線を出す。

### 2026-11-14 コーディング向け: 精度を保ったまま最速の Strata / Maya-S 設定
- 計測: 実行テスト付きのコーディング課題 10 問（貪欲デコードで近似の影響だけを見る）。
- Strata（UD-IQ4_XS・128k）: 既定の近似 2 つを止める（`STRATA_ROUTE_TAIL_SKIP=0`: キャッシュに無いエキスパートを飛ばさない、`STRATA_HC_Q8=0`: 重みの追加量子化をしない）。正答は既定と同じ 10/10、速度は 89.8→84.0 tok/s（約 -6%、HC_Q8 は誤差内）。丸め順だけが変わる高速化は残す。ControlDeck の既定にし、登録ごとに `engine_settings` で上書きできる（`--set` で渡す、形を検証）。
- Maya-S: エンジンは近似なし（MTP の下書きは答えを変えない）。既定の思考の深さを max→high（10/10、全 10 問で 412 秒。max は 1 回答に約 30 分）。設定画面から変更でき、再セットアップ後も戻す。
- AI アシスタントの会話は temperature を送らず、モデル側の推奨（Qwen 思考: 1.0/0.95/20）に任せる。一律 0.4 では思考が長引き（小説で 15〜16K トークン、1 回は上限で打ち切り）、推奨値では 6〜7K。小説での同じ文言の繰り返しの原因と判断。

### 2026-11-14 PR #363 実機受け入れ（R9700・グラフ制御 `curve`）

- 稼働中の展開ブランチへ取り込み、frontend を再ビルドしてサービスを再起動。保存済み設定（時定数の項目なし）は既定の昇温2秒・降温30秒で読み込まれ、制御は継続した。
- 負荷は Maya-S（GLM-5.3-Flash）。アイドル60秒 → 48トークンの短い負荷6回（12秒間隔）→ 3分連続 → 150秒アイドル。3秒ごとに実測温度・回転数・適用カーブを記録。
  - 短い負荷: hotspot は 61〜64°C まで跳ねたが、回転数はアイドルの約889rpmのまま。カーブの平坦部は22〜25%でほぼ動かない。
  - 3分連続: hotspot 86〜88°C、約2420rpmで一定。
  - 負荷終了後: 約60秒でアイドル回転に戻った。
- テスト: 対象4ファイル 79 passed。backend 全体 **1344 passed / 2 skipped**（`-x` 付きの初回は `test_samplebook_chat` が1件落ちたが単独では通る。既知のタイミング依存）。frontend production build 成功。
- NOT TESTED: 実測温度が目標+5°Cを超えたときの最大指令（実機で安全に再現できないため単体テストのみ）、PIDモードでの実機負荷、ブラウザでの実測/EMAグラフと時定数スライダーの表示。

### 2026-11-15 Radiance ランタイムと、Strata / Radiance の思考設定
- Radiance（StillDeadcode/radiance、Apache-2.0）を llama.cpp / Strata / Maya-S と同じ扱いで追加。公式イメージ 1.17.0（digest 記録）を rootless podman で動かす（Codeberg のソースは約 7KB/s で取得不能）。モデルごとにコンテナを systemd user unit で 127.0.0.1 に起動、モデルのフォルダは読み取り専用。
- 実機（R9700 1枚）: Qwen3.8-27B IQ5R-V4（19.4 GiB、SHA-256 一致）。起動 33 秒、重み 18.8 GiB・bf16 KV 10.7 GiB（約 15.3 万トークン）、DFlash2 深さ 7。コーディング課題 10 問（モデルカードのサンプリング）10/10、decode 76〜117 tok/s。llama.cpp UD-Q4_K_M（思考 low）は 10/10・41〜48 tok/s。
- 思考の既定（要求が指定しないとき）: Radiance は `--reasoning-effort` / `--thinking-token-budget`（high・max の要求は xhigh へ読み替え。テンプレートは low/medium/xhigh のみ）。Strata は `POST /settings`（Strata が保存、稼働中でも即反映。launch --set だと予算がエンジン引数に入り起動不能）。Maya-S（深さ・上限）と llama.cpp は既存。
- 実機確認: Radiance medium で同じ課題の思考 280 トークン、high 要求が 400 にならない。Strata medium/8192 を起動時に適用、稼働中の解除も反映。
- 注意: xhigh の思考を小さい上限で打ち切ると、回答が max_tokens まで続くことがある。

### 2026-11-15 モデル表記の統一・全エンジンの前処理/生成表示・Radiance 2.0.0
- 一覧: タイトルはモデル名（Qwen3.8-27B / Qwen3.8-Flash-Next / GLM-5.3-Flash）、下に「ランタイム · 量子化 · サイズ」。ゲートウェイの名前はツールチップと個別設定のタイトル。全行に同じ「…」メニュー。VISION は Strata（画像入力オン）・Radiance・設定済みの Maya-S に表示。
- LLM 利用状況: Strata / Maya-S / Radiance も前処理・生成を表示。エンジンが timings を返せばその値、無ければ ControlDeck が中継しながら測る（最初のトークンまで＝前処理、以降＝生成）。止まっている間は前回値。
- Vulkan: 未導入なら選択肢に出さない（ROCm に統一）。
- Radiance 2.0.0: 設定は YAML（`--config`）。登録ごとに書き出して読み取り専用でマウント、`--check` で検証。起動 15 秒（1.17.0 は 33 秒）。コーディング課題 10 問 10/10・計 100 秒・12.5K トークン（1.17.0 は 1,259 秒・97K トークン、思考が大幅に短い）。served model 名が一致しない要求はエラーになる（ControlDeck は別名を送るので影響なし）。
- 大きいモデル（Strata / Radiance）のロードは、同時ロード上限の判定より先に他の大きいモデルを解放する。

- 2026-11-15 Maya-S の画像入力を有効化: セットアップ・再セットアップ・更新で `--no-vision` を渡さない（画像エンコーダー 1.14GB を一度だけ取得・ビルド）。エンコーダーの CMake が `/opt/rocm-*/core-*` を ROCm ルートと誤認して構成に失敗するため、`ROCM_PATH=/opt/rocm` を渡す。再セットアップで消えていた実測 env（`MAYA_GLM_PCIE_SHARE`）を戻す。画像入力が設定済みなら AI ゲートウェイの `vision.analyze` も Maya-S に振る。

- 2026-11-15 Models の共存設定と会話の自動圧縮:
  - 「利用方式（排他／共存）」と「同時にロードできるモデル数」は、共存できるランタイム（llama.cpp・Ollama）を選んでいるときだけ表示する。Strata・Maya-S・Radiance は起動時に他のモデルを止めるため、選択中は説明だけを出す（runtime カードの `coexist`）。同時実行数（並列リクエスト）は 4 ランタイムとも対応しているので残す。
  - 会話の自動圧縮を共通設定にした（`RuntimePolicy.compaction`: enabled / mode auto・percent / percent / max_tokens）。発火点は `app/models_mgmt/compaction.py`: 自動なら窓 〜32K 70%・〜128K 75%・〜256K 80%・それ以上 85%、上限 262,144 トークン（1M の窓で毎ターン 90 万トークンを読み直さない）。返答の取り分は削らない。
  - OpenCode: `limit.input = 発火点 + compaction.reserved`（v1 は input - reserved、v2 は min(input, context) - buffer で発火するのを実装で確認）。`auto` の窓は選択中ランタイムのモデルで決める。圧縮を切ると `compaction.auto=false` で従来の「窓 - 返答」。
  - AI アシスタント: これまで履歴を全件送っていた。閾値を超えたら古いやり取りを同じモデルで要約し（思考オフ）、直近（閾値の 35%）は原文のまま残す。要約はその回の assistant メッセージの `meta.compaction` に保存し、次の回は要約から再開する。窓より大きい履歴（広いモデルから乗り換えた会話）は分割して積み上げ要約する（実機 Radiance: 5 つの事実を全部保持）。要約に失敗したら圧縮せずに続ける。

- 2026-11-15 同時実行数（スロット）をランタイムごとに実測して整理（ゲートウェイ経由で 1 件と同時 N 件、合計 tok/s）:
  - llama.cpp Qwen3.8-27B（4 スロット・共有KV）: 1件 66 → 4件 合計 195 tok/s。設定を残す。
  - Radiance（max-num-seqs 4）: 1件 171 → 4件 合計 480 tok/s。設定を残す。
  - Strata（--parallel 4）: 1件 102 → 4件 合計 106 tok/s。同時に受けて同時に返すが合計は伸びない。設定は残し、その旨を表示。
  - Maya-S（--parallel 2, 1M ctx）: 20 分たっても起動しない（2 本目の会話状態の確保で RAM 92GB が尽きる）。個別設定から同時実行数を外し、1 件ずつ処理と表示。
  - 試験の途中で、Strata の起動が llama.cpp のチャットモデルを止めず VRAM 不足で失敗する不具合を発見。`release_for(STRATA)` も llama.cpp のチャットモデルを解放するよう修正（Maya-S / Radiance と同じ）。
  - 既定値は変えていない（llama.cpp 1、Strata 1、Maya-S 1、Radiance 4）。

- 2026-11-15 Radiance の長いコンテキスト（R9700、Qwen3.8-27B IQ5R-V4、同時実行数 6）:
  - 2.x の KV 形式は bf16 / i8 / fp8 のみ（fp8 は GLM-5.3 の latent 専用）。1.x の fp8_v4・k6_v4 を外し i8 を追加。学習長 262,144 を超えると YaRN（`rope-scaling: yarn:<倍率>:262144`）を自動で書く。
  - `memory_saver`（状態 f16・画像エンコーダーを RAM）で KV に VRAM を回す。`ram_offload`（layer_offload・host pool 22GiB・flex kv）で重みを RAM へ逃がす。
  - 実測（i8・6 本）: 262K 全VRAM 1本 203 / 6本合計 744 tok/s。**320K 全VRAM（memory_saver）205 / 796**、KV 337,824 トークン。384K（重み 1.7GiB を RAM）50 / 272。512K（5.8GiB）32 / 171。640K 以上は静的な重みが VRAM に残せず起動しない。1M は 1 本の KV だけで 34GiB 要り不可。19 万トークン入力の読み取りはすべて正答。
  - 稼働設定: 327,680・i8・6 本・memory_saver（RAM へは逃がさない）。


### 2026-11-16 Models 共通 VRAM マージン

- Models → 共通設定 → GPU メモリに GiB 単位の VRAM マージンを追加（既定3GiB、RAM余裕とは別）。
- Strata、Maya各系列、Radiance、llama.cppのネイティブな予約設定へ次回起動時に自動変換。個別予約が大きければ維持。保存だけで推論を止めない。
- llama.cppは保存時に永続unitも同期。固定GPU層数や古いバイナリで自動調整できない場合は明示エラー。Mayaは設定ファイルの同一性・思考設定を維持し、元の予約を記録して縮小にも対応。
- 外部Ollamaのサービス環境変数変更は対象外。画面に共通値から換算した `OLLAMA_GPU_OVERHEAD` を表示。
- 検証: Backend全体1584件成功/2件skip、最終Maya重複フラグ修正後の独立集中テスト16件成功。Frontend production build成功。320px/1280pxの設定保存・再読込、関連Models UI、アクセシビリティ18件成功。Strata実CLIのplanとllama.cpp実バイナリhelpで対応引数を確認。
- 大型モデルの再ロード・推論負荷試験は未実施。詳細は `docs/models-vram-margin.md`。独立レビューは必須修正0件。サンドボックスミラーでも320px/1280pxの共通設定保存を確認。最終コードのCI: `43872798598`。PR #405。配備は正式Releaseから行う。
