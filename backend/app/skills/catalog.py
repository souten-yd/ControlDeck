"""導入できるスキルの一覧。

スキルは OpenCode が読む手順書である（`<dir>/SKILL.md`）。何を作るかではなく
「作る前に何を決めておくか」を書いたもので、たとえば画像なら主題・構図・配色・
安全領域を先に決めてから生成へ渡す、という順番を持たせる。指示が通ったのに
欲しい見た目にならない、という失敗はたいていここを飛ばしたときに起きる。

同梱するものと、外から取ってくるものがある。同梱するものは ControlDeck の
生成機能（media.* / sonic.* の MCP ツール）をそのまま実行先にしているので、
導入すれば動く。外から取ってくるものは、その手順が前提にしている実行環境が
別にあるかどうかで使えるかが決まるので、`requires` に書いて画面へ出す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    summary: str
    version: str
    source: str                      # "bundled" | "git"
    # git のとき。ref は必ず固定する。動く先を指すと、同じ導入操作が別物を持ってくる。
    repository: str = ""
    ref: str = ""
    # git のとき、取り出す部分。丸ごと置くと、その repo の設定や plugin まで
    # 読み込ませることになる。
    subpaths: tuple[str, ...] = ()
    # 使うために別途要るもの。無くても導入はできるが、画面に注意として出す。
    requires: str = ""
    license: str = ""


def _bundled_root() -> Path:
    return Path(__file__).parent / "bundled"


BUNDLED = (
    SkillEntry(
        id="controldeck-image",
        name="画像制作",
        summary="用途から画面寸法・構図・安全領域を先に決めて、MediaForge へ渡す手順。",
        version="1.0.0",
        source="bundled",
        license="MIT",
    ),
    SkillEntry(
        id="controldeck-sound-effects",
        name="効果音制作",
        summary="音源・素材感・空間・長さを組み立ててから SonicForge へ渡す手順。",
        version="1.0.0",
        source="bundled",
        license="MIT",
    ),
    SkillEntry(
        id="controldeck-music",
        name="音楽制作",
        summary="使用場面・曲調・BPM・展開・ループ条件を決めてから SonicForge へ渡す手順。",
        version="1.0.0",
        source="bundled",
        license="MIT",
    ),
    SkillEntry(
        id="controldeck-3d-scene",
        name="3D シーン制作",
        summary="用途・寸法・ポリゴン数・材質・書き出し形式を決めて、3D Studio へ渡す手順。",
        version="1.0.0",
        source="bundled",
        license="MIT",
    ),
)


EXTERNAL = (
    SkillEntry(
        id="blender-skills",
        name="Blender Skills",
        summary="Blender の制作工程を分野別に分けた 95 個の手順（blender-director が入口）。",
        version="2026.07.10",
        source="git",
        repository="https://github.com/arjun988/blender-skills",
        ref="8f778d2405a214b508d4c7d80742be8e43acdd52",
        subpaths=(".claude/skills", ".claude/references"),
        requires=(
            "Blender を起動し BlenderMCP addon（localhost:9876）を繋いだうえで、"
            "その MCP サーバーを OpenCode へ登録する必要がある。ControlDeck の "
            "media.scene.* とはツール体系が違うので、そのままでは実行部分が噛み合わない。"
            "手順そのもの（工程の分け方・ポリゴン予算・参考画像との突き合わせ）は読む価値がある。"
        ),
        license="MIT",
    ),
)


ALL: tuple[SkillEntry, ...] = BUNDLED + EXTERNAL
BY_ID = {entry.id: entry for entry in ALL}


def bundled_source(skill_id: str) -> Path:
    return _bundled_root() / skill_id
