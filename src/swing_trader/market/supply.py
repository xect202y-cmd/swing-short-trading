"""기관 수급 — 네이버 금융 frgn(외국인·기관 순매매) 무로그인 스크레이프 + 디스크 캐시.

pykrx 투자자별 순매수는 2025~ KRX 로그인 필요(빈 결과) → 네이버 frgn 폴백.
표: 날짜/종가/전일비/등락률/거래량/기관 순매매량/외국인 순매매량/... (기관=위치 5, +=순매수, 주식수).
"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd

log = logging.getLogger(__name__)
_DATE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def parse_frgn_html(html: str) -> pd.Series:
    """네이버 frgn HTML → 기관 순매매량 Series(index 'YYYY-MM-DD' 오름차순)."""
    try:
        tables = pd.read_html(io.StringIO(html))
    except (ValueError, ImportError):
        return pd.Series(dtype=float)
    for t in tables:
        if t.shape[1] < 9:
            continue
        rows: dict[str, float] = {}
        for _, row in t.dropna(how="all").iterrows():
            d = str(row.iloc[0]).strip()
            if not _DATE.match(d):
                continue
            try:
                inst = float(str(row.iloc[5]).replace(",", "").strip())
            except (ValueError, TypeError):
                continue
            rows[d.replace(".", "-")] = inst
        if rows:
            return pd.Series(rows).sort_index()
    return pd.Series(dtype=float)


def supply_ok(netbuy: "pd.Series | None", entry_date: str, supply_days: int) -> "bool | None":
    """진입일까지 최근 supply_days 기관 순매수 게이트. 데이터 부족/None → None(판단 위임)."""
    if netbuy is None or len(netbuy) == 0:
        return None
    s = netbuy[netbuy.index <= entry_date].tail(supply_days)
    if len(s) < supply_days:
        return None
    positives = int((s > 0).sum())
    return bool(s.sum() > 0 and positives >= supply_days - 1)
