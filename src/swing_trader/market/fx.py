"""실시간 USD/KRW 환율 — yfinance 'KRW=X'. 실패 시 config 폴백. 프로세스 내 캐시(1회 조회)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_cache: dict[str, float] = {}


def get_usdkrw(fallback: float = 1400.0) -> float:
    if "rate" in _cache:
        return _cache["rate"]
    try:
        import yfinance as yf  # type: ignore
        df = yf.download("KRW=X", period="5d", progress=False)
        if df is not None and len(df):
            rate = float(df["Close"].iloc[-1].item())
            if 800 < rate < 2500:                      # 정상범위 sanity
                _cache["rate"] = round(rate, 2)
                log.info("실시간 환율 USD/KRW = %.2f", _cache["rate"])
                return _cache["rate"]
    except Exception as e:  # noqa: BLE001
        log.warning("실시간 환율 조회 실패 → fallback %.0f: %s", fallback, e)
    _cache["rate"] = fallback
    return fallback


def reset_cache() -> None:
    _cache.clear()
