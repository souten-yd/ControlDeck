# ターミナル履歴が数行しか戻れない問題の修正

2026-10-13。通常の Terminal (V1) に適用。V2 Lab はこの変更の対象外。

## 原因

tmux の history-limit と xterm の scrollback はどちらも 100,000 行だった。
しかし tmux attach は元の全出力ではなく画面の更新を送る。通常 shell で 1,200 行を
まとめて出した実測では、tmux に約 1,200 行が残っていても xterm の baseY は 2 にしかならなかった。
フロントエンドが xterm のローカル履歴だけを動かしていたため、「少ししか戻れない」状態になった。
再接続時の snapshot は最大 10,000 行 / 512 KiB に制限されるため、再接続だけでも全履歴は参照できない。

また、このホストの既存 Codex セッションは tmux の alternate_on=1、history_size=0 またはごく少数だった。
Codex の tui.alternate_screen=auto は tmux 内で通常の scrollback を残さない。
これは ControlDeck の履歴参照経路とは別の要因。

## 変更

- 永続セッションの wheel、touch、右端バーを既存の認証済み history_scroll 制御へ接続。
- backend が tmux の実際の history_size / scroll_position を返し、バーもその位置を表示。
- 送信中は次の移動量を集約し、応答を受けてから送る。入力・切断後の古い応答は epoch で破棄。
- 長距離移動は tmux copy-mode の goto-line を使う。繰り返し scroll-up を呼ぶ試行では
  backend が先頭に達しても PC の表示が約 4,000 行移動地点に残り、再描画が滞留した。
  直接移動によりこの問題を解消。
- マウス入力を宣言する TUI ではアプリ自身への wheel を維持。非対応アプリへ PageUp 等を注入しない。
- 入力開始時は copy-mode を終了し、最新行へ戻す。
- ホスト設定 `/home/souten/.codex/config.toml` の `[tui]` に `alternate_screen = "never"` を追加。
  既存 Codex プロセスは停止せず、次回起動/再開から適用する。既に失われた画面履歴は復元できない。
  このホスト設定はリポジトリ成果物には含めない。

## 検証

- `./deck.sh` で実サービスに反映、HTTP health 成功。
- 実認証/実 tmux/Google Chrome: PC 1280px と touch 320px で 12,000 行を出力し、
  先頭まで移動、入力復帰、ページ再読み込み後の先頭参照を確認。横 overflow なし。
- `less --mouse` の touch で表示が 1 行目から 19 行目へ移動。mouse を使わない less には
  out-of-band history 制御だけが送られ、アプリへキー/escape の注入なし。
- 検証専用 Codex 起動: auto では alternate_on=1、設定適用時は 0。LLM への依頼送信なし。
- frontend production build 成功。
- backend 全体: **1175 passed / 2 skipped / 1 warning** (98.28秒)。
  初回は 1174 passed / 1 failed / 2 skipped。
  失敗した既存 Add-on cookie テストは fake-addon の実 HTTP 接続を試みて 502 になっていた。
  寿命検証に必要な成功応答を MockTransport で供給するようテストのみ補正した。
- 既存 TUI E2E は canvas 描画を innerText で検査して空文字になっていたため、
  opt-in 診断の実 terminal buffer を読むよう補正した。

検証用に作った端末だけを削除。ユーザーの既存 Codex/tmux セッションは保持。
物理スマートフォン、V2 Lab、100,000 行すべての走査は NOT TESTED。
証拠ログは同時作業プロジェクト `/data1tb/StrataEval/evidence/terminal/`。
ブラウザーの既存タブには再読み込みが必要。
