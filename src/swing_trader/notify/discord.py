"""Discord 알림(선택). 웹훅 없으면 콘솔 출력. Hermes 채널과 분리된 별도 웹훅 권장."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


def notify(webhook_url: str | None, content: str) -> bool:
    text = content if len(content) <= 1900 else content[:1900] + "…"
    if not webhook_url:
        print("[notify]\n" + text)
        return False
    try:
        r = requests.post(webhook_url, json={"content": text, "allowed_mentions": {"parse": []}}, timeout=10)
        return r.ok or r.status_code == 204
    except requests.RequestException as e:
        log.warning("Discord 알림 실패: %s", e)
        print("[notify-fallback]\n" + text)
        return False


def notify_embeds(webhook_url: str | None, embeds: list[dict], fallback_text: str = "") -> bool:
    """Discord 임베드(카드) 발송. 웹훅 없으면 fallback_text 를 콘솔 출력."""
    if not embeds:
        return False
    if not webhook_url:
        print("[notify]\n" + (fallback_text or "(embed)"))
        return False
    try:
        r = requests.post(webhook_url, json={"embeds": embeds[:10], "allowed_mentions": {"parse": []}}, timeout=10)
        if not (r.ok or r.status_code == 204):
            log.warning("Discord 임베드 응답 %s: %s", r.status_code, r.text[:200])
        return r.ok or r.status_code == 204
    except requests.RequestException as e:
        log.warning("Discord 임베드 실패: %s", e)
        print("[notify-fallback]\n" + (fallback_text or ""))
        return False
