---
name: controldeck-3d-scene
description: Use when building or editing 3D scenes and props with ControlDeck's 3D Studio — blockout, primitives, materials, UVs, lighting, cameras, GLB export for games and web. Triggers on "3D", "モデル", "シーン", "GLB", "書き出し", media.scene.create, media.scene.edit. Decide purpose, scale, silhouette and export format BEFORE building.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# 3D シーン

実行先は `media.scene.*`（MediaForge の 3D Studio。中身は Blender）。
**BlenderMCP とは別物**で、ツール名も操作の粒度も違う。ここに書いてある操作だけが使える。

## 使える操作

recipe に並べる `type` はこれだけである。無い操作を書かない。

| type | できること |
| --- | --- |
| `primitive.add` | 基本形状を 1 つ足す（`cube` / `cylinder` / `cone` / `uv_sphere`。寸法・位置はメートル） |
| `transform.set` | 既存オブジェクトの位置・回転・拡縮を置き換える |
| `modifier.bevel` | 面取り（非破壊。width と segments に上限あり） |
| `material.set` | Principled BSDF の単純な材質。外部テクスチャは使わない |
| `uv.smart_project` | smart project で UV を作る |
| `light.add` | ライトを 1 つ足す（`area` / `point` / `sun`。`energy` 必須） |
| `camera.add` | カメラを 1 つ足して有効にする |

オブジェクトは `object_id`（小文字・ハイフン可）で参照する。**Blender 上の名前ではない。**
後続の操作は全てこの ID を使うので、意味の分かる名前を付ける（`body`, `roof`, `wheel-fl`）。

## 作る前に決めること

| 項目 | 決め方 |
| --- | --- |
| 用途 | ゲーム内か、web 表示か、静止画のためか |
| 寸法 | 実寸をメートルで。人の背丈 1.7m を基準にすると狂いにくい |
| シルエット | 遠目に何に見えるか。細部より先に決める |
| 可動部 | 後で動かす部分は別オブジェクトに分ける |
| 材質 | 色と粗さ。テクスチャは使えないので形と色で見せる |
| 書き出し | `glb`（ゲーム・web）か `blend`（続きを作る） |

## 順番

**シルエットとカメラを先に合わせる。** 細部を作り込んでから「思っていた見た目と違う」と
なると、作り直しになる。

1. `media.scene.create` で骨格だけ作る（primitive を数個、カメラ、ライト）
2. `media.scene.snapshot` で今の状態を確かめる
3. `media.scene.edit` で足す・直す。`base_revision_id` に直前の版を渡す
4. 材質は `media.scene.material`
5. `media.scene.export` で `glb` を取り出す

## 呼び方

```json
{
  "name": "watchtower",
  "recipe": {
    "operations": [
      {"type": "primitive.add", "object_id": "base", "primitive": "cube",
       "name": "Base", "dimensions": [3, 3, 0.4], "location": [0, 0, 0.2]},
      {"type": "primitive.add", "object_id": "post", "primitive": "cylinder",
       "name": "Post", "dimensions": [0.3, 0.3, 6], "location": [0, 0, 3]},
      {"type": "material.set", "object_id": "post",
       "base_color": [0.35, 0.25, 0.15, 1], "roughness": 0.8},
      {"type": "camera.add", "object_id": "cam", "name": "Cam",
       "location": [8, -8, 5], "rotation_degrees": [65, 0, 45]},
      {"type": "light.add", "object_id": "key", "light": "area",
       "name": "Key", "energy": 500, "location": [5, -5, 8]}
    ]
  }
}
```

## 版と待ち方

`media.scene.create` と `media.scene.edit` は **job を返す**。`media.job.status` で
終わるまで待つ。編集は必ず `base_revision_id` に直前の版を渡す。渡さずに投げると、
別の版の上に重ねようとして弾かれる。`media.scene.snapshot` が今の版を教える。

## 画像が要るとき

テクスチャや背景板が要るなら `media.generate`（`controldeck-image` の手順）で作り、
`constraints.scene_texture` でこのシーンへ返す。**画像生成は 3D シーンを直接は変えない。**
反映は材質の操作を通す。

## やらないこと

- BlenderMCP 前提の手順（`execute_blender_code` など）をここで使わない。存在しない
- 外部テクスチャを前提にした材質を組まない。`material.set` は色と粗さだけ
- ポリゴン数を上げて解決しようとしない。ここで作れるのは blockout の粒度である
