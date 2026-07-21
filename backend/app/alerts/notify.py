"""通知チャンネルへの送信。"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

logger = logging.getLogger("control_deck.alerts")


async def send_notification(channel_type: str, destination: str, title: str, message: str) -> bool:
    if channel_type == "email":
        try:
            settings = json.loads(destination)
            await asyncio.to_thread(_send_email, settings, title, message)
            return True
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError, smtplib.SMTPException) as e:
            # destination、SMTP応答、認証情報はlogへ残さない。
            logger.warning("通知送信失敗 (%s, %s)", channel_type, type(e).__name__)
            return False
    if channel_type == "discord":
        payload = {"content": f"**{title}**\n{message}"}
    elif channel_type == "slack":
        payload = {"text": f"*{title}*\n{message}"}
    else:  # generic webhook
        payload = {"title": title, "message": message}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(destination, json=payload)
        return r.status_code < 400
    except httpx.HTTPError as e:
        # Webhook URLにはtokenが含まれ得るため例外本文を出さない。
        logger.warning("通知送信失敗 (%s, %s)", channel_type, type(e).__name__)
        return False


def _send_email(settings: dict, title: str, message: str) -> None:
    host = settings["host"]
    port = int(settings["port"])
    security = settings["security"]
    username = settings.get("username", "")
    password = settings.get("password", "")
    sender = settings["from_address"]
    recipients = settings["to_addresses"]
    if security not in {"starttls", "tls", "none"} or not isinstance(recipients, list) or not recipients:
        raise ValueError("invalid email destination")

    email = EmailMessage()
    email["Subject"] = title
    email["From"] = sender
    email["To"] = ", ".join(recipients)
    email.set_content(message)

    context = ssl.create_default_context()
    if security == "tls":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(email)
