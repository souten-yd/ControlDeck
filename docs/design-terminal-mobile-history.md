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

### 6.6 Visual Viewport・renderer・PTY resizeの統合設計（2026-07-16）

6.5の修正後も、Working出力とkeyboard開閉を重ねた試験でDOM上の行座標・背景色は正常なまま、一度だけ
描画layerが黒く残る事象を確認した。追加監査では次を確認した。

- `FitAddon.proposeDimensions()`はxterm要素の直接の親のcomputed width/heightを使うが、親自身のpaddingを引かない。
  従来のhostには左右計8px・上4pxのpaddingがあり、実描画域より大きい行列数を恒常的に算出していた。
- rendererは`@xterm/xterm 6`標準のDOM rendererで、WebGL/Canvas addon、transform、zoomは使用していない。
- React StrictModeでeffectは開発時に再実行されるが、Terminal、WebSocket、observer、viewport/touch listenerはcleanupされる。
- `visualViewport.resize`とhostの`ResizeObserver`は同じkeyboard animation中に繰り返し発火する。1 RAFだけの集約では
  animationの各中間寸法に対してresizeでき、非同期`Terminal.write()`のparseとも順序が保証されていなかった。
- PTY resize送信は最小寸法・同一値を検証せず、再接続時の明示的な最終寸法同期もなかった。

改修は以下へ統一する。

1. 装飾paddingを外側wrapperへ移し、xtermの直接の親hostは無padding・`min-width/height: 0`・`overflow: clip`とする。
2. `visualViewport.resize`、ResizeObserver、window resize、visibility復帰、pageshowは単一schedulerを使う。
   最新世代以外を破棄し、2 RAFと最終eventから50ms経過後にだけVisual Viewportの確定width/heightをrootへ反映して
   fitする。keyboard animationの中間heightでrootの合成layer自体を連続resizeしない。`visualViewport.scroll`は
   top/leftの座標同期だけにし、寸法が変わらないIME panでfitしない。
3. hostが未接続、document非表示、非finite、幅100px未満、高さ80px未満ならfitを行わない。
   診断はlocalStorageの`control-deck:terminal-geometry-debug=1`で明示的に有効化した場合だけconsoleへ記録する。
4. fit/resize/refreshをWebSocket受信の`Terminal.write()`と同じPromise queueへ積み、parse途中へ割り込ませない。
   行列または実geometryが変わった確定時だけ全行refreshし、scrollや通常出力では強制refreshしない。
5. PTY通知は`cols>=10 / rows>=3`だけを許可し、同一値の連続送信を抑止する。再接続時は現在の有効行列を
   接続世代へ強制同期する。fit世代番号により古い計算結果が新しい結果を上書きしない。backend側も接続queryと
   resizeをrows 3〜500 / cols 10〜1000へ正規化し、同一PTY寸法の`ioctl`を抑止する。
6. cleanupで両RAF、timer、ResizeObserver、visualViewport/window/document/pageshow/touch listener、WebSocket handler、
   Terminal（読み込んだFitAddonを含む）を破棄し、再mountで累積させない。

追加受入条件はkeyboard表示/非表示10回、80ms Working更新を同時実行し、各安定後にhostとscreenの寸法、行の等間隔、
cursor、入力結果、PTY resize列を確認すること。0/極小・重複・古いPTY寸法を送らず、再接続・background復帰後も
最終寸法と表示を復旧し、mobile touch/PC wheel履歴を維持することとする。

### 6.7 iOS IME compositionとfullscreen TUIの同期設計（2026-07-16）

#### 再監査対象

PR #73でVisual Viewportの中間寸法、PTY write中のfit、無効・重複resizeは抑止した。一方、xtermの
`.xterm-helper-textarea`がcomposition中かどうかは管理していないため、Codex/Claude Code/vim等のfullscreen TUIが
status/input行を更新している間にも、予約済みfit、全行refresh、Visual Viewport panによるroot移動が実行できる。
IME未確定文字はbrowserがtextarea座標へ別描画するため、xterm cursorとroot geometryを別時点で動かすと残像・重なりになる。

独自CSSでtextareaを移動・置換せず、xtermが持つtextareaを常に1個だけ使用する。WebGL addonは使用せず、xterm 6標準の
DOM rendererを維持する。文字列やstatus行数、特定TUIへ依存する例外処理は設けない。

#### controller構成

`XtermView`はTerminal/FitAddon/WebSocket/React state/UIとcontrollerの組み立てだけを担当する。

- `TerminalWriteQueue`: write/reset/resize/recoveryを受信順に直列化する。dispose後はtaskを実行せず、例外後も後続queueを維持する。
- `TerminalImeController`: textarea取得、composition/beforeinput/input/focus/blur、2 RAFのIME settle、最大200件のopt-in診断を担当する。
- `TerminalGeometryController`: viewport/observer/window/pageshow/visibility、fit世代、安定待ち、root geometry、PTY通知、scroll位置、
  layout検証、条件付きrenderer recoveryとcleanupを担当する。

依存は`XtermView → 各controller`とし、IME controllerはgeometry controllerを直接参照しない。XtermViewがcomposition settleを
geometryの`flushAfterComposition()`へcallback接続する。cleanup順はgeometry → IME → WebSocket → write queue → Terminalとする。

#### 状態遷移と禁止操作

```
IDLE → COMPOSING → COMPOSING_WITH_PENDING_GEOMETRY → IME_SETTLING
     → GEOMETRY_SETTLING → FIT_QUEUED → IDLE
```

- `compositionstart`から`compositionend + 2 RAF`まではgeometry lockとする。
- lock中はterm.resize、term.refresh、rootのtop/left/width/height変更、PTY resizeを行わない。
- lock中のfit reasonは最大16種＋集約表示へ制限し、viewport位置同期とrenderer recoveryはtype集合の1件だけ保持する。
- composition終了後2 RAFで位置同期を1回行い、既存の2 RAF + 50ms安定待ちを通してfitを1回だけqueueへ積む。
- focus復元はIME settle後に1回だけ行い、そこで発生するviewport eventも同じschedulerへ集約する。
- xterm helper textareaの座標・寸法とfocusはxterm本体へ委ねる。screen外寸からcellを近似してinline styleを上書きしたり、
  composition flush後に無条件focusしたりしない。fixed/transform/画面外移動も使わない。

#### fit・scroll・renderer方針

通常fitは有効寸法でcols/rowsが変わる場合だけ`term.resize()`し、全行`term.refresh()`は呼ばない。normal bufferではfit前の
`viewportY/baseY`を記録し、末尾閲覧中だけ`scrollToBottom()`、履歴閲覧中は可能な範囲で元のlineへ戻す。alternate bufferでは
scroll操作を加えない。

renderer recoveryはpageshow/visibility/再接続/明示診断時でも、screenが非finite・0サイズ・hostを2px超過する実測不一致時だけ
write queue経由で1回refreshする。composition中は延期する。単なる端数geometry、observer、Working、focusではrefreshしない。

#### 診断とlayout契約

`control-deck:terminal-geometry-debug=1`のときだけ、IME/geometryイベントを最大200件のring bufferへ保存しconsole debugへ出す。
event/data/inputType、composition/fit世代、pending reason、buffer type、cursor/base/viewport、textarea/screen/host/root/helper rect、
Visual Viewport、textarea数・activeElement・cursor推定差を記録する。通常時はconsole出力しない。

DOMへ`data-terminal-header/body/host/helper`を付け、各確定fitで次を検証する。

- `abs(header.height + body.height + helper.height - root.height) <= 1.5px`
- `body.bottom <= helper.top + 1px`、`host.bottom <= helper.top + 1px`、`screen.bottom <= helper.top + 2px`
- textareaは1個で、composition中もhost外/helper内へ残留しない。再mount/session切替でも旧textareaを残さない。

#### 自動・実機受入条件

Playwrightではcompositionstart中にviewport/observer/window/pageshowとPTY出力を発生させ、200ms後のresize/refresh/PTYを0、
compositionend後のfitを1、PTYを最大1、refreshを0とする。keyboard相当開閉10回、textarea一意性、helper非重複、session再mount、
touch/wheel/copy/paste、PCを回帰する。

PlaywrightはiOS Safariの候補UIを再現できないため、最終完了には実iPhone Safari（可能ならPWA）で日本語未確定3秒、候補変更、
英数字追加入力、開閉10回、background復帰、回線再接続を画面録画で確認する。実機結果が得られるまでは自動試験成功と区別して
`実機確認待ち`と記録し、完了扱いにしない。

#### 機能を維持する性能設計

Visual Viewport resize/scroll、window resize、ResizeObserver、pageshow、visibility、再接続、composition settleは
`invalidate(type, reason)`へ集約する。typeはsize/position/renderer/connectionの小さな集合、reasonもSetで保持し、同一frameの
多重eventはcontroller全体で1組だけ持つ2 RAF + 50ms schedulerへまとめる。composition lock中はscheduler/queueへtaskを積まず、
pending集合だけを更新する。queue投入済みgeometry taskも最大1件とし、世代が古ければ寸法を適用せず最新世代を1回だけ再予約する。

DOM処理はviewport値の読取→root style一括書込→次frameにroot/header/body/host/helper/screenを各1回計測→計算→resize queueの順とする。
同じflush内でread/writeを交互に行わない。0.5px未満、position-only、同一host寸法では`proposeDimensions()`、style更新、resize、PTY通知を
行わない。通常fitの全行refreshは0回とし、renderer実測不一致時だけ1秒cooldown・同一世代1回で復旧する。

composition/beforeinput/input、fit世代、counter、viewport値はcontroller内部変数としReact stateへ入れない。通常入力は既存WebSocket送信
以外のDOM計測を行わず、診断object/rect/cursor推定/console/Long Task observerはdebug有効時だけ使用する。touchmoveは座標差分・加算・RAF予約
だけとし、cell高はtouchstartで1回計測する。scrollback 100,000、touch/wheel/copy/paste、再接続、background/pageshow復帰は維持する。

性能回帰は処理時間ではなく回数を主判定とする。keyboard相当のsize 25件 + position 10件を集中発火して実fit/resize/PTYを各最大1、
refresh 0、composition中100件でfit/resize/refresh/PTY/DOM readを0、終了後fit 1、queue最大1とする。50ms出力200回と開閉10回、
任意の10分soakでmemory、listener、RAF/timer、Terminal/textarea/WebSocket残留も確認する。

### 6.8 iOS keyboard後のTUI入力行とPTY resize同期（2026-07-16）

#### 問題の分離

IME未確定文字の散乱とは別に、keyboard表示によるrows変更後、旧入力行のplaceholderとSIGWINCH後の新入力行が同時に残る場合がある。
調査では次の3状態を混同しない。

1. xterm bufferのcursor/周辺行にも二重描画がある: PTY/TUIのresizeと入力順序を原因として扱う。
2. bufferは正常でrenderer行だけ不一致: resize完了後、非composition、同一世代1回に限る局所renderer recoveryを検討する。
3. buffer/rendererは正常でIME overlayだけ不一致: textarea座標の問題として扱う。

実装する切分け診断ではresize要求/ACK/ACK後PTY出力/input保留・解放、buffer type、cursor/base/viewport、cursor前後2行、表示中DOM行、
textarea/cursor/screen/host rectを最大200件のring bufferへ記録する。PTY byte列は生ログにせず、CSI H/f/A/B/C/D/J/K/r、
DECSET/DECRST 1049、CR/LF/BSとprintable byte数だけを要約する。通常入力、Working出力、BackspaceではDOM計測・refreshを行わない。

#### xterm textarea方針

PR #75の`TerminalImeController.syncTextareaToCursor()`が行っていた`left/top/width/height/lineHeight`の直接上書きは削除する。
xterm 6の非公開`_syncTextArea()`をscreenの外寸から模倣すると、renderer内部cell寸法と更新世代を共有できず、TUI/cursor/IMEの
どれが真の位置かを隠すためである。比較はPR #74/PR #75のrevisionで行い、既定動作はxtermのcursor moveとrendererへ委ねる。
textareaをfixed/transform/画面外移動するCSSは追加しない。

#### 世代付きPTY resize ACKと入力barrier

PR #76反映後の物理iPhoneでも通常PTY文字の分散、入力行二重化、空白画面化が再現したため、この節を実装対象へ移行する。

cols/rowsが実際に変わり、WebSocketへresizeを送る場合だけbarrierを開始する。

```text
resize JSON(N, connection generation)（local xtermは旧sizeを維持）
  → backend TIOCSWINSZ成功
  → resize_ack(N)
  → TerminalWriteQueueでlocal term.resize(N)をcommit
  → ACKより後の最初のPTY出力をTerminalWriteQueueが描画完了
  → Nの入力bufferを受信単位・元順序のまま送信
```

local xtermを要求送信前にresizeすると、ACKまでに届いた旧rows前提のWorking/ANSI cursor制御を新rowsで解釈してbufferを壊す。
従ってACK前frameは旧sizeで描画し、WebSocketのACK handlerがlocal resize taskをqueueへ積んだ後、後続PTY frameを同じqueueへ積む。
これによりpre-ACK出力、local resize、SIGWINCH後出力の順序を固定する。

backendは同一サイズ要求でも対応する世代へACKを返すが、`TIOCSWINSZ`失敗時は成功ACKを返さない。ACKはrows/cols、resize generation、
connection generationを反射し、frontendは現在のWebSocketと両世代が一致するものだけ採用する。WebSocket送信はlockで直列化し、
ACK後のbinary frameを「ACK後PTY出力」と判別可能にする。

barrier解除はACKと、その後のPTY出力がxterm write queueで描画完了した両方を原則とする。shell等がSIGWINCHで出力しない場合だけ
125ms timeoutをフェイルセーフに使う。timerだけで順序問題を解決しない。通常入力、同一geometry、position-onlyではbarrierを作らない。
保留は`term.onData()`が渡した文字列単位とし、日本語確定文字、制御sequence、paste、surrogate pairを分割しない。件数・byte数に
上限を設け、超過時は入力を失わないようfail-openする。再接続・dispose時は旧世代を破棄し、旧socketのACKで新接続を解放しない。

`TerminalResizeBarrier`は最大256 input chunk / 256KiB相当を保持し、`term.onData()`の受信単位を分割しない。geometry controllerは
barrier中のsize invalidationを集合へ保持するだけで実行せず、transaction終了後に最新geometryを1件だけ再scheduleする。
WebSocket helper key、paste、通常keyboardは同じinput senderを通し、onData disposableもcleanupする。

#### WebSocket送信順序

backendはbinary PTY frame、history control、resize ACKを接続単位の`asyncio.Lock`で直列化する。receive coroutineは同期`TIOCSWINSZ`
成功直後にACK送信を待つため、ACKより後にbrowserへ届いた最初のbinary frameをSIGWINCH後候補として扱える。frontendはそのframeを
受信した時点でresize generation tokenを付け、`TerminalWriteQueue.enqueueWrite(..., onComplete)`のcallback完了時だけbarrierへ通知する。
ACK前に受信済みでqueue滞留していたframeは、ACK後にwrite完了しても解除条件へ数えない。

#### Visual Viewport位置

fixed rootへ`visualViewport.offsetTop/offsetLeft`を再適用するとSafari自身の自動panと二重移動になるため、rootのtop/leftはCSSの0へ固定し、
position-only eventではstyle、fit、PTY resizeを変更しない。keyboardでviewport実高・幅が変わった場合だけ既存root sizeとfitを更新する。

#### tmuxサイズ

ControlDeckの実行環境（tmux 3.4）は`window-size latest`であり、調査時の永続sessionはattach client 0、window 50x46だった。
接続PTYへ`TIOCSWINSZ`を適用した結果をACKする。debug要求時は`TIOCGWINSZ`、tmux window/client size、`window-size` policyをACK診断値へ
含める。複数attach時はtmuxのclient/window寸法を確認するが、特定TUI名や
固定rowsを使った補正は行わない。

実サービス測定で`38x23`要求後、PTYは即時`38x23`だがtmux client/windowが旧`38x41`に残ることを確認した。Web接続用の
`tmux attach-session`は`start_new_session=True`の独立process groupであり、このPTY構成ではmasterへの`TIOCSWINSZ`だけではattachへ
SIGWINCHが届かなかった。ControlDeckが所有するattach process groupへioctl成功後に`SIGWINCH`を明示送信する。変更後はACK時点と
transaction後probeの双方でPTY/client/window=`38x23`、policy=`latest`が一致した。fallback PTYは既存kernel通知を維持し、明示signalは
ControlDeckが所有するtmux attach processだけに限定する。

#### 限定renderer recovery

全行refreshは引き続き通常fitから除外する。bufferが1行、DOM rendererだけ2行という不一致を実測できた場合に限り、
composition中ではなく、resize ACKと再描画が完了後、同一generationで未実行の条件を満たしてcursor前後1行だけrefreshする。
input/Backspace/Workingごとには比較もrefreshも行わず、常時実行しない。bufferにも二重行があればrefreshで隠さずresize barrierを直す。

#### iPhone診断の取得

Safari Web Inspectorで次を実行してからページを完全reloadする。

```js
localStorage.setItem("control-deck:terminal-geometry-debug", "1");
location.reload();
```

再現直後に次を取得する。logは各300件上限で、PTY本文は保存せず制御種別と文字数だけを保持する。

```js
window.__controlDeckTerminalTest.resizeBarrierState();
window.__controlDeckTerminalTest.resizeBarrierLog();
window.__controlDeckTerminalTest.terminalLog();
window.__controlDeckTerminalTest.captureRenderState();
```

`captureRenderState()`の`mismatchedRows`が空で画面だけ壊れる場合はSafari合成layer、buffer/DOM双方に同じ分散があればPTY/TUI、
buffer正常でDOMだけ異なる場合はrendererと分類する。確認後はdebug keyを削除して通常hot pathへ戻す。

```js
localStorage.removeItem("control-deck:terminal-geometry-debug");
```
