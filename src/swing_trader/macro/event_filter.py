"""이벤트 필터 — 캘린더에서 D-0~D-2 대형 이벤트 감지, 종목/섹터 위험도 반환."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..models import RiskLevel

_DATE_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")


@dataclass
class UpcomingEvent:
    when: date
    d_minus: int          # 0=오늘, 1=내일, 2=모레
    title: str
    is_block: bool        # 대형 이벤트(신규매수 제한)


@dataclass
class EventState:
    events: list[UpcomingEvent] = field(default_factory=list)

    @property
    def has_block(self) -> bool:
        return any(e.is_block for e in self.events)

    def market_risk(self) -> RiskLevel:
        if any(e.is_block and e.d_minus <= 1 for e in self.events):
            return RiskLevel.HIGH
        if self.events:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def ticker_risk(self, note_text: str, sector: str | None) -> RiskLevel:
        """종목명/섹터 토큰이 임박 이벤트 제목에 언급되면 위험 가중."""
        base = self.market_risk()
        tokens = [t for t in [(sector or "").strip(), *(note_text or "").split()] if len(t) >= 2]
        for e in self.events:
            if e.d_minus <= 2 and any(tok in e.title for tok in tokens):
                return RiskLevel.HIGH
        return base


def parse_events(calendar_md: str, today: date, block_keywords: list[str], window_days: int = 2) -> EventState:
    st = EventState()
    if not calendar_md:
        return st
    for line in calendar_md.splitlines():
        m = _DATE_RE.search(line)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        d_minus = (d - today).days
        if d_minus < 0 or d_minus > window_days:
            continue
        title = line.strip().lstrip("-*|").strip()
        is_block = any(kw.lower() in line.lower() for kw in block_keywords)
        st.events.append(UpcomingEvent(when=d, d_minus=d_minus, title=title[:120], is_block=is_block))
    st.events.sort(key=lambda e: e.d_minus)
    return st
