# TTS エンジンの載せ替え（GPT-SoVITS / Style-Bert-VITS2）

最終更新: 2026-09-05
対象: SonicForge `worker_packs/`、`backend/sonicforge/workers.py`
状態: 調査完了・実装未着手

## 0. なぜ載せ替えるのか

現行の Qwen3-TTS は**実時間より遅く、速くする手立てが無い**ことが分かった。

```text
                        生成/音声      実時間比
Qwen3-TTS（実機実測）      1.77         0.56 倍   ← 実時間より遅い
GPT-SoVITS v2 ProPlus      0.028        36 倍     RTX 4060 Ti（公表値）
                           0.014        71 倍     RTX 4090（公表値、4分を 3.36 秒）
```

打ち切りの根拠は `plan-tts-latency.md` にある。要点だけ再掲する。

- 時間の 71% は波形合成（`Qwen3TTSTokenizerV2Decoder`）にあり、**GPU 使用率は中央値
  100%**。計算律速なので、カーネル起動を減らす手（CUDA/HIP graphs、torch.compile）が
  効く余地が無い（decode 45.32 秒のあいだ 456 標本を取得）
- attention は既に sdpa が選ばれており最良。eager は 2.7 倍遅い
- `non_streaming_mode=False` は公式が「97ms first-packet」と謳うが、実測では 1.8 倍遅い
- プロセス常駐化は実施済み（v0.5.5）。2 回目以降が約半分になったが、それが上限

**この機械が遅いのではない。** RTX 4090 の公式実装ベースラインも 0.82 倍速で、
実装がそういうものである。

## 1. 手順（この順に進める）

### 手順 1: GPT-SoVITS を R9700 で動かす（最優先）

**まず推論が 1 回通ることだけを確かめる。** 統合はその後で考える。

```text
上流         RVC-Boss/GPT-SoVITS
             souten-yd/GPTSoVITS は前処理ツール（UVR5 分離など）だけで、
             推論コードは含まない。Dockerfile.runpod も CUDA 前提なので使わない
環境         SonicForge の speech-rocm ランタイムに相乗りせず、別の venv を作る
             既存のランタイムを壊さないため
torch        /data1tb/ControlDeckMediaForge/runtimes/rocm-torch/.venv の
             2.10.0+rocm7.2.1（gfx1201 で動作確認済み）を .pth で共有してよい
```

**確かめること**

```text
1  推論が最後まで通り、wav が出る（秒数まで確認する）
2  温まった状態の生成時間と音声秒から 実時間比 を出す
3  ピーク VRAM（torch.cuda.max_memory_allocated）
4  GPU 使用率（rocm-smi を 0.05 秒間隔で標本化）
```

**判断基準**

```text
実時間比 5 倍以上   → 採用に進む（手順 3 へ）
実時間比 1〜5 倍    → Qwen3-TTS より速いので候補。音質と合わせて判断
実時間比 1 倍未満   → 乗り換える意味が無い。手順 2 へ
```

**予想される障害**（Qwen3-TTS で踏んだものと同種）

```text
float16 の桁溢れ    logits に NaN が入り multinomial の assert が HIP 719 で落ちる。
                    bfloat16 にすれば直る（Qwen3-TTS で単体再現・実証済み）
dtype 混在          fp32 へ揃えて解かない。bf16 へ揃える（§1.1）。fp32 は
                    この GPU で 8 倍遅い
numpy の ABI 不一致  pyopenjtalk-dict は numpy 1.x 前提の wheel。上流の
                    pyopenjtalk 0.4.1 なら numpy 2.x で通る（実証済み）
dtype 混在          Half の入力に float の bias（下の SBV2 で実際に踏んだ）
```

### 手順 2: Style-Bert-VITS2 を R9700 で動かす

非自己回帰の VITS2 系で、原理的には最速級。**推論の一歩手前まで進めてある。**

```text
導入          pip install style-bert-vits2 で入る（torch は .pth で共有）
モデル        litagin/style_bert_vits2_jvnv の jvnv-F1-jp
              ファイル名は jvnv-F1-jp_e160_s14000.safetensors（README と違う）
解決済み      numpy ABI → pip install --no-deps pyopenjtalk（0.4.1）で通る
dtype 混在    RuntimeError: Input type (c10::Half) and bias type (float)
              should be the same
              → **bfloat16 へ揃えて解くこと。fp32 へ落としてはいけない**（§1.1）
参考          souten-yd/Style-Bert-VITS2 は上流 litagin02 の master 追従で、
              独自の改変は無い。上流をそのまま使ってよい
```

### 1.1 dtype は bfloat16 へ揃える（fp32 で解かない）

RDNA4 の行列演算器（WMMA）は fp16 / bf16 / fp8 / int8 を加速する。**fp32 の行列積は
ベクタ ALU に落ちる。** 実測（gfx1201 / R9700、4096² の行列積、2026-09-05）:

```text
float32     10.40 ms    13.2 TFLOPS   基準
bfloat16     1.31 ms   105.2 TFLOPS   8.0 倍
float16      1.28 ms   107.0 TFLOPS   8.1 倍
```

**dtype 混在を fp32 へ揃えて解くと、この GPU の演算器を丸ごと使わないことになる。**
実際 Style-Bert-VITS2 の最初の評価で BERT と VITS を fp32 へ揃えた結果、音声 4.621 秒に
10.522 秒（実時間比 0.439）となり、採用基準 0.56 に届かず不採用と判断された。その
判断は根拠が崩れている。

fp16 ではなく bf16 を選ぶ理由は、指数部が fp32 と同じ幅で桁溢れしないこと。fp16 では
logits が溢れて NaN になり、ROCm では `torch.multinomial` の assert が HIP 719 で
落ちる（Qwen3-TTS で単体再現済み）。SonicForge の whisper / qwen_tts も bf16 に
揃えてある。

**GPU 使用率 100% を「計算律速だから速くならない」の根拠にしない。** 使用率はカーネル
が走っていることしか示さず、効率は示さない。fp32 カーネルが 8 分の 1 の効率で走って
いても 100% になる。dtype を確かめてから律速を論じること。

判断基準は手順 1 と同じ。

### 手順 3: SonicForge のエンジンとして組み込む

速い方を `worker_packs/` に足す。**既存の Qwen3-TTS は消さない。** 切り替えられる
ようにして、実際に使って比べられる状態にする。

```text
worker_packs/<engine>/worker.py   既存の worker と同じ protocol
                                  stdin から 1 行 JSON、stdout に progress / result
                                  読み取りは sys.stdin.readline()（後述）
backend/sonicforge/workers.py     route() に engine を足す
                                  _WARM_ENGINES に入れてモデルを載せたままにする
backend/sonicforge/jobs.py        _resource_estimate に実測の VRAM を書く
                                  多めに言うと LLM が載っている間に弾かれる
```

**必ず守ること**

```text
readline を使う      `for raw in sys.stdin` は先読みバッファが埋まるか EOF まで
                     1 行目を返さない。常駐させると最初の要求で止まる
                     （MediaForge と SonicForge の両方で踏んだ）
bfloat16 を使う      float16 は logits が溢れて NaN になり、ROCm では
                     multinomial の assert が HIP 719 で落ちる
申告は実測値         _resource_estimate には測った値だけを書く。推定を書かない
失敗した worker は捨てる  状態が分からないものを使い回さない
```

## 2. 測り方（手順 1 と 2 で共通）

```text
環境      llama-server を停止して GPU を占有する（VRAM の取り合いを避ける）
文        「これは合成音声の試験です。実機での速さを測っています。」
手順      同じ呼び出しを 3 回行い、最後を採る（初回は MIOpen の調整が入る）
指標      生成秒 / 音声秒 / 実時間比 / ピーク VRAM / GPU 使用率
dtype     測定に使った dtype を必ず記録する。fp32 の数字は参考にならない
記録      docs/implementation-status.md に実測値だけを書く
```

**比較の基準値**（同じ条件で測った現行 Qwen3-TTS）

```text
生成 11.74 秒 / 音声 6.64 秒 / 実時間比 0.56 / ピーク VRAM 2.49 GiB
```

## 2.1 やり直しが要る評価

Style-Bert-VITS2 は fp32 で測られたため、結果が無効である。**bf16 で測り直す。**

```text
無効な記録   音声 4.621 秒に 10.522 秒、実時間比 0.439、採用基準 0.56 未満で不採用
理由         BERT と VITS を fp32 へ揃えて測った。この GPU で fp32 は 8 倍遅い
やること     dtype を bf16 に揃えて §2 の手順で測り直し、記録を差し替える
```

GPT-SoVITS 側も、測定に使った dtype を確認すること。fp32 なら同じく無効である。

## 3. 完了の条件

```text
1  採用したエンジンが SonicForge の worker として動き、TTS job が通る
2  実機で wav が出る（秒数を確認する）
3  実時間比が Qwen3-TTS の 0.56 を明確に上回る
4  _resource_estimate の VRAM が実測に基づく
5  ./sf.sh test が全部通る
6  既存の Qwen3-TTS 経路が壊れていない（切り替えて両方動く）
```

**3 を満たさないなら載せ替えない。** 速くならないなら、依存を増やすだけである。

## 4. 参考（既に分かっていること）

```text
ROCm 7.2 で gfx1201 は公式サポート対象
同じ torch ビルドで FLUX 画像生成、Whisper ASR、Qwen3-TTS はすべて動く
  → ROCm や gfx1201 が原因で動かない、ということは考えにくい
vLLM-Omni は ROCm 対応を謳うが CI は MI300X（CDNA3）で、RDNA4 の記載が無い
  素の vLLM は gfx1201 で動かず、有志の patched イメージ（vllm-radiance）は
  experimental かつテキスト LLM のみ。音声は対象外なので、この線は追わない
```
