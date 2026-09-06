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
class ExecutionRequirement:
    addon_id: str
    tool_ids: tuple[str, ...]
    capability_path: str
    capability: str
    schema_version: str


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
    adapter: str = ""
    execution: ExecutionRequirement | None = None


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
        summary="94種の制作知識を保持し、ControlDeck対応の blender-director で3D制作を実行。実行範囲は型付き7操作・画像材質・GLB書き出し。",
        version="2026.07.10-cd1",
        source="git",
        repository="https://github.com/arjun988/blender-skills",
        ref="8f778d2405a214b508d4c7d80742be8e43acdd52",
        # references は skills の中にある（SKILL.md が ../references/ で参照する）。
        subpaths=(".claude/skills",),
        requires=(
            "MediaForgeとそのBlender基本環境、利用者のAdd-on実行権限。Blender GUIの常駐やBlenderMCPは不要。"
            "任意Python・リギング・シミュレーション等は未対応。上流94種を全操作対応として公開しません。"
        ),
        license="MIT",
        adapter="mediaforge-blender",
        execution=ExecutionRequirement(
            addon_id="media-forge",
            tool_ids=("media.capabilities", "media.scene.create", "media.scene.edit", "media.scene.snapshot",
                      "media.scene.material", "media.scene.export", "media.job.status", "media.job.cancel"),
            capability_path="/api/v1/capabilities",
            capability="3d.scene_recipe",
            schema_version="media-forge.scene-recipe@1",
        ),
    ),
)


ALL: tuple[SkillEntry, ...] = BUNDLED + EXTERNAL
BY_ID = {entry.id: entry for entry in ALL}


def bundled_source(skill_id: str) -> Path:
    return _bundled_root() / skill_id
