# モデルの目録の調査（2026-11-28・Claude）

利用者（2026-11-28）「目録はまず大きな種類（Qwen・Hy4・Inkling など）を選ばせ、検閲・無検閲、量子化を選べるように。載るかどうかと容量を調査して、Strata・Maya・Radiance それぞれの目録を作って。llama.cpp は目録をやめて従来のファイル選択に戻す」。

調べ方（推測で書かない）:
- **Strata**: 本番に入っている Strata 0.2.23（control 0.1.70）の目録のコード（`strata_cli/launch/catalog.py` の `Catalog.rows()`）を、Strata 自身の Python で動かして書き出した。系列・量子化・ダウンロード量・必要メモリ・この PC での判定（fits / slower / tight / no）が本体の値。ControlDeck はこれをその場で読む（`strata_live.py`）。
- **Maya**: 本番の Maya 1.1.84 の probe（ControlDeck の `maya_s.catalog()`・本体の `serve/models.py`）。setup id・大きさ・必要メモリ・公開済みか。文書は本体の `docs/MODELS.md`。
- **Radiance・Sly-Radiance・無検閲版**: Hugging Face の公開 API（検索とファイル一覧。モデルは取得していない）。大きさは HF の一覧の合計（GB = 10^9 バイト）。動く版は各モデルの説明（README）の「Needs radiance x.y」と本番の版（Radiance 2.3.0・Sly 1.15.1）で確かめた。

## Strata 0.2.23（本体の目録・この PC: RAM 91.8 GB・VRAM 31.9 GB）
| 系列 | 量子化（ダウンロード GB / 必要メモリ GB / 判定） |
|---|---|
| Qwen3.8-Flash-Next（GSQ-RCO） | Q2_0 66.4/35.0 fits・IQ2_XS 68.0/36.5 fits・IQ3_XXS 75.8/43.9 fits・IQ3_S 83.6/51.3 fits |
| Swift 1.5 | IQ2_XS・IQ3_XXS・IQ3_S（Qwen と同じ大きさ） |
| Qwen3.8-Flash-Next Coder | IQ1_M 58.4/24.4 fits |
| Qwen3.8-Flash-Next（Unsloth） | UD-IQ1_M〜UD-Q3_K_XL・UD-IQ4_XS 93.7/60.5・UD-Q4_K_XL 111.3/78.0 fits・UD-Q5_K_XL 158.3/99.4 slower・UD-Q6_K_XL・Q8_0 188.2/129.3 slower |
| Qwen3.8-Flash-Next（Gyro） | Gyro-S 58.5/27.6・Gyro-M 92.0/35.0 |
| Qwen3.6-35B-A3B | UD-Q4_K_XL 22.9/22.3・UD-Q6_K_XL 32.6/31.3・Q8_0 37.8/36.2 |
| Ornith-1.5-35B-A3B（新しい系列） | Q4_K_M 21.7・Q5_K_M 25.3・Q6_K 29.2・Q8_0 37.8 |
| Tencent Hy4-Flash | SQ-64 130.5/61.8 fits・SQ-96 158.1/89.4 fits・SQ-128 182.2/113.1 slower |
| Inkling-2-Mini | SQ-20 20.6・SQ-15 15.5・SQ-10 11.7・GSQ-RCO-IQ3_S 12.6・UD-Q4_K_XL 18.8（UD-Q8_K_XL は提供終了） |
NVFP4 の系列は NVIDIA だけなので、この PC（AMD）では出さない。

無検閲版（Strata の公式の目録には無い。HF の一覧で量子化の作りと大きさが公式と同じものだけ）:
| 取得元 | 量子化（GB） | 根拠 |
|---|---|---|
| SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF（取得数 約 419 万） | Q2_0 66.8・IQ2_XS 68.5・IQ3_XXS 76.1・IQ3_S 83.9 | 公式 GSQ-RCO と同じ 4 量子化・ほぼ同じ大きさ。本番の /data2tb/LLM にも IQ3_S を取得済み |
| SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF | Q2_0 66.5・IQ2_XS 68.2・IQ3_XXS 75.8・IQ3_S 83.5 | 同上 |
| JoaoZaokk/Inkling-2-Mini-Heretic-GGUF | UD-Q4_K_XL 17.6（＋DSpark 0.4） | 本番で Strata に登録して実測（63〜79 tok/s、2026-11-23）。手元のファイル名・大きさが一致 |
見送り: Gyro の abliterated（agentionai）はファイルの作りが公式と違う（TQ2_0/TQ3_0）。Hy4-Flash の無検閲版（orcarouter・alesha-pro の GGUF）は Strata の Hy4 が .strata の pack で動くので使えない。

## Maya 1.1.84（本体の probe）
| モデル | 量子化（大きさ GB / 必要メモリ GB） |
|---|---|
| GLM-5.3-Flash | Maya-XS 73.4/73・Maya-S24 94.7/95・Maya-S 96.5/96・Maya-M 116/116・Maya-L 156.3/156・Maya-M-Derisked 116/116（拒否を減らす編集・無検閲寄り） |
| DeepSeek-V4.1-Flash | Maya-XS 282.4/121・Maya-S 500.8/186（Maya-M は未公開） |
| MiniMax-M3.1-Flash | Maya-XS 93.0/100・Maya-S 122.5/130 |
| Hy4-Flash（Tencent） | Maya-XS 108.1/52・Maya-S 116.3/64・Maya-M 131.8/76・Maya-L 152.9/100 |
Maya は使われるエキスパートを GPU と RAM に、残りを SSD から読むので、メモリを超えても動く（遅くなる）。
見送り: DeepSeek の abliterated（orcarouter・gated）は Maya の setup の量子化ではなく ControlDeck の登録の口に無い。Inkling-2-Mini・GLM-5.3（744B）は probe に無い。

## Radiance 2.3.0（StillDeadcode の .rad。文章の LLM だけ）
| モデル | 量子化（GB） |
|---|---|
| Qwen3.8-27B | IQR-V4 の 15 段（IQ2R 10.9〜IQ8R 29.6）・MXFP4 19.4・FP8 30.8・IQ8R 30.0・IQ8R+DFlash 30.9 |
| Qwen3.8-27B 無検閲 | FP8 30.8・IQ8R 30.9 |
| Swift 1.5（Qwen3.8-27B） | FP8 30.8・IQ8R 30.9 |
| Qwen3.8-Flash-Next | FP8+IQ4R MoE 122.0・IQR-V4 121.7（無検閲版も同じ大きさ・Swift 版 122.0） |
| Qwen3.6-35B-A3B | FP8 37.2・IQ8R 37.6 |
| Inkling-2-Mini | IQ4R-V4 15.5（Radiance 2.3.0 から） |
| Gemma4-26B-A4B | DFlash FP8 36.4・DFlash I8 36.7 |
| MiniCPM5-2B | FP8 3.3・IQ8R 3.3・BF16 5.5 |
| TranslateGemma-27B（翻訳） | I8 30.3 |
| 試験的（作者が「試すための簡易変換」と書いたもの） | Flint IQ8R 40.7・Ling-3.0-tiny IQ8R 8.5・Muse-Glimmer-30B IQ8R+DFlash 38.3 |
画像・動画・音楽・音声の .rad（qwen-image・ltx・flux・krea・minimax-h3・music3・breeze-tts など）は LLM ではないので載せない。

## Sly-Radiance 1.15.1（vLLM。safetensors のフォルダ）
| モデル | 量子化（GB） |
|---|---|
| Qwen3.8-27B | Swift-1.5 heretic MXFP4（標準・無検閲寄り）19.8・Quark AWQ MXFP4（AMD）19.8・NVFP4（Unsloth）23.4・ParoQuant 18.8 |
| 先読み DFlash2 | W4A16 1.3 |
見送り: ThinkingCap-Qwen3.8-27B-MXFP4 は gated（規約の承諾が要る）で中身を確かめられない。

## 載るかの目安
- Strata・Maya は本体の判定（Strata は fits/slower/tight/no、Maya は必要メモリと RAM+VRAM の比較）。
- Radiance・Sly は重みを GPU に置くので、大きさ×1.15 が VRAM に収まれば「載る」、RAM と合わせて収まれば「ぎりぎり（遅い）」、それ以外は「載らない」。
