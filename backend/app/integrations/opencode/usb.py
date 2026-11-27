"""OpenCode のプロジェクトの箱へ渡す USB 機器（M5Stack・ESP32・Android・iOS の開発用）。

設定はプロジェクトごと: mode = off（既定。今の動きを変えない）/ auto（起動のたびに、つながっている開発向けの機器を全部）/
selected（選んだものだけ）。設定は ControlDeck の data（integration の usb-projects.json）に置く。プロジェクトの中
（.controldeck/project.json）には置かない: 箱の中の AI はプロジェクトに書けるので、自分で機器を足せてしまうため。
渡し方は作業場（sandbox.py）と同じ: --device <sysfs から解決した実体> と --group-add keep-groups。iOS は usbmuxd のソケットの bind。
利用者の入力のパスは使わない（devices.plan が今の /sys と /dev から解決した実体だけを返す）。
反映は「箱を起こすとき」: TUI・run は実行ごとに箱を起こすので次の実行から。会話（opencode serve）は常駐なので、
サーバーを起こし直すまで前の機器のまま（router の restart で止められる）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.maintenance import devices

MODES = ("off", "auto", "selected")
MAX_SELECTED = 8


def _store() -> Path:
    from app.integrations.opencode import provider

    return provider._integration_dir() / "usb-projects.json"  # noqa: SLF001


def _load() -> dict:
    try:
        raw = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _key(project: Path) -> str:
    return str(project.resolve())


def get(project: Path) -> dict:
    """{mode, selected}。壊れた・知らない形は off 扱い。"""
    raw = _load().get(_key(project))
    if not isinstance(raw, dict) or raw.get("mode") not in MODES:
        return {"mode": "off", "selected": []}
    selected = [{"kind": e["kind"], "id": e["id"]} for e in raw.get("selected") or [] if devices.valid_entry(e)]
    return {"mode": raw["mode"], "selected": selected[:MAX_SELECTED]}


def put(project: Path, mode: str, selected: list[dict]) -> dict:
    """設定を保存する。selected は今つながっている機器だけ選べる。返す: {mode, selected, before}。"""
    if mode not in MODES:
        raise ValueError("mode は off・auto・selected のどれかです")
    present = {(d["kind"], d["id"]) for d in devices.list_devices()}
    chosen: list[dict] = []
    for item in selected:
        if not devices.valid_entry(item):
            raise ValueError("機器の指定が正しくありません")
        entry = {"kind": item["kind"], "id": item["id"]}
        if (entry["kind"], entry["id"]) not in present:
            raise ValueError(f"機器 {entry['id']} は今つながっていないので選べません")
        if entry not in chosen:
            chosen.append(entry)
    if len(chosen) > MAX_SELECTED:
        raise ValueError(f"選べるのは {MAX_SELECTED} 台までです")
    before = get(project)
    data = _load()
    if mode == "off" and not chosen:
        data.pop(_key(project), None)
    else:
        data[_key(project)] = {"mode": mode, "selected": chosen}
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return {"mode": mode, "selected": chosen, "before": before}


def launch_plan(project: Path) -> dict | None:
    """この起動で箱へ渡すもの。off・渡す機器が無いときは None（今までの argv と同じ）。
    返す: {devices, binds, env, notes}。"""
    setting = get(project)
    if setting["mode"] == "off":
        return None
    entries = devices.auto_entries() if setting["mode"] == "auto" else setting["selected"]
    planned = devices.plan(entries)
    if not planned["items"]:
        return None
    return {"devices": planned["devices"], "binds": planned["binds"],
            "env": {"CONTROLDECK_DEVICES": json.dumps(planned["items"], ensure_ascii=False)},
            "notes": notes_text(planned["items"])}


def notes_text(items: list[dict]) -> str:
    lines = ["# USB 機器（ControlDeck が渡したもの）", "",
             "この箱には次の USB 機器が渡されています。環境変数 CONTROLDECK_DEVICES にも JSON（種類・名前・箱の中のパス）があります。", ""]
    for item in items:
        where = "usbmuxd のソケット（idevice* がこれを通して iPhone と話します）" if item["kind"] == "ios" else "デバイスファイル"
        lines.append(f"- {item['kind']}: {item['name']}（{where} {item['path']}）")
    lines += ["", "守ること:",
              "- sudo・modprobe は使えません（ドライバの操作はできません）。Permission denied になったら、権限を回避せず利用者に伝えてください。",
              "- esptool・platformio・adb・fastboot・idevice* は、箱の中に入っていれば使えます。入っていないときは入れ方を利用者に案内してください"
              "（自分でインストールしないでください）。",
              "- シリアルは上のパスを直接指定します（例: esptool --port <パス>、pio run -t upload --upload-port <パス>）。番号は差し直すと変わります。",
              "- 渡された機器以外には触れません。書き込み・消去は利用者が頼んだときだけ行ってください。"]
    return "\n".join(lines) + "\n"


def attach_notes(config: Path, text: str) -> None:
    """runtime config に説明ファイルを足し、instructions に加える（失敗しても起動は止めない）。"""
    directory = config / "opencode"
    try:
        notes = directory / "controldeck-usb.md"
        notes.write_text(text, encoding="utf-8")
        os.chmod(notes, 0o600)
        path = directory / "opencode.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        instructions = [i for i in data.get("instructions") or [] if i != str(notes)]
        data["instructions"] = [*instructions, str(notes)]
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
