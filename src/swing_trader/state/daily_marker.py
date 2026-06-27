"""액티브-패시브 페일오버용 일일 실행 마커.

state_dir/daily_done.json: { "YYYY-MM-DD": { "us": "<iso ts>", "kr": "<iso ts>" } }
오늘 날짜 아래 시장 키가 있으면 그 시장 런이 성공 완료된 것. 클라우드는 이를 읽어
로컬이 이미 돌린 시장을 건너뛴다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
_MARKETS = ("us", "kr")
_KEEP_DAYS = 7
_FILE = "daily_done.json"


def today_kst() -> date:
    return datetime.now(KST).date()


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / _FILE


def _load(state_dir: Path) -> dict:
    p = _path(state_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _parse(key: str) -> date | None:
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


def _prune(data: dict, today: date, keep_days: int = _KEEP_DAYS) -> dict:
    cutoff = today - timedelta(days=keep_days)
    out = {}
    for k, v in data.items():
        d = _parse(k)
        if d is not None and d >= cutoff:
            out[k] = v
    return out


def is_done(state_dir: Path, market: str, today: date) -> bool:
    return market in _load(state_dir).get(today.isoformat(), {})


def record_done(state_dir: Path, market: str, now: datetime) -> None:
    if market == "all":
        for m in _MARKETS:
            record_done(state_dir, m, now)
        return
    today = now.astimezone(KST).date()
    data = _prune(_load(state_dir), today)
    data.setdefault(today.isoformat(), {})[market] = now.astimezone(KST).isoformat()
    _path(state_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
