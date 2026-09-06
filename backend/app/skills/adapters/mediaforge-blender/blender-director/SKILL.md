---
name: blender-director
description: Execute 3D asset requests through ControlDeck MediaForge. Use for Blender props, low-poly game assets, GLB, scene creation/editing, dimensions, polygon budgets, image materials, and staged production. This is the ControlDeck execution adapter, not BlenderMCP.
license: MIT
metadata:
  author: ControlDeck
  version: "2026.07.10-cd1"
  upstream: arjun988/blender-skills@8f778d2405a214b508d4c7d80742be8e43acdd52
---

# Blender Director — ControlDeck 実行版

この実行版は上流の制作知識をMediaForgeの型付き契約へ接続する。
**依頼が制作なら、計画を印字して終了せず、実際のMCP toolを呼んで成果物まで確認する。**
実行先は `controldeck_addons` の `media.scene.*`。BlenderMCPではない。
GUIの手順書、生成Python、shell操作を実行の代わりにしない。

## 最初の確認

1. 現在のMCP一覧から `media.capabilities`、`media.scene.create/edit/snapshot/export`、
   `media.job.status/cancel` の実toolを確認する。OpenCodeの表示名ではドットが
   アンダースコアへ変換される場合があるので、公開されている名前とschemaを使う。
2. `media.capabilities` を呼び、`3d.scene_recipe.state=available` と
   `schema_version=media-forge.scene-recipe@1`、`local_only=true` を確認する。
3. 未提供・権限不足・接続失敗なら具体的な前提不足を報告して止める。
   Blender GUIを手動起動する必要はない。基本環境の導入・修復はMediaForge設定へ案内する。

## 制作方針と実行

用途、メートル単位の寸法、形の特徴、triangle予算、色/粗さ、GLB出力を短く決める。
小物なら妥当な値を置いて進め、依頼にない高精細化やリギングを付け足さない。

1. `media.scene.create` のrecipeで主要な形状を組む。
2. 返った `job_id` を `media.job.status` で終端まで追跡する。受付は完成ではない。
3. succeededの結果から実 `scene_id` / revisionを取り、`media.scene.snapshot` で検査する。
4. 必要な修正だけ `media.scene.edit` へ。snapshotのcurrent revisionを `base_revision_id` に渡す。
5. 画像材質が必要なら既存画像Assetを `media.scene.material` へ渡す。
   新規画像は既存 `media.generate` を使い、成功した画像IDだけをbindingへ使う。
6. `media.scene.export` の `format=glb` で検証済みAssetを得る。
   **exportは同期のAsset参照で、新規Jobを作らない。Assetのjob_idは過去の履歴なのでポーリングしない。**
7. scene/revision/Asset ID、検証で得た寸法・triangles、未確認項目を報告する。
   project配置を頼まれた場合だけ現在projectのoutput grantを取得し、receiptまで確認する。

## 実行対応表

| 制作工程 | 型付きrecipe操作 |
| --- | --- |
| 主要形状 | `primitive.add`: cube / cylinder / cone / uv_sphere |
| 寸法・配置・回転 | `transform.set` |
| 面取り | `modifier.bevel` |
| 基本PBR色・粗さ | `material.set` |
| UV展開 | `uv.smart_project` |
| 照明 | `light.add`: area / point / sun |
| カメラ | `camera.add` |

`object_id` はrecipeの安定IDで、Blenderの表示名ではない。作成後も同じIDを使う。
一度に最大64操作。スキーマにない引数を創作しない。
`material.set` へ画像やpathを渡さず、画像Assetの割当ては `media.scene.material` のbindingを使う。

最小の実行可能なcreate例（本番では依頼に合わせる）:

```json
{"name":"Low-poly pedestal","recipe":{"operations":[
  {"type":"primitive.add","object_id":"base","primitive":"cube","name":"Base","dimensions":[0.5,0.5,0.1],"location":[0,0,0.05]},
  {"type":"primitive.add","object_id":"pillar","primitive":"cylinder","name":"Pillar","dimensions":[0.2,0.2,0.7],"location":[0,0,0.45],"vertices":12},
  {"type":"material.set","object_id":"pillar","base_color":[0.3,0.35,0.4,1],"roughness":0.7}
]}}
```

## 参照画像と品質

参考画像がある場合、見える形・比率・色・視点を先に整理し、主要形状とカメラから合わせる。
実際のviewer画像や利用可能な画像検査で比較できた場合だけ、見た目の一致を確認済みとする。
`media.scene.snapshot` は構造の検査でありスクリーンショットではない。
画像が読めない、視覚比較のtoolがない場合はNOT TESTEDと報告し、見た目を捏造しない。

上流の参考資料はこの実行版の2階層上の `upstream/skills/` に保持している。
計画時は `../../upstream/skills/references/polycount-budgets.md`、
参照画像の分析には `../../upstream/skills/references/reference-analysis-template.md` を参照できる。
他の94種の資料も制作知識として保持しているが、**そこにあるBlenderMCP/bpyの実行指示は適用しない**。
実行方法の正は本書と現在のMediaForge tool schemaだけである。

## できないこと・失敗時

- `execute_blender_code`、任意Python、任意operator、localhost:9876を呼ばない。
- sculpt/rig/animation/simulation/geometry nodes/任意mesh編集/FBX/USDは現行契約で未対応。
  求められたら制約を説明し、別の簡略物を元の依頼の完成品と偽らない。
- 外部サービス・モデル重み・Blender addonを自動導入しない。
- 失敗したJobの理由を読み、可能な場合だけ同じ入力と `retry_job_id` で再試行する。
  成功済みの制作を初めから繰り返さない。競合ならsnapshotを再取得する。
- 中止依頼は `media.job.cancel` で取消し、終端を確認する。
- `.blend` / GLBはimmutable Assetとして残る。直接filesystemへ書き込まない。
- G8 ZIPが必要なら `media.generate(operation=asset.pack, profile=3d.project.glb)` は
  **GLB1件だけ**をinput_assetsに渡す。画像は別Assetとして配置する。
