# Terminal V2 並行再設計

更新: 2026-07-20

## 1. 目的

現行Terminalの画面レイアウトと操作を契約として保ちながら、接続、履歴復元、
xterm描画、モバイル入力、Visual Viewport追従を新しい実装へ置き換える。
現行実装はV1として残し、V2が合格するまで既定経路を変えない。

V1とV2を同一tmux sessionへ同時接続しない。tmuxは複数clientのサイズと
redrawが相互干渉するため、shadow renderは正しい比較にならない。V2検証は
そのbrowser tabが新規作成した専用sessionだけを使う。

## 2. 変えないUI契約

- 全画面Terminal、session選択、Live／Reconnecting／Exited、foreground program、cwd、閉じる。
- PCのCopy、モバイルのPasteタップ／上swipe Copy、Enter、Esc、Tab、Ctrl、矢印、
  `^C`／`^D`／`^Z`／`^L`。補助buttonはsoftware keyboardを開閉しない。
- Terminal本文の単指縦swipe、右端overlay history bar、通常tapのみ入力focus。
- Snippet／Automation panel、大容量Paste進捗／中止／再試行、session切替、Dark／Light、
  Safe Area、320pxの横overflow 0、44px touch target。
- reload／service restart後も同じsession IDと正しい最新画面に戻る。

V2の都合でbuttonを減らしたり、代替の中間入力欄を追加したりしない。

## 3. 責務分割

### TerminalWorkspace

Reactが担当するのはheader、status、xterm host、history bar、helper bar、Copy sheet、
Paste progressの配置だけとする。PTY outputやscrollback全量はReact stateに入れない。
V1/V2は同じpropsとaccessible nameを持つ。

### TerminalConnectionV2

`DISCONNECTED -> CONNECTING -> REPLAYING -> LIVE -> RECONNECTING -> CLOSED`だけを管理する。
WebSocket世代、journal sequence、resume/resetと有界input FIFOを管理し、DOMを触らない。

### TerminalRendererV2

xterm instanceはmount中1個だけとする。受信順のwrite queue、reset、最終paint境界、
scroll positionを管理する。履歴は最新部分を先に復元し、大きなframeは小さく分けて
browser taskへ定期的に制御を返す。

### TerminalInputV2

文字、Backspace、Enterはscroll、React update、focus変更を通さずWebSocketへ送る。
履歴中の初回入力だけ末尾へ戻す。Pasteはchunk、ACK、中止、再試行を独立管理する。

### TerminalGeometryV2

Visual Viewportの`width/height/offset`をrootへ即時反映する。xtermのlocal resizeは受信write順を
保って先にcommitし、PTY resizeはWebSocket順序で後続inputより前へ置く。
composition開始後の中間geometryは保留し、composition終了後2 paint以内に最新値へ合わせる。

### TerminalHistoryV2

本文swipeと右端barは同じlocal xterm bufferだけを操作する。touchendでtmuxへ
二重のscroll命令を送らない。古い履歴の追加取得は後続milestoneの明示操作とし、
初期接続のhot pathへ入れない。

## 4. 並行導入とロールバック

1. 既定はV1のまま。`?terminalLab=v2`は通常一覧の既存sessionをV2で開かない。
2. Lab tab内の「V2検証session作成」で作ったsession IDだけをV2で開く。
3. E2Eは自分が作成したsession以外のconnect/delete/inputをfail-closedで拒否する。
4. 合格後に管理者向けcanary settingを追加し、新規sessionから限定的にV2を使う。
5. V2を既定化しても少なくと1実装phaseはV1切替を残す。session/processは切り替えで削除しない。

## 5. 合格基準

- 同一LANの入力echo／Backspace反昧p95 50ms未満、最大250ms未満。
- 復元snapshot上限で接続開始からLIVEまで1秒以内を目標、4秒を失敗上限とする。
- 320x700、390x844、768x1024、1280x800で横overflow 0、操作名／配置／44px targetをV1と一致させる。
- keyboard表示中にroot、screen、cursor/input行、helper barがVisual Viewport内。IME textareaは1個。
- swipe中とtouchend後にbuffer行とDOM行が一致し、右端barと本文swipeの応答100ms未満。
- reload、WebSocket切断、service restartでsession ID、履歴末尾、実processを維持する。
- 100KB／300KB／UTF-8 Paste、Copy、補助key、Automation、session切替、Dark／Lightが同じ操作で成功する。
- Chromium mobile emulationだけでなく物理iPhone SafariとPWAで確認する。この証拠がない間はV1との入れ替えを完了としない。

## 6. 計測とプライバシー

保存する計測値は接続世代、履歴byte/chunk数、replay/write/paint時間、input/echo時間、
resize回数、scroll frame時間、reconnect回数だけとする。入力文字、画面本文、Clipboard、
cwd、コマンド、tokenをtelemetry/logへ保存しない。

物理端末の合格証拠はV2 Lab内でその場だけ生成する。Browser／Standalone、secure context、
viewport寸法・offset、横overflow、root containment、IME textarea数、rows／colsと上記数値だけを含め、
Session IDを含めない。サーバーへ自動送信・永続化せず、利用者が明示した場合だけJSONをClipboardへ
コピーする。SafariとStandalone PWAの証拠を別々に取得し、未計測sampleは合格扱いしない。
