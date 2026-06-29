"""시장 데이터 제공자 — pykrx/yfinance(있으면) → 없으면 결정적 합성 OHLCV.

키/네트워크 없이도 scan/run-once/backtest 가 동작하도록 synthetic 폴백을 둔다.
synthetic 은 ticker 시드 기반이라 재현 가능(테스트/데모용, 실거래 아님).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _is_kr(ticker: str) -> bool:
    t = ticker.split(".")[0]
    return t.isdigit() and len(t) == 6


def _synthetic(ticker: str, days: int) -> pd.DataFrame:
    seed = abs(hash(ticker)) % (2**32)
    rng = np.random.default_rng(seed)
    base = 50000 if _is_kr(ticker) else 100.0
    # 완만한 상승추세 + 노이즈 (스윙 셋업 다양성 위해 종목별 드리프트 차등)
    drift = 0.0003 + (seed % 7) * 0.0002
    rets = rng.normal(drift, 0.018, days)
    close = base * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.012, days)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, days)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.integers(300_000, 3_000_000, days).astype(float)
    vol[-3:] *= rng.uniform(1.0, 2.5)  # 최근 거래량 변동
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx
    )


def _try_pykrx(ticker: str, days: int) -> pd.DataFrame | None:
    if not _is_kr(ticker):
        return None
    try:
        import contextlib
        import io

        from pykrx import stock  # type: ignore

        end = pd.Timestamp.today().strftime("%Y%m%d")
        start = (pd.Timestamp.today() - pd.Timedelta(days=days * 2)).strftime("%Y%m%d")
        # pykrx 내부의 'KRX 로그인 실패' 등 노이즈 억제(개별종목 OHLCV는 로그인 없이 동작).
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = stock.get_market_ohlcv(start, end, ticker.split(".")[0])
        if df is None or df.empty:
            return None
        df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"})
        return df[["open", "high", "low", "close", "volume"]].astype(float).tail(days)
    except Exception as e:  # noqa: BLE001
        log.debug("pykrx 실패(%s): %s", ticker, e)
        return None


def _try_yfinance(ticker: str, days: int) -> pd.DataFrame | None:
    try:
        import yfinance as yf  # type: ignore

        # 한국 6자리 코드는 KOSPI(.KS)·KOSDAQ(.KQ) 둘 다 시도(어느 시장인지 코드만으론 불명).
        if _is_kr(ticker) and "." not in ticker:
            candidates = [f"{ticker}.KS", f"{ticker}.KQ"]
        else:
            candidates = [ticker]
        for sym in candidates:
            df = yf.download(sym, period=f"{max(days, 90)}d", progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                df = df.rename(columns=str.lower)
                return df[["open", "high", "low", "close", "volume"]].astype(float).tail(days)
        return None
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance 실패(%s): %s", ticker, e)
        return None


class DataProvider:
    def __init__(self, provider: str = "auto", lookback_days: int = 120, fx_usdkrw: float = 1400.0):
        self.provider = provider
        self.lookback = lookback_days
        self.fx = fx_usdkrw  # 미국주(USD) → KRW 환산(100만원 KRW 시드 기준 사이징 일관성)
        self.sources: dict[str, str] = {}  # ticker→마지막 수집 출처(pykrx/yfinance/synthetic). 건전성 점검용

    def _to_krw(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if _is_kr(ticker):
            return df
        df = df.copy()
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * self.fx
        return df

    def get_ohlcv(self, ticker: str) -> tuple[pd.DataFrame, str]:
        """(df, source) 반환. 가격은 KRW 기준(미국주는 환산). source: pykrx|yfinance|synthetic."""
        df, source = self._fetch(ticker)
        if ticker:
            self.sources[ticker] = source  # 건전성 점검(전부 synthetic=수집 실패) 판정용
        return df, source

    def _fetch(self, ticker: str) -> tuple[pd.DataFrame, str]:
        if not ticker:
            return self._to_krw(_synthetic("UNKNOWN", self.lookback), "UNKNOWN"), "synthetic"
        if self.provider in ("auto", "pykrx"):
            df = _try_pykrx(ticker, self.lookback)
            if df is not None and len(df) >= 20:
                return df, "pykrx"  # 이미 KRW
        if self.provider in ("auto", "yfinance"):
            df = _try_yfinance(ticker, self.lookback)
            if df is not None and len(df) >= 20:
                return self._to_krw(df, ticker), "yfinance"
        return self._to_krw(_synthetic(ticker, self.lookback), ticker), "synthetic"
