"""ニュース専用の画像生成の台本（子プロセスで動く。ControlDeck 本体の venv では動かさない）。

2026-11-23 利用者「ニュースフィード用に限定してすごく軽量な画像生成モデルを使って…CPU と RAM の利用のみ…画像を生成したらすぐにアンロード
…連続で生成する必要がある場合は、一気に順次生成していく、毎回下ろしたりはせずに…品質は 2 の次で、イメージ画像を生成できたらいい」。

- 専用 venv（data/features/news-imagegen/venv）の python で `python -I imagegen_worker.py generate` と動かす。
  標準入力に JSON の指示（models・出力先・スレッド数・記事の一覧）を 1 つ渡し、標準出力に 1 記事 1 行の JSON で結果を返す。
- モデルは 1 回だけ読み込み、渡された記事を順に生成し、終わったらプロセスごと終える（= アンロード）。
- SDXS-512 DreamShaper（OpenVINO。1 ステップ・ガイダンス無し）と TAESD の軽い復号。GPU は使わない（OpenVINO の CPU のみ）。
- このファイルの先頭は標準ライブラリだけ（ControlDeck 本体からは定数を読むために import される）。重い部品は関数の中で読み込む。
- `python -I imagegen_worker.py download <models のフォルダ>` はモデルの取得（導入ボタンから呼ばれる。Hugging Face の公式からだけ）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 取得するもの。版（リビジョン）を固定する。合計 約 1.7GB。
MODEL_REPOS: dict[str, dict] = {
    "sdxs-ds": {
        "repo": "XXX2333/sdxs-512-dreamshaper-openvino", "revision": "8bc3863149b374883150dc5504d890ef00cab0fb",
        "patterns": ["text_encoder/*", "unet/*", "tokenizer/merges.txt", "tokenizer/vocab.json", "model_index.json"],
    },
    "taesd": {
        "repo": "rupeshs/taesd-ov", "revision": "035ad7cc7e7258b69378894821a3e5c52cd4b547",
        "patterns": ["vae_decoder/*"],
    },
}
# 揃っていれば「取得済み」とみなすファイル（models のフォルダからの相対）
REQUIRED_FILES = (
    "sdxs-ds/text_encoder/openvino_model.xml", "sdxs-ds/text_encoder/openvino_model.bin",
    "sdxs-ds/unet/openvino_model.xml", "sdxs-ds/unet/openvino_model.bin",
    "sdxs-ds/tokenizer/vocab.json", "sdxs-ds/tokenizer/merges.txt",
    "taesd/vae_decoder/openvino_model.xml", "taesd/vae_decoder/openvino_model.bin",
)
SIZE = 512                 # 生成は正方形（モデルの形が固定）。出力は中央を 16:9 に切り出す
OUT_W, OUT_H = 512, 288
QUALITY = 70
_BOS, _EOS = 49406, 49407


def download(models_dir: Path) -> None:
    from huggingface_hub import snapshot_download

    models_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in MODEL_REPOS.items():
        snapshot_download(spec["repo"], revision=spec["revision"], local_dir=str(models_dir / name), allow_patterns=spec["patterns"])
        shutil_cache = models_dir / name / ".cache"
        if shutil_cache.is_dir():
            import shutil

            shutil.rmtree(shutil_cache, ignore_errors=True)
    missing = [f for f in REQUIRED_FILES if not (models_dir / f).is_file()]
    if missing:
        raise SystemExit(f"取得したのにファイルが足りません: {missing[0]}")


def _tokenizer(root: Path):
    """CLIP の BPE（vocab.json・merges.txt）。transformers を使わず tokenizers だけで組む。"""
    from tokenizers import Regex, Tokenizer, decoders, models, normalizers, pre_tokenizers

    bpe = models.BPE.from_file(str(root / "tokenizer" / "vocab.json"), str(root / "tokenizer" / "merges.txt"),
                               unk_token="<|endoftext|>", end_of_word_suffix="</w>")
    tok = Tokenizer(bpe)
    tok.normalizer = normalizers.Sequence([normalizers.NFC(), normalizers.Replace(Regex(r"\s+"), " "), normalizers.Lowercase()])
    pattern = Regex(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+")
    tok.pre_tokenizer = pre_tokenizers.Sequence([pre_tokenizers.Split(pattern, "removed", invert=True), pre_tokenizers.ByteLevel(add_prefix_space=False)])
    tok.decoder = decoders.ByteLevel()
    return tok


def _ids(tok, text: str) -> list[int]:
    body = tok.encode(text).ids[:75]
    return [_BOS, *body, _EOS, *([_EOS] * (75 - len(body)))]


def generate(job: dict, emit) -> int:
    """job の記事を順に生成して out_dir/<id>.webp へ。emit(dict) で 1 記事ごとに結果を返す。成功した件数。"""
    import numpy as np
    import openvino as ov
    from PIL import Image

    models = Path(job["models_dir"])
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + float(job.get("deadline_seconds", 600))
    core = ov.Core()
    config = {"INFERENCE_NUM_THREADS": int(job["threads"]), "PERFORMANCE_HINT": "LATENCY"}
    root = models / "sdxs-ds"
    text_encoder = core.compile_model(str(root / "text_encoder" / "openvino_model.xml"), "CPU", config)
    unet = core.compile_model(str(root / "unet" / "openvino_model.xml"), "CPU", config)
    decoder = core.compile_model(str(models / "taesd" / "vae_decoder" / "openvino_model.xml"), "CPU", config)
    tok = _tokenizer(root)
    # 1 ステップ（t=999）。epsilon 予測なので x0 = (x - sqrt(1-a)·eps) / sqrt(a)
    betas = np.linspace(0.00085 ** 0.5, 0.012 ** 0.5, 1000) ** 2
    alpha = float(np.cumprod(1.0 - betas)[999])
    done = 0
    for item in job["items"]:
        if time.monotonic() >= deadline:
            emit({"id": item["id"], "ok": False, "error": "deadline"})
            continue
        try:
            hidden = text_encoder(np.array([_ids(tok, str(item["prompt"]))], dtype=np.int64))[0]
            rng = np.random.default_rng(int(item["id"]) & 0xFFFFFFFF)
            latent = rng.standard_normal((1, 4, SIZE // 8, SIZE // 8)).astype(np.float32)
            feed = {}
            for port in unet.inputs:
                name = port.get_any_name()
                feed[name] = latent if name == "sample" else np.array(999, dtype=np.int64) if name == "timestep" else hidden
            eps = unet(feed)[0]
            x0 = ((latent - np.sqrt(1.0 - alpha) * eps) / np.sqrt(alpha)).astype(np.float32)
            picture = decoder(x0)[0][0].transpose(1, 2, 0)
            picture = np.clip((picture + 1.0) / 2.0, 0.0, 1.0)
            top = (SIZE - OUT_H) // 2
            image = Image.fromarray((picture[top:top + OUT_H, :OUT_W] * 255).astype(np.uint8))
            target = out_dir / f"{int(item['id'])}.webp"
            tmp = target.with_suffix(".tmp")
            image.save(tmp, format="WEBP", quality=QUALITY, method=4)
            os.replace(tmp, target)
            done += 1
            emit({"id": item["id"], "ok": True})
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で残りを止めない
            emit({"id": item["id"], "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return done


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "download":
        download(Path(argv[2]))
        return 0
    if len(argv) >= 2 and argv[1] == "generate":
        job = json.loads(sys.stdin.read())
        generate(job, lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
        return 0
    print("usage: imagegen_worker.py generate | download <models_dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
