# セキュリティモデル

## 脅威モデル

本アプリは PC 全体を操作できるため、侵害されるとホスト全権掌握に等しい。
そのため「初期状態は閉じる・特権は分離する・すべて記録する」を原則とする。

## 原則と実装

| 原則 | 実装 |
|---|---|
| root で動作させない | web は一般ユーザー。setup/install スクリプトは root 起動を拒否 |
| 特権操作の分離 | 再起動/シャットダウンは固定argvの `systemctl reboot/poweroff`（即時選択時だけ固定`--force`）をlogind/polkit境界で実行。任意commandは受け取らず、設定時はTOTP再認証を要求 |
| AMD GPU制御 | Webは一般ユーザー。`deck.sh service`がroot所有の専用helperと限定NOPASSWD規則を登録。helperはAMD BDF・実機cap/DPM levelを再検証し、電力/MCLK/SCLK属性以外を変更しない |
| system service制御 | Webは一般ユーザー。設定の固定ID／unit／start・stop・restartをroot所有catalogへ導入し、root所有・非symlink helperが固定`/usr/bin/systemctl`だけを配列実行。APIから任意unit／action／shellを渡せず、killも禁止 |
| 任意コマンド初期無効 | `security.allow_arbitrary_commands: false`。登録済みアプリ実行のみ |
| 許可コマンドHC | ローカル設定の固定ID→固定argvだけを選択可能。APIへargvを返さず、認証語を拒否し、出力破棄・resource上限付きsystemd user transient unitで最大4並列実行 |
| shell=True 禁止 | subprocess はすべて配列引数。CI/レビューで検査 |
| パス検証 | すべてのファイルパスは `Path(p).resolve(strict=...)` で正規化し、許可ルート配下（`os.path.commonpath`）を検証。symlink は resolve 後の実体で判定 |
| アーカイブ | ZIP／tar.gzの作成・展開は許可root内の一時pathへ実行し、Linux `renameat2(RENAME_NOREPLACE)`で原子的公開。`..`／絶対path／重複・競合path、symlink／hardlink／特殊file、10万項目超、設定size上限超、16MiBを超える200倍超の展開率、空き容量不足を拒否し、既存pathを上書きしない。管理backup restoreも既知root・通常file／directory限定の同等境界で展開する |
| 監査ログ | ログイン成功/失敗、アプリ登録/編集/削除/起動/停止/強制終了、ログ削除、電源操作、ユーザー/権限/設定変更を AuditLog へ記録 |
| 秘密情報のマスキング | 環境変数の値のうち TOKEN/SECRET/PASSWORD/PASS/API_KEY/PRIVATE_KEY/AUTH/COOKIE を含むキーは表示・ログ出力時にマスク。DB 保存時は暗号化（Fernet、鍵は data_dir 内 0600） |
| レート制限 | 直接peer IPごとにAPI 5,000回/分、download 300回/分、WebSocket handshake 300回/分（設定可能）。HTTP超過は429 + Retry-After、WebSocketは4429で拒否。未設定の転送headerは信用せず、bucket数も20,000へ制限 |
| ユーザー／Role管理 | `users.manage`必須。Custom roleへ付与・ユーザーへ割当可能な権限は操作者自身の権限subsetだけ。preset role不変、最後の有効administratorと自分自身の管理画面経由降格／無効化／password resetを拒否。role・状態・password・Custom role権限変更時は対象sessionを失効 |
| PostgreSQL credential | URLはYAML／unit本文へ書かず、固定`config/database.env`だけに保存。起動前に`O_NOFOLLOW`、通常file、実行user owner、0600、4KiB、固定1行、SQLite／PostgreSQL方言を検査。診断はbackend／host／port／databaseだけを表示し、pg_dump／pg_restoreはpasswordをargvへ渡さない |
| ネットワーク | 既定 127.0.0.1:8765。0.0.0.0 設定時は起動ログと UI に警告。HTTPS はリバースプロキシ（Caddy/Nginx 設定例を deploy/ に用意） |

## 認証

- パスワードハッシュ: Argon2id（argon2-cffi デフォルトパラメータ以上）
- セッション: 128bit 乱数トークン。DB には SHA-256 ハッシュのみ保存。
  Cookie 属性: `HttpOnly; SameSite=Lax; Path=/`（HTTPS 時 `Secure`）。有効期限は設定（既定 480 分）。
- TOTP（Phase 7）: RFC 6238、シークレットは暗号化保存。リカバリーコード対応。
- `security.totp_requirement`は`optional`／`administrators`／`all`。必須対象が未設定の場合は、認証済みsessionを
  enrollment専用の`me`／`setup`／`verify`／`logout`だけへ制限し、その他RESTを403、WebSocketを4403で拒否する。
  設定完了後に通常権限へ戻し、必須対象によるTOTP無効化は監査付きで拒否する。legacyの
  `require_totp_for_admin: true`は`administrators`と同じ強制として扱う。
- ログイン試行はレート制限し、失敗を監査ログへ記録する。
- パスワード変更は現在のパスワードで再認証し、成功時に本人の全セッションを失効する。失敗は接続元+user単位5回/15分、
  TOTP有効化確認／無効化は各5回/5分に制限し、成功／失敗／制限を秘密値なしで監査する。
- ログイン／TOTP／パスワード変更の失敗counterも最大20,000 keyへ制限し、異なるusernameの大量送信でmemoryを無制限に消費しない。
- 全APIには接続元別の共通上限を置き、ダウンロードとWebSocket接続は別の低い上限を使う。
  `/health`と`/meta`は死活監視を妨げないよう除外する。リバースプロキシ利用時も、信頼済みproxyの明示設定を実装するまでは
  `X-Forwarded-For`等をrate-limit keyへ使わず、ASGI serverが確定した直接peerを使う。
- ユーザー作成／表示名・role・有効状態・password変更とCustom role作成／権限変更／削除は監査する。
  password hashや入力値、permission本文は監査metadataへ含めず、変更field名、件数、session失効有無だけを記録する。

## CSRF

Cookie セッションのため、状態変更メソッド（POST/PATCH/PUT/DELETE）は
`X-Requested-With: ControlDeck` ヘッダーを必須とする（フォーム送信・単純リクエストでは付与不可能）。
加えて SameSite=Lax でクロスサイト POST の Cookie 送信を遮断する。

## WebSocket

接続時に (1) セッション Cookie 検証 (2) Origin ヘッダー検証（許可オリジンのみ）
(3) 対象リソースの権限確認 を行い、失敗時は 4401/4403 で即時クローズする。
接続元別上限を認証より前に検査し、超過時は4429で即時クローズする。

## systemd ユニット生成の安全性

- ユニットファイルは Python 側でフィールドごとに検証した値のみを埋め込む
  （パスは絶対パス + 存在確認 + 許可検証、引数は systemd-escape 相当のクォート処理）
- `ExecStart` は実行ファイルの絶対パス + 引数リスト。シェル経由起動を生成しない
- 環境変数キーは `[A-Za-z_][A-Za-z0-9_]*` のみ許可。`LD_PRELOAD` / `PYTHONPATH` / `BASH_ENV` /
  `ENV` / `PROMPT_COMMAND` は警告対象
- ユニット名はサーバー側生成の ID のみから構成（ユーザー入力を含めない）

## ファイルアクセス（Phase 4）

- 許可ルートは設定 `files.allowed_roots` で明示したもののみ
- 検証手順: 受領パス → `realpath` → 許可ルートいずれかの配下か `commonpath` で判定 → 拒否時 403
- 初期拒否: `/etc`, `/root`, `/proc`, `/sys`, `/dev`, `~/.ssh` 等は許可ルートに追加しても警告
- ZIP 展開時は各エントリの展開先を同様に検証（ZIP Slip 対策）
- アップロードはサイズ上限・チャンク検証

## エラーハンドリング

内部例外はサーバーログへ、クライアントには汎用メッセージ + エラー ID を返す。
スタックトレースを API レスポンスへ含めない。
