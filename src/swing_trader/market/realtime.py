"""실시간 시세 — 단타 계획용 당일 시가/현재가. KR=네이버 polling, US=야후.

볼트 노트 가격은 배치 시점 값이라 여기서만 가격을 읽는다(스펙 3b).
US는 KRW 환산(스윙 provider 와 동일 프레임). 정산은 확정 일봉이 정본이므로
여기 값은 '계획 표시/수량 산정'에만 쓴다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
_UA = {"User-Agent": "Mozilla/5.0"}


@dataclass(frozen=True)
class RealtimeQuote:
    price: float
    open: float | None
    prev_close: float | None
    source: str


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", ""))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_naver(j: dict) -> RealtimeQuote | None:
    try:
        d = (j.get("datas") or [{}])[0]
    except (AttributeError, IndexError):
        return None
    price = _num(d.get("closePrice"))
    if price is None:
        return None
    opn = _num(d.get("openPrice"))
    diff = None
    try:
        diff = float(str(d.get("compareToPreviousClosePrice", "")).replace(",", ""))
    except (TypeError, ValueError):
        pass
    prev = price - diff if diff is not None else None
    return RealtimeQuote(price=price, open=opn, prev_close=prev, source="naver")


def _parse_yahoo(j: dict, fx: float) -> RealtimeQuote | None:
    try:
        r = j["chart"]["result"][0]
        meta = r.get("meta", {})
    except (KeyError, IndexError, TypeError):
        return None
    price = _num(meta.get("regularMarketPrice"))
    if price is None:
        return None
    prev = _num(meta.get("chartPreviousClose"))
    opn = None
    try:
        opn = _num((r["indicators"]["quote"][0].get("open") or [None])[0])
    except (KeyError, IndexError, TypeError):
        pass
    return RealtimeQuote(
        price=price * fx, open=opn * fx if opn else None,
        prev_close=prev * fx if prev else None, source="yahoo")


def _is_kr(ticker: str) -> bool:
    c = (ticker or "").split(".")[0]
    return c.isdigit() and len(c) == 6


def get_quote(ticker: str, fx: float = 1400.0) -> RealtimeQuote | None:
    """실패 시 None(호출자가 스킵/경고 처리 — 조용한 폴백 금지)."""
    try:
        if _is_kr(ticker):
            r = requests.get(
                f"https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker.split('.')[0]}",
                headers=_UA, timeout=7)
            return _parse_naver(r.json()) if r.ok else None
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers=_UA, timeout=7)
        return _parse_yahoo(r.json(), fx) if r.ok else None
    except (requests.RequestException, ValueError) as e:
        log.warning("realtime quote 실패 %s: %s", ticker, e)
        return None
