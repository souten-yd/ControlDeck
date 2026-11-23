"""作業場へ渡せる USB 機器（第 1 段: シリアルと adb の USB だけ）。

読むだけ: /dev/serial/by-id と /sys/bus/usb/devices。機器の操作はしない。
渡す機器の選び方（設計は docs/design-device-dev.md）:
  - serial: /dev/serial/by-id の名前 1 つ（抜き差しで ttyUSB の番号が変わっても同じ機器を指す）。実体は ttyUSB* / ttyACM* だけ
  - adb: USB の「ベンダー:製品:シリアル」。adb のインターフェース（クラス ff・サブクラス 42・プロトコル 01）を持つ機器だけ。
    シリアルを持たない機器は 1 台に決められないので選べない。実体は /dev/bus/usb/BBB/DDD
渡す直前に毎回解決する（番号は抜き差しで変わる）。存在しない・形が違うものは渡さず警告。
"""
import re
from pathlib import Path

SYS_ROOT = Path("/sys")
DEV_ROOT = Path("/dev")

KINDS = ("serial", "adb")
SERIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,200}$")
ADB_ID = re.compile(r"^([0-9a-f]{4}):([0-9a-f]{4}):([A-Za-z0-9._:+-]{1,128})$")
SERIAL_NODE = re.compile(r"^tty(USB|ACM)\d{1,3}$")
USB_NODE = re.compile(r"^bus/usb/\d{3}/\d{3}$")


def valid_entry(entry: object) -> bool:
    """meta に保存してよい形か。名前に / や .. を含むものは通さない。"""
    if not isinstance(entry, dict) or set(entry) - {"kind", "id"}:
        return False
    kind, ident = entry.get("kind"), entry.get("id")
    if not isinstance(ident, str):
        return False
    if kind == "serial":
        return bool(SERIAL_ID.match(ident)) and ".." not in ident
    if kind == "adb":
        return bool(ADB_ID.match(ident))
    return False


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _tail(value: str, count: int = 4) -> str:
    return value[-count:] if value else ""


def _usb_devices() -> list[dict]:
    """sysfs の USB 機器（インターフェースを除く）。adb 対応かも調べる。"""
    base = SYS_ROOT / "bus" / "usb" / "devices"
    found = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    for entry in entries:
        if ":" in entry.name or entry.name.startswith("usb"):
            continue                    # インターフェース・ルートハブ
        vendor, product = _read(entry / "idVendor").lower(), _read(entry / "idProduct").lower()
        if not (re.fullmatch(r"[0-9a-f]{4}", vendor) and re.fullmatch(r"[0-9a-f]{4}", product)):
            continue
        adb = False
        try:
            for iface in entry.iterdir():
                if iface.name.startswith(entry.name + ":") and (
                        _read(iface / "bInterfaceClass").lower(), _read(iface / "bInterfaceSubClass").lower(),
                        _read(iface / "bInterfaceProtocol").lower()) == ("ff", "42", "01"):
                    adb = True
        except OSError:
            pass
        try:
            node = f"bus/usb/{int(_read(entry / 'busnum')):03d}/{int(_read(entry / 'devnum')):03d}"
        except ValueError:
            node = ""
        found.append({"vendor_id": vendor, "product_id": product, "manufacturer": _read(entry / "manufacturer"),
                      "product": _read(entry / "product"), "serial": _read(entry / "serial"), "adb": adb, "node": node})
    return found


def _serial_by_id() -> list[dict]:
    base = DEV_ROOT / "serial" / "by-id"
    found = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    for entry in entries:
        try:
            real = entry.resolve(strict=True)
        except OSError:
            continue
        if real.parent != DEV_ROOT.resolve() or not SERIAL_NODE.match(real.name) or not SERIAL_ID.match(entry.name):
            continue
        found.append({"id": entry.name, "node": real.name})
    return found


def list_devices() -> list[dict]:
    """渡せる機器の一覧。画面用に名前・ベンダー・シリアルの末尾だけを出す（シリアル全体は返さない）。"""
    usb = _usb_devices()
    items = []
    for item in _serial_by_id():
        # by-id の名前は usb-<ベンダー>_<製品>_<シリアル>-if00-port0 の形
        items.append({"kind": "serial", "id": item["id"], "label": item["id"], "path": f"/dev/{item['node']}",
                      "vendor": "", "serial_tail": _tail(re.sub(r"-if\d+.*$", "", item["id"]).rsplit("_", 1)[-1])})
    for dev in usb:
        if dev["adb"] and dev["serial"] and ADB_ID.match(f"{dev['vendor_id']}:{dev['product_id']}:{dev['serial']}") and dev["node"]:
            items.append({"kind": "adb", "id": f"{dev['vendor_id']}:{dev['product_id']}:{dev['serial']}",
                          "label": dev["product"] or f"{dev['vendor_id']}:{dev['product_id']}",
                          "path": f"/dev/{dev['node']}", "vendor": dev["manufacturer"], "serial_tail": _tail(dev["serial"])})
    return items


def resolve(entries: list[dict]) -> tuple[list[str], list[str]]:
    """選んだ機器を、今の /dev の実体へ解決する。返すのは (--device に渡す実体のパス, 警告)。
    今の一覧に無いもの（抜けている・形が違う）は渡さない。"""
    present = {(item["kind"], item["id"]): item["path"] for item in list_devices()}
    paths, warnings = [], []
    for entry in entries:
        if not valid_entry(entry):
            warnings.append("機器の指定の形が正しくないので渡しません")
            continue
        path = present.get((entry["kind"], entry["id"]))
        if path is None:
            warnings.append(f"機器 {entry['id']} が見つからないので渡しません（抜けている可能性）")
            continue
        real = str(DEV_ROOT / path[len("/dev/"):])
        if real not in paths:
            paths.append(real)
    return paths, warnings
