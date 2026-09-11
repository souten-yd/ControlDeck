# Add-on の道具の書き方

ControlDeck は Add-on の `agent_tools` を MCP の道具として OpenCode に見せる。
一覧に出る説明は、次の二つを繋いだものである（`app/addons/execution.py` の
`_agent_tool_description`）。

```
<addon_id> Add-on: <label>

<入力 schema の top-level description>
```

道具を選ぶのはモデルである。**ここに書いていないことは、選ぶ側には存在しない。**

## なぜ規約が要るか

2026-09-11 に MediaForge と SonicForge の 22 個を数えたところ、**11 個は
label 一行だけ**で、用途の説明が無かった。ローカルモデル（Qwen3.8-27B）で
測ると、誤って選ばれた道具は全部その側だった。

```
media.inspect          「Inspect a media asset」        41 文字
sonic.inspect          「Inspect a SonicForge job or asset」53 文字
media.scene.snapshot   「Inspect the current immutable 3D scene revision」67 文字
```

3D の場面に照明を足す依頼で、モデルは `media.scene.snapshot` を三回呼んで
`media.scene.export` へ流れ、`media.scene.edit` に辿り着けなかった。
snapshot の説明に「場面を変えるためには使わない——media.scene.edit を呼ぶこと」
を足すと通った。

Haiku では同じ題が全部通る。**書いていないものを名前から補えるかどうかの差**で、
補えないモデルのほうが多い。

## label

```
・動詞 + 目的語で始める
・80 文字以内（ControlDeck が検証する）。実際は 40 文字前後に収める
・実装語彙を書かない
    typed / durable / bounded / owner-scoped / immutable / Resolve / recipe
    これらは呼ぶ側の語ではない。書くと、用途を書くべき場所が埋まる
・その道具にしか当てはまらない語を必ず一つ入れる
・"Inspect" で始めてよいのは、読むことそのものが目的の道具だけ
```

悪い例と直し方:

```
Create a 3D scene with a typed recipe and return a durable Job
  → Create a 3D scene
Edit a 3D scene with bounded typed operations and return a durable Job
  → Edit an existing 3D scene
Inspect an owner-scoped durable Media Forge scene Job
  → Check whether a 3D scene job has finished
```

## 入力 schema の description

四つを、この順で書く。**書かなくてよいのは 4 だけ**である。

```
1  何のための道具か      呼ぶ側の言葉で。一文で言い切る
2  いつ呼ぶか / 前提     先に呼ぶべき道具があるなら名指しする
3  何に使わないか        近い道具を名指しで排除する
4  外すと壊れること      実測があれば書く
```

書けている例（`sonic.voice.create`）:

```
キャラクターの声を作る。返る voice_id を sonic.generate の input.voice_id に
渡すと、以後その声で喋る。                                          ← 1 と 2

design で作った声は……実測では同じ注文文で呼び直すと完全に別人になった。  ← 4
```

読むだけの道具は 3 が要る。読むだけの道具は名詞が依頼と一致しやすく、
行動する道具に勝ってしまうためである。

```
既にある素材の中身（種類・寸法・状態）を読み返すだけの道具である。
作る・直す・置くためには使わない——media.generate / media.generate.batch /
media.pack を直接呼ぶこと。
```

## 一つの schema を二つの道具で共有しない

top-level description は道具ごとの用途を書く場所である。共有すると、
どちらの用途も書けない。形が同じでもファイルを分ける。

```
悪い  media.job.status と media.job.cancel が scene-job-reference.json を共有
良い  scene-job-status-request.json / scene-job-cancel-request.json
```

## 費用

説明を足すと、その道具の定義は毎ターン少し重くなる。実測では 11 個ぶんを
埋めて 330〜800 トークン。道具定義の合計は 26,769 トークンなので、3% 未満である。
**選び間違えて呼び直す一往復のほうが高い。**

## 検査

機械で見られる部分は各リポジトリの試験で縛る。

```
・label が 80 文字以内で、禁止語彙を含まない
・agent_tools の入力 schema に top-level description がある
・読むだけの道具の description に「使わない」が書かれている
・一つの schema を二つの道具が共有していない
```
