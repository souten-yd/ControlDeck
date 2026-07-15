# ターミナル: モバイルキーボードと長期履歴の詳細設計

最終更新: 2026-07-16

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

## 6. xterm.js 6のモバイル入力・指スクロール追補設計（2026-07-16）

### 6.1 再現結果と原因

実サービスのtmux sessionへ300行を出力し、Playwright Chromiumを`320x700 / hasTouch`で接続した。
browser bufferと右端scrollbarには履歴が存在する一方、terminal面を指で下へ200px動かしても表示行が
`261..300`から変化しなかった。

- xterm.js 6は履歴位置をnativeな`.xterm-viewport.scrollTop`ではなく、内側の
  `.xterm-scrollable-element`と独自scrollbarで管理する。従来の`.xterm-viewport { touch-action: pan-y }`
  だけではtouch移動を`Terminal.scrollLines()`へ変換できず、履歴を指で遡れない。
- 履歴をpage/wheel移動した際、41表示行の一部に同じ末尾行が現れる現象もPC・mobile双方で再現した。
  tmux初期描画、`history_reset`、snapshotはWebSocket上では正しい順序だったが、`Terminal.write()`は非同期queueである。
  初期描画のparse完了前に同期的な`Terminal.reset()`が追い越し、その後に初期描画の残りがsnapshotへ混ざっていた。
  また初期描画中の端末能力queryへxtermが返した応答をtmuxへ即送信すると、snapshot後にtmuxが現在画面を再描画し、
  history/visible境界の1行を再度scrollbackへ押し出していた。
  既存の「重複解消済み」評価は末尾行の出現回数だけを見ており、履歴と現在画面の境界を検証できていなかった。
- 補助キーバーは`overflow-x-auto`により横scroll用領域を確保し、全要素共通の細いscrollbarも適用されるため、
  ボタンのない2段目に見える。さらに`.safe-bottom`がTailwindの`py-1.5`の下paddingを上書きし、
  Safe Areaがある端末ではボタン外に空の帯を作る。
- xtermの入力を複数chunkに分けて送っても、送信dataには改行を追加していない。再生bufferで確認した改行は、
  39桁のPTYへ60文字を入力した際の通常のsoft wrapであり、chunk境界ではない。ただし現状は
  `visualViewport.scroll`（keyboardの自動panやIME変換でも発生）ごとに`fit()`を実行するため、寸法が変わらなくても
  入力中の再描画を起こし、soft wrapが離散的に動いて見える。

### 6.2 改修方針

1. terminal面の単指縦dragをcell高単位で`Terminal.scrollLines()`へ変換し、指の移動中に履歴を追従表示する。
   8px未満のtapと複数指はscrollにせず、tap focusと補助キーバー横scrollを維持する。
   xterm 6のtouch処理とは二重実行せず、縦dragを確定したtouchmoveを背景pageへ伝播させない。
2. 補助キーバーは`flex-wrap: nowrap`を明示し、横scrollbarだけを視覚的に隠す。横swipe自体は維持する。
   高さ40pxの1行へ固定し、Safe Area paddingによるボタン外の空帯を作らない。
3. `visualViewport.scroll`はrootの座標だけ更新する。terminalの列・行再計算はhost寸法の変化時だけ行い、
   `FitAddon.proposeDimensions()`が現在値と異なる場合だけresizeする。これによりIME入力chunk間の不要なreflowと
   重複PTY resizeをなくす。物理幅を越えるcommandのsoft wrapは端末仕様として維持し、実改行は挿入しない。
4. tmux初期描画、`history_reset`、snapshot、通常streamをPromise chainへ積み、各`Terminal.write()`のcallback完了後に
   次のdata/resetを処理する。serverは`history_end`を明示し、それまではkeyboard入力と端末能力応答をtmuxへ返さない。
   これにより初期描画を確実にresetしてから全履歴snapshotを1回だけ構築し、後発の再描画も防ぐ。
5. tmux snapshotは`capture-pane -J`でsoft-wrapped物理行を論理行へ戻す。browser幅に応じた表示上の再wrapは許容するが、
   コピー内容や再接続履歴へ画面幅由来の実改行を混入させない。browser bufferの`isWrapped`もコピー時に連結する。

### 6.3 追加受入条件

- 320pxで補助ボタンのtop/bottomが全て一致し、視覚的な横scrollbar・ボタン外の空行がない。
- 300行出力後の単指drag中に表示位置が変わり、逆方向dragと新規入力で末尾へ戻れる。PCのwheel scrollも維持する。
- history/visible境界でも連番が連続し、同じ末尾行群を二重表示しない。
- 分割入力した長文の送信dataへCR/LFを追加しない。寸法不変のvisual viewport scrollではxterm/PTYをresizeしない。
- keyboard相当のviewport縮小・offset移動後もroot、入力cursor、補助キーバーが可視範囲内に収まる。

### 6.4 処理中アニメーションと入力の描画競合（2026-07-16）

`CR + EL`（`\r\x1b[K`）でWorking表示を80msごとに更新する処理を実tmuxで動かし、その途中に
`echo INPUT_DURING_WORK`を入力してPC/320pxで再現した。PTY上の入力と最終実行結果は保持されていたが、320pxでは
terminal面が黒くなり、旧行とWorking行が上下へ分離した。

第一原因はanimationやPTY dataではなく、xterm.js 6では空になった旧native viewportへ残していた
`-webkit-overflow-scrolling: touch`である。処理出力と入力が同時更新されるとiOS系の合成layerが部分的に未描画となり、
computed background/DOM行は白で正常なまま、画面上だけ黒いtileと分離行が現れた。また全scroll時の強制`refresh()`も
出力による自動scrollへ不要に介入していた。

- `onScroll`はviewport位置の記録だけに戻し、描画はxterm本来の差分更新へ委ねる。
- 空の旧native viewportは`overflow: hidden`とし、不要なmomentum-scroll合成layerを作らない。実履歴位置は
  xterm 6の`.xterm-scrollable-element`と明示的なtouch handlerで管理する。
- terminal viewportの既定黒背景を透明化し、hostをtheme背景色にする。renderer layerの更新間でも黒い下地を露出させない。
- Working更新中の分割入力、処理完了後の入力保持、履歴touch/wheel、PC/320pxを同じ試験で確認する。

### 6.5 IME確定・キーボード開閉による行位置ずれ（2026-07-16）

320px端末でsoftware keyboard相当のviewport高`700→410→700px`を4回切り替え、各縮小中に1文字ずつ
確定して再現した。処理停止中でも確定直後に全表示行が3px上へ移動し、terminalを閉じて開くと元へ戻る。
Working表示を80ms間隔で更新中にも同じ現象が発生した。

直接原因はxtermのbufferやPTY resizeではなく、外側`.terminal-xterm-host`の`overflow: hidden`である。
keyboard縮小時はhostのcontent高327pxに対してxtermの22行が丸め込みを含め330pxとなる。hidden要素は見た目を
切る一方でscroll containerなので、IME確定時にxtermの不可視textareaをcursor位置へ見せるbrowser処理が
`host.scrollTop=3`を設定し、xterm全体を上へ移動していた。再接続時はhost高が戻りscrollTopが0になるため正常に見える。

- hostを`overflow: clip`へ変更する。端数cellは従来どおり表示範囲で切るが、host自体はscroll containerにしない。
- terminal履歴はhostのnative scrollへ依存せず、既存のxterm buffer、`.xterm-scrollable-element`、touch handlerで維持する。
- viewport高切替4回について、idle/Working中の両方でhost scrollTop、先頭・末尾行座標、入力結果を検証する。

受入条件は、keyboard表示中の各文字確定前後で`host.scrollTop=0`、先頭行topと末尾行topが不変であり、
keyboardを閉じた後も配置が復元されること。処理中に確定した入力と履歴touch/wheelも失わないこととする。
