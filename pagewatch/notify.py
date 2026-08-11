"""Alert delivery. Console, webhook, Telegram.

Every channel takes the same Alert, so adding one is a class with a send().
Delivery failures never propagate: a broken webhook must not stop monitoring.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("pagewatch.notify")


@dataclass
class Alert:
    watch: str
    url: str
    summary: str
    kind: str = "change"        # change | first | blocked | error

    def as_text(self) -> str:
        icon = {"change": "●", "first": "○", "blocked": "▲", "error": "✕"}.get(self.kind, "•")
        return f"{icon} {self.watch}\n{self.summary}\n{self.url}"


class Channel(Protocol):
    def send(self, alert: Alert) -> bool: ...


class ConsoleChannel:
    def send(self, alert: Alert) -> bool:
        print(f"\n{alert.as_text()}\n")
        return True


class WebhookChannel:
    """POSTs JSON. Works with Slack, Discord, n8n, Make, or anything else."""

    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        payload = json.dumps({
            "text": alert.as_text(),
            "watch": alert.watch, "url": alert.url,
            "summary": alert.summary, "kind": alert.kind,
        }).encode()
        req = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status < 400
        except Exception as exc:                    # noqa: BLE001
            log.error("webhook failed", extra={"error": str(exc)})
            return False


class TelegramChannel:
    def __init__(self, token: str, chat_id: str, timeout: float = 10.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": alert.as_text(),
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=self.timeout) as r:
                return r.status < 400
        except Exception as exc:                    # noqa: BLE001
            log.error("telegram failed", extra={"error": str(exc)})
            return False


def build(config: dict) -> list[Channel]:
    """Construct channels from the config's `notify` block."""
    out: list[Channel] = []
    n = config.get("notify") or {}
    if n.get("console", True):
        out.append(ConsoleChannel())
    if n.get("webhook"):
        out.append(WebhookChannel(n["webhook"]))
    tg = n.get("telegram")
    if tg and tg.get("token") and tg.get("chat_id"):
        out.append(TelegramChannel(tg["token"], str(tg["chat_id"])))
    return out


def dispatch(channels: list[Channel], alert: Alert) -> int:
    delivered = 0
    for ch in channels:
        try:
            if ch.send(alert):
                delivered += 1
        except Exception:                           # noqa: BLE001
            log.exception("channel raised", extra={"channel": type(ch).__name__})
    return delivered
