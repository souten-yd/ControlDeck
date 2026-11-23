"""話題の語を、タイトルと抜粋から素朴に取り出す（LLM は使わない）。

1. 既知の語の辞書（モデル名・ソフト名・GPU・会社）。表記ゆれは別名でそろえる。
2. 辞書に無い固有名詞らしい語: カタカナの連なり・大文字や数字を含む英数字（GLM-5.3・R9700 など）。
どちらも出てきた順に最大 MAX_TOPICS 語。好みの重み（profile.py）と話題の動き（trends）の単位になる。
"""
from __future__ import annotations

import re

MAX_TOPICS = 6

# 正規の表記 → 別名（小文字）。ASCII は語の境界で、日本語は部分一致で見る。小さな表なので足していく。
KNOWN: dict[str, tuple[str, ...]] = {
    # モデル
    "Qwen": ("qwen",), "Llama": ("llama",), "Gemma": ("gemma",), "DeepSeek": ("deepseek",), "Mistral": ("mistral", "mixtral"),
    "GLM": ("glm",), "Kimi": ("kimi",), "Phi": ("phi-3", "phi-4", "phi-5"), "MiniMax": ("minimax",), "gpt-oss": ("gpt-oss",),
    "Claude": ("claude",), "GPT": ("gpt-4", "gpt-5", "chatgpt"), "Gemini": ("gemini",), "Grok": ("grok",),
    "Whisper": ("whisper",), "FLUX": ("flux",), "Stable Diffusion": ("stable diffusion", "sdxl"),
    # ソフト
    "llama.cpp": ("llama.cpp", "ggml"), "Ollama": ("ollama",), "vLLM": ("vllm",), "OpenCode": ("opencode",),
    "ControlDeck": ("controldeck", "control deck"), "Strata": ("strata",), "Radiance": ("radiance",),
    "ComfyUI": ("comfyui",), "LM Studio": ("lm studio", "lmstudio"), "PyTorch": ("pytorch",), "ROCm": ("rocm",),
    "CUDA": ("cuda",), "Vulkan": ("vulkan",), "Linux": ("linux", "linux kernel"), "Ubuntu": ("ubuntu",),
    "Python": ("python",), "Rust": ("rust",), "Docker": ("docker", "podman"), "Wayland": ("wayland",),
    "KDE": ("kde", "plasma"), "GNOME": ("gnome",), "Mesa": ("mesa",), "GGUF": ("gguf",), "MCP": ("mcp",),
    "Hugging Face": ("hugging face", "huggingface"), "GitHub": ("github",), "Firefox": ("firefox",), "Chrome": ("chrome", "chromium"),
    "Windows": ("windows",), "Android": ("android",), "iPhone": ("iphone",),
    # ハードウェア
    "Radeon": ("radeon",), "R9700": ("r9700",), "RDNA": ("rdna",), "NVIDIA": ("nvidia", "geforce", "blackwell"),
    "RTX": ("rtx",), "Intel": ("intel",), "AMD": ("amd",), "Ryzen": ("ryzen", "zen 5", "zen5"), "Apple Silicon": ("apple silicon",),
    # 会社
    "OpenAI": ("openai",), "Anthropic": ("anthropic",), "Google": ("google",), "Meta": ("meta ai",), "Microsoft": ("microsoft",),
    "Apple": ("apple",), "xAI": ("xai",), "Alibaba": ("alibaba",), "Mozilla": ("mozilla",),
    # 日本語の語
    "ニュース速報": ("速報",), "セキュリティ": ("セキュリティ", "脆弱性"), "ゲーム": ("ゲーム",), "AI": ("生成ai", "人工知能", "生成 ai"),
}

_STOP = {
    "the", "and", "for", "with", "from", "new", "how", "why", "what", "this", "that", "http", "https", "www", "com",
    "ニュース", "アップデート", "アプリ", "サービス", "ソフト", "ソフトウェア", "ユーザー", "データ", "システム", "機能", "リリース",
    "コメント", "ページ", "サイト", "ブログ", "メール", "ツール", "モデル", "バージョン", "ポイント", "ランキング",
}
_KATAKANA = re.compile(r"[ァ-ヴー]{3,}")
_ASCII = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+_-]{1,23}(?<![.+_-])")
_VERSION = re.compile(r"^[vbV]?\d[\d.]*$|^[bv]\d+(\.\d+)*$")
# 辞書の語は ASCII の別名だけ境界つきの 1 本の正規表現にまとめる（毎回ループしない）
_ALIAS_ASCII = {a: canon for canon, aliases in KNOWN.items() for a in aliases if a.isascii()}
_ALIAS_OTHER = {a: canon for canon, aliases in KNOWN.items() for a in aliases if not a.isascii()}
_ALIAS_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(a) for a in sorted(_ALIAS_ASCII, key=len, reverse=True)) + r")(?![a-z])")


def _proper(token: str) -> bool:
    """辞書に無い英数字を固有名詞らしいとみなす条件: 数字か 2 文字目以降の大文字を含む（GLM-5.3・R9700・LiteLLM）。"""
    if _VERSION.match(token) or token.lower() in _STOP:
        return False
    return any(c.isdigit() for c in token) or any(c.isupper() for c in token[1:])


def extract_topics(title: str, summary: str = "", extra: tuple[str, ...] | list[str] = ()) -> list[str]:
    """話題の語（重複なし・出てきた順・最大 MAX_TOPICS）。extra は出どころ由来の語（リポジトリ名など）で先頭に置く。"""
    text = f"{title}\n{summary[:400]}"
    low = text.lower()
    found: dict[str, int] = {}
    for word in extra:
        if word:
            found.setdefault(word, -1)
    for match in _ALIAS_RE.finditer(low):
        found.setdefault(_ALIAS_ASCII[match.group(1)], match.start())
    for alias, canon in _ALIAS_OTHER.items():
        at = low.find(alias)
        if at >= 0:
            found.setdefault(canon, at)
    known_low = {k.lower() for k in found}
    guessed: dict[str, int] = {}
    for match in _ASCII.finditer(text):
        token = match.group(0)
        if _proper(token) and not any(token.lower().startswith(k) for k in known_low):
            guessed.setdefault(token, match.start())
    for match in _KATAKANA.finditer(text):
        token = match.group(0)
        if token not in _STOP and token.lower() not in known_low:
            guessed.setdefault(token, match.start())
    ordered = [w for w, _ in sorted(found.items(), key=lambda kv: kv[1])] + [w for w, _ in sorted(guessed.items(), key=lambda kv: kv[1])]
    out: list[str] = []
    seen: set[str] = set()
    for word in ordered:
        if word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out[:MAX_TOPICS]
