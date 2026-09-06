---
name: controldeck-music
description: Use when creating music with ControlDeck — BGM, title themes, battle tracks, loops, stingers, jingles. Triggers on "音楽", "BGM", "曲を作って", "テーマ曲", "music", music.generate. Decide scene, mood, BPM, instruments, structure and loop condition BEFORE calling the tool.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# 音楽

実行先は `sonic.generate`（SonicForge）、task は `music.generate`。

## 呼ぶ前に決めること

| 項目 | 決め方 |
| --- | --- |
| 使用場面 | タイトル、探索、戦闘、勝利、静かな場面 |
| 曲調 | 何を感じさせたいか。緊張、高揚、寂しさ |
| BPM | 場面の速さに合わせる。戦闘は速く、探索は遅く |
| 楽器 | 主になる音色を 2〜3 挙げる |
| 展開 | 一定で回すのか、盛り上がりを作るのか |
| 長さ | 秒 |
| ボーカル | 要るか。要らなければ `instrumental: true` |
| ループ | 繰り返すのか、1 回で終わるのか |

## 呼び方

```json
{
  "task": "music.generate",
  "input": {
    "prompt": "tense orchestral battle theme, driving strings and low brass, relentless",
    "duration_sec": 60,
    "instrumental": true,
    "bpm": 140
  },
  "quality": "balanced"
}
```

## 引数と願望を分ける

`bpm` と `instrumental` は引数として効く。**ループの繋がり（頭と尻が自然に繋がるか）は
引数では保証されない。** プロンプトに「loopable」と書いても、それは希望であって
仕様ではない。出来上がりを 2 周聴いて、繋ぎ目を自分で確かめる。繋がらなければ、
使う側でフェードするか、繋がる長さに切り直す。

## 待ち方

`sonic.generate` はすぐ job を返す。`sonic.inspect` で `state` が `succeeded` に
なるまで待ち、`result.asset_id` を受け取る。**待たずに次を投げない。**

## 場面ごとに分けて作る

1 曲で全部を賄おうとしない。タイトル・探索・戦闘・勝利は別の曲として作り、
楽器と調を揃えることで統一感を出す。同じ `prompt` の骨格を使い、場面ごとの
語（tense / calm / triumphant）と BPM だけ変えるとまとまりやすい。

## 出来上がったら

`sonic.pack` で置く。効果音と一緒に鳴らして、音量と帯域がぶつからないかを見る。
BGM が主張しすぎて効果音が埋もれるのは、単体で聴いている間は気付けない。
