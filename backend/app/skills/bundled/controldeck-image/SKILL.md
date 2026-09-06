---
name: controldeck-image
description: Use when creating any image asset with ControlDeck — game backgrounds, title art, sprites, icons, emblems, UI elements, textures, character portraits. Triggers on "画像を作って", "背景", "タイトル画面", "アイコン", "スプライト", "generate an image", media.generate. Decide purpose, canvas, composition and safe areas BEFORE calling the tool.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# 画像制作

実行先は `media.generate`（MediaForge）。ローカル GPU で動く。

## この手順が要る理由

「横長の背景が欲しい」と書いたのに正方形が返ってくる、という失敗が実際に起きる。
`intent` に「wide landscape」と形容詞で書いても寸法は決まらないからで、**寸法は
`constraints.asset_brief` で決まる**。逆に brief を書けば、生成の前に画面が確定し、
出来上がりがそれと違えば job が失敗として返る。黙って違うものが返ることはない。

## 呼ぶ前に決めること

| 項目 | 決め方 |
| --- | --- |
| 主題 | 何が写っているか。`intent` に書く |
| 用途 | `role`（下表）。ここで既定の比と透過が決まる |
| 画面 | 使う側のレイアウトを読む。推測しない |
| 構図 | 被写体を画面のどこに置くか |
| 安全領域 | 題字や HUD を載せる場所。空けておく |
| 透過 | 何かの上に重ねるなら必須 |
| 統一 | 同じ世界の絵は `consistency_group` を揃える |

`role` は `background` / `key_visual` / `character_portrait` / `sprite` / `icon` /
`emblem` / `texture` / `ui_element` / `general`。`emblem` と `sprite` は重ねる前提なので
透過が既定で必須、`background` は横長・不透明が既定。

## 呼び方

```json
{
  "operation": "image.generate",
  "intent": "夜の街路。遠景に高層ビル、濡れた路面に光が映る",
  "constraints": {
    "asset_brief": {
      "role": "background",
      "target_surface": "game",
      "aspect_intent": "ratio",
      "aspect_ratio": "16:9",
      "alpha_intent": "forbidden",
      "consistency_group": "night-city",
      "safe_areas": [{"edge": "top", "fraction": 0.45, "purpose": "title and menu"}],
      "hard_constraints": ["no text in the image", "no watermark", "no border"]
    }
  },
  "output": {"format": "png", "count": 1}
}
```

- `intent` は主題と目的。**構造的な要求を形容詞で書かない**（brief に書く）
- 正確な画素数が要るときだけ `constraints.width` / `height`。これは全ての推論に優先する
- `model_policy` は `auto` のまま。利用者が速さや品質を明示したときだけ変える
- 文字を絵の中に描かせない。字は後から重ねる方が確実で、直しも効く

## 枚数

**候補が複数要るなら `output.count` で 1 回にまとめる。** 1 回の呼び出しが
「モデル読み込み 1 回」で N 枚出す。呼び分けると毎回読み直しになり、実測で
十数秒ずつ余計にかかる。

## 出来上がったら

`media.inspect` で寸法と透過を確かめ、使う場所へ `media.pack` で置く。
brief と違うものが出れば job が失敗し、理由（`canvas_mismatch`、`alpha_missing` 等）が
返る。**失敗理由を読まずに同じ呼び出しを繰り返さない。** 直すのは brief の側である。

## やらないこと

- 演出や表現の言い換えを LLM に別途頼まない。この経路は演出を挟まない設計で、
  挟むと画像モデルとの載せ替えが増えて待ち時間だけ伸びる
- 参考画像があるなら `inputs` に渡す。言葉で描写し直さない
