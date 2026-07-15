# ターミナル: モバイルキーボードと長期履歴の詳細設計

最終更新: 2026-07-15

## 1. 再監査結果と原因

### ソフトウェアキーボードで入力位置が隠れる

現状は全画面rootをCSSの`position: fixed; inset: 0`でlayout viewportへ固定し、
`visualViewport.resize`ではxtermの`fit()`だけを呼んでいる。iOS/Androidでキーボードが開くと
visual viewportだけが縮小・移動するため、root下端と補助キーバーはキーボード裏へ残る。
ブラウザがxterm内部textareaを見せようとlayout viewportを自動スクロールし、ユーザーの指スクロールと競合して画面がずれる。

### 古い出力が消え、再接続後に遡れない

- xtermのbrowser scrollbackは5,000行で、それ以前を破棄する。
- tmuxの既定history limitも通常2,000行で、Control Deckは値を設定していない。
- tmux接続は`attach-session`の現在画面を送るだけで、切断中を含むtmux historyをbrowserへ復元しない。
- tmuxなしfallbackは最大約100KBだけをメモリ再生し、Web再起動では消える。

従って「tmuxセッションが永続」であっても、過去出力をPC/モバイルから確認できることは保証されていなかった。

## 2. モバイルviewport設計

対象は320〜767pxのcoarse pointer端末。PCのlayoutは変更しない。

1. ターミナル表示中はbody/htmlを`overflow: hidden; overscroll-behavior: none`として背景ページの指スクロールを止める。
   bodyの`position`は固定せず、keyboardによるbrowserのvisual viewport panと競合させない。
2. rootの`top/left/width/height`を`visualViewport.offsetTop/offsetLeft/width/height`へ追従させる。
3. `visualViewport.resize`と`scroll`、rootの`ResizeObserver`を同じrequestAnimationFrameへ集約してxtermをfitする。
4. キーボード開閉後も補助キーバーをvisual viewport下端に置き、入力カーソルと重ねない。
5. xterm viewportは縦方向touch panを許可し、overscrollをterminal内に閉じ込める。補助キーの横scrollとは分離する。
6. 終了時はbody/htmlの元のstyleを必ず復元する。body位置は変更しないため、元ページのscroll位置も変化させない。

## 3. PC/モバイル共通の履歴設計

- browser xterm scrollback: 100,000行。
- tmux history limit: 100,000行。Control Deck専用tmux configを`data_dir`へ0600で生成し、
  tmux server初回起動前に`-f`で読み込む。既存serverにもglobal optionを設定してから新規paneを作る。
- WS接続ごとに`tmux capture-pane -p -e -S -`で履歴を取得し、最大16MiBまで送る。
  上限を超えた場合はUTF-8/ANSI列の途中を避けて古い側を切り、切り詰め通知を先頭に付ける。
- serverはtmux attach直後の端末初期化を先に読み捨てずbrowserへ適用し、その後に`history_reset`制御messageと
  snapshotを送る。browserはbufferをresetしてからsnapshotを受ける。
  これにより再接続時の全履歴二重追加を防ぎ、切断中の出力も復元する。
- tmuxなしfallbackも同じ制御messageを使い、bufferを16MiBへ拡張する。ただしWeb再起動を越えない旨は既存警告を維持する。
- session IDは8桁hexだけを許可し、tmux targetへ未検証文字列を渡さない。

「すべて」は無制限保存ではなく、ホストメモリとWS応答を枯渇させない明示上限100,000行/16MiBの範囲で、
PCとモバイル双方から先頭まで遡れることと定義する。上限到達は無言で消さずUI/履歴内へ表示する。

## 4. 受入条件

- 320pxでvisual viewportを300pxへ縮小・offset移動してもroot/補助キーバーがその範囲内にあり、横overflowがない。
- キーボードを閉じると元の全画面へ戻り、ターミナル終了時に元ページscroll位置が復元される。
- 新規tmux sessionへ10,000行を出力後、PC/320pxの初回接続と再接続のどちらでも1行目と10,000行目を確認できる。
- 再接続で履歴行が重複しない。接続中の通常streamと入力、resize、自動再接続を壊さない。
- tmux commandは配列引数、認証済みWS、root不要を維持する。

## 5. 実機検証結果（2026-07-15）

- Playwright Chromiumで320x800を使用し、software keyboard相当としてvisual viewportを
  `top=180 / height=300 / width=320`へ変更。rootは同じ座標・寸法、補助キーバー下端は480pxとなり、
  可視範囲内に収まった。bodyは`position: static`のまま、背景scrollだけを停止した。
- xtermの入力用textareaはfocus中も背景`transparent`、opacity 0。画面上の緑色はshell promptの文字色であり、
  入力欄の背景色変化ではないことをcomputed styleと撮影で確認した。
- 実tmux sessionへ10,000行を出力し、320pxと1280pxの双方で1行目・10,000行目を確認。
  末尾行は各1回だけで、再生snapshotとattach初期画面の重複がない。PCヘッダーにも全文コピー導線を追加した。
