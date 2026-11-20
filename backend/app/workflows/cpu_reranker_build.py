"""ruri-v3-reranker-310m を ONNX（int8）へ変換する（一時の build venv の python -I で動く）。

引数: <重みのフォルダ> <出力フォルダ>
1. PyTorch で読み、ONNX（fp32、opset 17）へ書き出す
2. ONNX Runtime の動的量子化で int8 にする（per-channel。MLP の出力側だけは fp32 のまま）
3. 固定の 8 組で、PyTorch fp32 と int8 の点数の順が同じかを確かめる（違えば失敗にする）
最後に 1 行の JSON（点数の最大差・順位の一致）を出す。

2026-11-20 調査: PyTorch の quantize_dynamic でも int8 にはなるが、torch（約 700 MB）を実行環境に要る。
ONNX Runtime なら実行環境は onnxruntime・tokenizers・numpy だけで済む。"""
import json
import sys
from pathlib import Path

PAIRS = [
    ("横浜の明日の天気は？", "横浜市の天気予報 - 明日は晴れのち曇り、最高気温 18 度。"),
    ("横浜の明日の天気は？", "番組表【神奈川: 地上波】今日のテレビ番組の一覧です。"),
    ("横浜の明日の天気は？", "こんにちは。今日もよろしくお願いします。"),
    ("RAG とは何ですか", "検索拡張生成（RAG）は、検索した文書を根拠に LLM が回答する手法です。"),
    ("RAG とは何ですか", "東京タワーは 1958 年に完成した電波塔です。"),
    ("How do I list files in Linux?", "Use the ls command to list directory contents."),
    ("How do I list files in Linux?", "The recipe needs two cups of flour and an egg."),
    ("ありがとう", "横浜市の天気予報 - 明日は晴れ。"),
]


def main() -> int:
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    import numpy as np
    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from tokenizers import Tokenizer
    from transformers import AutoModelForSequenceClassification

    torch.set_num_threads(4)
    model = AutoModelForSequenceClassification.from_pretrained(str(source), attn_implementation="eager").eval()
    tokenizer = Tokenizer.from_file(str(source / "tokenizer.json"))
    pad_id = tokenizer.token_to_id("<pad>") or 0

    def encode(pairs):
        encoded = tokenizer.encode_batch(pairs)
        width = max(len(e.ids) for e in encoded)
        ids = np.full((len(pairs), width), pad_id, dtype=np.int64)
        mask = np.zeros((len(pairs), width), dtype=np.int64)
        for row, e in enumerate(encoded):
            ids[row, :len(e.ids)] = e.ids
            mask[row, :len(e.ids)] = 1
        return ids, mask

    ids, mask = encode(PAIRS[:2])
    fp32 = out / "model_fp32.onnx"
    torch.onnx.export(model, (torch.tensor(ids), torch.tensor(mask)), str(fp32),
                      input_names=["input_ids", "attention_mask"], output_names=["logits"],
                      dynamic_axes={"input_ids": {0: "batch", 1: "tokens"}, "attention_mask": {0: "batch", 1: "tokens"},
                                    "logits": {0: "batch"}},
                      opset_version=17, dynamo=False)
    # MLP の出力側（Wo）は入力（GeGLU の出力）に大きな外れ値があり、int8 にすると精度が落ちる。そこだけ fp32 のまま残す
    # （2026-11-20 実測 JQaRA nDCG@10: fp32 0.877、全部 int8 0.834、Wo を残す 0.875）
    import onnx

    graph = onnx.load(str(fp32), load_external_data=False).graph
    targets = [n.name for n in graph.node if n.op_type == "MatMul"
               and n.name.endswith(("Wqkv/MatMul", "Wo/MatMul", "Wi/MatMul")) and "/mlp/Wo/" not in n.name]
    if len(targets) < 50:
        raise SystemExit(f"int8 にする層が見つかりません（{len(targets)} 個）")
    quantize_dynamic(str(fp32), str(out / "model_int8.onnx"), weight_type=QuantType.QInt8, per_channel=True,
                     nodes_to_quantize=targets)
    fp32.unlink()

    ids, mask = encode(PAIRS)
    with torch.inference_mode():
        reference = model(input_ids=torch.tensor(ids), attention_mask=torch.tensor(mask)).logits[:, 0].numpy()
    session = ort.InferenceSession(str(out / "model_int8.onnx"), providers=["CPUExecutionProvider"])
    quantized = session.run(None, {"input_ids": ids, "attention_mask": mask})[0][:, 0]
    # 同じ質問の中で、点数の順が PyTorch と同じか
    same_order = True
    for query in {q for q, _ in PAIRS}:
        rows = [i for i, (q, _) in enumerate(PAIRS) if q == query]
        if sorted(rows, key=lambda i: -reference[i]) != sorted(rows, key=lambda i: -quantized[i]):
            same_order = False
    sigmoid = lambda x: 1.0 / (1.0 + np.exp(-x))  # noqa: E731
    diff = float(np.max(np.abs(sigmoid(reference) - sigmoid(quantized))))
    print(json.dumps({"same_order": same_order, "max_score_diff": round(diff, 4)}))
    return 0 if same_order and diff < 0.15 else 3


if __name__ == "__main__":
    sys.exit(main())
