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

# 精度の段（設定の「モデルの精度」小・中・大）。部品と版（リビジョン）を固定する。
#   small  SDXS-512 DreamShaper（OpenVINO・1 ステップ・ガイダンス無し）+ TAESD。約 1.7GB。既定。
#   medium LCM-Dreamshaper v7（int8・4 ステップ・ガイダンスは w の埋め込み・本物の VAE）。約 1.0GB。
#   large  SDXL-Lightning 2 ステップ（int8・ガイダンス無し）+ TAESD-XL。約 4.2GB。
MODEL_REPOS: dict[str, dict] = {
    "sdxs-ds": {
        "repo": "XXX2333/sdxs-512-dreamshaper-openvino", "revision": "8bc3863149b374883150dc5504d890ef00cab0fb",
        "patterns": ["text_encoder/*", "unet/*", "tokenizer/merges.txt", "tokenizer/vocab.json", "model_index.json"],
    },
    "taesd": {
        "repo": "rupeshs/taesd-ov", "revision": "035ad7cc7e7258b69378894821a3e5c52cd4b547",
        "patterns": ["vae_decoder/*"],
    },
    "lcm-dreamshaper-int8": {
        "repo": "OpenVINO/LCM_Dreamshaper_v7-int8-ov", "revision": "340a03e5488f36f7f222e6efa29819850ad7e4be",
        "patterns": ["text_encoder/openvino_model.*", "unet/openvino_model.*", "vae_decoder/openvino_model.*",
                     "tokenizer/merges.txt", "tokenizer/vocab.json", "model_index.json"],
    },
    "sdxl-lightning-int8": {
        "repo": "rupeshs/SDXL-Lightning-2steps-openvino-int8", "revision": "347b21e571d7b8a716af83f64d68c5c028c51c56",
        "patterns": ["text_encoder/openvino_model.*", "text_encoder_2/openvino_model.*", "unet/openvino_model.*",
                     "tokenizer/merges.txt", "tokenizer/vocab.json", "tokenizer_2/merges.txt", "tokenizer_2/vocab.json", "model_index.json"],
    },
    "taesdxl": {
        "repo": "rupeshs/taesdxl-openvino", "revision": "299351c6056dc4435def142e2fda503e7189cc6f",
        "patterns": ["vae_decoder/*"],
    },
}
TIERS = ("small", "medium", "large")
# 段ごとの仕様。models は取得する部品（MODEL_REPOS の名前）。size は 16:9 に切り出す前の生成の大きさ（幅, 高さ。8 の倍数・潜在が 8 で割れる）。
# time_hint は 1 枚の目安（16 スレッド・読み込み別）、megabytes は取得の大きさ。画面に出す。guidance は 0 なら無し（ガイダンス無しの蒸留モデル）。
TIER_SPECS: dict[str, dict] = {
    "small": {"label": "小", "kind": "sd15-1step", "models": ["sdxs-ds", "taesd"], "steps": 1, "guidance": 0.0, "size": (512, 512),
              "out": (512, 288), "time_seconds": 1, "megabytes": 1700, "job_timeout": 900, "max_per_run": 30, "ram_gb": 3,
              "note": "SDXS-512 DreamShaper。速いが人物や構図は崩れやすい"},
    "medium": {"label": "中", "kind": "lcm", "models": ["lcm-dreamshaper-int8"], "steps": 4, "guidance": 8.0, "size": (512, 288),
               "out": (512, 288), "time_seconds": 2, "megabytes": 1000, "job_timeout": 900, "max_per_run": 30, "ram_gb": 5,
               "note": "LCM-Dreamshaper v7（int8・4 ステップ）。小より物の形と質感が自然"},
    "large": {"label": "大", "kind": "sdxl-lightning", "models": ["sdxl-lightning-int8", "taesdxl"], "steps": 2, "guidance": 0.0, "size": (1024, 576),
              "out": (1024, 576), "time_seconds": 4, "megabytes": 4200, "job_timeout": 1200, "max_per_run": 20, "ram_gb": 9,
              "note": "SDXL-Lightning（int8・2 ステップ・1024px）。いちばん精細で、題に合った絵になりやすい"},
}
# 揃っていれば「取得済み」とみなすファイル（models のフォルダからの相対）。段ごと
TIER_FILES: dict[str, tuple[str, ...]] = {
    "small": (
        "sdxs-ds/text_encoder/openvino_model.xml", "sdxs-ds/text_encoder/openvino_model.bin",
        "sdxs-ds/unet/openvino_model.xml", "sdxs-ds/unet/openvino_model.bin",
        "sdxs-ds/tokenizer/vocab.json", "sdxs-ds/tokenizer/merges.txt",
        "taesd/vae_decoder/openvino_model.xml", "taesd/vae_decoder/openvino_model.bin",
    ),
    "medium": (
        "lcm-dreamshaper-int8/text_encoder/openvino_model.xml", "lcm-dreamshaper-int8/text_encoder/openvino_model.bin",
        "lcm-dreamshaper-int8/unet/openvino_model.xml", "lcm-dreamshaper-int8/unet/openvino_model.bin",
        "lcm-dreamshaper-int8/tokenizer/vocab.json", "lcm-dreamshaper-int8/tokenizer/merges.txt",
        "lcm-dreamshaper-int8/vae_decoder/openvino_model.xml", "lcm-dreamshaper-int8/vae_decoder/openvino_model.bin",
    ),
    "large": (
        "sdxl-lightning-int8/text_encoder/openvino_model.xml", "sdxl-lightning-int8/text_encoder/openvino_model.bin",
        "sdxl-lightning-int8/text_encoder_2/openvino_model.xml", "sdxl-lightning-int8/text_encoder_2/openvino_model.bin",
        "sdxl-lightning-int8/unet/openvino_model.xml", "sdxl-lightning-int8/unet/openvino_model.bin",
        "sdxl-lightning-int8/tokenizer/vocab.json", "sdxl-lightning-int8/tokenizer/merges.txt",
        "sdxl-lightning-int8/tokenizer_2/vocab.json", "sdxl-lightning-int8/tokenizer_2/merges.txt",
        "taesdxl/vae_decoder/openvino_model.xml", "taesdxl/vae_decoder/openvino_model.bin",
    ),
}
REQUIRED_FILES = TIER_FILES["small"]   # 旧名（小の部品）
QUALITY = 70
_BOS, _EOS = 49406, 49407


def download(models_dir: Path, tier: str = "small") -> None:
    """段の部品を取得する（取得済みは飛ばす）。Hugging Face の公式から、版を固定して。"""
    from huggingface_hub import snapshot_download

    if tier not in TIER_SPECS:
        raise SystemExit(f"知らない段です: {tier}")
    models_dir.mkdir(parents=True, exist_ok=True)
    for name in TIER_SPECS[tier]["models"]:
        spec = MODEL_REPOS[name]
        snapshot_download(spec["repo"], revision=spec["revision"], local_dir=str(models_dir / name), allow_patterns=spec["patterns"])
        cache = models_dir / name / ".cache"
        if cache.is_dir():
            import shutil

            shutil.rmtree(cache, ignore_errors=True)
    missing = [f for f in TIER_FILES[tier] if not (models_dir / f).is_file()]
    if missing:
        raise SystemExit(f"取得したのにファイルが足りません: {missing[0]}")


def _tokenizer(root: Path):
    """CLIP の BPE（vocab.json・merges.txt）。transformers を使わず tokenizers だけで組む。"""
    from tokenizers import Regex, Tokenizer, decoders, models, normalizers, pre_tokenizers

    bpe = models.BPE.from_file(str(root / "vocab.json"), str(root / "merges.txt"), unk_token="<|endoftext|>", end_of_word_suffix="</w>")
    tok = Tokenizer(bpe)
    tok.normalizer = normalizers.Sequence([normalizers.NFC(), normalizers.Replace(Regex(r"\s+"), " "), normalizers.Lowercase()])
    pattern = Regex(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+")
    tok.pre_tokenizer = pre_tokenizers.Sequence([pre_tokenizers.Split(pattern, "removed", invert=True), pre_tokenizers.ByteLevel(add_prefix_space=False)])
    tok.decoder = decoders.ByteLevel()
    return tok


def _ids(tok, text: str, pad: int = _EOS) -> list[int]:
    body = tok.encode(text).ids[:75]
    return [_BOS, *body, _EOS, *([pad] * (75 - len(body)))]


def item_seed(item: dict) -> int:
    """記事ごとに違う絵になるよう、seed は記事の id から（job に seed があればそれ）。"""
    return int(item.get("seed", item["id"])) & 0xFFFFFFFF


def _alphas() -> "np.ndarray":
    import numpy as np

    betas = np.linspace(0.00085 ** 0.5, 0.012 ** 0.5, 1000) ** 2
    return np.cumprod(1.0 - betas)


def lcm_timesteps(steps: int) -> list[int]:
    """LCMScheduler（original_inference_steps=50・leading）の時刻。4 ステップなら 999, 759, 519, 279。"""
    origin = [(i + 1) * 20 - 1 for i in range(50)]
    skip = len(origin) // steps
    return origin[::-1][::skip][:steps] if skip else origin[::-1][:steps]


def guidance_embedding(w: float, dim: int = 256):
    import numpy as np

    w = w * 1000.0
    half = dim // 2
    freq = np.exp(np.arange(half, dtype=np.float32) * -(np.log(10000.0) / (half - 1)))
    emb = w * freq
    return np.concatenate([np.sin(emb), np.cos(emb)]).astype(np.float32)[None, :]


def _crop_save(picture, out_dir: Path, item_id: int, out_size: tuple[int, int]) -> None:
    """picture は (H, W, 3) の 0〜1。中央を 16:9 に切り出して webp で保存。"""
    import numpy as np
    from PIL import Image

    out_w, out_h = out_size
    h, w = picture.shape[:2]
    top, left = max(0, (h - out_h) // 2), max(0, (w - out_w) // 2)
    arr = (np.clip(picture[top:top + out_h, left:left + out_w], 0.0, 1.0) * 255).astype(np.uint8)
    target = out_dir / f"{int(item_id)}.webp"
    tmp = target.with_suffix(".tmp")
    Image.fromarray(arr).save(tmp, format="WEBP", quality=QUALITY, method=4)
    os.replace(tmp, target)


class _Pipeline:
    """段ごとの生成（読み込みは 1 度だけ）。run(prompt, seed) が (H, W, 3) の 0〜1 を返す。"""

    def __init__(self, tier: str, models: Path, threads: int) -> None:
        import openvino as ov

        self.spec = TIER_SPECS[tier]
        self.kind = self.spec["kind"]
        core = ov.Core()
        cfg = {"INFERENCE_NUM_THREADS": int(threads), "PERFORMANCE_HINT": "LATENCY"}

        def load(rel: str):
            return core.compile_model(str(models / rel / "openvino_model.xml"), "CPU", cfg)

        self.a = _alphas()
        self.w, self.h = self.spec["size"]
        if self.kind == "sdxl-lightning":
            root = models / "sdxl-lightning-int8"
            self.te1, self.te2 = load("sdxl-lightning-int8/text_encoder"), load("sdxl-lightning-int8/text_encoder_2")
            self.unet = load("sdxl-lightning-int8/unet")
            self.decoder = load("taesdxl/vae_decoder")
            self.tok1, self.tok2 = _tokenizer(root / "tokenizer"), _tokenizer(root / "tokenizer_2")
        else:
            name = "sdxs-ds" if self.kind == "sd15-1step" else "lcm-dreamshaper-int8"
            root = models / name
            self.te = load(f"{name}/text_encoder")
            self.unet = load(f"{name}/unet")
            self.decoder = load("lcm-dreamshaper-int8/vae_decoder" if self.kind == "lcm" else "taesd/vae_decoder")
            self.tok = _tokenizer(root / "tokenizer")

    def run(self, prompt: str, seed: int):
        import numpy as np

        rng = np.random.default_rng(seed)
        latent = rng.standard_normal((1, 4, self.h // 8, self.w // 8)).astype(np.float32)
        if self.kind == "sd15-1step":
            hidden = self.te(np.array([_ids(self.tok, prompt)], dtype=np.int64))[0]
            eps = self.unet(self._feed(latent, 999, hidden))[0]
            a = float(self.a[999])
            x0 = ((latent - np.sqrt(1.0 - a) * eps) / np.sqrt(a)).astype(np.float32)
        elif self.kind == "lcm":
            hidden = self.te(np.array([_ids(self.tok, prompt)], dtype=np.int64))[0]
            x0 = self._lcm(latent, hidden, rng) / 0.18215    # 本物の VAE の復号は潜在の倍率を戻す（TAESD は不要）
        else:
            x0 = self._sdxl(latent, prompt)
        pic = self.decoder(x0)[0][0].transpose(1, 2, 0)
        return np.clip((pic + 1.0) / 2.0, 0.0, 1.0)

    def _feed(self, latent, t: int, hidden, **extra):
        import numpy as np

        feed = {}
        for port in self.unet.inputs:
            name = port.get_any_name()
            if name == "sample":
                feed[name] = latent
            elif name == "timestep":
                feed[name] = np.array(t, dtype=np.int64)
            elif name == "encoder_hidden_states":
                feed[name] = hidden
            else:
                feed[name] = extra[name]
        return feed

    def _lcm(self, latent, hidden, rng):
        """LCMScheduler の手順（epsilon 予測・境界条件つき）。ガイダンスは w の埋め込みで入れる（否定のプロンプトは使えない）。"""
        import numpy as np

        cond = guidance_embedding(float(self.spec["guidance"]) - 1.0)
        steps = lcm_timesteps(int(self.spec["steps"]))
        x = latent
        for i, t in enumerate(steps):
            eps = self.unet(self._feed(x, t, hidden, timestep_cond=cond))[0]
            a_t = float(self.a[t])
            x0 = (x - np.sqrt(1.0 - a_t) * eps) / np.sqrt(a_t)
            scaled = t * 10.0
            c_skip = 0.25 / (scaled ** 2 + 0.25)
            c_out = scaled / np.sqrt(scaled ** 2 + 0.25)
            denoised = c_out * x0 + c_skip * x
            if i + 1 < len(steps):
                a_prev = float(self.a[steps[i + 1]])
                noise = rng.standard_normal(x.shape).astype(np.float32)
                x = (np.sqrt(a_prev) * denoised + np.sqrt(1.0 - a_prev) * noise).astype(np.float32)
            else:
                x = denoised.astype(np.float32)
        return x

    def _sdxl(self, latent, prompt: str):
        """SDXL-Lightning 2 ステップ（EulerDiscrete・trailing・ガイダンス無し）。"""
        import numpy as np

        out1 = self.te1(np.array([_ids(self.tok1, prompt)], dtype=np.int32))
        out2 = self.te2(np.array([_ids(self.tok2, prompt, pad=0)], dtype=np.int64))
        hs1 = [out1[o] for o in sorted((o for o in self.te1.outputs if o.get_any_name().startswith("hidden_states.")), key=lambda o: int(o.get_any_name().split(".")[1]))]
        hs2 = [out2[o] for o in sorted((o for o in self.te2.outputs if o.get_any_name().startswith("hidden_states.")), key=lambda o: int(o.get_any_name().split(".")[1]))]
        hidden = np.concatenate([hs1[-2], hs2[-2]], axis=-1).astype(np.float32)
        pooled = next(out2[o] for o in self.te2.outputs if o.get_any_name() == "text_embeds").astype(np.float32)
        time_ids = np.array([[self.h, self.w, 0, 0, self.h, self.w]], dtype=np.float32)
        steps = int(self.spec["steps"])
        ts = [round(1000 - i * (1000 / steps)) - 1 for i in range(steps)]    # trailing: 999, 499
        sig = [float(np.sqrt((1.0 - self.a[t]) / self.a[t])) for t in ts] + [0.0]
        x = latent * np.sqrt(sig[0] ** 2 + 1.0)
        for i, t in enumerate(ts):
            scaled = (x / np.sqrt(sig[i] ** 2 + 1.0)).astype(np.float32)
            eps = self.unet(self._feed(scaled, t, hidden, text_embeds=pooled, time_ids=time_ids))[0]
            x = (x + (sig[i + 1] - sig[i]) * eps).astype(np.float32)
        return x


def generate(job: dict, emit) -> int:
    """job の記事を順に生成して out_dir/<id>.webp へ。emit(dict) で 1 記事ごとに結果を返す。成功した件数。
    job["tier"]（small・medium・large。無ければ small）の部品を 1 回だけ読み込む。"""
    tier = str(job.get("tier") or "small")
    if tier not in TIER_SPECS:
        raise ValueError(f"知らない段です: {tier}")
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + float(job.get("deadline_seconds", 600))
    pipe = _Pipeline(tier, Path(job["models_dir"]), int(job["threads"]))
    done = 0
    for item in job["items"]:
        if time.monotonic() >= deadline:
            emit({"id": item["id"], "ok": False, "error": "deadline"})
            continue
        try:
            started = time.monotonic()
            picture = pipe.run(str(item["prompt"]), item_seed(item))
            _crop_save(picture, out_dir, int(item["id"]), TIER_SPECS[tier]["out"])
            done += 1
            emit({"id": item["id"], "ok": True, "seconds": round(time.monotonic() - started, 2)})
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で残りを止めない
            emit({"id": item["id"], "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return done


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "download":
        download(Path(argv[2]), argv[3] if len(argv) >= 4 else "small")
        return 0
    if len(argv) >= 2 and argv[1] == "generate":
        job = json.loads(sys.stdin.read())
        generate(job, lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
        return 0
    print("usage: imagegen_worker.py generate | download <models_dir> [small|medium|large]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
