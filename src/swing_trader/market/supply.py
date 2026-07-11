"""기관 수급 — 네이버 금융 frgn(외국인·기관 순매매) 무로그인 스크레이프 + 디스크 캐시.

pykrx 투자자별 순매수는 2025~ KRX 로그인 필요(빈 결과) → 네이버 frgn 폴백.
표: 날짜/종가/전일비/등락률/거래량/기관 순매매량/외국인 순매매량/... (기관=위치 5, +=순매수, 주식수).
"""
from __future__ import annotations

import io
import logging
import pickle
import re
from pathlib import Path

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


def _naver_fetch(ticker: str, page: int) -> "str | None":
    import urllib.request
    url = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(req, timeout=10).read().decode("euc-kr", "replace")
    except Exception as e:  # noqa: BLE001 — 네트워크 실패는 None
        log.debug("frgn 조회 실패(%s p%d): %s", ticker, page, e)
        return None


class SupplyProvider:
    """후보 종목의 기관 순매매 시계열 — 네이버 frgn 페이지 누적 + 디스크 캐시."""

    def __init__(self, state_dir, max_pages: int = 60, fetcher=None):
        self.dir = Path(state_dir) / "supply_cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self._fetch = fetcher or _naver_fetch

    def institution_netbuy(self, ticker):
        cache = self.dir / f"{ticker}.pkl"
        if cache.exists():
            try:
                return pickle.loads(cache.read_bytes())
            except Exception:  # noqa: BLE001 — 손상 캐시는 재수집
                pass
        frames: list[pd.Series] = []
        for page in range(1, self.max_pages + 1):
            html = self._fetch(ticker, page)
            if not html:
                break
            s = parse_frgn_html(html)
            if s.empty:
                break
            frames.append(s)
        if not frames:
            return None
        out = pd.concat(frames)
        out = out[~out.index.duplicated(keep="first")].sort_index()
        cache.write_bytes(pickle.dumps(out))
        return out
