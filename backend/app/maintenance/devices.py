"""箱（作業場・OpenCode のプロジェクト）へ渡せる USB 機器。

読むだけ: /sys（/sys/class/tty・/sys/bus/usb/devices）と /dev。機器への書き込み・ドライバの操作はしない。
渡す機器の選び方（設計は docs/design-device-dev.md）:
  - serial: /sys/class/tty の tty*（名前は ^tty[A-Za-z0-9]{1,24}$）のうち、親に USB のインターフェースを持つもの
    （ttyUSB・ttyACM・WCH の ttyCH343USB など。ドライバ名は決め打ちしない）。ブロックデバイス（sd* など）は出さない。
    id は /dev/serial/by-id の名前があればそれ、無ければ usb:<vendor>:<product>:<serial>（serial が無い・使えない文字なら
    usb:<vendor>:<product>@<USB のポートのパス>）。抜き差しで tty の番号が変わっても同じ機器を指す
  - adb / fastboot: USB の「ベンダー:製品:シリアル」。adb は ff/42/01、fastboot は ff/42/03 のインターフェースを持つ機器。
    シリアルを持たない機器は 1 台に決められないので選べない。実体は /dev/bus/usb/BBB/DDD
  - ios: ベンダー 05ac で usbmux のインターフェース（ff/fe/02）を持つ機器。渡すのは機器ではなくホストの usbmuxd のソケット
    （箱の中の idevice* がそれを通して話す）。usbmuxd が動いていなければ「渡せない」と表示するだけ（入れない）
渡す直前に毎回解決する（番号は抜き差しで変わる）。利用者の入力のパスは使わず、sysfs から解決した実体だけを返す。
"""
import re
import shutil
import stat
from pathlib import Path

SYS_ROOT = Path("/sys")
DEV_ROOT = Path("/dev")
USBMUXD_SOCKET = Path("/var/run/usbmuxd")

KINDS = ("serial", "adb", "fastboot", "ios")
SERIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@-]{0,200}$")
USB_ID = re.compile(r"^([0-9a-f]{4}):([0-9a-f]{4}):([A-Za-z0-9._:+-]{1,128})$")
ADB_ID = USB_ID
TTY_NAME = re.compile(r"^tty[A-Za-z0-9]{1,24}$")
SAFE_SERIAL = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
DEVPATH = re.compile(r"^[0-9][0-9.\-]{0,40}$")
USB_NODE = re.compile(r"^bus/usb/\d{3}/\d{3}$")

# 表示のためだけの推定（判定には使わない。増やし続けない）
GUESS = {("1a86", "55d4"): "M5Stack など（CH9102）", ("1a86", "7523"): "M5Stack・ESP32 など（CH340）",
         ("10c4", "ea60"): "ESP32 など（CP210x）"}

# 箱の中で使う道具（無ければ画面に出す。自動では入れない）
TOOLS = (("esptool", ("esptool", "esptool.py"), "pip install esptool（ESP32・M5Stack の書き込み）"),
         ("platformio", ("pio", "platformio"), "pip install platformio（M5・Arduino のビルドと書き込み）"),
         ("adb", ("adb",), "sudo apt install adb（Android）"),
         ("fastboot", ("fastboot",), "sudo apt install fastboot（Android の書き込み）"),
         ("libimobiledevice", ("idevice_id",), "sudo apt install libimobiledevice-utils（iOS）"))


def valid_entry(entry: object) -> bool:
    """meta に保存してよい形か。名前に / や .. を含むものは通さない。"""
    if not isinstance(entry, dict) or set(entry) - {"kind", "id"}:
        return False
    kind, ident = entry.get("kind"), entry.get("id")
    if not isinstance(ident, str):
        return False
    if kind == "serial":
        return bool(SERIAL_ID.match(ident)) and ".." not in ident
    if kind in ("adb", "fastboot", "ios"):
        return bool(USB_ID.match(ident))
    return False


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _tail(value: str, count: int = 4) -> str:
    return value[-count:] if value else ""


def _iface_kind(iface: Path) -> tuple[str, str, str]:
    return (_read(iface / "bInterfaceClass").lower(), _read(iface / "bInterfaceSubClass").lower(),
            _read(iface / "bInterfaceProtocol").lower())


def _usb_devices() -> list[dict]:
    """sysfs の USB 機器（インターフェースを除く）。adb・fastboot・iOS 対応かも調べる。"""
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
        adb = fastboot = ios = False
        try:
            for iface in entry.iterdir():
                if not iface.name.startswith(entry.name + ":"):
                    continue
                triple = _iface_kind(iface)
                adb = adb or triple == ("ff", "42", "01")
                fastboot = fastboot or triple == ("ff", "42", "03")
                ios = ios or (vendor == "05ac" and triple == ("ff", "fe", "02"))
        except OSError:
            pass
        try:
            node = f"bus/usb/{int(_read(entry / 'busnum')):03d}/{int(_read(entry / 'devnum')):03d}"
        except ValueError:
            node = ""
        found.append({"vendor_id": vendor, "product_id": product, "manufacturer": _read(entry / "manufacturer"),
                      "product": _read(entry / "product"), "serial": _read(entry / "serial"), "adb": adb,
                      "fastboot": fastboot, "ios": ios, "node": node, "devpath": entry.name})
    return found


def _by_id() -> dict[str, str]:
    """tty の名前 → /dev/serial/by-id の名前（直下の実体だけ）。"""
    base = DEV_ROOT / "serial" / "by-id"
    found: dict[str, str] = {}
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            real = entry.resolve(strict=True)
        except OSError:
            continue
        if real.parent == DEV_ROOT.resolve() and TTY_NAME.match(real.name) and SERIAL_ID.match(entry.name):
            found.setdefault(real.name, entry.name)
    return found


def _usb_parent(tty: Path) -> Path | None:
    """tty の device をたどり、USB のインターフェース（名前に : を含み、親が idVendor を持つ）を探す。"""
    try:
        node = (tty / "device").resolve(strict=True)
        sys_real = SYS_ROOT.resolve()
    except OSError:
        return None
    for _ in range(5):
        if node == sys_real or sys_real not in node.parents:
            return None
        if ":" in node.name and (node.parent / "idVendor").is_file():
            return node
        node = node.parent
    return None


def _serial_ttys() -> list[dict]:
    base = SYS_ROOT / "class" / "tty"
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    by_id = _by_id()
    found, seen = [], set()
    for entry in entries:
        if not TTY_NAME.match(entry.name):
            continue
        iface = _usb_parent(entry)
        try:
            mode = (DEV_ROOT / entry.name).stat().st_mode
        except OSError:
            continue                    # /dev に無いものは渡せない
        if iface is None or stat.S_ISBLK(mode):
            continue
        usb = iface.parent
        vendor, product = _read(usb / "idVendor").lower(), _read(usb / "idProduct").lower()
        serial = _read(usb / "serial")
        if not (re.fullmatch(r"[0-9a-f]{4}", vendor) and re.fullmatch(r"[0-9a-f]{4}", product)):
            continue
        if entry.name in by_id:
            ident = by_id[entry.name]
        elif SAFE_SERIAL.match(serial):
            ident = f"usb:{vendor}:{product}:{serial}"
        elif DEVPATH.match(usb.name):
            ident = f"usb:{vendor}:{product}@{usb.name}"
        else:
            continue
        if ident in seen or not SERIAL_ID.match(ident):
            continue
        seen.add(ident)
        found.append({"id": ident, "node": entry.name, "vendor_id": vendor, "product_id": product, "serial": serial,
                      "manufacturer": _read(usb / "manufacturer"), "product": _read(usb / "product")})
    return found


def _guess(vendor: str, product: str) -> str:
    if vendor == "303a":
        return "ESP32-S3（USB 直結）"
    return GUESS.get((vendor, product), "")


def usbmuxd_running() -> bool:
    try:
        return stat.S_ISSOCK(USBMUXD_SOCKET.stat().st_mode)
    except OSError:
        return False


def list_devices() -> list[dict]:
    """渡せる機器の一覧。画面用に名前・ベンダー・シリアルの末尾だけを出す（シリアル全体は返さない）。
    ios は usbmuxd のソケットが無いと available=False（note に理由）。"""
    items = []
    for item in _serial_ttys():
        items.append({"kind": "serial", "id": item["id"], "label": item["product"] or item["id"],
                      "path": f"/dev/{item['node']}", "vendor": item["manufacturer"],
                      "serial_tail": _tail(item["serial"]), "guess": _guess(item["vendor_id"], item["product_id"]),
                      "available": True, "note": ""})
    muxd = usbmuxd_running()
    for dev in _usb_devices():
        ident = f"{dev['vendor_id']}:{dev['product_id']}:{dev['serial']}"
        if not (dev["serial"] and USB_ID.match(ident)):
            continue
        base = {"id": ident, "label": dev["product"] or f"{dev['vendor_id']}:{dev['product_id']}",
                "vendor": dev["manufacturer"], "serial_tail": _tail(dev["serial"]), "guess": "",
                "available": True, "note": ""}
        for kind in ("adb", "fastboot"):
            if dev[kind] and dev["node"]:
                items.append({**base, "kind": kind, "path": f"/dev/{dev['node']}"})
        if dev["ios"]:
            items.append({**base, "kind": "ios", "path": str(USBMUXD_SOCKET), "available": muxd,
                          "note": "" if muxd else "ホストで usbmuxd を動かす必要があります"})
    return items


def tool_status(path: str | None = None) -> list[dict]:
    """箱・ホストに書き込みの道具があるか（読むだけ。入れ方は案内のみで、自動では入れない）。"""
    return [{"name": name, "ok": any(shutil.which(b, path=path) for b in binaries), "hint": hint}
            for name, binaries, hint in TOOLS]


def plan(entries: list[dict]) -> dict:
    """選んだ機器を、今の /dev の実体へ解決する。
    返す: {devices: --device に渡す実体のパス, binds: [(ホストのパス, 箱のパス)]（iOS の usbmuxd）,
    items: 渡す機器の説明, warnings}。今の一覧に無いもの（抜けている・形が違う・usbmuxd が無い）は渡さない。"""
    present = {(item["kind"], item["id"]): item for item in list_devices()}
    out: dict = {"devices": [], "binds": [], "items": [], "warnings": []}
    for entry in entries:
        if not valid_entry(entry):
            out["warnings"].append("機器の指定の形が正しくないので渡しません")
            continue
        item = present.get((entry["kind"], entry["id"]))
        if item is None:
            out["warnings"].append(f"機器 {entry['id']} が見つからないので渡しません（抜けている可能性）")
            continue
        if not item["available"]:
            out["warnings"].append(f"機器 {entry['id']} は渡せません（{item['note']}）")
            continue
        if item["kind"] == "ios":
            real = str(USBMUXD_SOCKET)
            if (USBMUXD_SOCKET, USBMUXD_SOCKET) not in out["binds"]:
                out["binds"].append((USBMUXD_SOCKET, USBMUXD_SOCKET))
        else:
            real = str(DEV_ROOT / item["path"][len("/dev/"):])
            if real not in out["devices"]:
                out["devices"].append(real)
        out["items"].append({"kind": item["kind"], "name": item["guess"] or item["label"], "path": real})
    return out


def resolve(entries: list[dict]) -> tuple[list[str], list[str]]:
    """plan の互換。返すのは (--device に渡す実体のパス, 警告)。iOS のソケットの bind は plan を見る。"""
    result = plan(entries)
    return result["devices"], result["warnings"]


def auto_entries() -> list[dict]:
    """「自動」: つながっている開発向けの機器（シリアル・adb・fastboot・渡せる ios）を全部。"""
    return [{"kind": i["kind"], "id": i["id"]} for i in list_devices() if i["available"]]
