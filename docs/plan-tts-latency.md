# Qwen3-TTS の遅さをどうするか（調査結果と実装計画）

最終更新: 2026-09-05
対象: SonicForge `worker_packs/qwen_tts`、`backend/sonicforge/workers.py`
状態: 調査完了。**GPU 側の高速化は打ち切り**（§5.1 の実測による）

## 0. 結論を先に

**GPU 側の高速化は打ち切る。** 時間の 71% を占める波形合成（decoder）は
**GPU 使用率 100% の計算律速**で、カーネル起動の削減（CUDA/HIP graphs、
torch.compile）が効く余地が無い。残る 26% の LM デコードを速くしても、
全体は 1.4 倍が上限である。

やるべきことが残っているとすれば、計算量そのものを減らす方向（§5.5）である。

## 1. 現状の実測（2026-09-05、gfx1201 / R9700、GPU 占有、bfloat16）

```text
Qwen3-TTS 0.6B CustomVoice
  生成 6.24〜9.52 秒の音声に 11〜18 秒        RTF 0.41〜0.56
  固定費 7.44 秒 + 1.98 秒/音声秒（2 点あてはめ）

  文字数  6 →  音声 2.56 秒 / 生成 12.51 秒 / RTF 0.20
  文字数 52 →  音声 13.04 秒 / 生成 33.25 秒 / RTF 0.39
```

短い発話ほど固定費に食われる。

### 時間の内訳（ここが重要）

```text
全体            7.27 秒（音声 4.08 秒）
  talker.generate   2.11 秒   29%   ← LM のデコード
  残り              5.16 秒   71%   ← 波形合成（token2wav / codec）ほか
```

**LM デコードは 3 割しか占めない。** 上流の高速化実装（CUDA graphs）が狙うのは
この 3 割の側である。仮に talker が無限に速くなっても、全体は 1.4 倍にしかならない。

## 2. これは環境固有ではない

```text
faster-qwen3-tts の報告      RTX 4090 / 0.6B のベースライン RTF 0.82
この機械                     RTF 0.56
本家 issue #89               RTX 5090 で「10 倍遅い、GPU 使用率 4〜5%」
                             → not planned で終了
```

公式実装がもともと遅い。ROCm や gfx1201 の問題ではない（同じ torch ビルドで
FLUX 画像生成は正常に動く）。

## 3. 既に試して否定された選択肢（すべて実測）

```text
attention  sdpa（既定）        16.13 秒   既に最良が選ばれている
           eager               43.23 秒   2.7 倍遅い
           flash_attention_2   利用不可（未導入、ROCm 版の導入は別途調査）

non_streaming_mode=True   18.60 秒  RTF 0.41   既定の方が速い
non_streaming_mode=False  33.08 秒  RTF 0.21   1.8 倍遅い

torch.compile(mode="reduce-overhead")   改善せず（測定値が大きくばらつく）
```

公式ドキュメントは `non_streaming_mode=False` で「97ms first-packet」と書くが、
実測では逆に遅い。同ドキュメント自身が「現状は擬似ストリーミング」と認めており、
`wavs, sr` を一括で返す API のままなのでチャンクは取り出せない。

## 4. HIP graphs の見込みと見送り条件

### 捕捉自体はできる

```text
torch.cuda.CUDAGraph の捕捉   成功（gfx1201 / torch 2.10.0+rocm7.2.1）
大きなカーネル 50 個          1.02 倍（差が出ない）
小さなカーネル 500 個         1.29 倍
```

上流が CUDA で報告する 5.8 倍に対し、**この機械の合成試験では 1.29 倍**である。

### 見積り

```text
talker が 1.29 倍速くなると仮定すると
  2.11 秒 → 1.64 秒、全体 7.27 → 6.80 秒（1.07 倍）
```

**投じる工数に見合わない。** 加えて次の障壁がある。

- 捕捉には静的な形状と固定アドレスが要る。デコードは `DynamicCache` を使うので
  `StaticCache` への置き換えが前提になる（`modeling_qwen3_tts.py` の 1083 行と
  1487 行で `DynamicCache()` を直接生成している）
- site-packages の `qwen_tts` 本体に手を入れることになる。fork して vendoring
  するなら、パッケージ更新のたびに追従が要る

### 見送り条件

次のいずれかに当たるなら、この案は捨てて §5 へ進む。

1. 合成試験（`scratchpad/speech/graph.py` 相当）の speedup が 2 倍未満
2. `talker.generate` の占有率が 50% 未満
3. `StaticCache` への置き換えで出力が変わる（同じ seed で波形が一致しない）

**現時点で 1 と 2 の両方に当たっている。**

## 5. 代わりに効く順（推奨）

### 5.1 波形合成側（調査完了・打ち切り）

全体の 71% がここにあり、単一の段に集中していた。

```text
全体                        11.74 秒（音声 6.64 秒）
  talker.generate            3.02 秒  26%
  speech_tokenizer.decode    8.33 秒  71%
    └ decoder                8.33 秒  71%   ← ここに集中
        pre_transformer      0.007 秒
        pre_conv             0.000 秒
```

`Qwen3TTSTokenizerV2Decoder`（`core/tokenizer_12hz/modeling_qwen3_tts_tokenizer_v2.py`
の 824 行）である。12Hz のコードを 24kHz 波形へ約 2000 倍に引き伸ばす転置畳み込みの
列で、`pre_transformer` / `pre_conv` は実質ゼロなので `upsample` の ModuleList と
residual unit に時間が入っている。

**ここで打ち切る判断をした。** decode の実行中に GPU 使用率を測ったところ:

```text
decode 45.32 秒のあいだ、0.05 秒間隔で 456 標本
  使用率 中央値 100%
  使用率 最大   100%
```

**計算律速である。** カーネル起動の削減（graphs、torch.compile）が効く余地は無い。
本家 issue #89 の「GPU 使用率 4〜5%」とは状況が違う（あちらは Windows / CUDA で、
おそらく別の要因）。

自己回帰でないので graph 捕捉自体は容易だが、**空いている時間が無いので速くならない**。

### 5.2 プロセス常駐化（v0.5.5 で実施済み）

```text
1 プロセスへ 3 要求   17.96 → 9.46 → 11.78 秒
```

2 回目以降が約半分になった。`_WARM_ENGINES` に `tts.qwen3` / `asr.whisper` を
入れている。音楽と効果音は 1 回が数分かかる上に大きいので入れていない。

### 5.5 計算量を減らす（残っている唯一の方向・未着手）

decoder が計算律速である以上、速くするには計算そのものを減らすしかない。

```text
候補                        見込み                          確かめ方
fp16 / bf16 の見直し        既に bfloat16。fp16 は NaN で   —（済み）
                            落ちるので不可
量子化（int8 など）          畳み込み列に効く可能性         精度劣化を聴いて確かめる
                            上流に対応が無いので自前
チャンク分割 + 並列          長文で効く。短文では効かない   長文で decode を分割して
                            （固定費が増える）              wall time を比べる
より小さい decoder           上流が出していない             —
```

**どれも上流の実装に手を入れる必要があり、音質への影響を聴いて確かめることになる。**
現状の RTF 0.56 で困っていないなら、着手しない判断が妥当である。

### 5.3 ROCm 版 flash-attn の導入（未着手）

起動時に `flash-attn is not installed. Will only run the manual PyTorch version.`
が出る。ただし本体の attention は既に sdpa を選んでおり、警告は 25Hz tokenizer の
`whisper_encoder.py` から来ている。12Hz モデルではその経路を通らない。
**5.1 で decoder が計算律速と分かったので、attention の話は本筋ではない。**

### 5.4 vLLM-Omni（未調査）

`/v1/audio/speech/stream` の WebSocket を提供する。ROCm 対応状況が未確認で、
別サービスを常駐させる構成変更になるため、5.1 の結果次第。

## 6. 成果の測り方

同じ条件で測り直せるよう、次を固定する。

```text
モデル   Qwen3-TTS-12Hz-0.6B-CustomVoice、bfloat16、device_map="cuda:0"
文       「これは合成音声の試験です。時間の内訳を測っています。」
手順     同じ呼び出しを 2 回行い、2 回目を採る（初回は MIOpen の調整が入る）
指標     生成秒、音声秒、RTF、talker.generate の占有率
環境     llama-server を停止し GPU を占有する（VRAM の取り合いを避ける）
```

**改善したと言うには、全体の RTF が 0.56 から有意に上がっていること。**
talker だけが速くなっても全体が変わらないなら、それは改善ではない。

## 7. 実施済みと打ち切り

```text
実施   プロセス常駐化（SonicForge v0.5.5）
       1 プロセスへ 3 要求で 17.96 → 9.46 → 11.78 秒。2 回目以降が約半分

打ち切り  CUDA/HIP graphs      decoder が計算律速（使用率 100%）で効かない
          torch.compile        同上。実測でも改善しなかった
          attention の変更     既に sdpa が最良。eager は 2.7 倍遅い
          non_streaming_mode   False の方が 1.8 倍遅い

残る唯一の方向  §5.5 計算量そのものを減らす（量子化・チャンク分割）
                上流の実装に手を入れ、音質への影響を聴いて確かめる必要がある
```

現状の RTF 0.56 は RTX 4090 の公式実装ベースライン 0.82 と同程度で、**この機械が
遅いのではなく実装がそういうもの**である。困っていないなら着手しない判断が妥当。
