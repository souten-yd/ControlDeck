# ローカルLLMランタイム：llama.cpp / Strata（2026-11）

## 実装方針

- ControlDeckの標準ローカルLLMランタイムは **llama.cpp**。Ollamaは引き続き独立したprovider。
- Strataは**任意のオプション機能**。導入、更新、削除だけを管理する。インストールだけでモデルを取得・ロードしたり、既定ランタイムやOpenCodeの設定を変更しない。
- Strata Terminalの公式インストーラは Niko1221/Strata のreleaseに含まれる install.sh。GitHub releaseのasset digest (SHA-256)を取得ファイルと照合してから、ControlDeckのユーザー権限で専用HOMEに導入する。root / sudo 不使用。
- Strataの実行操作はControlDeckのTerminalで行う。ControlDeckのAPI Gateway、既存llama.cpp model lifecycleへは自動登録しない。外部OpenAI互換providerとしての接続は利用者の明示設定とする。
- 更新には公式 strata update --yes を使用。稼働モデルを強制停止する --stop-model は付けない。
- 削除してもユーザーモデル、保存済みpreset、ControlDeckのデータ、llama.cpp、Ollama、外部インストールを消さない。稼働状況が不明なら削除拒否。
- 任意URL、コマンドや引数を受けるバックエンド実行APIは設けない。runtime semanticsは変えない。

## 端末からの試用

1. 設定 > オプション機能 > Strataから明示導入する。
2. 画面の strata-managed フルパスを使用し、まず launch --plan --family qwen --model iq2_xs を実行する。
3. ディスク容量とGPUメモリを確認してから launch --family qwen --model iq2_xs --yes --no-tui を実行する。
4. launch --status と launch --stop で状態と停止を管理する。

32GB RAM＋32GB VRAM（R9700）での初回試用はIQ2_XSのplanから始める。
Strata既定port 8080はllama.cppと衝突するため、同時稼働時は別portを明示する。

## Lucebox 廃止

- Luceboxのfeature ID、runtime選択、モデル登録、provider adapter、gateway分岐、RESTモデル管理、画面の選択肢、関連APIクライアントを撤去。
- 旧 model-runtime-policy.json で selected_runtime=lucebox を保存していた場合、読み込み時だけ ollama / backend空へ移行。それ以外の設定は維持し、保存元ファイルは読み取りで書き換えない。
- 既存のLucebox systemd unit・配布物・ユーザー所有GGUFは自動削除しない。コードから新規登録・自動起動・転送しない。
- 旧実装の説明と実測はGit履歴で閲覧できる。

## ControlDeck 本体更新

本体の更新は設定内の専用セクションに分離。公開Releaseを確認し、明示操作のみでバックアップ→fast-forward→サービス再起動→health checkを行う。問題発生時にはrollbackを試みる。運用規約は docs/development-release-policy.md を参照。
