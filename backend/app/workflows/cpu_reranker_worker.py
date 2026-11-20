"""reranker の worker（専用 venv の python -I で動く。ControlDeck の import は使わない）。

引数: <モデルの ONNX> <tokenizer.json> <スレッド数> [gpu <一度に並べる件数> <コンパイル結果の置き場>]。
起動時に 1 回だけ読み、標準入力の 1 行 1 依頼に 1 行で答える。
gpu: ONNX Runtime の MIGraphX（AMD GPU）で fp16 計算する。形（件数 × 1024 トークン）を 1 つに固定する。MIGraphX は形ごとに
コンパイル（初回 30 秒前後）し、置き場に保存して次から使い回すが、形を 2 つ使うと結果が壊れた（2026-11-20 実測）。
依頼: {"query": str, "documents": [str, ...], "max_tokens": int} → 応答: {"scores": [float, ...]}（0〜1、sigmoid 後）
失敗: {"error": str}。標準入力が閉じたら終わる。"""
import json
import sys


def main() -> int:
    model_file, tokenizer_file, threads = sys.argv[1], sys.argv[2], int(sys.argv[3])
    gpu = len(sys.argv) > 4 and sys.argv[4] == "gpu"
    batch = int(sys.argv[5]) if gpu else 8
    width_fixed = 1024 if gpu else 0
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(tokenizer_file)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    providers = ([("MIGraphXExecutionProvider", {"device_id": 0, "migraphx_fp16_enable": True,
                                                  "migraphx_model_cache_dir": sys.argv[6]})] if gpu else []) + ["CPUExecutionProvider"]
    session = ort.InferenceSession(model_file, options, providers=providers)
    if gpu and session.get_providers()[0] != "MIGraphXExecutionProvider":
        print(json.dumps({"error": "AMD GPU（MIGraphX）を使えません"}), flush=True)
        return 2
    inputs = {i.name for i in session.get_inputs()}
    pad_id = tokenizer.token_to_id("<pad>") or 0
    print(json.dumps({"ready": True}), flush=True)

    for line in sys.stdin:
        try:
            request = json.loads(line)
            query, documents = str(request["query"]), [str(d) for d in request["documents"]]
            # 質問＋資料 1 件の上限。超えた分は長い方（ふつうは資料の後ろ）を切る。GPU は形を固定するので 1024 まで
            limit = max(64, min(int(request.get("max_tokens", 512)), width_fixed or 2048))
            tokenizer.enable_truncation(max_length=limit, strategy="longest_first")
            scores = [0.0] * len(documents)
            # 長さの近いもの同士でまとめると詰め物が減って速い。CPU は 8 件ずつが 16 件ずつより速かった（2026-11-20 実測）
            order = sorted(range(len(documents)), key=lambda i: len(documents[i]))
            for start in range(0, len(order), batch):
                rows = order[start:start + batch]
                encoded = tokenizer.encode_batch([(query, documents[i]) for i in rows])
                width = width_fixed or max(len(e.ids) for e in encoded)
                height = batch if gpu else len(rows)
                ids = np.full((height, width), pad_id, dtype=np.int64)
                mask = np.zeros((height, width), dtype=np.int64)
                for row, e in enumerate(encoded):
                    ids[row, :len(e.ids)] = e.ids
                    mask[row, :len(e.ids)] = 1
                logits = session.run(None, {k: v for k, v in (("input_ids", ids), ("attention_mask", mask),
                                                                ("token_type_ids", np.zeros_like(ids))) if k in inputs})[0][:, 0]
                for row, index in enumerate(rows):
                    scores[index] = float(1.0 / (1.0 + np.exp(-float(logits[row]))))
            print(json.dumps({"scores": scores}), flush=True)
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で worker を落とさない
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
