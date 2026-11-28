# 引き継ぎ: ランタイムの目録・登録・設定の共通化

## 目的と利用者の決定（2026-11-28）

依頼全文は作業場所の `.nexarch/brief-runtime-unify.md`。Sly-Radiance を手本に Radiance・Sly-Radiance・Strata・Maya・llama.cpp の目録、停止・再開できる取得、ファイル登録、設定を共通部品にする。設定順はモデル、コンテキスト、同時処理、速さ、起動、エキスパート。Sly の prefix cache は常にオンとし、切る選択肢・値を受けない。

Strata の新しい目録は GSQ-RCO・Unsloth UD-*・Q8_0 のみにする。既存の登録済みモデルは壊さない。新規のファイル・フォルダ選択だけを realpath 検証の上で /data1tb と /data2tb に限定する。

## 現在の状態

- Nexarch の計画は10項目。run1 のコード・公式資料調査、run2 の追加公式調査、run3 の共通バックエンド実装が終了。
- run3 で入れたもの（詳細は `docs/PLAN-model-catalog.md`「run3 の作業一覧」）:
  - 目録の集約 `backend/app/models_mgmt/runtime_catalog.py`（Sly 6 行・Strata 44 行・Maya 1 行。大きさ未確認は null）。
  - 共通 API 6 本（catalog・files・acquire-jobs・instances・register/inspect・settings/capabilities）。既存の各ランタイム API は残した。
  - 停止/再開/取消の意味分離（`jobs.stop`・`resume_stopped`・`can_resume`、`.part` の保持と `paused` 印）。
  - HF 取得の guarded transport・redirect 検査・開始時の大きさ取得（`fetch_sizes`）。
  - Sly の prefix cache 常時オン（`save_instance` と `serve_args` の両層で false を拒否）。
  - 新規選択パスの `/data1tb`・`/data2tb` 制限（realpath・symlink 検証）。
- run3 の試験: 新規 16 件合格。既存 9 ファイル（strata_catalog、sly_radiance、hf_download、maya_register、radiance_runtime、maya_probe、strata_memory、route_map、jobs_persistence）も合格。実機の登録・起動と HF の実重み取得は未試験（偽 HF）。
- run4（2026-11-28）で run3 の差分を読み、依頼の必須条件と照合して是正した。詳細は下の「run4」の作業記録。関連 10 ファイル 133 件が合格（`test_runtime_catalog.py` は 19 件に増えた）。
- 共通UIと各パネルの実装、統合後の試験・E2E・レビューは未着手。run3・run4 は backend の実装と単体試験まで。
- 初期の explore 報告にある「Sly prefix cache の方針」「Strata の適用単位」は未決事項ではない。上記と依頼全文の決定に従う。

## 環境の注意

- 担い手の OpenCode は箱の中。ホストで見える Git の worktree 管理領域や依存のリンク先が箱では見えない場合がある。`.git` の書き換え・git init・依存の導入で回避しない。ソースを書き、試験不能の理由を報告する。リーダーがホスト側の Nexarch run_checks で試験し、Nexarch がコミット・統合する。
- リーダーが依頼書を各 worktree にコピーした。`.venv` は指定の既存環境、frontend/node_modules は指定の既存依存へのリンク。npm/pip/モデル/ブラウザの取得は禁止。
- backend試験: `env -C backend -u CONTROL_DECK_CONFIG /data1tb/ControlDeck/app-strata/.venv/bin/python -m pytest -q -p no:cacheprovider <対象>`。全件試験は禁止。
- E2E は `env -u CONTROL_DECK_CONFIG PLAYWRIGHT_BROWSERS_PATH=/data1tb/ControlDeck/data/cache/ms-playwright ./deck.sh e2e <対象spec>`。ブラウザの既存保存先を明示する。venv の .req-stamp と requirements は一致を確認済み。依存を変更したら deck.sh が取得に進まないことを再確認する。

## 事前の検証（変更後の合格ではない）

- リーダーがホストのチームworktreeで隔離Backend 8ファイル82件の合格を確認した（strata_catalog、sly_radiance、hf_download、maya_register、radiance_runtime、maya_probe、strata_memory、route_map）。
- Frontend build 成功。既存 Models E2E は12件合格・1件既存skip（llama未登録）。変更後の共通化試験はmock登録を使い、このskipに依存しない。

## 守ることと残作業

### 事前レビュー run4 の必須条件（リーダーが該当コードも確認）

- `hf.py` の取得器には Range の仕組みがあるが、呼び出し側 `download()` の finally が `.part` を消している。停止は途中を保持し、取消と区別する。Job の終端状態で再開記録を消す処理とも整合させる。再起動、所有者、回数・期間制限を偽HFで試験する。
- metadata の問い合わせと再開時も外部許可を適用する。redirect のSSRFと認証漏洩、repo/revision/files の不正値、保存先・途中ファイルのsymlinkを試験する。
- `router.py` と `sly_radiance.py` の両層で prefix false を拒否する。既存設定に false が残っていても起動argvは常にprefix onにする。
- Radiance の `.rad` と Sly の safetensors フォルダを相互に誤登録させない。新目録を実際に登録・起動可能な形式へ接続する。
- GGUF/config inspection は読取量・件数・時間を制限し、子のsymlinkも検証する。同期処理でevent loopを止めない。
- 新規選択の2ルート制限はAPIでも検証する。既存の他ルートの表示・再保存・起動は維持する。
- 設定値は実際のargv/YAMLまで往復して確認する。稼働中の設定変更の制限を維持する。
- 目録のサイズ・shaは取得開始時にAPIで確認し、推測値を入れない。新目録の形式制限で旧登録IDを壊さない。

本番 checkout/data/モデルを変更・停止しない。外部からファイルを取得しない。公開資料を読む調査のみ許可。秘密値を読まない・出さない。push・PR・main マージは禁止。AIアシスタントの画面は変更禁止。

実装後にこの文書、PLAN-model-catalog.md の公式URL付き設定調査表・進捗、implementation-status.md を更新する。実装の差分確認、チームブランチへの統合、対象Backend・Models E2E・dev-check、読み取り専用の最終レビューが残る。

開発機能への要望: 実行中の live snapshot が wait/read_result に反映されない。OpenCode の既定アカウントは Zen/Go のモデルでもローカルとして並列数を制限される。worktree の依頼書・Git 管理領域・依存と箱の可視性に差がある。本仕事ではこれらの仕組みは改修しない。

## 作業記録

### run3（2026-11-28・共通バックエンド）

追加したファイル:

- `backend/app/models_mgmt/runtime_catalog.py` — 5 ランタイムの目録（Sly は既存 `sly_radiance_catalog`、Strata は既存 `strata_catalog`、Maya は probe から）。`fit` は総メモリと VRAM 余裕から判定し、`api_payload` が API の形。
- `backend/app/models_mgmt/model_paths.py` — 新規選択のルート検証（`/data1tb`・`/data2tb` のみ、realpath と symlink）、GGUF/config の読み取り（読む量を制限）、`inspect`。
- `backend/app/models_mgmt/runtime_settings.py` — 5 ランタイムの設定 capability。欄 id は保存 API の引数名と同じで、選択肢・上限は既存の検証定数から読む。
- `backend/app/schemas/models.py` — `AcquireBody`・`AcquireRegister`（runtime は llama/sly-radiance/maya のみ）。
- `backend/tests/test_runtime_catalog.py` — 目録の形・推測値なし・capability と保存 API の一致・prefix 欄なし・inspect の GGUF/config/ルート外拒否・壊れた GGUF・共通 API・取得の拒否と Job 引数。

変えたファイル:

- `backend/app/jobs/service.py`・`router.py` — `stop`（`interrupted`＋`paused` 印＋`.part` 保持）、`resume_stopped`、`resume_record_ids`、一覧/詳細の `can_resume`、`POST /jobs/{id}/stop`・`/resume`（監査 `job.stop`・`job.resume`）。
- `backend/app/models_mgmt/hf.py` — `fetch_sizes`（開始時に HF API から大きさ）、`files=[]` の展開、`_open_stream` の redirect 検査、`.part` の保持、runtime 別の登録 dispatch、guarded client。
- `backend/app/models_mgmt/resume.py` — `fetch_sizes` 付きなら空の files を許す（展開は `hf.download`）。
- `backend/app/models_mgmt/router.py` — 共通 API 6 本と `SlyRadianceModelBody.prefix_cache` のバリデータ。
- `backend/app/models_mgmt/sly_radiance.py` — prefix cache 常時オン（false を拒否、argv は常に `--enable-prefix-caching`）。
- `backend/app/route_map.py`・`docs/ROUTE-MAP.md` — 新規の内部 API と HF の出口。
- `backend/tests/test_hf_download.py`・`test_jobs_persistence.py` — `fetch_sizes` の展開・停止/取消の `.part`・停止と再開・`can_resume`。
- `docs/PLAN-model-catalog.md`・`CLAUDE.md` — 契約の記録とリンク。

試験（隔離: `env -u CONTROL_DECK_CONFIG`、`/data1tb/ControlDeck/app-strata/.venv`）:

- 新規 `test_runtime_catalog.py`: 16 passed。
- 関連 9 ファイル（strata_catalog・sly_radiance・hf_download・maya_register・radiance_runtime・maya_probe・strata_memory・route_map・jobs_persistence）: 合格。
- 全件試験は禁止のため未実行。実機の登録・起動、HF の実重み取得、Frontend と E2E は未確認。

残り（run3 の外）:

- 共通 UI と各パネル（別担当）。
- 実機で Sly/Radiance/Strata/Maya/llama の取得→登録→起動を確認（実重みの取得は許可が要る）。
- 統合後の `./deck.sh dev-check`・Models E2E・`docs/implementation-status.md` の更新・最終レビュー。

### run4（2026-11-28・検証と契約の是正）

run3 の差分を読み、依頼の「事前レビュー run4 の必須条件」と突き合わせた。見つけた食い違いを直した。

直したもの:

- `backend/app/models_mgmt/runtime_settings.py`
  - **Maya の欄が保存 API と食い違っていた**: `context` の下限 1024（実際の `MayaInstanceSettingsBody` は 8192）、`parallel` の上限 1（実際は 4）、`alias`（Maya の登録 API の引数ではなく目録の `setup_id` から決まる）、`instance_settings` が「速さ」と「エキスパート」に重複。→ `setup_id` に直し、下限 8192・上限 4 にし、重複を消した。`reasoning_effort` の選択肢は `MayaInstanceSettingsBody` の `Literal` から読む（`_literal_choices` で `Optional` も扱う）。
  - **Strata の量子化**: 系列と無関係な量子化を並べていた。既定系列（`qwen`）の `strata_catalog.quantizations()` から読む。系列の表示可否は `has_nvidia_gpu()` を 1 回だけ呼ぶ。
- `backend/app/models_mgmt/runtime_catalog.py` — 同じ GPU 判定の繰り返しを 1 回にした（系列ごとに sysfs を走査しない）。
- `backend/tests/test_runtime_catalog.py`
  - `test_capability_fields_match_the_save_api` に **maya** を追加（登録 `MayaRegisterBody` と調整 `MayaInstanceSettingsBody` の両方と照合。両方が持つ欄は共通部分の範囲）。
  - `test_capability_ids_are_unique_and_defaults_fit_the_choices` を追加（同じ id を 2 節に出さない・数値の既定が範囲内・選択の既定が選択肢内）。

run4 の差分レビュー（別担当）で出た指摘への対応:

- **取得後の登録が保存 API より緩かった**（major）: `AcquireRegister` に Sly の保存範囲
  （`context` 4096〜262144・`max_num_seqs` 1〜8・`kv_cache`/`quantization`/`prefill_chunk`/
  `reasoning_effort` の選択肢）の検証を追加。`POST /models/acquire-jobs` でも Sly の別名・
  ポートを先に検証する。数 GB の取得を終えてから登録で失敗するのを防ぐ。
- **Maya の壊れた settings.json で設定 API 全体が 500**（major）: `maya_s.get_settings` が
  読めない・辞書でないときに既定へ戻し、`logger.warning` を残す。`runtime_settings` の
  Maya 既定値（`reasoning_effort`・`thinking_budget`）も範囲外なら安全側へ寄せる。
- `kv_resident` の刻み（`multiple_of=1024`）を capability の `step` として出し、試験で
  保存 API の `multiple_of` と照合する（minor）。
- Sly 登録のポート: 指定が無ければ 8100〜8199 の空きを探す（8100 固定だと既存 Sly と
  重複して全取得後に失敗する。minor）。
- `list_model_files` を 1 回の取得で 2 度呼ばない（`_fetch_file_sizes` に一覧を渡す。minor）。
- 再開の二重実行を防ぐ印（`_resuming`。処理中の Job id を await 前に立てる）を追加。
  `stop` の `can_resume` は再開記録が実際に残っているときだけ true（minor）。
- 設定の既定を実体に合わせた: Maya の `context` は 8192（`maya_s` の既定）、Strata の
  `family`/量子化の既定は目録の先頭系列から（info）。
- 取得ファイル名の検証に `?` `#` `%` を追加（URL への補間で要求先・保存名が変わる。info）。

再レビュー（第 2 輪。critical 0 / major 0、minor 3、info 3）で直した点:

- **登録の欄の適用範囲**（minor）: `AcquireBody` がランタイムごとに使える欄を検査し、
  他ランタイムの欄（例: sly-radiance の `ctx_size`）を黙って捨てず 422 にする。Maya の
  登録もここで断る（`router.py` の同じ検査は実行されなくなったので消した）。
- **ポート衝突の判定**（minor）: `container_runtime` が衝突の例外に印（`port_conflict`）を
  付け、`hf._register_sly_radiance` は文言の部分一致ではなく印で再試行する。空きポートは
  全ランタイムの使用中のポートを 1 度だけ読んで選ぶ。
- **停止したままの記録の期限**（minor）: `paused` の記録は自動再開から外すだけで残していた
  ため、放置すると `/jobs` の走査が増え続けた。30 日を過ぎた記録は起動時の整理で消す
  （自動再開が有効なとき）。
- 残した info: `llama` の目録行が空（`/acquire-jobs?runtime=llama` は常に 404。UI は目録の
  行からしか取らないため実害なし）、`models_acquire` が同期の DB 書き込みを行う（既存の
  `hf_download` と同じ形で、この段の回帰ではない。`jobs.create` が `asyncio` の通知を使う
  ため endpoint を `def` へ移すのは別の改修）、dev-check の失敗は `/dev/shm` が読み取り
  専用の環境要因。

run4 で確認したこと（コードを読んだ結果。実機・実重みは未確認）:

- redirect で HF のトークンを別ホスト（CDN）へ送らない・https 以外を断る（試験追加）。
- repo・revision・files の不正値（`..`・絶対パス・制御文字）を断る（試験追加）。
- `.part` は停止・失敗・サーバー停止で残り、取消だけが消す。`paused` 印は自動再開で拾わない。再開は期限 7 日・3 回の歯止めを引き継ぐ。
- metadata の問い合わせと再開も `ensure_external_allowed` と guarded transport を通る。保存先・`.part` の symlink を拒否。GGUF/config の読取量・件数を制限。inspect は同期 endpoint。
- 設定値は既存の保存 API と一致（llama/radiance/sly/strata は既存の照合試験が合格。Maya も run4 から対象）。argv/YAML への往復は既存試験で確認済み。
- **意図的に空にした目録行**: llama.cpp は確定した repo が無く既存の検索・ファイル選択の流れがあるため推奨行を置かない。Radiance は `.rad` を登録する既存の流れがあるため推奨行を置かない。両ランタイムとも `instances` と登録の受け口は共通 API に出す。依頼の「全 5 形式の adapter と一覧を backend に一本化」は満たし、**取得目録の行だけ**を既存フローへ委ねる判断である（PLAN にも記載）。

試験（隔離: `env -u CONTROL_DECK_CONFIG`、`/data1tb/ControlDeck/app-strata/.venv`）:

- `test_runtime_catalog.py` 22 passed（レビュー対応の 2 件 + 欄の適用範囲の 1 件）。
- `test_jobs_persistence.py` 29 passed（再開の同時実行を拒む試験・停止の記録の期限の試験を追加）。
- 関連 17 ファイル（runtime_catalog・hf_download・jobs_persistence・sly_radiance・
  strata_catalog・strata_gateway・strata_optional・strata_memory・local_llm_gateway・
  radiance_runtime・models_local_register・maya_register・maya_probe・maya_s_addon・
  llama_runtime・route_map・features）: 278 passed。
- 全件試験は禁止のため未実行。実重みの HF 取得・実機の登録/起動・Frontend/E2E は未確認。

残り（run4 の外）:

- 第 2 輪の修正（13 ファイル）は作業ツリーに残る。`547cd5ed`（Nexarch のコミット）のあとに
  当てた分で、この環境からは `.git/objects` が読み取り専用のためコミットできない
  （`git add` が `Read-only file system`）。差分は `/tmp/opencode/nexarch-3/` の
  `run4-files.tar.gz`・`tracked.patch` に取ってある。

- 共通 UI と各パネル（別担当）。Maya/Strata の欄の契約は変わったので、UI は `docs/PLAN-model-catalog.md` の「共通 API の契約」の Maya・Strata の項を読む。
- 実機で 5 ランタイムの取得→登録→起動を確認（実重みの取得は許可が要る）。
- 統合後の `./deck.sh dev-check`・Models E2E・最終レビュー。
