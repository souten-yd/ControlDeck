# Qwen3-TTS の遅さをどうするか（調査結果と実装計画）

最終更新: 2026-09-05
対象: SonicForge `worker_packs/qwen_tts`、`backend/sonicforge/workers.py`
状態: 調査完了・未着手（常駐化のみ v0.5.5 で実施済み）

## 0. 結論を先に

**CUDA/HIP graphs による高速化は、期待した効果が出ない見込みが高い。** 着手前に
§4 の「見送り条件」を確認し、満たすなら §5 の代替へ進むこと。

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

### 5.1 波形合成側を調べる（未着手・最優先）

全体の 71% がここにある。まだ何も測っていない。

```text
やること
  m.model の talker 以外（base_model 配下の codec / token2wav 相当）を
  1 メソッドずつ時間計測して、どの段が重いかを特定する
  scratchpad/speech/inner.py と同じ形の wrapper で足りる
判断基準
  単一の段が 40% 以上を占めるなら、その段だけを最適化する価値がある
```

### 5.2 プロセス常駐化（v0.5.5 で実施済み）

```text
1 プロセスへ 3 要求   17.96 → 9.46 → 11.78 秒
```

2 回目以降が約半分になった。`_WARM_ENGINES` に `tts.qwen3` / `asr.whisper` を
入れている。音楽と効果音は 1 回が数分かかる上に大きいので入れていない。

### 5.3 ROCm 版 flash-attn の導入（未着手）

起動時に `flash-attn is not installed. Will only run the manual PyTorch version.`
が出る。ただし本体の attention は既に sdpa を選んでいるので、この警告は
`core/tokenizer_25hz/vq/whisper_encoder.py` など**波形合成側の別経路**から来て
いる。5.1 でそこが重いと分かった場合にのみ意味がある。

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
