"""通知チャンネルへの送信。"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("control_deck.alerts")


async def send_notification(channel_type: str, url: str, title: str, message: str) -> bool:
    if channel_type == "discord":
        payload = {"content": f"**{title}**\n{message}"}
    elif channel_type == "slack":
        payload = {"text": f"*{title}*\n{message}"}
    else:  # generic webhook
        payload = {"title": title, "message": message}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
        return r.status_code < 400
    except httpx.HTTPError as e:
        logger.warning("通知送信失敗 (%s): %s", channel_type, e)
        return False
