---
name: controldeck-sound-effects
description: Use when creating sound effects or ambience with ControlDeck — impacts, footsteps, weapons, UI clicks, explosions, room tone, weather beds, loops. Triggers on "効果音", "SE", "環境音", "アンビエンス", "sound effect", "ambience", sonic.generate. Decide source, material, space and length BEFORE calling the tool.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# 効果音・環境音

実行先は `sonic.generate`（SonicForge）。task は `audio.sfx.generate` か
`audio.ambience.generate`。

## 呼ぶ前に決めること

効果音の指示は、名前だけでは決まらない。「剣の音」では、木の棒を叩いた音も
金属の擦れも同じ要求になる。次の 5 つを組み立ててから渡す。

| 項目 | 例 |
| --- | --- |
| 発生源 | 何が音を出しているか（金属の刃、木の扉、布の服） |
| 素材・動作 | どう当たったか（叩く、擦る、割れる、軋む） |
| 立ち上がり | 鋭いか、鈍いか |
| 余韻・空間 | 乾いた近接か、残響のある広い場所か |
| 長さ | 秒。短い当たり音は 0.5〜2 秒 |

`prompt` は英語のほうが安定する。日本語で頼まれても、この 5 つを英語の短い句で
並べる。SonicForge 側で正規化されるが、こちらで具体化しておくほど狙いに寄る。

## 呼び方

```json
{
  "task": "audio.sfx.generate",
  "input": {
    "prompt": "a short metallic impact, dry, close, small room",
    "duration_sec": 2
  },
  "quality": "fast"
}
```

環境音は `audio.ambience.generate` に変え、長さを伸ばす（`duration_sec` 20〜60）。
ループさせるなら、繋ぎ目が目立たない持続音（雨、空調、雑踏）を選ぶ。**打楽器的な
音をループ素材にしない。**

`quality` は `fast` / `balanced` / `quality`。試作は `fast` でよい。

## 待ち方

**`sonic.generate` はすぐ返る。返るのは job であって音ではない。**

```json
{"job_id": "job:...", "host_job_id": "..."}
```

`sonic.inspect` に `job_id` を渡して `state` が `succeeded` になるまで見る。
`result.asset_id` が出来上がった音である。生成し直す前に、必ず結果を聴取・確認する。

## バリエーション

同じ場面で何度も鳴る音（足音、着弾）は、1 つだけ作ると耳につく。`seed` を変えて
数本作り、使う側で切り替える。**プロンプトを変えて作り直すのではなく、seed で振る。**

## 引数と願望を分ける

長さは `duration_sec` で実際に効く。ループの可否や音の重なりは引数では指定できない
ので、**指定したから実現した扱いにしない。** 出来上がりを聴いて確かめる。

## 出来上がったら

`sonic.pack` で使う場所へ置く。ゲームに入れるなら、鳴る場面と重なる音（BGM、
他の SE）と一緒に聴いて、音量と帯域がぶつからないかを見る。
