"""有効な Add-on が GPU に置いているものを申告し、頼まれたら退かせる。

これまで、場所を空けられるのは常駐 LLM だけだった。Add-on（画像・音）は時計で
自分の model を降ろすしかなく、短くすれば続けて作るたびに読み直し、長くすれば
他の枠を削る。どちらに振っても片方が痛む。実測:

  2026-09-11  batch のあとに画像 worker が 19.4GB を抱えたまま残り、音楽生成が
              300 秒待って期限切れになった
  2026-09-16  音楽の常駐を 180 秒で片付ける掃除が、走っている音楽 worker を
              殺していた（失敗 12 件の生存時間が 215.1 秒と 245.1 秒に割れ、
              差の 30 秒が掃除の周期だった）

引き金を時計から需要へ移せば、その板挟みが消える。誰も欲しがらない間は抱えた
ままでよく、欲しがられた瞬間に降りる。退くかどうかを決めるのは Add-on 側で、
**走っている処理は切らない**——これは LLM の step_aside と同じ約束である。

申告される量は目安である。worker は別の process なので Add-on からも正確な量は
見えない。ここではその目安を「誰に頼むか」を決めるためだけに使う。空きの判断は
device の観測値が支配する（`admitted_used = max(観測値, 申告値の合計)`）ので、
目安が外れても受け入れが甘くなることはない。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.addons import health, registry, tokens
from app.resources.providers import ProviderReservation, ResourceProvider

logger = logging.getLogger("control_deck.resources")

# 申告を取りに行く先。Add-on 契約 2.0 の任意の入口で、持っていない Add-on は
# 404 を返す——古い Add-on は「何も抱えていない」として扱う。
RESIDENCY_PATH = "/addon/v1/resources/residency"
STEP_ASIDE_PATH = "/addon/v1/resources/step-aside"

# 申告を取りに行く間隔と、1 回の待ち時間。
#
# ここは admission の途中で使う値なので、待たせない方を優先する。申告が少し古くて
# も、空きの判断は device の観測値が支配するので害が小さい。
REFRESH_INTERVAL_SEC = 10.0
RESIDENCY_TIMEOUT_SEC = 2.0
# 退いてもらうのは model を降ろす仕事なので、こちらは長めに待つ。
STEP_ASIDE_TIMEOUT_SEC = 30.0

# 申告できる量の上限。壊れた Add-on が莫大な数を返しても、それに引きずられない。
MAX_DECLARED_BYTES = 128 * 1024**3


class AddonResidencyProvider(ResourceProvider):
    """Add-on が抱えている GPU を broker へ申告し、頼まれたら退かせる。"""

    id = "addons"
    can_step_aside = True
    # 先に頼む。add-on の model は載せ直しに数十秒で、会話は止まらない。
    step_aside_order = 10

    def __init__(self, *, device_id: str = "gpu0") -> None:
        self._device_id = device_id
        # addon_id -> 申告バイト数。取りに行った結果を写しておく。
        self._held: dict[str, int] = {}

    # ── broker から呼ばれる面 ────────────────────────────────────────

    def reservations(self) -> list[ProviderReservation]:
        return [
            ProviderReservation(
                provider_id=self.id,
                device_id=self._device_id,
                owner=f"addon:{addon_id}",
                reserved_bytes=value,
            )
            for addon_id, value in sorted(self._held.items())
            if value > 0
        ]

    async def step_aside(self, device_id: str) -> tuple[bool, str, int]:
        """抱えている Add-on へ、順に一度だけ頼む。

        多く抱えている方から頼む。少ない方から回ると、空けたい量に届くまでに
        何本も降ろすことになり、要らないものまで降ろす。
        """
        if device_id != self._device_id:
            return False, "not_this_device", 0
        ordered = sorted(self._held.items(), key=lambda item: -item[1])
        freed = 0
        reasons: list[str] = []
        for addon_id, value in ordered:
            if value <= 0:
                continue
            released, reason, bytes_freed = await self._ask(addon_id)
            reasons.append(f"{addon_id}:{reason}")
            if released:
                # 降りたなら申告も下げる。次の判断まで古い数を残さない。
                self._held[addon_id] = 0
                freed += bytes_freed
        return freed > 0, ",".join(reasons) or "no_addon_here", freed

    # ── 申告を取りに行く面 ──────────────────────────────────────────

    async def refresh_loop(self) -> None:
        """申告を定期的に取り直す。落ちても service は止めない。"""
        while True:
            try:
                await asyncio.sleep(REFRESH_INTERVAL_SEC)
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 取得の失敗で service を落とさない
                logger.debug("addon residency refresh failed", exc_info=True)

    async def refresh(self) -> dict[str, int]:
        """有効な Add-on に「いま何を抱えているか」を聞いて回る。"""
        held: dict[str, int] = {}
        for item in self._enabled_addons():
            addon_id = str(item.get("id") or "")
            if not addon_id:
                continue
            value = await self._read(addon_id)
            if value > 0:
                held[addon_id] = value
        self._held = held
        return held

    # ── 中身 ────────────────────────────────────────────────────────

    @staticmethod
    def _enabled_addons() -> list[dict[str, Any]]:
        try:
            return [item for item in registry.list_addons() if item.get("enabled")]
        except Exception:  # noqa: BLE001 - 申告の取得で admission を止めない
            return []

    def _url(self, addon_id: str, path: str) -> str | None:
        try:
            current = registry.status(addon_id)
            return health.approved_health_url(current["runtime"]["base_url"], path)
        except Exception:  # noqa: BLE001 - 入口が分からない Add-on は数えない
            return None

    @staticmethod
    def _headers(addon_id: str) -> dict[str, str]:
        token = tokens.issue(addon_id, subject="control-deck", kind="service")
        return {"Authorization": f"Bearer {token}", "X-Control-Deck-Addon-ID": addon_id}

    async def _read(self, addon_id: str) -> int:
        url = self._url(addon_id, RESIDENCY_PATH)
        if url is None:
            return 0
        try:
            async with httpx.AsyncClient(timeout=RESIDENCY_TIMEOUT_SEC) as client:
                response = await client.get(url, headers=self._headers(addon_id))
            if response.status_code == 404:
                # この入口を持たない Add-on。何も抱えていないものとして扱う。
                return 0
            if response.status_code != 200:
                return 0
            body = response.json()
        except Exception:  # noqa: BLE001 - 届かない Add-on は数えない
            return 0
        if not isinstance(body, dict) or str(body.get("device_id") or "") != self._device_id:
            return 0
        try:
            value = int(body.get("reserved_bytes") or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(value, MAX_DECLARED_BYTES))

    async def _ask(self, addon_id: str) -> tuple[bool, str, int]:
        url = self._url(addon_id, STEP_ASIDE_PATH)
        if url is None:
            return False, "no_endpoint", 0
        try:
            async with httpx.AsyncClient(timeout=STEP_ASIDE_TIMEOUT_SEC) as client:
                response = await client.post(url, headers=self._headers(addon_id))
            if response.status_code == 404:
                return False, "not_supported", 0
            if response.status_code != 200:
                return False, f"http_{response.status_code}", 0
            body = response.json()
        except Exception:  # noqa: BLE001 - 退去の失敗で admission を止めない
            return False, "unreachable", 0
        if not isinstance(body, dict):
            return False, "invalid_answer", 0
        released = bool(body.get("released"))
        try:
            freed = int(body.get("freed_bytes") or 0)
        except (TypeError, ValueError):
            freed = 0
        reason = str(body.get("reason") or ("released" if released else "refused"))[:64]
        return released, reason, max(0, min(freed, MAX_DECLARED_BYTES))


# process にひとつ。broker は起動時に一度だけ登録する。
provider = AddonResidencyProvider()
