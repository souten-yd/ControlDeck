# サーバーが落ちる・繋がらないときの調べ方

「モバイルで繋がらない」「再接続中が消えない」「Add-on の画面がデータを取得
できない」「生成が host_unreachable (ReadTimeout) や worker_crash -15 で落ちる」
——これらは別々の不具合に見えて、**同じ 1 つの原因**であることがある。
ControlDeck のプロセスが落ちていれば、そのとき繋いでいたものは全部そうなる。

だから症状側から直し始めない。まず落ちていないかを見る。

## 1. 落ちていないか

```
journalctl --user -u control-deck-web.service --since "1 day ago" \
  | grep -E "Watchdog timeout|Scheduled restart"
```

`Watchdog timeout (limit 30s)!` が出ていれば、systemd が SIGABRT で落としている。
プロセスが `WATCHDOG=1` を送れなくなったということで、原因はほぼ
「event loop が止まっている」である。

2026-09-06 の実測では 3 日で 68 回、うち 2 時間で約 25 回落ちていた。数分おきに
全接続が切れるので、画面の側で何を直しても直らない。

## 2. 何が止めているか

`app/maintenance/watchdog.py` の stall watcher が、loop が 12 秒動かないと全
thread の stack を journal へ書き出す。watchdog に落とされるより先に記録される。

```
journalctl --user -u control-deck-web.service --since "<時刻>" \
  | grep -A 400 "応答していない" | grep -E "Current thread|backend/app/"
```

`Current thread` の側が、そのとき loop を握っていた場所である。

## 3. これまでに見つかった原因

### async の endpoint から ORM を触る（2026-09-06, PR #283）

```
addon_frame_proxy (async)
  → user_permissions → user.role の遅延読み込み
    → sqlalchemy pool/queue.py get   ← 接続待ちで同期ブロック
```

async の endpoint の中で ORM を触ると、接続待ちは event loop の上で起きる。
**1 本の要求ではなくプロセス全体が止まる。** `pool_timeout` の既定 30 秒は
`WatchdogSec` と同じなので、待ち始めた時点で kill が確定していた。

枯渇したのは frame が接続を長く握るためだった。認証のあと session を返さないまま
上流 Add-on へ往復するので、その間ずっと 1 本を占める。1 画面から何十本も同時に
飛ぶので、既定の 5+10 はすぐ尽きる。

対策は 3 つとも入っている（`tests/test_frame_proxy_loop_safety.py` が守る）。

- 使う関連は一緒に読む。あとから触ると、そこで問い合わせが走る
- 用が済んだら session を返す。上流への往復の間は抱えない
- pool は同時本数に見合う大きさにし、待ちは watchdog より十分短くする

### 同じ形をした危険

`async def` の endpoint や background task の中で、次を呼んでいないか。

- ORM の属性アクセス（遅延読み込み）、`db.execute`、`db.commit`
- `requests` など同期の HTTP client
- 大きなファイルの読み書き、hash 計算
- `subprocess.run`、`time.sleep`

どれも loop を止める。同期処理は `asyncio.to_thread` へ出すか、endpoint を
`def` にして Starlette の threadpool に任せる。

## 4. 直したつもりで直っていないとき

`journalctl` の kill 件数が減ったかどうかで見る。減っていなければ別の場所が
loop を止めている。stack をもう一度取る。
